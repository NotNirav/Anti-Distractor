"""Event engine coordinating debounce, dwell checks, heartbeat, and three-tier judge calls."""

from __future__ import annotations

from collections import deque
import logging
import threading
import time
from typing import Callable

from app.cache import VerdictCache
from app.config import TimingConfig
import app.judge as judge_module
from app.models import (
    ActivityContext,
    GoalProfile,
    JudgeVerdict,
    TabInfo,
    TabSnapshot,
    TriggerType,
    WindowInfo,
)
from app.profile import normalize_domain
from app.server import TabSnapshotStore
import app.window_watcher as window_watcher_module

logger = logging.getLogger(__name__)


class EventEngine:
    """Coordinates triggers, debouncing, borderline dwell checks, heartbeat, and judge calls."""

    def __init__(
        self,
        goal: str,
        profile: GoalProfile,
        cache: VerdictCache,
        tab_store: TabSnapshotStore,
        timings: TimingConfig,
        api_key: str,
        on_verdict: Callable[[ActivityContext, JudgeVerdict], None],
        *,
        get_foreground_fn: Callable[[], WindowInfo | None] | None = None,
        list_windows_fn: Callable[[], list[str]] | None = None,
        evaluate_fn: Callable[..., JudgeVerdict] | None = None,
        clock_fn: Callable[[], float] | None = None,
        rate_limit_backoff_seconds: float | None = None,
    ) -> None:
        self.goal = goal
        self.profile = profile
        self.cache = cache
        self.tab_store = tab_store
        self.timings = timings
        self.api_key = api_key
        self.on_verdict = on_verdict

        self._get_foreground_fn = get_foreground_fn
        self._list_windows_fn = list_windows_fn
        self._evaluate_fn = evaluate_fn
        self._clock_fn = clock_fn
        self._rate_limit_backoff_seconds = (
            rate_limit_backoff_seconds
            if rate_limit_backoff_seconds is not None
            else min(max(self.timings.heartbeat_seconds, 5.0), 60.0)
        )

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._sync_check_lock = threading.Lock()

        self._running = False
        self._stopped = False
        self._ever_started = False
        self._thread: threading.Thread | None = None

        self._pending_trigger: TriggerType | None = None
        self._debounce_deadline: float | None = None
        self._immediate_queue: deque[tuple[TriggerType, bool]] = deque()
        self._next_heartbeat_at: float | None = None
        self._dwell_deadline: float | None = None
        self._backoff_until: float = 0.0

    def _now(self) -> float:
        if self._clock_fn is not None:
            return self._clock_fn()
        return time.monotonic()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        """Start timers (heartbeat, dwell monitor, debounce worker) and trigger baseline check."""
        with self._lock:
            if self._running:
                return
            now = self._now()
            self._running = True
            self._stopped = False
            self._ever_started = True
            self._pending_trigger = None
            self._debounce_deadline = None
            self._dwell_deadline = None
            self._immediate_queue.clear()
            self._immediate_queue.append(("baseline", True))
            self._next_heartbeat_at = now + self.timings.heartbeat_seconds

            self._thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="EventEngineThread",
            )
            self._thread.start()
            self._cond.notify_all()

    def stop(self) -> None:
        """Stop all timers and worker threads cleanly."""
        with self._lock:
            self._running = False
            self._stopped = True
            self._pending_trigger = None
            self._debounce_deadline = None
            self._dwell_deadline = None
            self._next_heartbeat_at = None
            self._immediate_queue.clear()
            thread = self._thread
            self._cond.notify_all()

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def submit_trigger(self, trigger_type: TriggerType) -> None:
        """Schedule a debounced evaluation for a tab or window trigger."""
        with self._lock:
            if not self._running:
                return
            now = self._now()
            self._pending_trigger = trigger_type
            self._debounce_deadline = now + self.timings.debounce_seconds
            # Reset dwell countdown on new activity; it will re-arm after the check if still borderline
            self._dwell_deadline = None
            self._cond.notify_all()

    def run_immediate_check(
        self,
        trigger_type: TriggerType = "manual_recheck",
        force_vision: bool = True,
    ) -> None:
        """Bypass debounce to immediately evaluate current context (e.g. baseline or lockout end)."""
        run_sync = False
        with self._lock:
            if self._stopped:
                return
            if self._running:
                self._pending_trigger = None
                self._debounce_deadline = None
                self._dwell_deadline = None
                self._immediate_queue.append((trigger_type, force_vision))
                self._cond.notify_all()
                return
            if not self._ever_started:
                run_sync = True

        if run_sync:
            with self._sync_check_lock:
                self._execute_check(trigger_type, force_vision)

    def _worker_loop(self) -> None:
        while True:
            task: tuple[TriggerType, bool] | None = None
            with self._lock:
                while self._running:
                    now = self._now()

                    if self._immediate_queue:
                        task = self._immediate_queue.popleft()
                        break

                    if self._debounce_deadline is not None and now >= self._debounce_deadline:
                        trig = self._pending_trigger or "tab_updated"
                        self._pending_trigger = None
                        self._debounce_deadline = None
                        task = (trig, False)
                        break

                    if self._dwell_deadline is not None and now >= self._dwell_deadline:
                        self._dwell_deadline = None
                        task = ("dwell", True)
                        break

                    if self._next_heartbeat_at is not None and now >= self._next_heartbeat_at:
                        self._next_heartbeat_at = now + self.timings.heartbeat_seconds
                        task = ("heartbeat", True)
                        break

                    deadlines = [
                        d
                        for d in (
                            self._debounce_deadline,
                            self._dwell_deadline,
                            self._next_heartbeat_at,
                        )
                        if d is not None
                    ]
                    if deadlines:
                        wait_s = max(0.0, min(deadlines) - now)
                        if self._clock_fn is not None:
                            wait_s = min(wait_s, 0.01)
                        self._cond.wait(timeout=wait_s)
                    else:
                        self._cond.wait()

                if not self._running:
                    return

            if task is not None:
                trigger_type, force_vision = task
                try:
                    self._execute_check(trigger_type, force_vision)
                except Exception:
                    logger.exception(
                        "Unhandled exception in EventEngine check (%s); keeping engine alive.",
                        trigger_type,
                    )

    @staticmethod
    def _get_active_tab(tab_snapshot: TabSnapshot | None) -> TabInfo | None:
        if not tab_snapshot or not tab_snapshot.tabs:
            return None
        if tab_snapshot.active_tab_id is not None:
            for tab in tab_snapshot.tabs:
                if tab.tab_id == tab_snapshot.active_tab_id:
                    return tab
        for tab in tab_snapshot.tabs:
            if tab.active:
                return tab
        return tab_snapshot.tabs[0]

    def _build_context(self, trigger_type: TriggerType) -> ActivityContext:
        fg_win: WindowInfo | None = None
        try:
            if self._get_foreground_fn is not None:
                fg_win = self._get_foreground_fn()
            else:
                fg_win = window_watcher_module.get_foreground_window()
        except Exception:
            logger.exception("Failed to read foreground window in EventEngine")

        tab_snap: TabSnapshot | None = None
        try:
            tab_snap = self.tab_store.get_latest()
        except Exception:
            logger.exception("Failed to read TabSnapshotStore in EventEngine")

        open_wins: list[str] = []
        try:
            if self._list_windows_fn is not None:
                open_wins = self._list_windows_fn()
            else:
                open_wins = window_watcher_module.list_open_window_titles()
        except Exception:
            logger.exception("Failed to list open windows in EventEngine")

        source_key = ""
        display_title = ""
        active_url = ""

        if fg_win is not None and not fg_win.is_browser:
            source_key = fg_win.process_name
            display_title = fg_win.title
        else:
            active_tab = self._get_active_tab(tab_snap)
            if active_tab is not None:
                domain = active_tab.domain or normalize_domain(active_tab.url)
                source_key = domain or (fg_win.process_name if fg_win else "")
                display_title = active_tab.title or (fg_win.title if fg_win else "")
                active_url = active_tab.url
            elif fg_win is not None:
                source_key = fg_win.process_name
                display_title = fg_win.title

        return ActivityContext(
            trigger_type=trigger_type,
            goal=self.goal,
            foreground_window=fg_win,
            tab_snapshot=tab_snap,
            open_windows=open_wins,
            source_key=source_key,
            display_title=display_title,
            active_url=active_url,
            timestamp=time.time(),
        )

    def _is_borderline_context(self, ctx: ActivityContext) -> bool:
        if ctx.foreground_window is not None and not ctx.foreground_window.is_browser:
            return False

        domain_candidate = ""
        active_tab = self._get_active_tab(ctx.tab_snapshot)
        if active_tab is not None:
            domain_candidate = active_tab.domain or normalize_domain(active_tab.url)
        if not domain_candidate and ctx.active_url:
            domain_candidate = normalize_domain(ctx.active_url)
        if not domain_candidate and ctx.source_key and not ctx.source_key.lower().endswith(".exe"):
            domain_candidate = normalize_domain(ctx.source_key) or ctx.source_key.strip().lower()

        if not domain_candidate:
            return False
        return judge_module.domain_matches(domain_candidate, self.profile.borderline_domains)

    def _evaluate_with_backoff(
        self,
        ctx: ActivityContext,
        force_vision: bool,
    ) -> JudgeVerdict:
        with self._lock:
            in_backoff = self._now() < self._backoff_until

        if in_backoff:
            tier0 = judge_module.tier0_evaluate(ctx, self.profile, self.cache)
            if tier0 is not None:
                return tier0
            label = ctx.display_title or ctx.source_key or "current activity"
            return JudgeVerdict(
                activity=f"Rate-limit backoff: {label}",
                on_task=True,
                confidence=0.0,
                reason="API rate-limit backoff active; treating unknown activity as on-task.",
                tabs_to_close=[],
                tier_used=0,
                from_cache=False,
                rate_limited=True,
            )

        if self._evaluate_fn is not None:
            verdict = self._evaluate_fn(
                ctx,
                self.profile,
                self.cache,
                self.api_key,
                force_vision=force_vision,
            )
        else:
            verdict = judge_module.evaluate_context(
                ctx,
                self.profile,
                self.cache,
                self.api_key,
                force_vision=force_vision,
            )

        if verdict.rate_limited:
            with self._lock:
                self._backoff_until = self._now() + self._rate_limit_backoff_seconds
                logger.warning(
                    "Rate limit detected; backing off AI calls for %.1fs.",
                    self._rate_limit_backoff_seconds,
                )

        return verdict

    def _execute_check(self, trigger_type: TriggerType, force_vision: bool) -> None:
        ctx = self._build_context(trigger_type)

        # Avoid wasting a Tier 2 dwell check if the user already moved off a borderline domain
        if trigger_type == "dwell" and not self._is_borderline_context(ctx):
            with self._lock:
                self._dwell_deadline = None
            return

        verdict = self._evaluate_with_backoff(ctx, force_vision=force_vision)

        with self._lock:
            if self._stopped or (self._ever_started and not self._running):
                return

            now = self._now()
            if trigger_type == "heartbeat":
                self._next_heartbeat_at = now + self.timings.heartbeat_seconds

            if self._is_borderline_context(ctx):
                if self._pending_trigger is None and not self._immediate_queue:
                    if trigger_type != "heartbeat" or self._dwell_deadline is None:
                        self._dwell_deadline = now + self.timings.dwell_seconds
            else:
                self._dwell_deadline = None

        try:
            self.on_verdict(ctx, verdict)
        except Exception:
            logger.exception("Error in EventEngine on_verdict callback")
