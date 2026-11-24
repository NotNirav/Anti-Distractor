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


def test_ui_layer2_warning_has_auto_close_and_layer1_stays():
    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        # Layer 1 popup
        ui.show_layer1_popup("Goal A", "Reason A", on_choice=lambda c: None)
        _flush_events(ui, root)
        assert len(ui._active_popups) == 1
        l1_win = ui._active_popups[0]
        assert getattr(l1_win, "_auto_close_id", None) is None

        # Layer 2 warning
        ui.show_layer2_warning("Goal B", "Reason B")
        _flush_events(ui, root)
        assert len(ui._active_popups) == 1
        l2_win = ui._active_popups[0]
        assert getattr(l2_win, "_auto_close_id", None) is not None

        # Hide popups cleans up cleanly
        ui.hide_popups_and_overlay()
        _flush_events(ui, root)
        assert len(ui._active_popups) == 0
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_ui_hud_draggable_and_ai_unavailable_status():
    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        ui.show_monitoring_hud("Study Algorithms", "normal", on_end_session=lambda: None)
        _flush_events(ui, root)

        # Verify draggable callbacks exist and move root geometry
        assert hasattr(ui, "_hud_start_drag")
        assert hasattr(ui, "_hud_on_drag")
        ev_start = tk.Event()
        ev_start.x, ev_start.y = 10, 10
        ui._hud_start_drag(ev_start)

        ev_move = tk.Event()
        ev_move.x, ev_move.y = 30, 40
        ui._hud_on_drag(ev_move)

        # Verify "AI unavailable - rules only" status is displayed clearly without truncation
        ai_msg = "AI unavailable - rules only"
        ui.update_hud_status("ON_TASK", ai_msg)
        _flush_events(ui, root)

        assert ui._hud_summary_label.cget("text") == ai_msg

        # Verify AI calls HUD update
        ui.update_ai_calls(4, 2)
        _flush_events(ui, root)
        assert ui._hud_ai_calls_label.cget("text") == "AI calls: T1 4 / T2 2"
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_ui_top5_polish_behaviors():
    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        # 1. AI unavailable turns summary red
        ui.show_monitoring_hud("Study DSA", "strict", on_end_session=lambda: None)
        _flush_events(ui, root)
        ui.update_hud_status("ON_TASK", "AI unavailable - rules only")
        _flush_events(ui, root)
        assert str(ui._hud_summary_label.cget("foreground")) == "#C62828"

        # 2. Tier-0 shift popup deduplicates and cleans up
        ui.show_tier0_shift_popup("First notice")
        ui.show_tier0_shift_popup("Second notice")
        _flush_events(ui, root)
        assert len(ui._active_popups) == 1
        assert ui._tier0_popup is ui._active_popups[0]
        ui.hide_popups_and_overlay()
        _flush_events(ui, root)
        assert len(ui._active_popups) == 0
        assert ui._tier0_popup is None

        # 3. Strict overlay binds Escape and F12
        done_calls = []
        ui.show_strict_overlay("Study DSA", "Distracted", 10.0, lambda: done_calls.append(True))
        _flush_events(ui, root)
        assert ui._strict_overlay_win is not None
        assert ui._strict_overlay_win.bind("<Escape>")
        assert ui._strict_overlay_win.bind("<F12>")
        ui.hide_popups_and_overlay()
        _flush_events(ui, root)

        # 4. Review screen strips raw markdown syntax into formatted text
        ui.show_review("## Summary\n- **Focus:** 90%", on_close=lambda: None)
        _flush_events(ui, root)
        txt_widget = next(
            child
            for w in ui._current_frame.winfo_children()
            for child in ([w] + w.winfo_children())
            if isinstance(child, tk.Text)
        )
        rendered = txt_widget.get("1.0", "end")
        assert "##" not in rendered
        assert "**" not in rendered
        assert "• Focus: 90%" in rendered
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_ui_setup_fits_ready_button_and_inline_validation():
    """Verify Setup window fits its Ready button without clipping and validates inline."""
    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        started = []
        ui.show_setup(
            on_start=lambda g, d, m: started.append((g, d, m)),
            initial_goal="",
            initial_duration=25,
            initial_mode="strict",
        )
        _flush_events(ui, root)

        # Window geometry and minsize must fit requested content so the Start/Ready button is never crushed
        fit_w, fit_h = ui._last_fitted_size
        min_w, min_h = root.minsize()
        assert fit_w >= root.winfo_reqwidth()
        assert fit_h >= root.winfo_reqheight()
        assert min_w >= root.winfo_reqwidth()
        assert min_h >= root.winfo_reqheight()
        assert ui._start_btn.winfo_reqheight() >= 28

        # Empty goal triggers inline error without calling on_start or modal dialog
        from unittest.mock import patch
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            ui._setup_handle_start()
            _flush_events(ui, root)
            mock_warn.assert_not_called()

        assert started == []
        assert "enter a focus goal" in ui._setup_status_lbl.cget("text").lower()
    finally:
        ui.close()


def test_ui_hud_unmaximizes_disables_resize_toggles_pin_and_shows_timer():
    """Verify HUD exits maximized state, disables resize/maximize, toggles pin, and shows timer."""
    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        ui.show_setup(on_start=lambda g, d, m: None)
        _flush_events(ui, root)

        # Transition to HUD with duration_minutes
        ui.show_monitoring_hud(
            "Study Algorithms",
            "strict",
            on_end_session=lambda: None,
            duration_minutes=25,
        )
        _flush_events(ui, root)

        # HUD must not be resizable (disables Windows Maximize button)
        assert root.resizable() == (0, 0) or root.resizable() == (False, False)
        assert bool(root.attributes("-topmost")) is True

        # Pin button toggles topmost off and back on
        assert ui._hud_pin_btn is not None
        ui._hud_pin_btn.invoke()
        _flush_events(ui, root)
        assert bool(root.attributes("-topmost")) is False
        assert "Unpinned" in ui._hud_pin_btn.cget("text")

        ui._hud_pin_btn.invoke()
        _flush_events(ui, root)
        assert bool(root.attributes("-topmost")) is True
        assert "Pinned" in ui._hud_pin_btn.cget("text")

        # Session countdown timer is populated
        assert ui._hud_timer_label is not None
        assert "left" in ui._hud_timer_label.cget("text")

        # Review screen updates in-place when transitioning from loading -> final
        new_session_clicked = []
        ui.show_review("Generating your review...", on_close=lambda: None, on_new_session=lambda: new_session_clicked.append(True))
        _flush_events(ui, root)
        first_txt_widget = ui._review_text_widget
        assert first_txt_widget is not None

        ui.show_review("## Done\n- **Score:** 95%", on_close=lambda: None, on_new_session=lambda: new_session_clicked.append(True))
        _flush_events(ui, root)
        assert ui._review_text_widget is first_txt_widget
        assert "• Score: 95%" in first_txt_widget.get("1.0", "end")
    finally:
        ui.close()


def test_ui_hud_extension_dot_pause_theme_rules_tray_and_multimonitor_overlay():
    """Verify Extension Dot, Pause 5m button, Dark/Light theme toggle, Rules Editor, Tray, and Dual-Monitor Overlay."""
    from unittest.mock import patch
    from app.models import GoalProfile

    tray_events: list[tuple[str, Any]] = []

    class FakeTrayManager:
        def __init__(self, on_restore, on_toggle_pause=None, on_end_session=None, on_quit=None, icon_factory=None):
            self.on_restore = on_restore
            self.started = False
            self.stopped = False
            tray_events.append(("init", self))

        def start(self, tooltip="Anti-Distractor", state="ON_TASK") -> bool:
            self.started = True
            tray_events.append(("start", state))
            return True

        def update_state(self, state: str, paused: bool = False, tooltip: str | None = None) -> None:
            tray_events.append(("update", (state, paused)))

        def stop(self) -> None:
            self.stopped = True
            tray_events.append(("stop", True))

    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root, tray_factory=FakeTrayManager)
        _flush_events(ui, root)

        # 1. Setup + theme toggle preserves goal & mode
        ui.show_setup(on_start=lambda g, d, m: None, initial_goal="Compile Kernel", initial_duration=45, initial_mode="normal")
        _flush_events(ui, root)
        assert ui.theme == "light"
        ui.toggle_theme()
        _flush_events(ui, root)
        assert ui.theme == "dark"
        assert ui._setup_goal_var.get() == "Compile Kernel"
        assert ui._setup_dur_var.get() == "45"
        assert ui._setup_mode_var.get() == "normal"

        # Switch back to light before HUD check
        ui.set_theme("light")
        _flush_events(ui, root)

        # 2. HUD with Pause, Rules, Extension Dot
        pause_clicks = []
        rules_clicks = []
        ui.show_monitoring_hud(
            "Compile Kernel",
            "strict",
            on_end_session=lambda: None,
            duration_minutes=45,
            on_toggle_pause=lambda: pause_clicks.append(True),
            on_open_rules=lambda: rules_clicks.append(True),
        )
        _flush_events(ui, root)

        assert ui._hud_ext_label.cget("text") == "Ext: Off"
        ui.update_extension_status(True)
        _flush_events(ui, root)
        assert ui._hud_ext_label.cget("text") == "Ext: Live"

        ui.update_pause_status(True, 295.0)
        _flush_events(ui, root)
        assert ui._hud_pause_btn.cget("text") == "Resume"
        assert "PAUSED" in ui._hud_state_label.cget("text")
        assert "Paused 04:55" in ui._hud_timer_label.cget("text")

        ui.update_pause_status(False, 0.0)
        _flush_events(ui, root)
        assert ui._hud_pause_btn.cget("text") == "Pause 5m"

        # Dark toggle on HUD preserves state & live ext/ai telemetry
        ui.update_ai_calls(2, 1)
        _flush_events(ui, root)
        ui.toggle_theme()
        _flush_events(ui, root)
        assert ui.theme == "dark"
        assert ui._hud_ext_label.cget("text") == "Ext: Live"
        assert ui._hud_ai_calls_label.cget("text") == "AI calls: T1 2 / T2 1"

        # 3. Editable Rules Dialog
        profile = GoalProfile(
            goal="Compile Kernel",
            on_task_topics=["c", "linux"],
            allowed_domains=["kernel.org"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        )
        saved_profiles: list[GoalProfile] = []
        ui.show_rules_editor(profile, on_save=lambda p: saved_profiles.append(p))
        _flush_events(ui, root)

        widgets = ui._rules_editor_widgets
        widgets["entries"]["allowed_domains"].delete(0, "end")
        widgets["entries"]["allowed_domains"].insert(0, "https://www.docs.python.org/3/")
        widgets["add_btns"]["allowed_domains"].invoke()
        widgets["listboxes"]["blocked_domains"].selection_set(0)
        widgets["remove_btns"]["blocked_domains"].invoke()
        _flush_events(ui, root)
        widgets["save_btn"].invoke()
        _flush_events(ui, root)
        assert len(saved_profiles) == 1
        assert "docs.python.org" in saved_profiles[0].allowed_domains
        assert "instagram.com" not in saved_profiles[0].blocked_domains

        # 4. System Tray minimize + restore
        ui.minimize_to_tray()
        _flush_events(ui, root)
        assert any(ev[0] == "start" for ev in tray_events)
        ui.restore_from_tray()
        _flush_events(ui, root)
        assert any(ev[0] == "stop" for ev in tray_events)

        # 5. Multi-Monitor Virtual Screen Overlay Bounds
        metrics = {76: -1920, 77: 0, 78: 3840, 79: 1080, 0: 1920, 1: 1080}
        with patch("app.ui.ctypes.windll.user32.GetSystemMetrics", side_effect=lambda idx: metrics[idx]):
            bounds = ui._get_virtual_screen_bounds()
            assert bounds == (-1920, 0, 3840, 1080, 1920, 1080)
            ui.show_strict_overlay("Compile Kernel", "Distracted", 5.0, lambda: None)
            _flush_events(ui, root)
            assert ui._strict_overlay_win is not None
            geom = ui._strict_overlay_win.geometry()
            assert "3840x1080" in geom
            ui.hide_popups_and_overlay()
            _flush_events(ui, root)
    finally:
        ui.close()
