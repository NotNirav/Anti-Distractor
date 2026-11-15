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

    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None:
        self.setup_cb = on_start

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
        self.started = False
        self.stopped = False
        self.immediate_checks: list[tuple[str, bool]] = []
        self.triggers: list[str] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

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
    assert controller.session_active is True
    assert controller.watcher is not None and controller.watcher.started is True
    assert controller.engine is not None and controller.engine.started is True
    assert controller.engine.immediate_checks == [("baseline", False)]
    assert len(scheduled) == 1
    assert scheduled[0][0] == 500

    # Verdict callback reaches state machine
    ctx = ActivityContext(
        timestamp=1000.0,
        trigger_type="tab_activated",
        foreground_window=None,
        tab_snapshot=None,
        open_window_titles=[],
    )
    verdict = JudgeVerdict(
        activity="Instagram",
        on_task=False,
        confidence=0.95,
        reason="Distracting site",
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
    assert len(ui.reviews) == 1
    assert "Study DSA" in ui.reviews[0]
