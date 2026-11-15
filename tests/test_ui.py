"""Tests for TkinterUI components (Task 5.2)."""

from __future__ import annotations

import threading
import tkinter as tk
import time

from app.ui import TkinterUI


def _flush_events(ui: TkinterUI, root: tk.Tk) -> None:
    """Pump pending Tkinter events and queue callbacks."""
    ui.drain_queue()
    root.update_idletasks()
    root.update()


def test_ui_setup_to_hud_to_review_lifecycle():
    root = tk.Tk()
    root.withdraw()  # Headless execution for unit tests

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        # 1. Test Setup Screen
        started_args = []

        def on_start(goal: str, duration: int, mode: str):
            started_args.append((goal, duration, mode))

        ui.show_setup(on_start)
        _flush_events(ui, root)

        assert ui._current_frame is not None
        assert "Setup" in root.title()

        # Simulate start button callback directly
        ui.show_monitoring_hud("Study Algorithms", "strict", on_end_session=lambda: None)
        _flush_events(ui, root)

        # 2. Test HUD Screen
        assert "HUD" in root.title()
        assert ui._hud_goal_label is not None
        assert "Study Algorithms" in ui._hud_goal_label.cget("text")
        assert ui._hud_mode_label is not None
        assert "[STRICT]" in ui._hud_mode_label.cget("text")
        assert ui._hud_state_label is not None
        assert "ON_TASK" in ui._hud_state_label.cget("text")

        # 3. Test HUD status updates
        ui.update_hud_status("GRACE_PERIOD", "YouTube detected")
        _flush_events(ui, root)
        assert "GRACE_PERIOD" in ui._hud_state_label.cget("text")
        assert "YouTube detected" in ui._hud_summary_label.cget("text")

        ui.update_hud_status("LAYER_2_ULTIMATUM", "Strict tab closed")
        _flush_events(ui, root)
        assert "LAYER_2_ULTIMATUM" in ui._hud_state_label.cget("text")

        # 4. Test Cross-thread dispatch
        dispatched = []

        def worker():
            ui.update_hud_status("ON_TASK", "Back to work")
            ui.dispatch(lambda: dispatched.append(True))

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=1.0)

        # Pump queue
        time.sleep(0.06)
        _flush_events(ui, root)
        assert dispatched == [True]
        assert "ON_TASK" in ui._hud_state_label.cget("text")

        # 5. Test Review Screen
        closed_called = []
        sample_review = "## Focus Review\n\nTotal focus: 85% on-task across 12 checks."
        ui.show_review(sample_review, on_close=lambda: closed_called.append(True))
        _flush_events(ui, root)

        assert "Review" in root.title()

    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_ui_setup_start_button_state_and_failure_reenable():
    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        def failing_start(goal: str, duration: int, mode: str):
            raise RuntimeError("Simulated startup crash")

        ui.show_setup(on_start=failing_start)
        _flush_events(ui, root)

        assert ui._start_btn is not None
        # Trigger start handler which will fail and re-enable button
        from unittest.mock import patch
        with patch("tkinter.messagebox.showerror"):
            ui._setup_handle_start()
            _flush_events(ui, root)

        assert str(ui._start_btn.cget("state")) != "disabled"
        assert "I'm Ready" in str(ui._start_btn.cget("text"))
    finally:
        try:
            root.destroy()
        except Exception:
            pass
