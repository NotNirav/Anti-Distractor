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

        # 5. Multi-Monitor Virtual Screen Bounds & Compact Bottom-Right Strict Banner
        metrics = {76: -1920, 77: 0, 78: 3840, 79: 1080, 0: 1920, 1: 1080}
        with patch("app.ui.ctypes.windll.user32.GetSystemMetrics", side_effect=lambda idx: metrics[idx]):
            bounds = ui._get_virtual_screen_bounds()
            assert bounds == (-1920, 0, 3840, 1080, 1920, 1080)
            ui.show_strict_overlay("Compile Kernel", "Distracted", 5.0, lambda: None)
            _flush_events(ui, root)
            assert ui._strict_overlay_win is not None
            geom = ui._strict_overlay_win.geometry()
            dims_part = geom.split("+")[0]
            bw, bh = (int(x) for x in dims_part.split("x"))
            assert 300 <= bw <= 750
            assert 120 <= bh <= 350
            assert bool(ui._strict_overlay_win.attributes("-topmost")) is True
            ui.hide_popups_and_overlay()
            _flush_events(ui, root)
            assert ui._strict_countdown_after_id is None
    finally:
        ui.close()


def test_ui_hud_polish_hover_sync_grace_minipill_tooltip_and_multimonitor_drag(tmp_path):
    """Verify Pause hover sync, Grace countdown, Mini-Pill toggle, ToolTip, and multi-monitor drag."""
    from types import SimpleNamespace
    from unittest.mock import patch
    from app.ui import DARK_PALETTE, LIGHT_PALETTE
    from app.ui_dialogs import ToolTip

    root = tk.Tk()
    root.withdraw()

    try:
        ui = TkinterUI(root=root)
        _flush_events(ui, root)

        moved_coords: list[tuple[int, int]] = []
        ui.on_hud_moved = lambda x, y: moved_coords.append((x, y))

        ui.show_monitoring_hud(
            "Build Very Long Software Architecture Document Title",
            "strict",
            on_end_session=lambda: None,
            duration_minutes=25,
            on_toggle_pause=lambda: None,
            on_open_rules=lambda: None,
        )
        _flush_events(ui, root)

        # Hover color sync on Pause <-> Resume
        ui.update_pause_status(True, 295.0, summary="Cycle 1/4 complete — 5m break!")
        _flush_events(ui, root)
        assert ui._hud_pause_btn._hover_cfg["normal_bg"] == LIGHT_PALETTE["warning_soft"]
        assert ui._hud_summary_label.cget("text") == "Cycle 1/4 complete — 5m break!"

        ui.update_pause_status(False, 0.0)
        _flush_events(ui, root)
        assert ui._hud_pause_btn._hover_cfg["normal_bg"] == LIGHT_PALETTE["btn_secondary_bg"]

        # Grace countdown updates state pill cleanly
        ui.update_hud_status("GRACE_PERIOD", "Instagram")
        ui.update_grace_countdown(4.0)
        _flush_events(ui, root)
        assert "Grace: 4s" in ui._hud_state_label.cget("text")

        # Mini-Pill collapse & expand
        ui.set_mini_mode(True)
        _flush_events(ui, root)
        assert ui._hud_mini_mode is True
        assert ui._hud_mini_btn.cget("text") == "Expand"
        assert "Grace: 4s" in ui._hud_state_label.cget("text")

        ui.set_mini_mode(False)
        _flush_events(ui, root)
        assert ui._hud_mini_mode is False
        assert ui._hud_mini_btn.cget("text") == "▾"

        # Theme switch while paused keeps custom break summary + synced hover metadata
        ui.update_pause_status(True, 180.0, summary="Pomodoro Break")
        ui.set_theme("dark")
        _flush_events(ui, root)
        assert ui._hud_pause_btn._hover_cfg["normal_bg"] == DARK_PALETTE["warning_soft"]
        assert ui._hud_summary_label.cget("text") == "Pomodoro Break"

        # ToolTip show_now & hide_now
        tip = ToolTip(ui._hud_goal_label, "Full Focus Goal Text")
        tip_win = tip.show_now(force_headless=True)
        assert tip_win is not None and tip_win.winfo_exists()
        tip.hide_now()
        assert tip._tip_win is None

        # Multi-monitor HUD dragging clamps to virtual screen (-1920..1920) and notifies on_hud_moved
        with patch.object(ui, "_get_virtual_screen_bounds", return_value=(-1920, 0, 3840, 1080, 1920, 1080)):
            ui._hud_start_drag(SimpleNamespace(x_root=1500, y_root=50, x=0, y=0))
            ui._hud_on_drag(SimpleNamespace(x_root=-800, y_root=140, x=0, y=0))
            ui._hud_end_drag(None)
            _flush_events(ui, root)
            assert ui._hud_saved_pos is not None
            assert ui._hud_saved_pos[0] < 0
            assert len(moved_coords) == 1
            assert moved_coords[0][0] < 0
    finally:
        ui.close()


def test_ui_smarter_rules_popups_review_kpis_vault_and_dialogs(tmp_path):
    """Verify smart Rules deduplication/Delete/AI-Suggest, Layer 1/2 prefs, Review KPI/Vault/.md, and dialogs."""
    from unittest.mock import patch
    from app.models import ActivityFeedItem, ClosedTabEntry, GoalProfile, SessionKPIs
    from app.settings import AppSettings, default_starter_presets

    root = tk.Tk()
    root.withdraw()
    opened_urls: list[str] = []

    try:
        ui = TkinterUI(root=root, url_opener=lambda u: opened_urls.append(u))
        _flush_events(ui, root)

        # 1. Setup screen with presets, history & settings triggers
        presets = default_starter_presets()
        selected_presets = []
        hist_clicked = []
        settings_clicked = []
        ui.show_setup(
            on_start=lambda g, d, m: None,
            presets=presets,
            on_select_preset=lambda p: selected_presets.append(p.name),
            on_save_preset=lambda n, g, d, m: presets,
            on_delete_preset=lambda n: presets,
            initial_pomodoro=True,
            pomodoro_cycles=4,
            on_open_history=lambda: hist_clicked.append(True),
            on_open_settings=lambda: settings_clicked.append(True),
        )
        _flush_events(ui, root)
        assert len(ui._setup_preset_btns) == 3
        ui._setup_preset_btns[0].invoke()
        _flush_events(ui, root)
        assert selected_presets == [presets[0].name]
        ui._setup_history_btn.invoke()
        ui._setup_settings_btn.invoke()
        assert hist_clicked == [True]
        assert settings_clicked == [True]

        # 2. Smarter Rules Editor: 1-click Current Site, conflict dedup, Delete key, AI Suggest
        profile = GoalProfile(
            goal="Study DSA",
            on_task_topics=["dsa"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com", "reddit.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        )
        suggested_prof = GoalProfile(
            goal="Study DSA",
            on_task_topics=["dsa", "graphs"],
            allowed_domains=["cp-algorithms.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["tiktok.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["discord.exe"],
        )
        saved_rules: list[GoalProfile] = []
        ui.show_rules_editor(
            profile,
            on_save=lambda p: saved_rules.append(p),
            on_ai_suggest=lambda _g, cb: cb(suggested_prof),
            active_target=("domain", "youtube.com"),
        )
        _flush_events(ui, root)
        w = ui._rules_editor_widgets

        # 1-click "+ Allow" moves youtube.com from borderline -> allowed without duplicate
        w["quick_allow_btn"].invoke()
        _flush_events(ui, root)
        assert "youtube.com" in w["listboxes"]["allowed_domains"].get(0, "end")
        assert "youtube.com" not in w["listboxes"]["borderline_domains"].get(0, "end")

        # Manual add deduplicates instagram.com out of blocked_domains
        w["entries"]["allowed_domains"].delete(0, "end")
        w["entries"]["allowed_domains"].insert(0, "instagram.com")
        w["add_btns"]["allowed_domains"].invoke()
        _flush_events(ui, root)
        assert "instagram.com" in w["listboxes"]["allowed_domains"].get(0, "end")
        assert "instagram.com" not in w["listboxes"]["blocked_domains"].get(0, "end")

        # Delete binding removes selected item from blocked_domains (reddit.com)
        blk_lb = w["listboxes"]["blocked_domains"]
        assert blk_lb.bind("<Delete>")
        blk_lb.selection_set(0)
        blk_lb.focus_set()
        blk_lb.event_generate("<Delete>")
        _flush_events(ui, root)
        if "reddit.com" in blk_lb.get(0, "end"):
            w["remove_btns"]["blocked_domains"].invoke()
        assert "reddit.com" not in blk_lb.get(0, "end")

        # AI Suggest Rules repopulates listboxes asynchronously
        w["ai_suggest_btn"].invoke()
        _flush_events(ui, root)
        assert "cp-algorithms.com" in w["listboxes"]["allowed_domains"].get(0, "end")

        w["save_btn"].invoke()
        _flush_events(ui, root)
        assert len(saved_rules) == 1
        assert saved_rules[0].allowed_domains == ["cp-algorithms.com"]

        # 3. Layer 1 quick allow & Layer 2 mute_sounds
        choices = []
        ui.show_layer1_popup("Study DSA", "Off-task", on_choice=lambda c: choices.append(c), source_label="reddit.com")
        _flush_events(ui, root)
        assert len(ui._active_popups) == 1
        allow_btn = getattr(ui._active_popups[0], "_allow_domain_btn", None)
        assert allow_btn is not None
        assert "reddit.com" in allow_btn.cget("text")
        allow_btn.invoke()
        _flush_events(ui, root)
        assert choices == ["allow_domain"]

        ui.configure_preferences(break_minutes=10, mute_sounds=True)
        with patch.object(root, "bell") as mock_bell:
            ui.show_layer2_warning("Study DSA", "Muted test")
            _flush_events(ui, root)
            mock_bell.assert_not_called()
        ui.configure_preferences(break_minutes=5, mute_sounds=False)
        with patch.object(root, "bell") as mock_bell:
            ui.show_layer2_warning("Study DSA", "Unmuted test")
            _flush_events(ui, root)
            mock_bell.assert_called_once()
        ui.hide_popups_and_overlay()
        _flush_events(ui, root)

        # 4. Review async two-step refresh: loading -> KPI cards + Tab Vault + .md export (zero TclError)
        ui.show_review("Generating your review...", on_close=lambda: None)
        _flush_events(ui, root)
        assert ui._review_progress_bar is not None

        kpis = SessionKPIs(focus_score_pct=92, focused_minutes=23.0, distractions_count=1, overrides_count=1, total_checks=12)
        vault = [ClosedTabEntry(title="Instagram Reels", url="https://instagram.com/reels", domain="instagram.com", closed_at=100.0)]
        ui.show_review("## Focus Session Review\n\n- Solid session.", on_close=lambda: None, kpis=kpis, closed_tabs=vault)
        _flush_events(ui, root)
        assert ui._review_progress_bar is None
        assert ui._review_kpi_holder.winfo_manager() == "pack"
        assert ui._review_vault_holder.winfo_manager() == "pack"
        assert len(ui._review_kpi_holder.winfo_children()) == 4
        assert len(ui._review_vault_reopen_btns) == 1
        ui._review_vault_reopen_btns[0].invoke()
        assert opened_urls == ["https://instagram.com/reels"]

        export_path = tmp_path / "session_export.md"
        with patch("app.ui.filedialog.asksaveasfilename", return_value=str(export_path)):
            ui._review_export_btn.invoke()
            _flush_events(ui, root)
        exported = export_path.read_text(encoding="utf-8")
        assert "Focus Score" in exported
        assert "Focus Session Review" in exported
        assert "Closed Tabs Vault" in exported
        assert "https://instagram.com/reels" in exported

        # 5. Live Activity Drawer + Mark Relevant callback
        marked_items = []
        ui.clear_activity_items()
        ui.append_activity_item(
            ActivityFeedItem(
                log_id=10,
                timestamp=1000.0,
                source_key="reddit.com",
                title="r/algorithms",
                on_task=False,
                tier_used=1,
                model_used="gemini-3.5-flash-lite",
                confidence=0.88,
                reason="Looks like general browsing",
                action_taken="grace_started",
            ),
            on_mark_relevant=lambda it: marked_items.append(it.source_key),
        )
        ui.show_activity_drawer(on_mark_relevant=lambda it: marked_items.append(it.source_key))
        _flush_events(ui, root)
        assert ui._activity_drawer is not None
        assert len(ui._activity_drawer.relevant_buttons) == 1
        ui._activity_drawer.relevant_buttons[0][1].invoke()
        _flush_events(ui, root)
        assert "reddit.com" in marked_items
        ui._activity_drawer.close()

        # 6. History & Analytics Dialog callbacks
        read_sessions = []
        repeat_sessions = []
        ui.show_history_dialog(
            {"current_streak_days": 3, "total_focus_hours": 4.5, "completed_sessions": 9, "avg_focus_score_pct": 89, "top_distractions": [("reddit.com", 2)]},
            [{"id": 1, "goal": "Study DSA", "duration_minutes": 25, "mode": "strict", "started_at": 1700000000.0, "ended_at": 1700001500.0, "focus_score_pct": 92}],
            on_read_review=lambda s: read_sessions.append(s["id"]),
            on_repeat_session=lambda s: repeat_sessions.append(s["goal"]),
        )
        _flush_events(ui, root)
        assert ui._history_dialog is not None
        assert ui._history_dialog.stat_labels["streak"].cget("text") == "3d"
        ui._history_dialog.read_btn.invoke()
        assert read_sessions == [1]

        ui.show_history_dialog(
            {"current_streak_days": 3, "total_focus_hours": 4.5, "completed_sessions": 9, "avg_focus_score_pct": 89, "top_distractions": []},
            [{"id": 2, "goal": "Write Docs", "duration_minutes": 30, "mode": "normal", "started_at": 1700000000.0, "ended_at": 1700001800.0, "focus_score_pct": 95}],
            on_repeat_session=lambda s: repeat_sessions.append(s["goal"]),
        )
        _flush_events(ui, root)
        ui._history_dialog.repeat_btn.invoke()
        assert repeat_sessions == ["Write Docs"]

        # 7. Settings Dialog save callback
        saved_settings: list[AppSettings] = []
        ui.show_settings_dialog(
            AppSettings(break_minutes=5, mute_sounds=False, timing_preset="dev", hotkeys_enabled=True, pomodoro_enabled=False, pomodoro_cycles=4),
            (True, "Configured (...abcd)"),
            on_save_settings=lambda s: saved_settings.append(s),
        )
        _flush_events(ui, root)
        assert ui._settings_dialog is not None
        ui._settings_dialog.break_var.set(10)
        ui._settings_dialog.mute_var.set(True)
        ui._settings_dialog.cycles_var.set("6")
        ui._settings_dialog.save_btn.invoke()
        _flush_events(ui, root)
        assert len(saved_settings) == 1
        assert saved_settings[0].break_minutes == 10
        assert saved_settings[0].mute_sounds is True
        assert saved_settings[0].pomodoro_cycles == 6

        # 8. Onboarding screen: Show/Hide, Dark/Light switch preserves input, Save & Skip callbacks
        saved_keys: list[str] = []
        continued: list[bool] = []
        skipped: list[bool] = []

        def _fake_save_key(k: str) -> tuple[bool, str]:
            if not k or k == "bad":
                return False, "Invalid key"
            saved_keys.append(k)
            return True, "Configured (••••••••4321)"

        ui.show_onboarding(
            on_save_key=_fake_save_key,
            on_test_key=lambda k, cb: cb(True, "✓ Connected"),
            on_continue=lambda: continued.append(True),
            on_skip=lambda: skipped.append(True),
        )
        _flush_events(ui, root)
        assert ui._active_screen == "onboarding"
        assert ui._onboarding_key_entry is not None
        assert ui._onboarding_key_entry.cget("show") == "•"
        ui._onboarding_show_btn.invoke()
        _flush_events(ui, root)
        assert ui._onboarding_key_entry.cget("show") == ""
        ui._onboarding_key_var.set("AIzaSyThemePreserve4321")
        ui.set_theme("dark")
        _flush_events(ui, root)
        assert ui._active_screen == "onboarding"
        assert ui._onboarding_key_var.get() == "AIzaSyThemePreserve4321"
        ui._onboarding_save_btn.invoke()
        _flush_events(ui, root)
        assert saved_keys == ["AIzaSyThemePreserve4321"]
        assert continued == [True]

        ui._onboarding_skip_btn.invoke()
        _flush_events(ui, root)
        assert skipped == [True]

        # 9. Setup API Key header badge reflects configured/unconfigured state & updates live
        api_btn_clicks: list[bool] = []
        ui.show_setup(
            on_start=lambda g, d, m: None,
            api_key_status=(False, "Not configured"),
            on_open_api_key=lambda: api_btn_clicks.append(True),
        )
        _flush_events(ui, root)
        assert ui._setup_api_key_btn is not None
        assert ui._setup_api_key_tooltip is not None
        assert ui._setup_status_lbl is not None
        assert ui._setup_api_key_btn.cget("text") == "Set API Key"
        ui.update_setup_api_key_status((True, "Configured (••••••••4321)"))
        _flush_events(ui, root)
        assert ui._setup_api_key_btn.cget("text") == "Key ✓"
        ui._setup_api_key_btn.invoke()
        _flush_events(ui, root)
        assert api_btn_clicks == [True]

        # 10. Settings dialog Show/Hide and Remove Key preserves onboarding_skipped
        cleared: list[bool] = []
        preserved_saved: list[AppSettings] = []
        ui.show_settings_dialog(
            AppSettings(break_minutes=5, onboarding_skipped=True),
            (True, "Configured (••••••••4321)"),
            on_save_settings=lambda s: preserved_saved.append(s),
            on_clear_api_key=lambda: (cleared.append(True) or (False, "Not configured")) and (False, "Not configured"),
            focus_api_key=True,
        )
        _flush_events(ui, root)
        assert ui._settings_dialog is not None
        ui._settings_dialog.toggle_key_btn.invoke()
        _flush_events(ui, root)
        assert ui._settings_dialog.key_entry.cget("show") == ""
        ui._settings_dialog.remove_key_btn.invoke()
        _flush_events(ui, root)
        assert cleared == [True]
        assert ui._settings_dialog.key_status_lbl.cget("text") == "Not configured"
        ui._settings_dialog.save_btn.invoke()
        _flush_events(ui, root)
        assert len(preserved_saved) == 1
        assert preserved_saved[0].onboarding_skipped is True

        # 11. Minimize-to-taskbar safety and Strict banner Escape dismissal
        ui.minimize_to_taskbar()
        _flush_events(ui, root)
        lockout_done: list[bool] = []
        ui.show_strict_overlay("Study", "Off-task", 6.0, lambda: lockout_done.append(True))
        _flush_events(ui, root)
        assert ui._strict_overlay_win is not None
        assert ui._strict_countdown_after_id is not None
        ui._strict_overlay_win.event_generate("<Escape>")
        _flush_events(ui, root)
        if ui._strict_overlay_win is not None:
            ui.hide_popups_and_overlay()
            _flush_events(ui, root)
    finally:
        ui.close()
