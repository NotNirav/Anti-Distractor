"""Main entry point orchestrating server, watchers, event engine, state machine, logger, and UI."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable

from app.cache import VerdictCache
from app.config import TimingConfig, get_gemini_api_key, get_timings
from app.event_engine import EventEngine
from app.judge import generate_session_review, get_model_manager
from app.logger import SessionLogger
from app.models import ActivityContext, GoalProfile, JudgeVerdict, ModeType
from app.profile import fallback_goal_profile, generate_goal_profile
from app.server import CommandQueue, TabSnapshotStore, create_app, start_server_thread
from app.state_machine import AgentStateMachine
from app.ui import TkinterUI, UIHooks
from app.window_watcher import WindowWatcher

logger = logging.getLogger(__name__)


def _is_fake_judge_enabled() -> bool:
    return os.getenv("ANTI_DISTRACTOR_FAKE_JUDGE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def make_fake_judge(
    verdicts: list[JudgeVerdict] | None = None,
) -> Callable[..., JudgeVerdict]:
    """Return a zero-quota fake evaluate_fn cycling through canned verdicts."""
    canned = verdicts or [
        JudgeVerdict(
            activity="Working on goal",
            on_task=True,
            confidence=0.95,
            reason="Fake judge: on-task baseline.",
            tabs_to_close=[],
            tier_used=1,
        ),
        JudgeVerdict(
            activity="Browsing social media",
            on_task=False,
            confidence=0.95,
            reason="Fake judge: off-task distraction.",
            tabs_to_close=[],
            tier_used=1,
        ),
    ]
    idx = 0
    lock = threading.Lock()

    def _fake_evaluate(
        ctx: ActivityContext,
        profile: GoalProfile,
        cache: VerdictCache,
        api_key: str,
        force_vision: bool = False,
    ) -> JudgeVerdict:
        nonlocal idx
        with lock:
            verdict = canned[idx % len(canned)]
            idx += 1
        return verdict

    return _fake_evaluate


class AppController:
    """Coordinates lifecycle, threading, and callbacks across all Anti-Distractor modules."""

    def __init__(
        self,
        ui: UIHooks,
        timings: TimingConfig,
        api_key: str,
        *,
        db_path: str = "anti_distractor.db",
        host: str = "127.0.0.1",
        port: int = 8765,
        fake_judge: bool = False,
        session_logger_cls: type[SessionLogger] = SessionLogger,
        engine_cls: type[EventEngine] = EventEngine,
        watcher_cls: type[WindowWatcher] = WindowWatcher,
        server_starter: Callable[..., Any] = start_server_thread,
        app_factory: Callable[..., Any] = create_app,
        profile_fn: Callable[[str, str], GoalProfile] = generate_goal_profile,
        review_fn: Callable[..., str] = generate_session_review,
        schedule_fn: Callable[[int, Callable[[], None]], Any] | None = None,
        cancel_schedule_fn: Callable[[Any], None] | None = None,
    ) -> None:
        self.ui = ui
        self.timings = timings
        self.api_key = api_key
        self.db_path = db_path
        self.host = host
        self.port = port
        self.fake_judge = fake_judge

        self._session_logger_cls = session_logger_cls
        self._engine_cls = engine_cls
        self._watcher_cls = watcher_cls
        self._server_starter = server_starter
        self._app_factory = app_factory
        self._profile_fn = profile_fn
        self._review_fn = review_fn
        self._schedule_fn = schedule_fn
        self._cancel_schedule_fn = cancel_schedule_fn

        self._lock = threading.RLock()
        self.session_active = False
        self.current_goal: str | None = None
        self._current_mode: ModeType = "normal"
        self._session_id: int | None = None
        self._session_deadline: float | None = None
        self._last_log_id: int | None = None
        self._tick_handle: Any = None
        self._ending = False
        self._t1_calls = 0
        self._t2_calls = 0
        self._paused = False
        self._pause_started_at: float | None = None
        self._pause_until: float | None = None
        self._last_ext_connected: bool | None = None
        self.profile: GoalProfile | None = None
        self._draft_profile: GoalProfile | None = None

        self.logger: SessionLogger | None = None
        self.tab_store: TabSnapshotStore | None = None
        self.command_queue: CommandQueue | None = None
        self.cache: VerdictCache | None = None
        self.state_machine: AgentStateMachine | None = None
        self.engine: EventEngine | None = None
        self.watcher: WindowWatcher | None = None
        self.server_handle: Any = None

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> Any:
        if self._schedule_fn is not None:
            return self._schedule_fn(delay_ms, callback)
        root = getattr(self.ui, "root", None)
        if root is not None:
            return root.after(delay_ms, callback)
        return None

    def _cancel_schedule(self, handle: Any) -> None:
        if handle is None:
            return
        if self._cancel_schedule_fn is not None:
            self._cancel_schedule_fn(handle)
            return
        root = getattr(self.ui, "root", None)
        if root is not None:
            try:
                root.after_cancel(handle)
            except Exception:
                pass

    def _get_last_session_path(self) -> str:
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        return os.path.join(db_dir, "last_session.json")

    def _load_last_session(self) -> dict[str, Any]:
        try:
            path = self._get_last_session_path()
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception:
            logger.exception("Failed to load last_session.json")
        return {}

    def _save_last_session(
        self,
        goal: str,
        duration_minutes: int,
        mode: ModeType,
        theme: str | None = None,
    ) -> None:
        try:
            existing = self._load_last_session()
            path = self._get_last_session_path()
            resolved_theme = theme if theme in ("light", "dark") else existing.get("theme")
            payload: dict[str, Any] = {
                "goal": goal,
                "duration_minutes": duration_minutes,
                "mode": mode,
            }
            if resolved_theme in ("light", "dark"):
                payload["theme"] = resolved_theme
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            logger.exception("Failed to save last_session.json")

    def _save_theme_preference(self, theme: str) -> None:
        if theme not in ("light", "dark"):
            return
        try:
            existing = self._load_last_session()
            existing["theme"] = theme
            path = self._get_last_session_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception:
            logger.exception("Failed to save theme preference in last_session.json")

    def start(self) -> None:
        """Show the initial setup screen, prefilled with last session and theme if available."""
        root = getattr(self.ui, "root", None)
        if root is not None and hasattr(root, "protocol"):
            try:
                root.protocol("WM_DELETE_WINDOW", self.shutdown)
            except Exception:
                pass

        last_sess = self._load_last_session()
        init_goal = last_sess.get("goal")
        init_dur = last_sess.get("duration_minutes")
        init_mode = last_sess.get("mode")
        init_theme = last_sess.get("theme")

        if hasattr(self.ui, "on_theme_changed"):
            try:
                self.ui.on_theme_changed = self._save_theme_preference
            except Exception:
                pass
        if init_theme in ("light", "dark") and hasattr(self.ui, "set_theme"):
            try:
                self.ui.set_theme(init_theme)
            except Exception:
                logger.exception("Failed to apply saved theme")

        try:
            self.ui.show_setup(
                on_start=self.start_session,
                initial_goal=init_goal,
                initial_duration=init_dur,
                initial_mode=init_mode,
                on_edit_rules=self.open_setup_rules_editor,
            )
        except TypeError:
            try:
                self.ui.show_setup(
                    on_start=self.start_session,
                    initial_goal=init_goal,
                    initial_duration=init_dur,
                    initial_mode=init_mode,
                )
            except TypeError:
                self.ui.show_setup(on_start=self.start_session)

    def start_session(self, goal: str, duration_minutes: int, mode: ModeType) -> None:
        """Show HUD immediately and initialize session components in a background thread."""
        self._save_last_session(goal, duration_minutes, mode)
        with self._lock:
            if self.session_active or getattr(self, "_starting", False):
                return
            self._starting = True
            self._ending = False
            self.current_goal = goal
            self._current_mode = mode
            self._last_log_id = None
            self._t1_calls = 0
            self._t2_calls = 0
            self._paused = False
            self._pause_started_at = None
            self._pause_until = None
            self._last_ext_connected = None

        try:
            self.ui.show_monitoring_hud(
                goal,
                mode,
                on_end_session=self.end_session,
                duration_minutes=duration_minutes,
                on_toggle_pause=self.toggle_pause,
                on_open_rules=self.open_rules_editor,
                on_quit=self.shutdown,
            )
        except TypeError:
            try:
                self.ui.show_monitoring_hud(
                    goal,
                    mode,
                    on_end_session=self.end_session,
                    duration_minutes=duration_minutes,
                )
            except TypeError:
                self.ui.show_monitoring_hud(goal, mode, on_end_session=self.end_session)
        if hasattr(self.ui, "update_extension_status"):
            try:
                self.ui.update_extension_status(False)
            except Exception:
                pass
        if hasattr(self.ui, "update_hud_status"):
            try:
                self.ui.update_hud_status("ON_TASK", "Starting...")
            except Exception:
                pass

        def _startup_worker() -> None:
            try:
                with self._lock:
                    draft = self._draft_profile
                    self._draft_profile = None

                if draft is not None:
                    profile = GoalProfile(
                        goal=goal,
                        on_task_topics=list(draft.on_task_topics) or [goal.strip()],
                        allowed_domains=list(draft.allowed_domains),
                        borderline_domains=list(draft.borderline_domains),
                        blocked_domains=list(draft.blocked_domains),
                        allowed_apps=list(draft.allowed_apps),
                        blocked_apps=list(draft.blocked_apps),
                    )
                elif self.fake_judge:
                    profile = fallback_goal_profile(goal)
                else:
                    try:
                        profile = self._profile_fn(goal, self.api_key)
                    except Exception:
                        logger.exception("Goal profile generation failed; using fallback profile")
                        profile = fallback_goal_profile(goal)

                with self._lock:
                    self.profile = profile

                session_logger = self._session_logger_cls(db_path=self.db_path)
                session_logger.init_db()
                session_id = session_logger.start_session(goal, mode, duration_minutes, profile)

                model_manager = get_model_manager()
                def _on_tier0_shift(msg: str) -> None:
                    if hasattr(self.ui, "show_tier0_shift_popup"):
                        try:
                            self.ui.show_tier0_shift_popup(msg)
                        except Exception:
                            logger.exception("Failed to show Tier 0 shift popup")
                    if hasattr(self.ui, "update_hud_status"):
                        try:
                            self.ui.update_hud_status("ON_TASK", "AI unavailable - rules only")
                        except Exception:
                            pass

                model_manager.on_shift_to_tier0 = _on_tier0_shift

                tab_store = TabSnapshotStore()
                command_queue = CommandQueue()
                cache = VerdictCache()

                def _on_override(_ctx: ActivityContext | None, _reason: str) -> None:
                    with self._lock:
                        log_id = self._last_log_id
                    if log_id is not None:
                        try:
                            session_logger.mark_override(log_id)
                        except Exception:
                            logger.exception("Failed to mark override in SQLite")

                def _on_lockout_done() -> None:
                    with self._lock:
                        eng = self.engine
                    if eng is not None:
                        try:
                            eng.run_immediate_check("manual_recheck", force_vision=True)
                        except Exception:
                            logger.exception("Failed to run post-lockout immediate check")

                state_machine = AgentStateMachine(
                    goal=goal,
                    mode=mode,
                    timings=self.timings,
                    cache=cache,
                    command_queue=command_queue,
                    ui_hooks=self.ui,
                    on_override=_on_override,
                    on_lockout_done=_on_lockout_done,
                )

                def _on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
                    try:
                        decision = state_machine.handle_verdict(ctx, verdict)
                    except Exception:
                        logger.exception("State machine handle_verdict failed")
                        return
                    try:
                        log_id = session_logger.log_event(
                            session_id=session_id,
                            ctx=ctx,
                            verdict=verdict,
                            action_taken=decision.action_taken,
                        )
                        with self._lock:
                            self._last_log_id = log_id
                            if verdict.tier_used == 1:
                                self._t1_calls += 1
                            elif verdict.tier_used == 2:
                                self._t2_calls += 1
                            t1_cnt = self._t1_calls
                            t2_cnt = self._t2_calls

                        if hasattr(self.ui, "update_ai_calls"):
                            try:
                                self.ui.update_ai_calls(t1_cnt, t2_cnt)
                            except Exception:
                                pass
                    except Exception:
                        logger.exception("Failed to log verdict to SQLite")

                evaluate_fn = make_fake_judge() if self.fake_judge else None
                engine = self._engine_cls(
                    goal=goal,
                    profile=profile,
                    cache=cache,
                    tab_store=tab_store,
                    timings=self.timings,
                    api_key=self.api_key,
                    on_verdict=_on_verdict,
                    evaluate_fn=evaluate_fn,
                )

                def _on_tab_event(trigger_type: Any) -> None:
                    try:
                        engine.submit_trigger(trigger_type)
                    except Exception:
                        logger.exception("submit_trigger failed on tab event")

                def _on_window_changed(_info: Any) -> None:
                    try:
                        engine.submit_trigger("window_changed")
                    except Exception:
                        logger.exception("submit_trigger failed on window change")

                watcher = self._watcher_cls(
                    on_window_changed=_on_window_changed,
                    poll_interval=self.timings.window_poll_seconds,
                )

                fastapi_app = self._app_factory(
                    tab_store=tab_store,
                    command_queue=command_queue,
                    on_tab_event=_on_tab_event,
                    session_status_provider=lambda: (self.session_active, self.current_goal),
                )
                try:
                    server_handle = self._server_starter(fastapi_app, host=self.host, port=self.port)
                except Exception:
                    logger.exception("Failed to start FastAPI bridge server")
                    server_handle = None

                with self._lock:
                    if self._ending:
                        if server_handle is not None:
                            try:
                                if hasattr(server_handle, "should_exit"):
                                    server_handle.should_exit = True
                                elif hasattr(server_handle, "stop"):
                                    server_handle.stop()
                            except Exception:
                                pass
                        return
                    self.logger = session_logger
                    self._session_id = session_id
                    self.tab_store = tab_store
                    self.command_queue = command_queue
                    self.cache = cache
                    self.state_machine = state_machine
                    self.engine = engine
                    self.watcher = watcher
                    self.server_handle = server_handle
                    self.session_active = True
                    self._session_deadline = time.monotonic() + max(1, duration_minutes) * 60.0

                if hasattr(self.ui, "update_hud_status"):
                    try:
                        self.ui.update_hud_status("ON_TASK", "Monitoring active.")
                    except Exception:
                        pass
                watcher.start()
                engine.start()
                engine.run_immediate_check("baseline", force_vision=False)
                if hasattr(self.ui, "dispatch"):
                    self.ui.dispatch(self._schedule_tick)
                else:
                    self._schedule_tick()
            except Exception:
                logger.exception("Session startup failed")
                if hasattr(self.ui, "update_hud_status"):
                    try:
                        self.ui.update_hud_status("ON_TASK", "Startup fallback error.")
                    except Exception:
                        pass
            finally:
                with self._lock:
                    self._starting = False

        t = threading.Thread(target=_startup_worker, daemon=True, name="session-startup")
        with self._lock:
            self._startup_thread = t
        t.start()

    def wait_for_startup(self, timeout: float = 5.0) -> None:
        """Wait for background session startup thread to finish (for tests)."""
        with self._lock:
            t = getattr(self, "_startup_thread", None)
        if t is not None:
            t.join(timeout=timeout)

    def _schedule_tick(self) -> None:
        with self._lock:
            if not self.session_active or self._ending:
                return
            self._tick_handle = self._schedule(500, self._on_tick)

    def _on_tick(self) -> None:
        with self._lock:
            if not self.session_active or self._ending:
                return
            sm = self.state_machine
            deadline = self._session_deadline
            tab_store = self.tab_store
            paused = self._paused
            pause_until = self._pause_until
            prev_ext = self._last_ext_connected

        if tab_store is not None and hasattr(tab_store, "is_extension_connected"):
            try:
                connected = bool(tab_store.is_extension_connected(stale_seconds=8.0))
                if connected != prev_ext:
                    with self._lock:
                        self._last_ext_connected = connected
                    if hasattr(self.ui, "update_extension_status"):
                        self.ui.update_extension_status(connected)
            except Exception:
                logger.exception("Error checking extension connectivity")

        if paused:
            rem = max(0.0, (pause_until or 0.0) - time.monotonic())
            if rem <= 0.0:
                self.resume_session()
            else:
                if hasattr(self.ui, "update_pause_status"):
                    try:
                        self.ui.update_pause_status(True, rem)
                    except Exception:
                        pass
            self._schedule_tick()
            return

        if deadline is not None and time.monotonic() >= deadline:
            self.end_session()
            return

        if sm is not None:
            try:
                sm.handle_tick()
            except Exception:
                logger.exception("Error in state_machine.handle_tick")

        self._schedule_tick()

    def pause_session(self, duration_seconds: float = 300.0) -> None:
        """Intentionally pause monitoring for a short break without ending the session."""
        dur = max(1.0, float(duration_seconds))
        now = time.monotonic()
        with self._lock:
            if not self.session_active or self._ending:
                return
            self._paused = True
            self._pause_started_at = now
            self._pause_until = now + dur
            engine = self.engine

        if engine is not None and hasattr(engine, "pause"):
            try:
                engine.pause(dur)
            except Exception:
                logger.exception("Failed to pause EventEngine")

        if hasattr(self.ui, "hide_popups_and_overlay"):
            try:
                self.ui.hide_popups_and_overlay()
            except Exception:
                pass
        if hasattr(self.ui, "update_pause_status"):
            try:
                self.ui.update_pause_status(True, dur)
            except Exception:
                logger.exception("Failed to update HUD pause status")

    def resume_session(self) -> None:
        """Resume monitoring after a break, shifting deadline forward by the paused duration."""
        now = time.monotonic()
        with self._lock:
            if not self._paused:
                return
            elapsed = max(0.0, now - (self._pause_started_at or now))
            if self._session_deadline is not None:
                self._session_deadline += elapsed
            self._paused = False
            self._pause_started_at = None
            self._pause_until = None
            engine = self.engine
            active = self.session_active and not self._ending

        if engine is not None and hasattr(engine, "resume"):
            try:
                engine.resume()
            except Exception:
                logger.exception("Failed to resume EventEngine")

        if hasattr(self.ui, "update_pause_status"):
            try:
                self.ui.update_pause_status(False, 0.0)
            except Exception:
                pass
        if active and hasattr(self.ui, "update_hud_status"):
            try:
                self.ui.update_hud_status("ON_TASK", "Monitoring resumed.")
            except Exception:
                pass

    def toggle_pause(self, duration_seconds: float = 300.0) -> None:
        """Toggle between active monitoring and a 5-minute break."""
        with self._lock:
            currently_paused = self._paused
        if currently_paused:
            self.resume_session()
        else:
            self.pause_session(duration_seconds=duration_seconds)

    def update_goal_profile(self, new_profile: GoalProfile) -> None:
        """Apply edited allow/block rules live to the active engine and clear cached verdicts."""
        with self._lock:
            self.profile = new_profile
            engine = self.engine
            cache = self.cache

        if cache is not None:
            try:
                cache.clear()
            except Exception:
                logger.exception("Failed clearing VerdictCache after profile update")
        if engine is not None and hasattr(engine, "update_profile"):
            try:
                engine.update_profile(new_profile)
            except Exception:
                logger.exception("Failed updating EventEngine profile")
        if hasattr(self.ui, "update_hud_status"):
            try:
                self.ui.update_hud_status("ON_TASK", "Rules updated.")
            except Exception:
                pass

    def _save_draft_profile(self, draft: GoalProfile) -> None:
        with self._lock:
            self._draft_profile = draft

    def open_setup_rules_editor(self, goal_text: str = "") -> None:
        """Open the rules editor before starting a session (prefilled from draft or defaults)."""
        clean_goal = (goal_text or "").strip() or "Focus"
        with self._lock:
            draft = self._draft_profile
        if draft is None or draft.goal != clean_goal:
            draft = fallback_goal_profile(clean_goal)
        if hasattr(self.ui, "show_rules_editor"):
            try:
                self.ui.show_rules_editor(draft, on_save=self._save_draft_profile)
            except Exception:
                logger.exception("Failed to open setup rules editor")

    def open_rules_editor(self) -> None:
        """Open the live editable domain/app rules dialog for the current session."""
        with self._lock:
            profile = self.profile or fallback_goal_profile(self.current_goal or "Focus")
        if hasattr(self.ui, "show_rules_editor"):
            try:
                self.ui.show_rules_editor(profile, on_save=self.update_goal_profile)
            except Exception:
                logger.exception("Failed to open rules editor")

    def _stop_background_services(self) -> None:
        with self._lock:
            tick_handle = self._tick_handle
            self._tick_handle = None
            engine = self.engine
            watcher = self.watcher
            server = self.server_handle
            self.session_active = False
            self._paused = False
            self._pause_started_at = None
            self._pause_until = None
            self._last_ext_connected = None

        self._cancel_schedule(tick_handle)

        if engine is not None:
            try:
                engine.stop()
            except Exception:
                logger.exception("Error stopping EventEngine")

        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                logger.exception("Error stopping WindowWatcher")

        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                logger.exception("Error stopping uvicorn server")

    def _show_review_screen(self, review_text: str) -> None:
        try:
            self.ui.show_review(
                review_text,
                on_close=self.shutdown,
                on_new_session=self.restart_to_setup,
            )
        except TypeError:
            self.ui.show_review(review_text, on_close=self.shutdown)

    def restart_to_setup(self) -> None:
        """Stop any active services and return cleanly to the Setup screen for a new session."""
        self._stop_background_services()
        with self._lock:
            self._ending = False
            self._starting = False
            self._session_deadline = None
            self.session_active = False
            self.profile = None
        self.start()

    def end_session(self) -> None:
        """Stop monitoring, show review screen immediately, and generate review off the UI thread."""
        with self._lock:
            was_starting = bool(getattr(self, "_starting", False))
            if self._ending or (not self.session_active and not was_starting):
                return
            self._ending = True
            sm = self.state_machine
            session_logger = self.logger
            session_id = self._session_id
            goal = self.current_goal or ""
            mode = self._current_mode
            api_key = self.api_key

        try:
            self._stop_background_services()
        except Exception:
            logger.exception("Error stopping background services")

        if sm is not None:
            try:
                sm.end_session()
            except Exception:
                logger.exception("Error ending state machine")

        try:
            self._show_review_screen("Generating your review...")
        except Exception:
            logger.exception("Failed to show initial review screen")

        def _local_summary(logs: list[dict[str, Any]]) -> str:
            total = len(logs)
            if total == 0:
                return f"Session completed for goal: '{goal}' ({mode} mode). No activity recorded."
            on_task_count = sum(1 for row in logs if bool(row.get("on_task")))
            pct = int((on_task_count / total) * 100)
            return f"Session summary for '{goal}' ({mode} mode): {pct}% on-task across {total} checks."

        def _review_worker() -> None:
            logs: list[dict[str, Any]] = []
            review_text = ""
            try:
                if session_logger is not None and session_id is not None:
                    try:
                        logs = session_logger.get_session_logs(session_id)
                    except Exception:
                        logger.exception("Failed to fetch session logs")

                fallback_text = _local_summary(logs)
                review_box: list[str] = []
                done_event = threading.Event()

                def _run_review_fn() -> None:
                    try:
                        text = self._review_fn(goal, mode, logs, api_key)
                        review_box.append((text or "").strip() or fallback_text)
                    except Exception:
                        logger.exception("Review generation failed")
                        review_box.append(fallback_text)
                    finally:
                        done_event.set()

                inner = threading.Thread(target=_run_review_fn, daemon=True, name="session-review-fn")
                inner.start()
                if done_event.wait(timeout=15.0) and review_box:
                    review_text = review_box[0]
                else:
                    review_text = fallback_text

                model_counts: dict[str, int] = {}
                for row in logs:
                    m = row.get("model_used")
                    if m:
                        model_counts[m] = model_counts.get(m, 0) + 1
                    elif row.get("tier_used") == 0:
                        model_counts["Tier 0 (rules)"] = model_counts.get("Tier 0 (rules)", 0) + 1

                if model_counts:
                    breakdown_lines = ["\n\n### Model Usage:"]
                    for m_name, count in sorted(model_counts.items(), key=lambda x: -x[1]):
                        breakdown_lines.append(f"- {m_name}: {count}")
                    review_text += "\n".join(breakdown_lines)

                if session_logger is not None and session_id is not None:
                    try:
                        session_logger.finish_session(session_id, review_text)
                    except Exception:
                        logger.exception("Failed to save session review to SQLite")
            except Exception:
                logger.exception("Unexpected error in end-of-session review worker")
                review_text = _local_summary(logs)
            finally:
                if not review_text:
                    review_text = _local_summary(logs)
                try:
                    self._show_review_screen(review_text)
                except Exception:
                    logger.exception("Failed to show final review screen")

        t = threading.Thread(target=_review_worker, daemon=True, name="session-review")
        with self._lock:
            self._review_thread = t
        t.start()

    def wait_for_review(self, timeout: float = 5.0) -> None:
        """Wait for background session review thread to finish (for tests)."""
        with self._lock:
            t = getattr(self, "_review_thread", None)
        if t is not None:
            t.join(timeout=timeout)

    def shutdown(self) -> None:
        """Cleanly stop all background threads and close the Tkinter window."""
        self._stop_background_services()
        with self._lock:
            sm = self.state_machine
        if sm is not None:
            try:
                sm.end_session()
            except Exception:
                pass
        if hasattr(self.ui, "close"):
            try:
                self.ui.close()
                return
            except Exception:
                logger.exception("Failed to close UI cleanly")
        root = getattr(self.ui, "root", None)
        if root is not None:
            try:
                root.quit()
                root.destroy()
            except Exception:
                pass


def main() -> None:
    """Initialize all Anti-Distractor components and run the Tkinter mainloop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    timings = get_timings()
    api_key = get_gemini_api_key()
    fake_judge = _is_fake_judge_enabled()

    if not api_key and not fake_judge:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing GEMINI_API_KEY",
            "GEMINI_API_KEY is missing from .env.\n\n"
            "Add your key to .env, or set ANTI_DISTRACTOR_FAKE_JUDGE=1 for offline demo mode.",
        )
        root.destroy()
        return

    ui = TkinterUI()
    controller = AppController(
        ui=ui,
        timings=timings,
        api_key=api_key,
        fake_judge=fake_judge,
    )
    controller.start()
    ui.run()


if __name__ == "__main__":
    main()
