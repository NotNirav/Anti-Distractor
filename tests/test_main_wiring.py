"""Wiring tests for AppController in app/main.py using fakes (no windows, no API)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from app.config import DEV_TIMINGS
from app.main import AppController
from app.models import ActivityContext, AgentStateName, GoalProfile, JudgeVerdict, ModeType


class FakeUI:
    def __init__(self) -> None:
        self.setup_cb: Callable[[str, int, ModeType], None] | None = None
        self.end_session_cb: Callable[[], None] | None = None
        self.review_close_cb: Callable[[], None] | None = None
        self.hud_updates: list[tuple[AgentStateName, str]] = []
        self.reviews: list[str] = []
        self.last_initial: tuple[Any, Any, Any] | None = None
        self.ext_statuses: list[bool] = []
        self.pause_updates: list[tuple[bool, float]] = []
        self.theme: str = "light"
        self.on_theme_changed: Callable[[str], None] | None = None
        self.rules_opened_for: list[GoalProfile] = []
        self.rules_save_cb: Callable[[GoalProfile], None] | None = None

    def set_theme(self, theme: str) -> None:
        self.theme = theme

    def update_extension_status(self, connected: bool) -> None:
        self.ext_statuses.append(connected)

    def update_pause_status(self, paused: bool, remaining_seconds: float = 0.0) -> None:
        self.pause_updates.append((paused, remaining_seconds))

    def show_rules_editor(self, profile: GoalProfile, on_save: Callable[[GoalProfile], None]) -> None:
        self.rules_opened_for.append(profile)
        self.rules_save_cb = on_save

    def show_setup(
        self,
        on_start: Callable[[str, int, ModeType], None],
        initial_goal: str | None = None,
        initial_duration: int | None = None,
        initial_mode: ModeType | None = None,
        on_edit_rules: Callable[[str], None] | None = None,
    ) -> None:
        self.setup_cb = on_start
        self.last_initial = (initial_goal, initial_duration, initial_mode)

    def show_monitoring_hud(
        self, goal: str, mode: ModeType, on_end_session: Callable[[], None]
    ) -> None:
        self.end_session_cb = on_end_session

    def update_hud_status(self, state: AgentStateName, summary: str) -> None:
        self.hud_updates.append((state, summary))

    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
    ) -> None:
        pass

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        pass

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        pass

    def hide_popups_and_overlay(self) -> None:
        pass

    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None:
        self.reviews.append(review_text)
        self.review_close_cb = on_close


class FakeEngine:
    def __init__(self, **kwargs: Any) -> None:
        self.on_verdict = kwargs["on_verdict"]
        self.profile = kwargs.get("profile")
        self.started = False
        self.stopped = False
        self.paused_calls: list[float] = []
        self.resumed_calls: int = 0
        self.immediate_checks: list[tuple[str, bool]] = []
        self.triggers: list[str] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def pause(self, duration_seconds: float = 300.0) -> None:
        self.paused_calls.append(duration_seconds)

    def resume(self) -> None:
        self.resumed_calls += 1

    def update_profile(self, profile: GoalProfile) -> None:
        self.profile = profile

    def submit_trigger(self, trigger_type: str) -> None:
        self.triggers.append(trigger_type)

    def run_immediate_check(
        self, trigger_type: str = "manual_recheck", force_vision: bool = True
    ) -> None:
        self.immediate_checks.append((trigger_type, force_vision))


class FakeWatcher:
    def __init__(self, on_window_changed: Callable[[Any], None], poll_interval: float = 0.5) -> None:
        self.on_window_changed = on_window_changed
        self.poll_interval = poll_interval
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeServerHandle:
    def __init__(self) -> None:
        self.should_exit = False


def test_main_wiring_start_verdict_tick_lockout_and_stop(tmp_path: Path) -> None:
    ui = FakeUI()
    scheduled: list[tuple[int, Callable[[], None]]] = []
    cancelled: list[Any] = []
    server_handle = FakeServerHandle()

    def _schedule(delay_ms: int, cb: Callable[[], None]) -> int:
        scheduled.append((delay_ms, cb))
        return len(scheduled)

    def _cancel(handle: Any) -> None:
        cancelled.append(handle)

    controller = AppController(
        ui=ui,
        timings=DEV_TIMINGS,
        api_key="fake-key",
        db_path=str(tmp_path / "test_wiring.db"),
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: server_handle,
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: GoalProfile(
            goal=goal,
            on_task_topics=["algorithms"],
            allowed_domains=["leetcode.com"],
            borderline_domains=[],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=[],
        ),
        review_fn=lambda goal, mode, logs, _key: f"Review for {goal} ({len(logs)} logs)",
        schedule_fn=_schedule,
        cancel_schedule_fn=_cancel,
    )

    controller.start()
    assert ui.setup_cb is not None

    # Start session
    ui.setup_cb("Study DSA", 25, "strict")
    controller.wait_for_startup()
    assert controller.session_active is True
    assert controller.watcher is not None and controller.watcher.started is True
    assert controller.engine is not None and controller.engine.started is True
    assert controller.engine.immediate_checks == [("baseline", False)]
    assert len(scheduled) == 1
    assert scheduled[0][0] == 500

    # Verdict callback reaches state machine
    ctx = ActivityContext(
        trigger_type="tab_activated",
        goal="Study DSA",
        foreground_window=None,
        tab_snapshot=None,
        open_windows=[],
        source_key="instagram.com",
        display_title="Instagram",
        active_url="https://www.instagram.com/",
        timestamp=1000.0,
    )
    verdict = JudgeVerdict(
        activity="Instagram",
        on_task=False,
        confidence=0.95,
        reason="Distracting site",
        tabs_to_close=[],
        tier_used=0,
    )
    controller.engine.on_verdict(ctx, verdict)
    assert controller.state_machine is not None
    assert controller.state_machine.state == "GRACE_PERIOD"
    assert any(state == "GRACE_PERIOD" for state, _ in ui.hud_updates)

    # Scheduled tick runs handle_tick and reschedules
    scheduled[0][1]()
    assert len(scheduled) == 2

    # on_lockout_done triggers immediate check
    assert controller.state_machine.on_lockout_done is not None
    controller.state_machine.on_lockout_done()
    assert controller.engine.immediate_checks[-1] == ("manual_recheck", True)

    # End session stops everything cleanly and shows review
    assert ui.end_session_cb is not None
    ui.end_session_cb()
    assert controller.session_active is False
    assert controller.engine.stopped is True
    assert controller.watcher.stopped is True
    assert server_handle.should_exit is True
    assert len(cancelled) >= 1
    assert ui.reviews[0] == "Generating your review..."
    controller.wait_for_review()
    assert len(ui.reviews) == 2
    assert "Study DSA" in ui.reviews[-1]


def test_timer_expiry_runs_end_session_and_shows_review(tmp_path: Path) -> None:
    import time

    ui = FakeUI()
    scheduled: list[tuple[int, Callable[[], None]]] = []
    cancelled: list[Any] = []
    server_handle = FakeServerHandle()

    controller = AppController(
        ui=ui,
        timings=DEV_TIMINGS,
        api_key="fake-key",
        db_path=str(tmp_path / "test_expiry.db"),
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: server_handle,
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: GoalProfile(
            goal=goal,
            on_task_topics=["coding"],
            allowed_domains=[],
            borderline_domains=[],
            blocked_domains=[],
            allowed_apps=[],
            blocked_apps=[],
        ),
        review_fn=lambda goal, mode, logs, _key: f"Review for {goal} ({len(logs)} logs)",
        schedule_fn=lambda delay_ms, cb: (scheduled.append((delay_ms, cb)) or len(scheduled)),
        cancel_schedule_fn=lambda h: cancelled.append(h),
    )

    controller.start()
    assert ui.setup_cb is not None
    ui.setup_cb("Timer Test", 1, "gentle")
    controller.wait_for_startup()

    controller._session_deadline = time.monotonic() - 1.0
    scheduled[0][1]()

    assert controller.session_active is False
    assert ui.reviews[0] == "Generating your review..."
    controller.wait_for_review()
    assert len(ui.reviews) == 2
    assert "Timer Test" in ui.reviews[-1]


def test_review_timeout_and_exception_fallback(tmp_path: Path, monkeypatch: Any) -> None:
    import time
    from app import judge

    class _SlowModels:
        def generate_content(self, **_kwargs: Any) -> Any:
            time.sleep(0.5)
            return type("Resp", (), {"text": "Too late"})()

    class _SlowClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.models = _SlowModels()

    monkeypatch.setattr(judge.genai, "Client", _SlowClient)
    summary = judge.generate_session_review(
        goal="Timeout Goal",
        mode="gentle",
        logs=[{"on_task": True, "timestamp": 1, "app_or_domain": "code.exe", "title": "main.py"}],
        api_key="fake-key",
        timeout_seconds=0.05,
    )
    assert "100% on-task across 1 checks" in summary

    ui = FakeUI()
    controller = AppController(
        ui=ui,
        timings=DEV_TIMINGS,
        api_key="fake-key",
        db_path=str(tmp_path / "test_exc.db"),
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: GoalProfile(
            goal=goal,
            on_task_topics=[],
            allowed_domains=[],
            borderline_domains=[],
            blocked_domains=[],
            allowed_apps=[],
            blocked_apps=[],
        ),
        review_fn=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
        schedule_fn=lambda _delay, _cb: 1,
        cancel_schedule_fn=lambda _h: None,
    )

    controller.start()
    assert ui.setup_cb is not None
    ui.setup_cb("Fallback Test", 5, "gentle")
    controller.wait_for_startup()
    assert ui.end_session_cb is not None
    ui.end_session_cb()
    assert ui.reviews[0] == "Generating your review..."
    controller.wait_for_review()
    assert len(ui.reviews) == 2
    assert "Session completed for goal: 'Fallback Test'" in ui.reviews[-1]


def test_remember_last_session_json_and_prefill(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    ui = FakeUI()

    controller = AppController(
        ui=ui,
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        fake_judge=True,
    )

    # 1. Start setup before any saved session
    controller.start()
    assert ui.last_initial == (None, None, None)

    # 2. Start a session with custom values
    controller.start_session("Build Compiler", 45, "normal")
    controller._stop_background_services()

    # Verify last_session.json exists next to DB
    last_json = tmp_path / "last_session.json"
    assert last_json.is_file()

    # 3. New controller instance starts and loads previous values
    ui2 = FakeUI()
    controller2 = AppController(
        ui=ui2,
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        fake_judge=True,
    )
    controller2.start()
    assert ui2.last_initial == ("Build Compiler", 45, "normal")

    # 4. Corrupting the file never crashes loading
    last_json.write_text("invalid json syntax!!!", encoding="utf-8")
    ui3 = FakeUI()
    controller3 = AppController(
        ui=ui3,
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        fake_judge=True,
    )
    controller3.start()
    assert ui3.last_initial == (None, None, None)


def test_self_window_ignored_at_tier0_and_restart_to_setup(tmp_path: Path) -> None:
    """Verify Anti-Distractor's own window passes at Tier 0 and restart_to_setup works cleanly."""
    import os
    from app.cache import VerdictCache
    from app.judge import tier0_evaluate
    from app.models import WindowInfo

    profile = GoalProfile(
        goal="Study DSA",
        on_task_topics=["algorithms"],
        allowed_domains=["leetcode.com"],
        borderline_domains=[],
        blocked_domains=["instagram.com"],
        allowed_apps=["code.exe"],
        blocked_apps=[],
    )
    own_win = WindowInfo(
        hwnd=9999,
        title="Anti-Distractor — HUD",
        process_name="python.exe",
        pid=os.getpid(),
        is_browser=False,
    )
    ctx = ActivityContext(
        trigger_type="baseline",
        goal="Study DSA",
        foreground_window=own_win,
        tab_snapshot=None,
        open_windows=[],
        source_key="python.exe",
        display_title="Anti-Distractor — HUD",
    )
    verdict = tier0_evaluate(ctx, profile, VerdictCache())
    assert verdict is not None
    assert verdict.on_task is True
    assert verdict.tier_used == 0

    # Verify restart_to_setup stops session and re-opens setup with saved settings
    ui = FakeUI()
    controller = AppController(
        ui=ui,
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(tmp_path / "restart.db"),
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: profile,
        review_fn=lambda goal, mode, logs, _key: "Review ok",
        schedule_fn=lambda _delay, _cb: 1,
        cancel_schedule_fn=lambda _h: None,
    )
    controller.start()
    controller.start_session("Study DSA", 30, "strict")
    controller.wait_for_startup()
    assert controller.session_active is True

    controller.restart_to_setup()
    assert controller.session_active is False
    assert ui.last_initial == ("Study DSA", 30, "strict")


def test_extension_connected_pause_resume_rules_and_theme_persistence(tmp_path: Path) -> None:
    """Verify TabSnapshotStore liveness, pause/resume deadline extension, live rules save, and theme."""
    import json
    import time
    from fastapi.testclient import TestClient
    from app.server import CommandQueue, TabSnapshotStore, create_app

    store = TabSnapshotStore()
    queue = CommandQueue()
    assert store.is_extension_connected(stale_seconds=5.0) is False

    fastapi_app = create_app(store, queue)
    client = TestClient(fastapi_app)
    resp = client.get("/commands")
    assert resp.status_code == 200
    assert store.is_extension_connected(stale_seconds=5.0) is True
    assert store.is_extension_connected(stale_seconds=-1.0) is False

    ui = FakeUI()
    db_file = tmp_path / "features.db"
    controller = AppController(
        ui=ui,
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: GoalProfile(
            goal=goal,
            on_task_topics=["algo"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        ),
        review_fn=lambda goal, mode, logs, _key: "Review ok",
        schedule_fn=lambda _delay, _cb: 1,
        cancel_schedule_fn=lambda _h: None,
    )

    controller.start()
    assert ui.on_theme_changed is not None
    ui.on_theme_changed("dark")
    saved_data = json.loads((tmp_path / "last_session.json").read_text(encoding="utf-8"))
    assert saved_data["theme"] == "dark"

    # Re-create controller -> loads saved dark theme automatically
    ui2 = FakeUI()
    controller2 = AppController(
        ui=ui2,
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: GoalProfile(
            goal=goal,
            on_task_topics=["algo"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        ),
        review_fn=lambda goal, mode, logs, _key: "Review ok",
        schedule_fn=lambda _delay, _cb: 1,
        cancel_schedule_fn=lambda _h: None,
    )
    controller2.start()
    assert ui2.theme == "dark"

    controller2.start_session("Study DSA", 25, "strict")
    controller2.wait_for_startup()

    # Extension status tick update
    assert controller2.tab_store is not None
    controller2.tab_store.mark_extension_seen()
    controller2._on_tick()
    assert ui2.ext_statuses[-1] is True

    # Pause & Resume shifts session deadline forward
    orig_deadline = controller2._session_deadline
    assert orig_deadline is not None
    controller2.pause_session(300.0)
    assert controller2.engine.paused_calls == [300.0]
    assert ui2.pause_updates[-1][0] is True
    time.sleep(0.03)
    controller2.resume_session()
    assert controller2.engine.resumed_calls == 1
    assert ui2.pause_updates[-1][0] is False
    assert controller2._session_deadline >= orig_deadline

    # Live rules editor opens and saves into engine + clears cache
    controller2.cache.record_override("youtube.com", "Random video")
    controller2.open_rules_editor()
    assert len(ui2.rules_opened_for) == 1
    assert ui2.rules_save_cb is not None
    edited = GoalProfile(
        goal="Study DSA",
        on_task_topics=["algo"],
        allowed_domains=["leetcode.com", "coursera.org"],
        borderline_domains=["youtube.com"],
        blocked_domains=["instagram.com", "reddit.com"],
        allowed_apps=["code.exe"],
        blocked_apps=["spotify.exe"],
    )
    ui2.rules_save_cb(edited)
    assert controller2.engine.profile.allowed_domains == ["leetcode.com", "coursera.org"]
    assert controller2.cache.get("youtube.com", "Random video") is None

    # Pre-session Setup "Edit Rules" saves draft profile and feeds into next start_session
    controller2.restart_to_setup()
    controller2.open_setup_rules_editor("Study DSA")
    assert len(ui2.rules_opened_for) == 2
    draft = GoalProfile(
        goal="Study DSA",
        on_task_topics=["algo"],
        allowed_domains=["cp-algorithms.com"],
        borderline_domains=["youtube.com"],
        blocked_domains=["tiktok.com"],
        allowed_apps=["code.exe"],
        blocked_apps=["discord.exe"],
    )
    ui2.rules_save_cb(draft)
    controller2.start_session("Study DSA", 25, "strict")
    controller2.wait_for_startup()
    assert controller2.engine.profile.allowed_domains == ["cp-algorithms.com"]
    assert controller2.engine.profile.blocked_apps == ["discord.exe"]

    controller2.shutdown()
