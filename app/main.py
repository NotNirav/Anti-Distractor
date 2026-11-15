"""Main entry point orchestrating server, watchers, event engine, state machine, logger, and UI."""

from __future__ import annotations

import logging
import os
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

    def start(self) -> None:
        """Show the initial setup screen."""
        root = getattr(self.ui, "root", None)
        if root is not None and hasattr(root, "protocol"):
            try:
                root.protocol("WM_DELETE_WINDOW", self.shutdown)
            except Exception:
                pass
        self.ui.show_setup(on_start=self.start_session)

    def start_session(self, goal: str, duration_minutes: int, mode: ModeType) -> None:
        """Show HUD immediately and initialize session components in a background thread."""
        with self._lock:
            if self.session_active or getattr(self, "_starting", False):
                return
            self._starting = True
            self._ending = False
            self.current_goal = goal
            self._current_mode = mode
            self._last_log_id = None

        self.ui.show_monitoring_hud(goal, mode, on_end_session=self.end_session)
        if hasattr(self.ui, "update_hud_status"):
            try:
                self.ui.update_hud_status("ON_TASK", "Starting...")
            except Exception:
                pass

        def _startup_worker() -> None:
            try:
                if self.fake_judge:
                    profile = fallback_goal_profile(goal)
                else:
                    try:
                        profile = self._profile_fn(goal, self.api_key)
                    except Exception:
                        logger.exception("Goal profile generation failed; using fallback profile")
                        profile = fallback_goal_profile(goal)

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
                        if server_handle is not None and hasattr(server_handle, "stop"):
                            try:
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

                watcher.start()
                engine.start()
                engine.run_immediate_check("baseline", force_vision=False)
                if hasattr(self.ui, "update_hud_status"):
                    try:
                        self.ui.update_hud_status("ON_TASK", "Monitoring active.")
                    except Exception:
                        pass
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

        if deadline is not None and time.monotonic() >= deadline:
            self.end_session()
            return

        if sm is not None:
            try:
                sm.handle_tick()
            except Exception:
                logger.exception("Error in state_machine.handle_tick")

        self._schedule_tick()

    def _stop_background_services(self) -> None:
        with self._lock:
            tick_handle = self._tick_handle
            self._tick_handle = None
            engine = self.engine
            watcher = self.watcher
            server = self.server_handle
            self.session_active = False

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

    def end_session(self) -> None:
        """Stop monitoring, persist session review, and show the review screen."""
        with self._lock:
            if self._ending or not self.session_active:
                return
            self._ending = True
            sm = self.state_machine
            session_logger = self.logger
            session_id = self._session_id
            goal = self.current_goal or ""
            mode = self._current_mode

        self._stop_background_services()

        if sm is not None:
            try:
                sm.end_session()
            except Exception:
                logger.exception("Error ending state machine")

        logs: list[dict[str, Any]] = []
        if session_logger is not None and session_id is not None:
            try:
                logs = session_logger.get_session_logs(session_id)
            except Exception:
                logger.exception("Failed to fetch session logs")

        try:
            review_text = self._review_fn(goal, mode, logs, self.api_key)
        except Exception:
            logger.exception("Review generation failed")
            review_text = f"## Focus Session Review\n\nGoal: {goal}\nChecks logged: {len(logs)}"

        if session_logger is not None and session_id is not None:
            try:
                session_logger.finish_session(session_id, review_text)
            except Exception:
                logger.exception("Failed to save session review to SQLite")

        self.ui.show_review(review_text, on_close=self.shutdown)

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
