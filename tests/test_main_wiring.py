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


def test_tab_vault_pomodoro_presets_quick_rule_activity_feed_and_hud_pos(tmp_path: Path) -> None:
    """Verify Tab Vault capture/DB, Pomodoro cycle transitions, presets, quick rules, activity feed & HUD pos."""
    import json
    import time
    from app.logger import SessionLogger
    from app.models import ClosedTabEntry, SessionKPIs, TabInfo, TabSnapshot, WindowInfo
    from app.settings import AppSettings

    class ExtendedFakeUI(FakeUI):
        def __init__(self) -> None:
            super().__init__()
            self.on_hud_moved: Callable[[int, int], None] | None = None
            self.setup_pomodoro_enabled: bool | None = None
            self.reviews_with_extras: list[tuple[str, SessionKPIs | None, list[ClosedTabEntry] | None]] = []
            self.reset_timer_calls: list[float] = []
            self.grace_updates: list[float | None] = []
            self.activity_items: list[Any] = []
            self.overridden_items: list[tuple[int | None, str]] = []
            self.prefs_history: list[tuple[int, bool]] = []
            self.setup_progress: list[tuple[str, bool]] = []
            self.setup_presets_count: int = 0

        def configure_preferences(self, *, break_minutes: int | None = None, mute_sounds: bool | None = None) -> None:
            self.prefs_history.append((break_minutes or 5, bool(mute_sounds)))

        def show_setup(
            self,
            on_start: Callable[[str, int, ModeType], None],
            initial_goal: str | None = None,
            initial_duration: int | None = None,
            initial_mode: ModeType | None = None,
            on_edit_rules: Callable[[str], None] | None = None,
            **kwargs: Any,
        ) -> None:
            super().show_setup(
                on_start=on_start,
                initial_goal=initial_goal,
                initial_duration=initial_duration,
                initial_mode=initial_mode,
                on_edit_rules=on_edit_rules,
            )
            presets = kwargs.get("presets") or []
            self.setup_presets_count = len(presets)

        def show_monitoring_hud(
            self,
            goal: str,
            mode: ModeType,
            on_end_session: Callable[[], None],
            duration_minutes: int | None = None,
            on_toggle_pause: Callable[[], None] | None = None,
            on_open_rules: Callable[[], None] | None = None,
            on_quit: Callable[[], None] | None = None,
            **kwargs: Any,
        ) -> None:
            self.end_session_cb = on_end_session

        def update_setup_progress(self, stage_text: str, running: bool = True) -> None:
            self.setup_progress.append((stage_text, running))

        def update_pause_status(
            self, paused: bool, remaining_seconds: float = 0.0, summary: str | None = None
        ) -> None:
            self.pause_updates.append((paused, remaining_seconds, summary))  # type: ignore[arg-type]

        def reset_hud_timer(self, duration_seconds: float) -> None:
            self.reset_timer_calls.append(duration_seconds)

        def update_grace_countdown(self, remaining_seconds: float | None) -> None:
            self.grace_updates.append(remaining_seconds)

        def append_activity_item(self, item: Any, on_mark_relevant: Callable[[Any], None] | None = None) -> None:
            self.activity_items.append(item)

        def mark_activity_overridden(self, log_id: int | None, source_key: str = "") -> None:
            self.overridden_items.append((log_id, source_key))

        def clear_activity_items(self) -> None:
            self.activity_items.clear()

        def show_review(
            self,
            review_text: str,
            on_close: Callable[[], None],
            on_new_session: Callable[[], None] | None = None,
            *,
            kpis: SessionKPIs | None = None,
            closed_tabs: list[ClosedTabEntry] | None = None,
        ) -> None:
            self.reviews.append(review_text)
            self.reviews_with_extras.append((review_text, kpis, closed_tabs))
            self.review_close_cb = on_close

    db_file = tmp_path / "pomo_vault.db"
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=initial_key\n", encoding="utf-8")

    ui = ExtendedFakeUI()
    controller = AppController(
        ui=ui,  # type: ignore[arg-type]
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        env_path=str(env_file),
        enable_os_hotkeys=False,
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
        profile_fn=lambda goal, _key: GoalProfile(
            goal=goal,
            on_task_topics=["algo"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["reddit.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        ),
        review_fn=lambda goal, mode, logs, _key: f"Review for {goal} ({len(logs)} events)",
        schedule_fn=lambda _delay, _cb: 1,
        cancel_schedule_fn=lambda _h: None,
    )

    controller.start()
    assert ui.setup_presets_count == 3  # 3 starter presets seeded

    # Persist HUD position into last_session.json and verify reload
    assert ui.on_hud_moved is not None
    ui.on_hud_moved(-640, 120)
    saved = json.loads((tmp_path / "last_session.json").read_text(encoding="utf-8"))
    assert saved["hud_x"] == -640 and saved["hud_y"] == 120

    # Apply Settings (enables Pomodoro 2 cycles + 5m break + mute)
    controller.apply_settings(
        AppSettings(
            break_minutes=5,
            mute_sounds=True,
            timing_preset="dev",
            hotkeys_enabled=False,
            pomodoro_enabled=True,
            pomodoro_cycles=2,
        )
    )
    assert controller.settings.mute_sounds is True
    assert ui.prefs_history[-1] == (5, True)

    # Select starter preset -> pre-populates draft profile cleanly
    preset0 = controller.presets_store.list_presets()[0]
    controller._on_select_preset(preset0)
    controller.start_session(preset0.goal, 25, "strict")
    controller.wait_for_startup()
    assert controller.engine.profile.allowed_domains == preset0.profile.allowed_domains

    # Trigger strict_block on tab 77 -> records into Closed Tabs Vault + Activity Feed
    assert controller.tab_store is not None
    reddit_tab = TabInfo(
        tab_id=77,
        window_id=1,
        title="r/funny - Reddit",
        url="https://www.reddit.com/r/funny",
        domain="reddit.com",
        active=True,
    )
    controller.tab_store.update(TabSnapshot(tabs=[reddit_tab], active_tab_id=77, window_focused=True, updated_at=10.0))
    win = WindowInfo(hwnd=55, title="Reddit - Chrome", process_name="chrome.exe", pid=999, is_browser=True)
    ctx = ActivityContext(
        trigger_type="tab_activated",
        goal=preset0.goal,
        foreground_window=win,
        tab_snapshot=controller.tab_store.get_latest(),
        open_windows=[],
        source_key="reddit.com",
        display_title="r/funny - Reddit",
        active_url="https://www.reddit.com/r/funny",
        timestamp=100.0,
    )
    off_verdict = JudgeVerdict(
        activity="Reddit",
        on_task=False,
        confidence=0.95,
        reason="Distracting social feed",
        tabs_to_close=[77],
        tier_used=0,
    )
    controller.engine.on_verdict(ctx, off_verdict)  # -> GRACE_PERIOD
    assert controller.state_machine is not None
    controller.state_machine._grace_deadline = time.monotonic() - 1.0  # expire grace
    controller.engine.on_verdict(ctx, off_verdict)  # -> LAYER_1_ASK
    controller.engine.on_verdict(ctx, off_verdict)  # -> LAYER_2_ULTIMATUM (strict_block closing tab 77)

    assert len(controller._closed_tabs_vault) == 1
    assert controller._closed_tabs_vault[0].url == "https://www.reddit.com/r/funny"
    assert len(ui.activity_items) >= 1

    # Quick-rule allows domain live and clears cache
    controller.cache.record_override("reddit.com", "r/funny - Reddit")
    qr = controller.apply_quick_rule("allow", "https://www.reddit.com/r/funny")
    assert qr["ok"] is True and qr["domain_rule"] == "allowed"
    assert "reddit.com" in controller.engine.profile.allowed_domains

    # Mark activity item relevant updates feed + SQLite
    feed_item = ui.activity_items[-1]
    controller.mark_activity_item_relevant(feed_item)
    assert len(ui.overridden_items) >= 1

    # Pomodoro Loop: expire cycle 1 work -> enters cycle 1/2 break -> expire break -> cycle 2 work -> expire -> review
    controller._session_deadline = time.monotonic() - 1.0
    controller._on_tick()
    assert controller._paused is True
    assert ui.pause_updates[-1][0] is True
    assert "Cycle 1/2 complete" in str(ui.pause_updates[-1][2])

    controller._pause_until = time.monotonic() - 1.0
    controller._on_tick()
    assert controller._paused is False
    assert controller._pomodoro_cycle == 2
    assert len(ui.reset_timer_calls) == 1
    assert any("Pomodoro 2/2" in msg for _, msg in ui.hud_updates)

    # Expire final cycle 2 -> ends session & passes KPIs + Closed Tabs Vault to review & SQLite
    controller._session_deadline = time.monotonic() - 1.0
    controller._on_tick()
    controller.wait_for_review()

    assert controller.session_active is False
    assert len(ui.reviews_with_extras) >= 1
    _rev_txt, kpis, vault_entries = ui.reviews_with_extras[-1]
    assert kpis is not None and kpis.total_checks >= 3
    assert vault_entries is not None and len(vault_entries) == 1
    assert vault_entries[0].url == "https://www.reddit.com/r/funny"

    persisted_sessions = SessionLogger(db_path=str(db_file)).list_sessions_summary()
    assert len(persisted_sessions) == 1
    assert len(persisted_sessions[0]["closed_tabs"]) == 1

    # Fresh controller picks up persisted hud_x / hud_y automatically
    ui2 = ExtendedFakeUI()
    controller2 = AppController(
        ui=ui2,  # type: ignore[arg-type]
        timings=DEV_TIMINGS,
        api_key="mock_key",
        db_path=str(db_file),
        env_path=str(env_file),
        enable_os_hotkeys=False,
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
    )
    controller2.start()
    assert controller2._hud_pos == (-640, 120)

    controller.shutdown()
    controller2.shutdown()
    assert controller.hotkey_manager.is_running is False


def test_first_launch_api_key_onboarding_skip_and_clear_lifecycle(tmp_path: Path, monkeypatch) -> None:
    """Verify onboarding launches when GEMINI_API_KEY is missing/placeholder, persists key, and handles Skip & Remove."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    class OnboardingFakeUI(FakeUI):
        def __init__(self) -> None:
            super().__init__()
            self.onboarding_calls: list[dict[str, Any]] = []
            self.setup_calls: list[dict[str, Any]] = []
            self.api_badge_updates: list[tuple[bool, str]] = []
            self.setup_progress_msgs: list[tuple[str, bool]] = []

        def show_onboarding(
            self,
            *,
            on_save_key: Callable[[str], tuple[bool, str]],
            on_test_key: Callable[[str, Callable[[bool, str], None]], None] | None = None,
            on_continue: Callable[[], None],
            on_skip: Callable[[], None],
            initial_key: str = "",
        ) -> None:
            self.onboarding_calls.append(
                {
                    "on_save_key": on_save_key,
                    "on_test_key": on_test_key,
                    "on_continue": on_continue,
                    "on_skip": on_skip,
                    "initial_key": initial_key,
                }
            )

        def show_setup(
            self,
            on_start: Callable[[str, int, ModeType], None],
            initial_goal: str | None = None,
            initial_duration: int | None = None,
            initial_mode: ModeType | None = None,
            on_edit_rules: Callable[[str], None] | None = None,
            **extra: Any,
        ) -> None:
            super().show_setup(on_start, initial_goal, initial_duration, initial_mode, on_edit_rules)
            self.setup_calls.append(extra)

        def update_setup_api_key_status(self, api_key_status: tuple[bool, str]) -> None:
            self.api_badge_updates.append(api_key_status)

        def update_setup_progress(self, stage_text: str, running: bool = True) -> None:
            self.setup_progress_msgs.append((stage_text, running))

    db_file = tmp_path / "sessions.db"
    env_file = tmp_path / ".env"

    # 1. Placeholder key triggers onboarding instead of Setup
    ui1 = OnboardingFakeUI()
    c1 = AppController(
        ui=ui1,  # type: ignore[arg-type]
        timings=DEV_TIMINGS,
        api_key="your_gemini_api_key_here",
        db_path=str(db_file),
        env_path=str(env_file),
        fake_judge=False,
        enable_os_hotkeys=False,
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
    )
    c1.start()
    assert len(ui1.onboarding_calls) == 1
    assert len(ui1.setup_calls) == 0

    # Reject placeholder key inside onboarding save callback
    ok_bad, _msg_bad = ui1.onboarding_calls[0]["on_save_key"]("your_gemini_api_key_here")
    assert ok_bad is False

    # Saving real key updates .env + controller.api_key and transitions to Setup with Key ✓ status
    ok_good, status_good = ui1.onboarding_calls[0]["on_save_key"]("AIzaSyTestOnboarding9876")
    assert ok_good is True
    assert "9876" in status_good
    assert c1.api_key == "AIzaSyTestOnboarding9876"
    assert "GEMINI_API_KEY=AIzaSyTestOnboarding9876" in env_file.read_text(encoding="utf-8")

    ui1.onboarding_calls[0]["on_continue"]()
    assert len(ui1.setup_calls) == 1
    assert ui1.setup_calls[-1]["api_key_status"][0] is True

    # 2. Removing the key clears .env and updates badge live
    cleared_ok, cleared_msg = c1._clear_api_key()
    assert cleared_ok is False
    assert cleared_msg == "Not configured"
    assert c1.api_key == ""
    assert "GEMINI_API_KEY=" not in env_file.read_text(encoding="utf-8")
    assert ui1.api_badge_updates[-1] == (False, "Not configured")
    c1.shutdown()

    # 3. Skipping onboarding persists onboarding_skipped=True so next launch goes straight to Setup
    ui2 = OnboardingFakeUI()
    c2 = AppController(
        ui=ui2,  # type: ignore[arg-type]
        timings=DEV_TIMINGS,
        api_key="",
        db_path=str(db_file),
        env_path=str(env_file),
        fake_judge=False,
        enable_os_hotkeys=False,
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
    )
    c2.start()
    assert len(ui2.onboarding_calls) == 1
    ui2.onboarding_calls[0]["on_skip"]()
    assert c2.settings.onboarding_skipped is True
    assert len(ui2.setup_calls) == 1
    assert ui2.setup_calls[-1]["api_key_status"] == (False, "Not configured")
    c2.shutdown()

    ui3 = OnboardingFakeUI()
    c3 = AppController(
        ui=ui3,  # type: ignore[arg-type]
        timings=DEV_TIMINGS,
        api_key="",
        db_path=str(db_file),
        env_path=str(env_file),
        fake_judge=False,
        enable_os_hotkeys=False,
        engine_cls=FakeEngine,  # type: ignore[arg-type]
        watcher_cls=FakeWatcher,  # type: ignore[arg-type]
        server_starter=lambda _app, host="127.0.0.1", port=8765: FakeServerHandle(),
        app_factory=lambda **kwargs: object(),
    )
    c3.start()
    assert len(ui3.onboarding_calls) == 0
    assert len(ui3.setup_calls) == 1
    c3.shutdown()
