"""Acceptance and hardening tests for the 2-minute demo runbook (Task 6.1)."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from app.cache import VerdictCache
from app.config import DEMO_TIMINGS, DEV_TIMINGS, get_timings
from app.event_engine import EventEngine
from app.logger import SessionLogger
from app.main import AppController
from app.models import ActivityContext, GoalProfile, JudgeVerdict, TabInfo, TabSnapshot, WindowInfo
from app.server import CommandQueue, TabSnapshotStore
from app.state_machine import AgentStateMachine


class MockUI:
    """Mock UI to simulate presenter actions and capture UI calls during the demo script."""

    def __init__(self) -> None:
        self.hud_shown = False
        self.current_goal = ""
        self.current_mode = ""
        self.hud_states: list[str] = []
        self.layer1_popups: list[dict[str, Any]] = []
        self.layer2_warnings: list[dict[str, Any]] = []
        self.strict_overlays: list[dict[str, Any]] = []
        self.reviews: list[str] = []
        self.popups_hidden = 0

    def show_setup(self, on_start: Any) -> None:
        self.on_start = on_start

    def show_monitoring_hud(self, goal: str, mode: str, on_end_session: Any) -> None:
        self.hud_shown = True
        self.current_goal = goal
        self.current_mode = mode
        self.on_end_session = on_end_session

    def update_hud_status(self, state: str, summary: str) -> None:
        self.hud_states.append(f"{state}: {summary}")

    def show_layer1_popup(self, goal: str, reason: str, on_choice: Any) -> None:
        self.layer1_popups.append({"goal": goal, "reason": reason, "on_choice": on_choice})

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        self.layer2_warnings.append({"goal": goal, "reason": reason})

    def show_strict_overlay(self, goal: str, reason: str, lockout_seconds: float, on_lockout_done: Any) -> None:
        self.strict_overlays.append({
            "goal": goal,
            "reason": reason,
            "lockout_seconds": lockout_seconds,
            "on_lockout_done": on_lockout_done,
        })

    def hide_popups_and_overlay(self) -> None:
        self.popups_hidden += 1

    def update_ai_calls(self, t1_count: int, t2_count: int) -> None:
        pass

    def show_review(self, review_text: str, on_close: Any) -> None:
        self.reviews.append(review_text)
        self.on_close = on_close


def wait_for_state(sm: AgentStateMachine, target_state: str, timeout: float = 2.0) -> bool:
    """Poll state machine until it reaches target state or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if sm.state == target_state:
            return True
        time.sleep(0.02)
    return False


def test_timing_presets():
    """Verify timing configuration presets for dev and demo modes."""
    dev = get_timings("dev")
    demo = get_timings("demo")

    assert dev.debounce_seconds == 1.5
    assert dev.grace_period_seconds == 5.0
    assert dev.strict_lockout_seconds == 8.0

    assert demo.debounce_seconds == 3.0
    assert demo.heartbeat_seconds == 300.0
    assert demo.grace_period_seconds == 25.0
    assert demo.strict_lockout_seconds == 15.0


def test_two_minute_demo_script_walkthrough():
    """Step-by-step verification of the 6-step demo script (plan.md section 11)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        mock_ui = MockUI()
        timings = DEV_TIMINGS  # Fast dev timings for rapid automated verification

        # Custom profile matching plan.md §11 demo
        demo_profile = GoalProfile(
            goal="DSA",
            on_task_topics=["binary search", "leetcode", "algorithms"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        )

        controller = AppController(
            ui=mock_ui,
            timings=timings,
            api_key="test_api_key",
            db_path=db_path,
            profile_fn=lambda g, k: demo_profile,
            server_starter=lambda app, host, port: None,  # no-op server starter for test
            schedule_fn=lambda ms, fn: None,             # manual tick driving
            cancel_schedule_fn=lambda h: None,
        )

        current_fg: list[WindowInfo | None] = [None]

        # ---------------------------------------------------------
        # Step 1: Set goal "DSA" in Strict mode, click "I'm ready"
        # ---------------------------------------------------------
        controller.start()
        controller.start_session(goal="DSA", duration_minutes=25, mode="strict")
        controller.wait_for_startup()
        controller.engine._get_foreground_fn = lambda: current_fg[0]

        assert mock_ui.hud_shown is True
        assert mock_ui.current_goal == "DSA"
        assert mock_ui.current_mode == "strict"
        assert controller.session_active is True
        assert controller.state_machine is not None

        # ---------------------------------------------------------
        # Step 2: Open a LeetCode problem. The agent passes it silently (Tier 0).
        # ---------------------------------------------------------
        fg_leetcode = WindowInfo(hwnd=10, title="LeetCode", process_name="chrome.exe", pid=1, is_browser=True)
        current_fg[0] = fg_leetcode
        snap_leetcode = TabSnapshot(
            tabs=[TabInfo(tab_id=101, window_id=1, title="Two Sum - LeetCode", url="https://leetcode.com/problems/two-sum", domain="leetcode.com", active=True)],
            active_tab_id=101,
            window_focused=True,
            updated_at=time.time(),
        )
        controller.tab_store.update(snap_leetcode)
        controller.engine.run_immediate_check("manual_recheck", force_vision=False)
        assert wait_for_state(controller.state_machine, "ON_TASK")
        assert controller.state_machine.strikes == 0
        assert len(mock_ui.layer1_popups) == 0
        assert len(mock_ui.strict_overlays) == 0

        # ---------------------------------------------------------
        # Step 3: Open Instagram. Tier 0 blocks it instantly -> enters Grace.
        # ---------------------------------------------------------
        fg_insta = WindowInfo(hwnd=10, title="Instagram", process_name="chrome.exe", pid=1, is_browser=True)
        current_fg[0] = fg_insta
        snap_insta = TabSnapshot(
            tabs=[TabInfo(tab_id=102, window_id=1, title="Instagram Reels", url="https://instagram.com/reels", domain="instagram.com", active=True)],
            active_tab_id=102,
            window_focused=True,
            updated_at=time.time(),
        )
        controller.tab_store.update(snap_insta)
        controller.engine.run_immediate_check("manual_recheck", force_vision=False)
        assert wait_for_state(controller.state_machine, "GRACE_PERIOD")

        # ---------------------------------------------------------
        # Step 4: Grace expires -> Layer 1 popup ("Are you studying DSA?").
        # ---------------------------------------------------------
        # Advance past grace period
        controller.state_machine.handle_tick(now=time.monotonic() + timings.grace_period_seconds + 1.0)
        assert controller.state_machine.state == "LAYER_1_ASK"
        assert len(mock_ui.layer1_popups) == 1
        assert "DSA" in mock_ui.layer1_popups[0]["goal"]

        # ---------------------------------------------------------
        # Step 5: Ignore it. Second off-task -> Strict mode closes tab & shows overlay.
        # ---------------------------------------------------------
        controller.engine.run_immediate_check("manual_recheck", force_vision=False)
        assert wait_for_state(controller.state_machine, "LAYER_2_ULTIMATUM")
        assert len(mock_ui.strict_overlays) == 1
        assert mock_ui.strict_overlays[0]["lockout_seconds"] == timings.strict_lockout_seconds

        # Verify command queue has close_tabs command for tab 102
        cmds = controller.command_queue.poll_commands()
        assert len(cmds) == 1
        assert cmds[0]["action"] == "close_tabs"
        assert cmds[0]["tab_ids"] == [102]

        # Presenter uses emergency escape hatch / lockout timer completes
        mock_ui.strict_overlays[0]["on_lockout_done"]()
        assert controller.state_machine._lockout_deadline is None

        # ---------------------------------------------------------
        # Step 6: Go back to work (LeetCode), end session, verify AI review.
        # ---------------------------------------------------------
        current_fg[0] = fg_leetcode
        controller.tab_store.update(snap_leetcode)
        controller.engine.run_immediate_check("manual_recheck", force_vision=False)
        assert wait_for_state(controller.state_machine, "ON_TASK")
        assert controller.state_machine.strikes == 0

        # End session
        controller.end_session()
        assert controller.session_active is False
        assert mock_ui.reviews[0] == "Generating your review..."
        controller.wait_for_review()
        assert len(mock_ui.reviews) == 2
        assert "Focus Session Review" in mock_ui.reviews[-1] or "Session summary" in mock_ui.reviews[-1]

        # Verify SQLite logs were saved
        logs = controller.logger.get_session_logs(controller._session_id)
        assert len(logs) >= 3


def test_extension_disconnect_resilience():
    """If browser extension disconnects or never sends snapshots, system still monitors apps cleanly."""
    mock_ui = MockUI()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "resilience.db")
        profile = GoalProfile(
            goal="DSA",
            on_task_topics=["algorithms"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        )
        controller = AppController(
            ui=mock_ui,
            timings=DEV_TIMINGS,
            api_key="test_key",
            db_path=db_path,
            profile_fn=lambda g, k: profile,
            server_starter=lambda app, host, port: None,
            schedule_fn=lambda ms, fn: None,
            cancel_schedule_fn=lambda h: None,
        )
        controller.start_session("DSA", 25, "strict")
        controller.wait_for_startup()

        # Foreground window is a blocked desktop app (spotify.exe)
        current_win = [WindowInfo(hwnd=99, title="Spotify Free", process_name="spotify.exe", pid=456, is_browser=False)]
        controller.engine._get_foreground_fn = lambda: current_win[0]
        # tab_store is completely empty (no extension)
        assert controller.tab_store.get_latest() is None

        # Immediate check should cleanly evaluate the desktop app without crashing
        controller.engine.run_immediate_check("window_changed", force_vision=False)
        assert wait_for_state(controller.state_machine, "GRACE_PERIOD")

        controller.end_session()
        assert controller.session_active is False
        controller.wait_for_review()
