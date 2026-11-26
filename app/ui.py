"""Tkinter UI components: setup, monitoring HUD, Layer 1/2 popups, strict overlay, rules editor, tray, dialogs, and review."""

from __future__ import annotations

import ctypes
import logging
import math
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any, Callable, Literal, Protocol
import webbrowser

import customtkinter as ctk

from app.models import (
    ActivityFeedItem,
    AgentStateName,
    ClosedTabEntry,
    GoalProfile,
    ModeType,
    SessionKPIs,
)
from app.profile import normalize_domain
from app.settings import AppSettings, FocusPreset
from app.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    PALETTE,
    RADII,
    SPACING,
    STATE_COLORS,
    ButtonVariant,
    ModernButton,
    ModernEntry,
    ModernLabel,
    ModernProgressBar,
    StatusPill,
    SurfaceCard,
    ThemeType,
    apply_ttk_theme,
    bind_drag,
    detect_system_theme,
    fade_in_window,
    get_state_pill_style,
    make_button as _make_button,
    mono_font,
    resolve_variant_colors as _resolve_variant_colors,
    set_button_variant as _set_button_variant,
    ui_font,
)
from app.tray import SystemTrayManager
from app.ui_dialogs import (
    ActivityDrawerDialog,
    HistoryAnalyticsDialog,
    SettingsDialog,
    ToolTip,
)
from app.win32_ui import (
    apply_brand_icon,
    apply_dark_titlebar,
    apply_noactivate_topmost,
    apply_rounded_corners,
    get_monitor_work_area,
)

logger = logging.getLogger(__name__)


class UIHooks(Protocol):
    """Thread-safe interface used by the app controller to drive Tkinter screens."""

    def show_setup(
        self,
        on_start: Callable[[str, int, ModeType], None],
        initial_goal: str | None = None,
        initial_duration: int | None = None,
        initial_mode: ModeType | None = None,
        on_edit_rules: Callable[[str], None] | None = None,
    ) -> None: ...
    def show_monitoring_hud(
        self,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
        duration_minutes: int | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_open_rules: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None: ...
    def update_hud_status(self, state: AgentStateName, summary: str) -> None: ...
    def update_extension_status(self, connected: bool) -> None: ...
    def update_pause_status(self, paused: bool, remaining_seconds: float = 0.0) -> None: ...
    def show_rules_editor(
        self,
        profile: GoalProfile,
        on_save: Callable[[GoalProfile], None],
    ) -> None: ...
    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant", "allow_domain"]], None],
    ) -> None: ...
    def show_layer2_warning(self, goal: str, reason: str) -> None: ...
    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None: ...
    def hide_popups_and_overlay(self) -> None: ...
    def show_tier0_shift_popup(self, message: str) -> None: ...
    def update_ai_calls(self, t1_count: int, t2_count: int) -> None: ...
    def show_review(
        self,
        review_text: str,
        on_close: Callable[[], None],
        on_new_session: Callable[[], None] | None = None,
    ) -> None: ...


def _bind_hover(
    widget: tk.Widget,
    normal_bg: str,
    hover_bg: str,
    normal_fg: str | None = None,
    hover_fg: str | None = None,
) -> None:
    """Attach mutable hover color transitions to a flat tk.Widget without stacking bindings."""
    cfg = {
        "normal_bg": normal_bg,
        "hover_bg": hover_bg,
        "normal_fg": normal_fg,
        "hover_fg": hover_fg,
    }
    setattr(widget, "_hover_cfg", cfg)

    if getattr(widget, "_hover_bound", False):
        return
    setattr(widget, "_hover_bound", True)

    def _on_enter(_event: tk.Event) -> None:
        try:
            if str(widget.cget("state")) == "disabled":
                return
            cur = getattr(widget, "_hover_cfg", cfg)
            widget.configure(bg=cur["hover_bg"])
            if cur["hover_fg"] is not None:
                widget.configure(fg=cur["hover_fg"])
        except Exception:
            pass

    def _on_leave(_event: tk.Event) -> None:
        try:
            cur = getattr(widget, "_hover_cfg", cfg)
            widget.configure(bg=cur["normal_bg"])
            if cur["normal_fg"] is not None:
                widget.configure(fg=cur["normal_fg"])
        except Exception:
            pass

    widget.bind("<Enter>", _on_enter, add="+")
    widget.bind("<Leave>", _on_leave, add="+")


class TkinterUI:
    """Thread-safe Tkinter implementation of UIHooks running on the main thread."""

    def __init__(
        self,
        root: tk.Tk | None = None,
        tray_factory: Callable[..., SystemTrayManager] | None = None,
        url_opener: Callable[[str], Any] | None = None,
        initial_theme: ThemeType | None = None,
    ) -> None:
        self._owns_root = root is None
        self.root = root if root is not None else tk.Tk()
        self._main_thread_id = threading.get_ident()
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._tray_factory = tray_factory or SystemTrayManager
        self._tray: SystemTrayManager | None = None
        self._url_opener: Callable[[str], Any] = url_opener or webbrowser.open

        if initial_theme in ("light", "dark"):
            self._theme: ThemeType = initial_theme
        elif root is None:
            self._theme = detect_system_theme()
        else:
            self._theme = "light"
        self.on_theme_changed: Callable[[ThemeType], None] | None = None
        self.on_hud_moved: Callable[[int, int], None] | None = None

        # Configurable runtime preferences
        self._break_minutes: int = 5
        self._mute_sounds: bool = False
        self._hud_saved_pos: tuple[int, int] | None = None

        self._configure_theme()

        self._active_screen: Literal["none", "onboarding", "setup", "hud", "review"] = "none"
        self._current_frame: tk.Frame | None = None
        self._theme_btn: tk.Button | None = None

        # Onboarding live state for seamless theme switches
        self._onboarding_callbacks: dict[str, Any] = {}
        self._onboarding_key_var: tk.StringVar | None = None
        self._onboarding_show_key: bool = False
        self._onboarding_key_entry: ttk.Entry | None = None
        self._onboarding_show_btn: tk.Button | None = None
        self._onboarding_feedback_lbl: tk.Label | None = None
        self._onboarding_test_btn: tk.Button | None = None
        self._onboarding_save_btn: tk.Button | None = None
        self._onboarding_skip_btn: tk.Button | None = None

        # Setup live state for seamless theme switches
        self._setup_on_start: Callable[[str, int, ModeType], None] | None = None
        self._setup_on_edit_rules: Callable[[str], None] | None = None
        self._setup_extra_kwargs: dict[str, Any] = {}
        self._setup_goal_var: tk.StringVar | None = None
        self._setup_dur_var: tk.StringVar | None = None
        self._setup_mode_var: tk.StringVar | None = None
        self._setup_pomodoro_var: tk.BooleanVar | None = None
        self._setup_progress_bar: ttk.Progressbar | None = None
        self._setup_api_key_btn: tk.Button | None = None
        self._setup_api_key_tooltip: ToolTip | None = None

        # HUD live state & widgets
        self._hud_state_label: tk.Label | None = None
        self._hud_summary_label: tk.Label | None = None
        self._hud_mode_label: tk.Label | None = None
        self._hud_goal_label: tk.Label | None = None
        self._hud_ai_calls_label: tk.Label | None = None
        self._hud_timer_label: tk.Label | None = None
        self._hud_ext_dot: tk.Label | None = None
        self._hud_ext_label: tk.Label | None = None
        self._hud_pin_btn: tk.Button | None = None
        self._hud_pause_btn: tk.Button | None = None
        self._hud_rules_btn: tk.Button | None = None
        self._hud_activity_btn: tk.Button | None = None
        self._hud_tray_btn: tk.Button | None = None
        self._hud_mini_btn: tk.Button | None = None
        self._hud_startup_bar: ttk.Progressbar | None = None
        self._hud_tooltips: list[ToolTip] = []

        self._hud_pinned: bool = True
        self._hud_mini_mode: bool = False
        self._hud_deadline: float | None = None
        self._hud_session_remaining_on_pause: float | None = None
        self._hud_timer_after_id: str | None = None
        self._hud_paused: bool = False
        self._hud_pause_remaining: float = 0.0
        self._hud_grace_remaining: float | None = None
        self._hud_ext_connected: bool = False
        self._hud_last_goal: str = ""
        self._hud_last_mode: ModeType = "strict"
        self._hud_last_state: AgentStateName = "ON_TASK"
        self._hud_last_summary: str = "Monitoring active."
        self._hud_pause_summary: str = "Break active — monitoring paused."
        self._hud_t1_count: int = 0
        self._hud_t2_count: int = 0
        self._hud_callbacks: dict[str, Any] = {}

        # Live Activity Feed & Secondary Dialogs
        self._activity_items: list[ActivityFeedItem] = []
        self._activity_mark_relevant_cb: Callable[[ActivityFeedItem], None] | None = None
        self._activity_drawer: ActivityDrawerDialog | None = None
        self._history_dialog: HistoryAnalyticsDialog | None = None
        self._settings_dialog: SettingsDialog | None = None

        # Review live state & widgets
        self._review_text_widget: tk.Text | None = None
        self._review_sub_label: tk.Label | None = None
        self._review_copy_btn: tk.Button | None = None
        self._review_export_btn: tk.Button | None = None
        self._review_progress_bar: ttk.Progressbar | None = None
        self._review_kpi_holder: tk.Frame | None = None
        self._review_vault_holder: tk.Frame | None = None
        self._review_raw_text: str = ""
        self._review_kpis: SessionKPIs | None = None
        self._review_closed_tabs: list[ClosedTabEntry] = []
        self._review_callbacks: dict[str, Any] = {}

        # Popups / rules editor / strict overlay
        self._active_popups: list[tk.Toplevel] = []
        self._tier0_popup: tk.Toplevel | None = None
        self._rules_editor_win: tk.Toplevel | None = None
        self._rules_editor_widgets: dict[str, Any] = {}
        self._strict_overlay_win: tk.Toplevel | None = None
        self._strict_countdown_after_id: str | None = None
        self._pump_after_id: str | None = None
        self._screen_gen: int = 0
        self._last_fitted_size: tuple[int, int] = (0, 0)
        self._closed: bool = False

        # Start the queue pump
        self._pump()

    def _p(self, key: str) -> str:
        """Return color token for the currently active theme."""
        pal = DARK_PALETTE if self._theme == "dark" else LIGHT_PALETTE
        return pal[key]

    def _active_palette(self) -> dict[str, str]:
        return DARK_PALETTE if self._theme == "dark" else LIGHT_PALETTE

    @property
    def theme(self) -> ThemeType:
        return self._theme

    @property
    def setup_pomodoro_enabled(self) -> bool:
        if self._setup_pomodoro_var is not None:
            try:
                return bool(self._setup_pomodoro_var.get())
            except Exception:
                return False
        return False

    def configure_preferences(
        self,
        *,
        break_minutes: int | None = None,
        mute_sounds: bool | None = None,
        hud_pos: tuple[int, int] | None = None,
    ) -> None:
        """Update break duration, mute flag, and remembered HUD position safely."""
        if break_minutes is not None:
            self._break_minutes = max(1, int(break_minutes))
        if mute_sounds is not None:
            self._mute_sounds = bool(mute_sounds)
        if hud_pos is not None:
            self._hud_saved_pos = (int(hud_pos[0]), int(hud_pos[1]))

        def _sync_btn() -> None:
            if (
                self._active_screen == "hud"
                and not self._hud_paused
                and self._hud_pause_btn is not None
                and self._hud_pause_btn.winfo_exists()
                and not self._hud_mini_mode
            ):
                self._hud_pause_btn.configure(text=f"Pause {self._break_minutes}m")

        if threading.get_ident() == self._main_thread_id:
            _sync_btn()
        else:
            self.dispatch(_sync_btn)

    def _configure_theme(self) -> None:
        """Apply consistent colors, CTk appearance mode, brand icon, and native titlebar."""
        pal = self._active_palette()
        dark = self._theme == "dark"
        try:
            ctk.set_appearance_mode("dark" if dark else "light")
        except Exception:
            pass
        try:
            self.root.configure(bg=pal["bg"])
        except Exception:
            pass
        apply_brand_icon(self.root)
        apply_dark_titlebar(self.root, dark=dark)
        apply_ttk_theme(self.root, pal)

    def minimize_to_taskbar(self) -> None:
        """Minimize main window to the taskbar if visible (never un-withdraws headless roots)."""

        def _action() -> None:
            try:
                if self.root.state() != "withdrawn":
                    self.root.iconify()
            except Exception:
                pass

        if threading.get_ident() == self._main_thread_id:
            _action()
        else:
            self.dispatch(_action)

    def _sync_open_toplevel_titlebars(self) -> None:
        dark = self._theme == "dark"
        candidates: list[tk.Misc | None] = [
            self._rules_editor_win,
            self._activity_drawer.win if self._activity_drawer is not None else None,
            self._history_dialog.win if self._history_dialog is not None else None,
            self._settings_dialog.win if self._settings_dialog is not None else None,
            *self._active_popups,
        ]
        for w in candidates:
            if w is not None:
                try:
                    if w.winfo_exists():
                        apply_dark_titlebar(w, dark=dark)
                except Exception:
                    pass

    def set_theme(self, theme: ThemeType) -> None:
        """Switch UI theme ('light' or 'dark') and re-render the current view in place."""
        if theme not in ("light", "dark"):
            return

        def _apply() -> None:
            if self._theme == theme:
                return
            self._theme = theme
            self._configure_theme()
            self._sync_open_toplevel_titlebars()

            if self._active_screen == "onboarding" and "on_save_key" in self._onboarding_callbacks:
                cur_key = self._onboarding_key_var.get() if self._onboarding_key_var is not None else ""
                self._render_onboarding_now(
                    on_save_key=self._onboarding_callbacks["on_save_key"],
                    on_test_key=self._onboarding_callbacks.get("on_test_key"),
                    on_continue=self._onboarding_callbacks["on_continue"],
                    on_skip=self._onboarding_callbacks["on_skip"],
                    initial_key=cur_key,
                )
            elif self._active_screen == "setup" and self._setup_on_start is not None:
                cur_goal = self._setup_goal_var.get() if self._setup_goal_var is not None else None
                cur_dur_raw = self._setup_dur_var.get() if self._setup_dur_var is not None else None
                try:
                    cur_dur = int(cur_dur_raw.strip()) if cur_dur_raw else None
                except ValueError:
                    cur_dur = None
                cur_mode = self._setup_mode_var.get() if self._setup_mode_var is not None else None
                resolved_mode: ModeType | None = cur_mode if cur_mode in ("strict", "normal") else None  # type: ignore[assignment]
                cur_pomo = self._setup_pomodoro_var.get() if self._setup_pomodoro_var is not None else None
                merged_extra = dict(self._setup_extra_kwargs)
                if cur_pomo is not None:
                    merged_extra["initial_pomodoro"] = bool(cur_pomo)
                self._render_setup_now(
                    self._setup_on_start,
                    initial_goal=cur_goal,
                    initial_duration=cur_dur,
                    initial_mode=resolved_mode,
                    on_edit_rules=self._setup_on_edit_rules,
                    **merged_extra,
                )
            elif self._active_screen == "hud" and "on_end_session" in self._hud_callbacks:
                prev_pos: tuple[int, int] | None = None
                try:
                    if self.root.state() != "withdrawn":
                        prev_pos = (self.root.winfo_x(), self.root.winfo_y())
                except Exception:
                    pass
                self._render_hud_now(
                    goal=self._hud_last_goal,
                    mode=self._hud_last_mode,
                    on_end_session=self._hud_callbacks["on_end_session"],
                    duration_minutes=None,
                    on_toggle_pause=self._hud_callbacks.get("on_toggle_pause"),
                    on_open_rules=self._hud_callbacks.get("on_open_rules"),
                    on_open_activity=self._hud_callbacks.get("on_open_activity"),
                    on_quit=self._hud_callbacks.get("on_quit"),
                    preserve_state=True,
                    preserve_pos=prev_pos,
                )
            elif self._active_screen == "review" and "on_close" in self._review_callbacks:
                self._review_text_widget = None
                self._render_review_now(
                    self._review_raw_text,
                    on_close=self._review_callbacks["on_close"],
                    on_new_session=self._review_callbacks.get("on_new_session"),
                    kpis=self._review_kpis,
                    closed_tabs=self._review_closed_tabs,
                )

        if threading.get_ident() == self._main_thread_id:
            _apply()
        else:
            self.dispatch(_apply)

    def toggle_theme(self) -> None:
        """Toggle between Light and Dark theme and notify optional persistence callback."""
        next_theme: ThemeType = "dark" if self._theme == "light" else "light"
        self.set_theme(next_theme)
        if self.on_theme_changed is not None:
            try:
                self.on_theme_changed(next_theme)
            except Exception:
                logger.exception("on_theme_changed callback failed")

    def _dpi_scale(self) -> float:
        """Return the Windows display DPI scale factor (1.0 at 96 DPI, 1.25 at 120 DPI, etc.)."""
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
            if dpi > 0:
                return max(1.0, min(2.5, dpi / 96.0))
        except Exception:
            pass
        return 1.0

    def _s(self, px: int) -> int:
        """Scale a logical 96-DPI pixel size to physical display pixels."""
        return int(round(px * self._dpi_scale()))

    def _get_virtual_screen_bounds(self) -> tuple[int, int, int, int, int, int]:
        """Return (vx, vy, vw, vh, primary_w, primary_h) across all Windows monitors."""
        try:
            screen_w = max(640, int(self.root.winfo_screenwidth()))
            screen_h = max(480, int(self.root.winfo_screenheight()))
        except Exception:
            screen_w, screen_h = 1920, 1080

        if sys.platform == "win32":
            try:
                u32 = ctypes.windll.user32
                vx = int(u32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
                vy = int(u32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
                vw = int(u32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
                vh = int(u32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
                pw = int(u32.GetSystemMetrics(0)) or screen_w  # SM_CXSCREEN
                ph = int(u32.GetSystemMetrics(1)) or screen_h  # SM_CYSCREEN
                if vw > 0 and vh > 0:
                    return vx, vy, vw, vh, pw, ph
            except Exception:
                pass

        return 0, 0, screen_w, screen_h, screen_w, screen_h

    def _get_hud_monitor_work_area(self) -> tuple[int, int, int, int]:
        """Return work area (left, top, right, bottom) of whichever monitor currently hosts the HUD."""
        try:
            sw = max(640, int(self.root.winfo_screenwidth()))
            sh = max(480, int(self.root.winfo_screenheight()))
        except Exception:
            sw, sh = 1920, 1080
        fallback = (0, 0, sw, max(400, sh - self._s(48)))

        anchor: tuple[int, int] | None = None
        if self._active_screen == "hud":
            try:
                if self.root.state() != "withdrawn":
                    anchor = (int(self.root.winfo_x()), int(self.root.winfo_y()))
            except Exception:
                pass
        if anchor is None and self._hud_saved_pos is not None:
            anchor = self._hud_saved_pos
        if anchor is None:
            return fallback
        return get_monitor_work_area(anchor[0] + 40, anchor[1] + 20, fallback=fallback)

    def _reset_window_state(
        self,
        win: tk.Tk | tk.Toplevel,
        *,
        topmost: bool = False,
        resizable: tuple[bool, bool] = (True, True),
        deiconify: bool = False,
    ) -> bool:
        """Reset maximized/fullscreen/topmost flags cleanly without flashing withdrawn test roots."""
        was_withdrawn = False
        try:
            current_state = win.state()
            was_withdrawn = current_state == "withdrawn"
        except Exception:
            current_state = "normal"

        try:
            win.attributes("-fullscreen", False)
        except Exception:
            pass

        try:
            if current_state == "zoomed" or (deiconify and current_state == "iconic"):
                win.state("normal")
        except Exception:
            pass

        try:
            win.attributes("-topmost", topmost)
        except Exception:
            pass

        try:
            win.resizable(resizable[0], resizable[1])
        except Exception:
            pass

        if deiconify and not was_withdrawn:
            try:
                win.deiconify()
                win.lift()
            except Exception:
                pass

        return was_withdrawn

    def _fit_window(
        self,
        win: tk.Tk | tk.Toplevel,
        min_w: int,
        min_h: int,
        *,
        pos: Literal["center", "upper_center", "top_right", "bottom_right"] = "center",
        explicit_pos: tuple[int, int] | None = None,
        monitor_rect: tuple[int, int, int, int] | None = None,
        pad_w: int = 8,
        pad_h: int = 8,
        allow_resize: bool = True,
    ) -> tuple[int, int]:
        """Compute DPI-aware, multi-monitor-aware geometry so buttons/labels never clip."""
        win.update_idletasks()
        screen_w = max(640, win.winfo_screenwidth())
        screen_h = max(480, win.winfo_screenheight())
        vx, vy, vw, vh, _pw, _ph = self._get_virtual_screen_bounds()

        req_w = win.winfo_reqwidth()
        req_h = win.winfo_reqheight()

        scaled_w = self._s(min_w)
        scaled_h = self._s(min_h)

        if monitor_rect is not None:
            m_left, m_top, m_right, m_bottom = monitor_rect
            avail_w = max(360, m_right - m_left - 24)
            avail_h = max(220, m_bottom - m_top - 24)
        else:
            m_left, m_top, m_right, m_bottom = 0, 0, screen_w, screen_h
            avail_w = screen_w - 40
            avail_h = screen_h - 80

        target_w = min(avail_w, max(scaled_w, req_w + pad_w))
        target_h = min(avail_h, max(scaled_h, req_h + pad_h))

        placed_explicit = False
        if explicit_pos is not None:
            ex, ey = int(explicit_pos[0]), int(explicit_pos[1])
            # Validate saved coordinates are still on an attached monitor
            visible_margin = 48
            if (
                ex + target_w >= vx + visible_margin
                and ex <= vx + vw - visible_margin
                and ey + target_h >= vy + 24
                and ey <= vy + vh - 24
            ):
                x = max(vx, min(max(vx, vx + vw - target_w), ex))
                y = max(vy, min(max(vy, vy + vh - target_h), ey))
                placed_explicit = True

        if not placed_explicit:
            margin = self._s(18)
            taskbar_pad = self._s(16) if monitor_rect is not None else self._s(68)
            mw = m_right - m_left
            mh = m_bottom - m_top
            if pos == "top_right":
                x = max(m_left + 12, m_right - target_w - margin)
                y = m_top + margin
            elif pos == "bottom_right":
                x = max(m_left + 12, m_right - target_w - margin)
                y = max(m_top + 12, m_bottom - target_h - taskbar_pad)
            elif pos == "upper_center":
                x = m_left + max(12, (mw - target_w) // 2)
                y = m_top + max(28, (mh - target_h) // 3)
            else:  # center
                x = m_left + max(12, (mw - target_w) // 2)
                y = m_top + max(24, (mh - target_h) // 2 - self._s(16))

        win.geometry(f"{target_w}x{target_h}+{x}+{y}")
        try:
            if allow_resize:
                win.minsize(min(target_w, max(self._s(320), req_w)), min(target_h, max(self._s(240), req_h)))
            else:
                win.minsize(target_w, target_h)
        except Exception:
            pass
        try:
            win.update_idletasks()
        except Exception:
            pass
        apply_dark_titlebar(win, dark=(self._theme == "dark"))
        self._last_fitted_size = (target_w, target_h)
        return target_w, target_h

    def drain_queue(self, max_items: int | None = None) -> None:
        """Execute pending queue callbacks on the current thread (optionally bounded per tick)."""
        processed = 0
        while not self._queue.empty():
            if max_items is not None and processed >= max_items:
                break
            try:
                callback = self._queue.get_nowait()
                processed += 1
                try:
                    callback()
                except Exception:
                    logger.exception("Error executing UI queue callback")
            except queue.Empty:
                break

    def _pump(self) -> None:
        """Drain background-thread callbacks safely on the Tkinter main thread."""
        self._pump_after_id = None
        if self._closed:
            return
        self.drain_queue(max_items=25)

        try:
            if not self._closed and self.root.winfo_exists():
                delay_ms = 10 if not self._queue.empty() else 50
                self._pump_after_id = self.root.after(delay_ms, self._pump)
        except Exception:
            pass

    def dispatch(self, callback: Callable[[], None]) -> None:
        """Enqueue a callback to execute on the main GUI thread."""
        self._queue.put(callback)

    def run(self) -> None:
        """Enter the Tkinter mainloop on the main thread."""
        self.root.mainloop()

    def _stop_tray(self) -> None:
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                logger.exception("Failed to stop tray manager")
            self._tray = None

    def minimize_to_tray(self) -> None:
        """Hide the HUD window and show a Windows system tray icon (with safe iconify fallback)."""

        def _action() -> None:
            try:
                if self._active_screen == "hud" and self.root.state() != "withdrawn":
                    self._hud_saved_pos = (self.root.winfo_x(), self.root.winfo_y())
            except Exception:
                pass

            on_toggle = self._hud_callbacks.get("on_toggle_pause")
            on_end = self._hud_callbacks.get("on_end_session")
            on_quit = self._hud_callbacks.get("on_quit")

            if self._tray is None:
                try:
                    self._tray = self._tray_factory(
                        on_restore=self.restore_from_tray,
                        on_toggle_pause=(lambda: self.dispatch(on_toggle)) if on_toggle else None,
                        on_end_session=(lambda: self.dispatch(on_end)) if on_end else None,
                        on_quit=(lambda: self.dispatch(on_quit)) if on_quit else self.close,
                    )
                except Exception:
                    logger.exception("Failed creating tray manager; falling back to iconify")
                    self._tray = None

            started = False
            if self._tray is not None:
                try:
                    goal_label = self._hud_last_goal or "Focus"
                    started = bool(
                        self._tray.start(
                            tooltip=f"Anti-Distractor — {goal_label}",
                            state="PAUSED" if self._hud_paused else self._hud_last_state,
                        )
                    )
                except Exception:
                    logger.exception("Failed starting tray icon; falling back to iconify")
                    started = False

            if started:
                try:
                    self.root.withdraw()
                except Exception:
                    pass
            else:
                try:
                    self.root.iconify()
                except Exception:
                    pass

        if threading.get_ident() == self._main_thread_id:
            _action()
        else:
            self.dispatch(_action)

    def restore_from_tray(self) -> None:
        """Restore the HUD window from the system tray back onto the screen."""

        def _action() -> None:
            self._stop_tray()
            try:
                self.root.deiconify()
                self.root.state("normal")
                self.root.lift()
                if self._active_screen == "hud":
                    self.root.attributes("-topmost", self._hud_pinned)
            except Exception:
                logger.exception("Failed restoring HUD from tray")

        self.dispatch(_action)

    def toggle_hud_visibility(self) -> None:
        """Toggle HUD between system tray and visible desktop state (Ctrl+Alt+H)."""

        def _action() -> None:
            try:
                st = self.root.state()
            except Exception:
                st = "normal"
            if self._tray is not None or st in ("withdrawn", "iconic"):
                self.restore_from_tray()
            elif self._active_screen == "hud":
                self.minimize_to_tray()
            else:
                try:
                    self.root.deiconify()
                    self.root.lift()
                except Exception:
                    pass

        self.dispatch(_action)

    def close(self) -> None:
        """Safely cancel timers, stop tray, destroy popups/dialogs, and tear down Tk on the main thread."""

        def _do_close() -> None:
            if self._closed:
                return
            self._closed = True
            self._cancel_hud_timer()
            if self._pump_after_id is not None:
                try:
                    self.root.after_cancel(self._pump_after_id)
                except Exception:
                    pass
                self._pump_after_id = None
            self._stop_tray()
            self._hide_popups_and_overlay_now()
            for attr in ("_rules_editor_win",):
                w = getattr(self, attr, None)
                if w is not None:
                    try:
                        if w.winfo_exists():
                            w.destroy()
                    except Exception:
                        pass
                    setattr(self, attr, None)
            for dlg_attr in ("_activity_drawer", "_history_dialog", "_settings_dialog"):
                dlg = getattr(self, dlg_attr, None)
                if dlg is not None:
                    try:
                        dlg.close()
                    except Exception:
                        pass
                    setattr(self, dlg_attr, None)
            try:
                self.root.quit()
            except Exception:
                pass
            try:
                if self.root.winfo_exists():
                    self.root.destroy()
            except Exception:
                pass

        if threading.get_ident() == self._main_thread_id:
            _do_close()
        else:
            self.dispatch(_do_close)

    def _cancel_hud_timer(self) -> None:
        if self._hud_timer_after_id is not None:
            try:
                self.root.after_cancel(self._hud_timer_after_id)
            except Exception:
                pass
            self._hud_timer_after_id = None
        self._hud_deadline = None

    def _clear_hud_tooltips(self) -> None:
        for tip in self._hud_tooltips:
            try:
                tip.hide_now()
            except Exception:
                pass
        self._hud_tooltips.clear()

    def _clear_root_widgets(self) -> None:
        """Remove all existing children from the root window and reset screen-specific refs."""
        self._cancel_hud_timer()
        self._clear_hud_tooltips()
        self._screen_gen += 1
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self._current_frame = None
        self._theme_btn = None
        self._onboarding_key_entry = None
        self._onboarding_show_btn = None
        self._onboarding_feedback_lbl = None
        self._onboarding_test_btn = None
        self._onboarding_save_btn = None
        self._onboarding_skip_btn = None
        self._setup_progress_bar = None
        self._setup_api_key_btn = None
        if self._setup_api_key_tooltip is not None:
            try:
                self._setup_api_key_tooltip.hide_now()
            except Exception:
                pass
            self._setup_api_key_tooltip = None
        self._hud_startup_bar = None
        self._review_text_widget = None
        self._review_sub_label = None
        self._review_copy_btn = None
        self._review_export_btn = None
        self._review_progress_bar = None
        self._review_kpi_holder = None
        self._review_vault_holder = None

    def show_onboarding(
        self,
        *,
        on_save_key: Callable[[str], tuple[bool, str]],
        on_test_key: Callable[[str, Callable[[bool, str], None]], None] | None = None,
        on_continue: Callable[[], None],
        on_skip: Callable[[], None],
        initial_key: str = "",
    ) -> None:
        """Render the first-launch Google Gemini API key onboarding welcome screen."""
        self.dispatch(
            lambda: self._render_onboarding_now(
                on_save_key=on_save_key,
                on_test_key=on_test_key,
                on_continue=on_continue,
                on_skip=on_skip,
                initial_key=initial_key,
            )
        )

    def _render_onboarding_now(
        self,
        *,
        on_save_key: Callable[[str], tuple[bool, str]],
        on_test_key: Callable[[str, Callable[[bool, str], None]], None] | None = None,
        on_continue: Callable[[], None],
        on_skip: Callable[[], None],
        initial_key: str = "",
    ) -> None:
        pal = self._active_palette()
        self._hide_popups_and_overlay_now()
        self._clear_root_widgets()
        self._active_screen = "onboarding"
        self._onboarding_callbacks = {
            "on_save_key": on_save_key,
            "on_test_key": on_test_key,
            "on_continue": on_continue,
            "on_skip": on_skip,
        }

        self.root.title("Anti-Distractor — Welcome")
        self.root.configure(bg=pal["bg"])
        self._reset_window_state(self.root, topmost=False, resizable=(True, True))

        shell = tk.Frame(self.root, bg=pal["bg"])
        shell.pack(fill="both", expand=True)
        outer = ctk.CTkFrame(shell, fg_color=pal["bg"], corner_radius=0)
        outer.pack(fill="both", expand=True)
        self._current_frame = shell

        content = ctk.CTkFrame(outer, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=26, pady=22)

        # Header block
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(fill="x")

        ModernLabel(
            title_row,
            text="Anti-Distractor",
            font=ui_font(19, "bold", display=True),
            text_color=pal["text"],
        ).pack(side="left")

        StatusPill(
            title_row,
            text="First-time setup",
            bg=pal["primary_soft"],
            fg=pal["primary"],
            border=pal["primary_border"],
            font=ui_font(10, "bold"),
            padx=8,
            pady=2,
        ).pack(side="left", padx=(10, 0), pady=(2, 0))

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        theme_btn = _make_button(
            title_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="ghost",
            font=ui_font(10, "bold"),
            padx=10,
            pady=4,
            palette=pal,
            height=28,
        )
        theme_btn.pack(side="right")
        self._theme_btn = theme_btn  # type: ignore[assignment]

        ModernLabel(
            header,
            text="Connect your Google Gemini API key once to enable AI focus profiles, smart distraction detection, and session reviews.",
            font=ui_font(11),
            text_color=pal["text_secondary"],
            wraplength=self._s(500),
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(8, 0))

        # Main card
        card = SurfaceCard(content, palette=pal, corner_radius=RADII["lg"], border_width=1)
        card.pack(fill="both", expand=True)

        card_in = ctk.CTkFrame(card, fg_color="transparent")
        card_in.pack(fill="both", expand=True, padx=20, pady=18)

        key_hdr = ctk.CTkFrame(card_in, fg_color="transparent")
        key_hdr.pack(fill="x", pady=(0, 8))

        ModernLabel(
            key_hdr,
            text="Google Gemini API key",
            font=ui_font(11, "bold"),
            text_color=pal["text_secondary"],
        ).pack(side="left")

        get_key_lbl = ModernLabel(
            key_hdr,
            text="Get a free key from Google AI Studio ↗",
            font=ui_font(10, "bold"),
            text_color=pal["primary"],
            cursor="hand2",
        )
        get_key_lbl.pack(side="right")
        get_key_lbl.bind("<Button-1>", lambda _e: self._url_opener("https://aistudio.google.com/apikey"))

        entry_row = ctk.CTkFrame(card_in, fg_color="transparent")
        entry_row.pack(fill="x", pady=(0, 8))

        key_var = tk.StringVar(value=initial_key)
        self._onboarding_key_var = key_var

        key_entry = ModernEntry(
            entry_row,
            palette=pal,
            textvariable=key_var,
            show="" if self._onboarding_show_key else "•",
            font=ui_font(12),
            height=38,
        )
        key_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._onboarding_key_entry = key_entry  # type: ignore[assignment]

        def _toggle_show() -> None:
            self._onboarding_show_key = not self._onboarding_show_key
            key_entry.configure(show="" if self._onboarding_show_key else "•")
            show_btn.configure(text="Hide" if self._onboarding_show_key else "Show")

        show_btn = _make_button(
            entry_row,
            text="Hide" if self._onboarding_show_key else "Show",
            command=_toggle_show,
            variant="secondary",
            font=ui_font(10, "bold"),
            padx=12,
            pady=6,
            palette=pal,
            height=38,
        )
        show_btn.pack(side="right")
        self._onboarding_show_btn = show_btn  # type: ignore[assignment]

        feedback_lbl = ModernLabel(
            card_in,
            text="Saved locally in .env on this device — never shared or uploaded.",
            font=ui_font(10),
            text_color=pal["text_muted"],
            anchor="w",
            wraplength=self._s(480),
            justify="left",
        )
        feedback_lbl.pack(fill="x", pady=(0, 16))
        self._onboarding_feedback_lbl = feedback_lbl  # type: ignore[assignment]

        def _set_feedback(msg: str, color_key: str) -> None:
            if feedback_lbl.winfo_exists():
                feedback_lbl.configure(text=msg, text_color=pal[color_key])

        def _handle_test() -> None:
            if on_test_key is None:
                return
            candidate = key_var.get().strip()
            if not candidate:
                _set_feedback("Paste your Gemini API key above before testing.", "warning")
                key_entry.focus_set()
                return
            test_btn.configure(state="disabled", text="Testing...")
            _set_feedback("Testing Gemini API connection...", "text_secondary")

            def _done(ok: bool, msg: str) -> None:
                if not test_btn.winfo_exists():
                    return
                test_btn.configure(state="normal", text="Test Key")
                _set_feedback(msg, "success" if ok else "danger")

            on_test_key(candidate, _done)

        def _handle_save_continue() -> None:
            raw = key_var.get().strip()
            ok, msg = on_save_key(raw)
            if ok:
                on_continue()
            else:
                _set_feedback(msg or "Please enter a valid Google Gemini API key.", "danger")
                key_entry.focus_set()

        btn_row = ctk.CTkFrame(card_in, fg_color="transparent")
        btn_row.pack(fill="x")

        test_btn = _make_button(
            btn_row,
            text="Test Key",
            command=_handle_test,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=8,
            palette=pal,
            height=36,
        )
        if on_test_key is not None:
            test_btn.pack(side="left")
        self._onboarding_test_btn = test_btn  # type: ignore[assignment]

        save_btn = _make_button(
            btn_row,
            text="Save & Continue",
            command=_handle_save_continue,
            variant="primary",
            font=ui_font(11, "bold"),
            padx=18,
            pady=8,
            palette=pal,
            height=36,
        )
        save_btn.pack(side="right")
        self._onboarding_save_btn = save_btn  # type: ignore[assignment]

        # Footer skip option
        footer = ctk.CTkFrame(content, fg_color="transparent")
        footer.pack(fill="x", pady=(14, 0))

        ModernLabel(
            footer,
            text="You can add or change your API key anytime in Settings.",
            font=ui_font(10),
            text_color=pal["text_muted"],
        ).pack(side="left")

        skip_btn = _make_button(
            footer,
            text="Skip — Use Local Rules Only",
            command=on_skip,
            variant="ghost",
            font=ui_font(10, "bold"),
            padx=10,
            pady=4,
            palette=pal,
            height=28,
        )
        skip_btn.pack(side="right")
        self._onboarding_skip_btn = skip_btn  # type: ignore[assignment]

        key_entry.bind("<Return>", lambda _e: _handle_save_continue())
        key_entry.focus_set()

        self._fit_window(self.root, min_w=560, min_h=330, pos="center", pad_w=14, pad_h=14, allow_resize=True)

    def show_setup(
        self,
        on_start: Callable[[str, int, ModeType], None],
        initial_goal: str | None = None,
        initial_duration: int | None = None,
        initial_mode: ModeType | None = None,
        on_edit_rules: Callable[[str], None] | None = None,
        *,
        presets: list[FocusPreset] | None = None,
        on_select_preset: Callable[[FocusPreset], None] | None = None,
        on_save_preset: Callable[[str, str, int, ModeType], list[FocusPreset]] | None = None,
        on_delete_preset: Callable[[str], list[FocusPreset]] | None = None,
        initial_pomodoro: bool | None = None,
        pomodoro_cycles: int = 4,
        on_pomodoro_toggled: Callable[[bool], None] | None = None,
        on_open_history: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        api_key_status: tuple[bool, str] | None = None,
        on_open_api_key: Callable[[], None] | None = None,
    ) -> None:
        """Render the goal/duration/mode setup screen with presets, Pomodoro toggle, History, and Settings."""
        extra = {
            "presets": presets,
            "on_select_preset": on_select_preset,
            "on_save_preset": on_save_preset,
            "on_delete_preset": on_delete_preset,
            "initial_pomodoro": initial_pomodoro,
            "pomodoro_cycles": pomodoro_cycles,
            "on_pomodoro_toggled": on_pomodoro_toggled,
            "on_open_history": on_open_history,
            "on_open_settings": on_open_settings,
            "api_key_status": api_key_status,
            "on_open_api_key": on_open_api_key,
        }
        self.dispatch(
            lambda: self._render_setup_now(
                on_start,
                initial_goal=initial_goal,
                initial_duration=initial_duration,
                initial_mode=initial_mode,
                on_edit_rules=on_edit_rules,
                **extra,
            )
        )

    def _render_setup_now(
        self,
        on_start: Callable[[str, int, ModeType], None],
        initial_goal: str | None = None,
        initial_duration: int | None = None,
        initial_mode: ModeType | None = None,
        on_edit_rules: Callable[[str], None] | None = None,
        *,
        presets: list[FocusPreset] | None = None,
        on_select_preset: Callable[[FocusPreset], None] | None = None,
        on_save_preset: Callable[[str, str, int, ModeType], list[FocusPreset]] | None = None,
        on_delete_preset: Callable[[str], list[FocusPreset]] | None = None,
        initial_pomodoro: bool | None = None,
        pomodoro_cycles: int = 4,
        on_pomodoro_toggled: Callable[[bool], None] | None = None,
        on_open_history: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        api_key_status: tuple[bool, str] | None = None,
        on_open_api_key: Callable[[], None] | None = None,
    ) -> None:
        pal = self._active_palette()
        self._hide_popups_and_overlay_now()
        self._clear_root_widgets()
        self._active_screen = "setup"
        self._setup_on_start = on_start
        self._setup_on_edit_rules = on_edit_rules
        self._setup_extra_kwargs = {
            "presets": presets,
            "on_select_preset": on_select_preset,
            "on_save_preset": on_save_preset,
            "on_delete_preset": on_delete_preset,
            "initial_pomodoro": initial_pomodoro,
            "pomodoro_cycles": pomodoro_cycles,
            "on_pomodoro_toggled": on_pomodoro_toggled,
            "on_open_history": on_open_history,
            "on_open_settings": on_open_settings,
            "api_key_status": api_key_status,
            "on_open_api_key": on_open_api_key,
        }

        self.root.title("Anti-Distractor — Session Setup")
        self.root.configure(bg=pal["bg"])
        self._reset_window_state(self.root, topmost=False, resizable=(True, True))

        shell = tk.Frame(self.root, bg=pal["bg"])
        shell.pack(fill="both", expand=True)
        outer = ctk.CTkFrame(shell, fg_color=pal["bg"], corner_radius=0)
        outer.pack(fill="both", expand=True)
        self._current_frame = shell

        content = ctk.CTkFrame(outer, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=20)

        # Header block
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x", pady=(0, 14))

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(fill="x")

        ModernLabel(
            title_row,
            text="Anti-Distractor",
            font=ui_font(19, "bold", display=True),
            text_color=pal["text"],
        ).pack(side="left")

        StatusPill(
            title_row,
            text="Focus Agent",
            bg=pal["primary_soft"],
            fg=pal["primary"],
            border=pal["primary_border"],
            font=ui_font(10, "bold"),
            padx=8,
            pady=2,
        ).pack(side="left", padx=(10, 0), pady=(2, 0))

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        theme_btn = _make_button(
            title_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="ghost",
            font=ui_font(10, "bold"),
            padx=9,
            pady=3,
            palette=pal,
            height=28,
        )
        theme_btn.pack(side="right")
        self._theme_btn = theme_btn  # type: ignore[assignment]

        if on_open_settings is not None:
            settings_btn = _make_button(
                title_row,
                text="Settings",
                command=on_open_settings,
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=10,
                pady=3,
                palette=pal,
                height=28,
            )
            settings_btn.pack(side="right", padx=(0, 6))
            self._setup_settings_btn = settings_btn
        else:
            self._setup_settings_btn = None

        if on_open_api_key is not None:
            configured, status_str = api_key_status or (False, "Not configured")
            key_btn = _make_button(
                title_row,
                text="Key ✓" if configured else "Set API Key",
                command=on_open_api_key,
                variant="success_outline" if configured else "warning_outline",
                font=ui_font(10, "bold"),
                padx=10,
                pady=3,
                palette=pal,
                height=28,
            )
            key_btn.pack(side="right", padx=(0, 6))
            self._setup_api_key_btn = key_btn  # type: ignore[assignment]
            tip_text = (
                f"Gemini API Key: {status_str} — Click to view or change"
                if configured
                else "No Gemini API key configured — Click to add your key"
            )
            self._setup_api_key_tooltip = ToolTip(key_btn, tip_text)
        else:
            self._setup_api_key_btn = None
            self._setup_api_key_tooltip = None

        if on_open_history is not None:
            history_btn = _make_button(
                title_row,
                text="History",
                command=on_open_history,
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=10,
                pady=3,
                palette=pal,
                height=28,
            )
            history_btn.pack(side="right", padx=(0, 6))
            self._setup_history_btn = history_btn
        else:
            self._setup_history_btn = None

        ModernLabel(
            header,
            text="Define your focus goal and enforcement style. Press Enter or click Start when ready.",
            font=ui_font(11),
            text_color=pal["text_secondary"],
            wraplength=self._s(510),
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(6, 0))

        # Main card
        card = SurfaceCard(content, palette=pal, corner_radius=RADII["lg"], border_width=1)
        card.pack(fill="both", expand=True)

        card_in = ctk.CTkFrame(card, fg_color="transparent")
        card_in.pack(fill="both", expand=True, padx=20, pady=18)

        # Goal input
        ModernLabel(
            card_in,
            text="What are you working on?",
            font=ui_font(11, "bold"),
            text_color=pal["text_secondary"],
        ).pack(anchor="w", pady=(0, 6))

        goal_val_default = initial_goal if initial_goal is not None else "Study Data Structures and Algorithms"
        goal_var = tk.StringVar(value=goal_val_default)
        self._setup_goal_var = goal_var
        goal_entry = ModernEntry(
            card_in,
            palette=pal,
            textvariable=goal_var,
            font=ui_font(12),
            height=38,
        )
        goal_entry.pack(fill="x", pady=(0, 10 if presets is not None else 14))

        dur_val_default = str(initial_duration) if initial_duration is not None else "25"
        dur_var = tk.StringVar(value=dur_val_default)
        self._setup_dur_var = dur_var

        mode_val_default = initial_mode if initial_mode in ("strict", "normal") else "strict"
        mode_var = tk.StringVar(value=mode_val_default)
        self._setup_mode_var = mode_var

        # Optional Saved Focus Presets strip
        self._setup_preset_btns: list[Any] = []
        if presets is not None:
            presets_row = ctk.CTkFrame(card_in, fg_color="transparent")
            presets_row.pack(fill="x", pady=(0, 14))

            def _render_preset_pills(current_presets: list[FocusPreset]) -> None:
                self._setup_extra_kwargs["presets"] = current_presets
                for child in presets_row.winfo_children():
                    child.destroy()
                self._setup_preset_btns.clear()

                ModernLabel(
                    presets_row,
                    text="Presets",
                    font=ui_font(10, "bold"),
                    text_color=pal["text_muted"],
                ).pack(side="left", padx=(0, 8))

                for p in current_presets[:5]:
                    def _pick_preset(preset: FocusPreset = p) -> None:
                        goal_var.set(preset.goal)
                        dur_var.set(str(preset.duration_minutes))
                        mode_var.set(preset.mode)
                        if on_select_preset is not None:
                            on_select_preset(preset)
                        _set_status(f"Loaded preset '{preset.name}' (instant start — rules ready).")

                    pill = _make_button(
                        presets_row,
                        text=p.name,
                        command=_pick_preset,
                        variant="chip",
                        font=ui_font(10, "bold"),
                        padx=10,
                        pady=3,
                        corner_radius=RADII["pill"],
                        height=26,
                        palette=pal,
                    )
                    if on_delete_preset is not None:
                        pill.bind(
                            "<Button-3>",
                            lambda _e, name=p.name: _render_preset_pills(on_delete_preset(name)),
                        )
                        ToolTip(pill, f"{p.goal} ({p.duration_minutes}m · {p.mode}) — Right-click to delete")
                    pill.pack(side="left", padx=(0, 6))
                    self._setup_preset_btns.append(pill)

                if on_save_preset is not None:
                    def _prompt_save_preset() -> None:
                        g_txt = goal_var.get().strip()
                        if not g_txt:
                            _set_status("Enter a goal first before saving a preset.", is_error=True)
                            return
                        name = simpledialog.askstring(
                            "Save Focus Preset",
                            "Preset name (e.g. 'System Design'):",
                            parent=self.root,
                        )
                        if not name or not name.strip():
                            return
                        try:
                            d_int = max(1, min(720, int(dur_var.get().strip())))
                        except Exception:
                            d_int = 25
                        m_sel: ModeType = "normal" if mode_var.get() == "normal" else "strict"
                        updated = on_save_preset(name.strip(), g_txt, d_int, m_sel)
                        _render_preset_pills(updated)
                        _set_status(f"Saved preset '{name.strip()}'.")

                    save_p_btn = _make_button(
                        presets_row,
                        text="+ Save",
                        command=_prompt_save_preset,
                        variant="ghost",
                        font=ui_font(10, "bold"),
                        padx=8,
                        pady=3,
                        height=26,
                        palette=pal,
                    )
                    save_p_btn.pack(side="right")
                    self._setup_save_preset_btn = save_p_btn

            _render_preset_pills(list(presets))

        # Duration row + quick preset chips + optional Pomodoro toggle
        ModernLabel(
            card_in,
            text="Session duration (minutes)",
            font=ui_font(11, "bold"),
            text_color=pal["text_secondary"],
        ).pack(anchor="w", pady=(0, 6))

        dur_row = ctk.CTkFrame(card_in, fg_color="transparent")
        dur_row.pack(fill="x", pady=(0, 14))

        dur_entry = ModernEntry(
            dur_row,
            palette=pal,
            textvariable=dur_var,
            width=72,
            height=34,
            font=mono_font(12, "bold"),
            justify="center",
        )
        dur_entry.pack(side="left")

        preset_btns: dict[int, ModernButton] = {}

        def _refresh_preset_styles(*_args: object) -> None:
            cur = dur_var.get().strip()
            for mins, pbtn in preset_btns.items():
                active = cur == str(mins)
                if active:
                    pbtn.configure(
                        fg_color=pal["primary_soft"],
                        hover_color=pal["primary_soft"],
                        text_color=pal["primary"],
                        border_color=pal["primary"],
                        border_width=1,
                    )
                else:
                    pbtn.configure(
                        fg_color=pal["surface_alt"],
                        hover_color=pal["surface_hover"],
                        text_color=pal["text_secondary"],
                        border_color=pal["border_strong"],
                        border_width=1,
                    )

        def _select_preset(mins: int) -> None:
            dur_var.set(str(mins))
            _refresh_preset_styles()

        for mins in (15, 25, 45, 60):
            pbtn = _make_button(
                dur_row,
                text=f"{mins}m",
                command=lambda m=mins: _select_preset(m),
                variant="chip",
                font=ui_font(11, "bold"),
                padx=12,
                pady=4,
                height=34,
                corner_radius=RADII["md"],
                palette=pal,
            )
            pbtn.pack(side="left", padx=(8, 0))
            preset_btns[mins] = pbtn

        dur_var.trace_add("write", _refresh_preset_styles)
        _refresh_preset_styles()

        pomo_var = tk.BooleanVar(value=bool(initial_pomodoro) if initial_pomodoro is not None else False)
        self._setup_pomodoro_var = pomo_var
        if initial_pomodoro is not None or on_pomodoro_toggled is not None:
            def _on_pomo_toggle() -> None:
                if on_pomodoro_toggled is not None:
                    try:
                        on_pomodoro_toggled(bool(pomo_var.get()))
                    except Exception:
                        pass

            pomo_chk = ctk.CTkSwitch(
                dur_row,
                text=f"Pomodoro ({max(2, int(pomodoro_cycles))}×)",
                variable=pomo_var,
                command=_on_pomo_toggle,
                font=ui_font(10, "bold"),
                text_color=pal["text_secondary"],
                fg_color=pal["border_strong"],
                progress_color=pal["primary"],
                button_color="#FFFFFF",
                button_hover_color="#E4E4E7",
                switch_width=34,
                switch_height=18,
            )
            pomo_chk.pack(side="right")
            ToolTip(
                pomo_chk,
                f"Auto-cycle {max(2, int(pomodoro_cycles))} work intervals with {self._break_minutes}m breaks",
            )
            self._setup_pomodoro_chk = pomo_chk
        else:
            self._setup_pomodoro_chk = None

        # Enforcement Mode selectable cards (two clean side-by-side cards)
        ModernLabel(
            card_in,
            text="Enforcement mode",
            font=ui_font(11, "bold"),
            text_color=pal["text_secondary"],
        ).pack(anchor="w", pady=(0, 8))

        mode_grid = ctk.CTkFrame(card_in, fg_color="transparent")
        mode_grid.pack(fill="x", pady=(0, 14))
        mode_grid.columnconfigure(0, weight=1, uniform="mode")
        mode_grid.columnconfigure(1, weight=1, uniform="mode")

        mode_cards: dict[str, tuple[SurfaceCard, ModernLabel, ModernLabel, ModernLabel]] = {}

        def _refresh_mode_cards(*_args: object) -> None:
            sel = mode_var.get()
            for m_val, (m_frame, dot_lbl, title_lbl, desc_lbl) in mode_cards.items():
                is_sel = m_val == sel
                if is_sel:
                    bg = pal["danger_soft"] if m_val == "strict" else pal["primary_soft"]
                    bd = pal["danger"] if m_val == "strict" else pal["primary"]
                    dot_col = bd
                else:
                    bg = pal["surface_alt"]
                    bd = pal["border"]
                    dot_col = pal["text_muted"]
                m_frame.configure(fg_color=bg, border_color=bd, border_width=2 if is_sel else 1)
                dot_lbl.configure(text="◉" if is_sel else "○", text_color=dot_col)
                title_lbl.configure(text_color=pal["text"])
                desc_lbl.configure(text_color=pal["text_secondary"])

        def _build_mode_card(
            col: int,
            value: ModeType,
            title: str,
            badge: str,
            badge_bg: str,
            badge_fg: str,
            badge_bd: str,
            desc: str,
        ) -> SurfaceCard:
            m_card = SurfaceCard(
                mode_grid,
                palette=pal,
                fg_color=pal["surface_alt"],
                border_color=pal["border"],
                corner_radius=10,
                border_width=1,
            )
            m_card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            m_card.configure(cursor="hand2")

            inner = ctk.CTkFrame(m_card, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=12, pady=10)

            top = ctk.CTkFrame(inner, fg_color="transparent")
            top.pack(fill="x")

            dot_lbl = ModernLabel(
                top,
                text="○",
                font=ui_font(13, "bold"),
                text_color=pal["text_muted"],
                width=16,
            )
            dot_lbl.pack(side="left", padx=(0, 6))

            title_lbl = ModernLabel(
                top,
                text=title,
                font=ui_font(12, "bold"),
                text_color=pal["text"],
            )
            title_lbl.pack(side="left")

            tag = StatusPill(
                top,
                text=badge,
                bg=badge_bg,
                fg=badge_fg,
                border=badge_bd,
                font=ui_font(9, "bold"),
                padx=7,
                pady=1,
            )
            tag.pack(side="right")

            desc_lbl = ModernLabel(
                inner,
                text=desc,
                font=ui_font(10),
                text_color=pal["text_secondary"],
                wraplength=self._s(210),
                justify="left",
                anchor="w",
            )
            desc_lbl.pack(anchor="w", pady=(6, 0))

            def _pick(_e: tk.Event) -> None:
                mode_var.set(value)

            def _bind_recursive(widget: tk.Misc) -> None:
                try:
                    widget.bind("<Button-1>", _pick, add="+")
                except Exception:
                    pass
                for ch in widget.winfo_children():
                    _bind_recursive(ch)

            _bind_recursive(m_card)
            mode_cards[value] = (m_card, dot_lbl, title_lbl, desc_lbl)
            return m_card

        _build_mode_card(
            0,
            "strict",
            "Strict",
            "Auto-enforce",
            pal["danger_soft"],
            pal["danger"],
            pal["danger_border"],
            "Closes off-task tabs, minimizes distracting apps, and locks briefly.",
        )
        _build_mode_card(
            1,
            "normal",
            "Normal",
            "Gentle nudges",
            pal["primary_soft"],
            pal["primary"],
            pal["primary_border"],
            "Shows non-intrusive check-in popups and warnings without closing tabs.",
        )

        mode_var.trace_add("write", _refresh_mode_cards)
        _refresh_mode_cards()

        # Footer inside card: inline validation status + optional Edit Rules + Start button
        status_row = ctk.CTkFrame(card_in, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 10))

        status_lbl = ModernLabel(
            status_row,
            text="Ready to begin your focus session.",
            font=ui_font(11),
            text_color=pal["text_muted"],
            anchor="w",
            wraplength=self._s(350 if on_edit_rules is not None else 450),
            justify="left",
        )
        status_lbl.pack(side="left", fill="x", expand=True)
        self._setup_status_lbl = status_lbl  # type: ignore[assignment]

        if on_edit_rules is not None:
            setup_rules_btn = _make_button(
                status_row,
                text="Edit Rules",
                command=lambda: on_edit_rules(goal_var.get().strip()),
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=12,
                pady=4,
                height=28,
                palette=pal,
            )
            setup_rules_btn.pack(side="right", padx=(8, 0))
            self._setup_rules_btn = setup_rules_btn
        else:
            self._setup_rules_btn = None

        self._setup_progress_bar = ModernProgressBar(  # type: ignore[assignment]
            card_in,
            palette=pal,
            height=5,
            mode="indeterminate",
        )

        def _set_status(msg: str, is_error: bool = False) -> None:
            status_lbl.configure(
                text=msg,
                text_color=pal["danger"] if is_error else pal["text_muted"],
                font=ui_font(11, "bold") if is_error else ui_font(11),
            )

        def _handle_start() -> None:
            goal_val = goal_var.get().strip()
            if not goal_val:
                _set_status("Please enter a focus goal before starting.", is_error=True)
                goal_entry.focus_set()
                return

            try:
                dur_val = int(dur_var.get().strip())
                if dur_val <= 0 or dur_val > 720:
                    raise ValueError()
            except ValueError:
                _set_status("Duration must be a whole number between 1 and 720 minutes.", is_error=True)
                dur_entry.focus_set()
                return

            selected_mode: ModeType = "strict" if mode_var.get() == "strict" else "normal"
            start_btn.configure(state="disabled", text="Starting...", fg_color=pal["primary_hover"])
            _set_status("Generating focus profile...")
            try:
                if self._setup_progress_bar is not None:
                    self._setup_progress_bar.pack(fill="x", pady=(0, 10), before=start_btn)
                    self._setup_progress_bar.start()
            except Exception:
                pass
            self.root.update_idletasks()
            try:
                on_start(goal_val, dur_val, selected_mode)
            except Exception as err:
                try:
                    if self._setup_progress_bar is not None:
                        self._setup_progress_bar.stop()
                        self._setup_progress_bar.pack_forget()
                except Exception:
                    pass
                _set_button_variant(start_btn, "primary", text="I'm Ready — Start Session", palette=pal)
                start_btn.configure(state="normal")
                _set_status(f"Failed to start: {err}", is_error=True)
                messagebox.showerror("Start Failed", f"Could not start session: {err}")

        start_btn = _make_button(
            card_in,
            text="I'm Ready — Start Session",
            command=_handle_start,
            variant="primary",
            font=ui_font(13, "bold"),
            padx=16,
            pady=10,
            height=42,
            palette=pal,
        )
        self._start_btn = start_btn  # type: ignore[assignment]
        self._setup_handle_start = _handle_start
        start_btn.pack(fill="x")

        goal_entry.bind("<Return>", lambda _e: _handle_start())
        dur_entry.bind("<Return>", lambda _e: _handle_start())
        goal_entry.focus_set()

        self._fit_window(self.root, min_w=560, min_h=480, pos="center", pad_w=12, pad_h=12, allow_resize=True)

    def update_setup_progress(self, stage_text: str, running: bool = True) -> None:
        """Update staged startup progress text and indeterminate bar on Setup or HUD."""

        def _action() -> None:
            if self._active_screen == "setup" and hasattr(self, "_setup_status_lbl"):
                if self._setup_status_lbl is not None and self._setup_status_lbl.winfo_exists():
                    self._setup_status_lbl.configure(text=stage_text, text_color=self._p("text_secondary"))
                if self._setup_progress_bar is not None and self._setup_progress_bar.winfo_exists():
                    if running:
                        if not self._setup_progress_bar.winfo_ismapped():
                            self._setup_progress_bar.pack(fill="x", pady=(0, 10))
                        self._setup_progress_bar.start()
                    else:
                        self._setup_progress_bar.stop()
                        self._setup_progress_bar.pack_forget()
            elif self._active_screen == "hud":
                self._hud_last_summary = stage_text
                if self._hud_summary_label is not None and self._hud_summary_label.winfo_exists():
                    self._hud_summary_label.configure(text=stage_text, text_color=self._p("text_secondary"))
                if self._hud_startup_bar is not None and self._hud_startup_bar.winfo_exists():
                    if running:
                        if not self._hud_startup_bar.winfo_ismapped():
                            self._hud_startup_bar.pack(fill="x", padx=14, pady=(2, 4))
                        self._hud_startup_bar.start()
                    else:
                        self._hud_startup_bar.stop()
                        self._hud_startup_bar.pack_forget()

        self.dispatch(_action)

    def show_monitoring_hud(
        self,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
        duration_minutes: int | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_open_rules: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        *,
        on_open_activity: Callable[[], None] | None = None,
        initial_hud_pos: tuple[int, int] | None = None,
    ) -> None:
        """Render the compact, non-maximizable floating monitoring HUD."""
        if initial_hud_pos is not None:
            self._hud_saved_pos = (int(initial_hud_pos[0]), int(initial_hud_pos[1]))
        self.dispatch(
            lambda: self._render_hud_now(
                goal=goal,
                mode=mode,
                on_end_session=on_end_session,
                duration_minutes=duration_minutes,
                on_toggle_pause=on_toggle_pause,
                on_open_rules=on_open_rules,
                on_open_activity=on_open_activity,
                on_quit=on_quit,
                preserve_pos=self._hud_saved_pos,
            )
        )

    def set_mini_mode(self, mini: bool) -> None:
        """Switch HUD between compact Mini-Pill and full telemetry view."""

        def _action() -> None:
            if self._hud_mini_mode == bool(mini):
                return
            self._hud_mini_mode = bool(mini)
            if self._active_screen == "hud" and "on_end_session" in self._hud_callbacks:
                prev_pos = self._hud_saved_pos
                try:
                    if self.root.state() != "withdrawn":
                        prev_pos = (self.root.winfo_x(), self.root.winfo_y())
                except Exception:
                    pass
                self._render_hud_now(
                    goal=self._hud_last_goal,
                    mode=self._hud_last_mode,
                    on_end_session=self._hud_callbacks["on_end_session"],
                    duration_minutes=None,
                    on_toggle_pause=self._hud_callbacks.get("on_toggle_pause"),
                    on_open_rules=self._hud_callbacks.get("on_open_rules"),
                    on_open_activity=self._hud_callbacks.get("on_open_activity"),
                    on_quit=self._hud_callbacks.get("on_quit"),
                    preserve_state=True,
                    preserve_pos=prev_pos,
                )

        if threading.get_ident() == self._main_thread_id:
            _action()
        else:
            self.dispatch(_action)

    def toggle_mini_mode(self) -> None:
        """Toggle HUD Mini-Pill mode."""
        self.set_mini_mode(not self._hud_mini_mode)

    def _format_state_badge_text(self) -> str:
        if self._hud_paused:
            return " PAUSED "
        if (
            self._hud_last_state == "GRACE_PERIOD"
            and self._hud_grace_remaining is not None
            and self._hud_grace_remaining > 0
        ):
            secs = max(1, int(math.ceil(self._hud_grace_remaining)))
            return f" Grace: {secs}s "
        return f" {self._hud_last_state} "

    def _render_hud_now(
        self,
        *,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
        duration_minutes: int | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_open_rules: Callable[[], None] | None = None,
        on_open_activity: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        preserve_state: bool = False,
        preserve_pos: tuple[int, int] | None = None,
    ) -> None:
        pal = self._active_palette()
        saved_deadline = self._hud_deadline if preserve_state else None
        self._clear_root_widgets()
        self._active_screen = "hud"

        self._hud_last_goal = goal
        self._hud_last_mode = mode
        self._hud_callbacks = {
            "on_end_session": on_end_session,
            "on_toggle_pause": on_toggle_pause,
            "on_open_rules": on_open_rules,
            "on_open_activity": on_open_activity,
            "on_quit": on_quit,
        }
        if not preserve_state:
            self._hud_pinned = True
            self._hud_paused = False
            self._hud_pause_remaining = 0.0
            self._hud_grace_remaining = None
            self._hud_session_remaining_on_pause = None
            self._hud_last_state = "ON_TASK"
            self._hud_last_summary = "Monitoring active."
            self._hud_pause_summary = "Break active — monitoring paused."
            self._hud_t1_count = 0
            self._hud_t2_count = 0

        if saved_deadline is not None:
            self._hud_deadline = saved_deadline
        elif duration_minutes is not None and duration_minutes > 0:
            self._hud_deadline = time.monotonic() + duration_minutes * 60.0

        self.root.title("Anti-Distractor — HUD")
        self.root.configure(bg=pal["bg"])

        self._reset_window_state(
            self.root,
            topmost=self._hud_pinned,
            resizable=(False, False),
        )

        shell = tk.Frame(self.root, bg=pal["bg"])
        shell.pack(fill="both", expand=True)
        frame = SurfaceCard(
            shell,
            palette=pal,
            fg_color=pal["surface"],
            border_color=pal["border_strong"],
            corner_radius=0,
            border_width=1,
        )
        frame.pack(fill="both", expand=True)
        self._current_frame = shell

        # Multi-monitor smooth dragging + persisted position callback
        self._drag_data: dict[str, int | None] = {
            "x": 0,
            "y": 0,
            "win_x": 0,
            "win_y": 0,
            "use_root": 0,
            "moved": 0,
            "last_x": None,
            "last_y": None,
        }

        def _start_drag(event: tk.Event) -> None:
            self._drag_data["moved"] = 0
            x_root = getattr(event, "x_root", None)
            y_root = getattr(event, "y_root", None)
            if isinstance(x_root, int) and isinstance(y_root, int) and (x_root != 0 or y_root != 0):
                self._drag_data["use_root"] = 1
                self._drag_data["x"] = x_root
                self._drag_data["y"] = y_root
                self._drag_data["win_x"] = self.root.winfo_x()
                self._drag_data["win_y"] = self.root.winfo_y()
            else:
                self._drag_data["use_root"] = 0
                self._drag_data["x"] = getattr(event, "x", 0) or 0
                self._drag_data["y"] = getattr(event, "y", 0) or 0

        def _on_drag(event: tk.Event) -> None:
            vx, vy, vw, vh, _pw, _ph = self._get_virtual_screen_bounds()
            win_w = max(100, self.root.winfo_width())
            win_h = max(36, self.root.winfo_height())

            if self._drag_data.get("use_root"):
                dx = (getattr(event, "x_root", 0) or 0) - (self._drag_data["x"] or 0)
                dy = (getattr(event, "y_root", 0) or 0) - (self._drag_data["y"] or 0)
                new_x = (self._drag_data["win_x"] or 0) + dx
                new_y = (self._drag_data["win_y"] or 0) + dy
            else:
                new_x = self.root.winfo_x() + ((getattr(event, "x", 0) or 0) - (self._drag_data["x"] or 0))
                new_y = self.root.winfo_y() + ((getattr(event, "y", 0) or 0) - (self._drag_data["y"] or 0))

            clamped_x = max(vx, min(max(vx, vx + vw - win_w), new_x))
            clamped_y = max(vy, min(max(vy, vy + vh - win_h), new_y))
            self.root.geometry(f"+{clamped_x}+{clamped_y}")
            self._hud_saved_pos = (clamped_x, clamped_y)
            self._drag_data["moved"] = 1
            self._drag_data["last_x"] = clamped_x
            self._drag_data["last_y"] = clamped_y

        def _end_drag(_event: tk.Event | None = None) -> None:
            if self._drag_data.get("moved") and self._drag_data.get("last_x") is not None:
                lx = int(self._drag_data["last_x"] or 0)
                ly = int(self._drag_data["last_y"] or 0)
                self._drag_data["moved"] = 0
                if self.on_hud_moved is not None:
                    try:
                        self.on_hud_moved(lx, ly)
                    except Exception:
                        logger.exception("on_hud_moved callback failed")

        self._hud_start_drag = _start_drag
        self._hud_on_drag = _on_drag
        self._hud_end_drag = _end_drag

        pill_bg, pill_fg, pill_bd, pill_dot = get_state_pill_style(
            self._theme,
            self._hud_last_state,
            paused=self._hud_paused,
        )
        pill_text = self._format_state_badge_text().strip()

        # Compact single-row Mini-Pill Mode for small laptop screens
        if self._hud_mini_mode:
            self._hud_goal_label = None
            self._hud_mode_label = None
            self._hud_summary_label = None
            self._hud_ai_calls_label = None
            self._hud_ext_dot = None
            self._hud_ext_label = None
            self._hud_pin_btn = None
            self._hud_rules_btn = None
            self._hud_activity_btn = None
            self._hud_tray_btn = None
            self._hud_startup_bar = None

            pill_row = ctk.CTkFrame(frame, fg_color="transparent")
            pill_row.pack(fill="both", expand=True, padx=12, pady=8)

            grip = ModernLabel(
                pill_row,
                text="⋮⋮",
                font=ui_font(11, "bold"),
                text_color=pal["border_strong"],
                cursor="fleur",
            )
            grip.pack(side="left", padx=(0, 8))

            self._hud_state_label = StatusPill(  # type: ignore[assignment]
                pill_row,
                text=pill_text,
                bg=pill_bg,
                fg=pill_fg,
                border=pill_bd,
                dot_color=pill_dot,
                font=ui_font(10, "bold"),
                padx=8,
                pady=2,
                cursor="fleur",
            )
            self._hud_state_label.pack(side="left")

            self._hud_timer_label = ModernLabel(  # type: ignore[assignment]
                pill_row,
                text="--:--",
                font=mono_font(11, "bold"),
                text_color=pal["warning"] if self._hud_paused else pal["text"],
                cursor="fleur",
            )
            self._hud_timer_label.pack(side="left", padx=10)
            if self._hud_paused:
                p_mins, p_secs = divmod(max(0, int(math.ceil(self._hud_pause_remaining))), 60)
                self._hud_timer_label.configure(text=f"{p_mins:02d}:{p_secs:02d}")
            elif self._hud_deadline is not None:
                self._tick_hud_timer()

            self._hud_mini_btn = _make_button(  # type: ignore[assignment]
                pill_row,
                text="Expand",
                command=self.toggle_mini_mode,
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=10,
                pady=3,
                height=26,
                palette=pal,
            )
            self._hud_mini_btn.pack(side="right", padx=(6, 0))
            self._hud_tooltips.append(ToolTip(self._hud_mini_btn, "Expand full HUD"))

            if on_toggle_pause is not None:
                self._hud_pause_btn = _make_button(  # type: ignore[assignment]
                    pill_row,
                    text="Resume" if self._hud_paused else "Pause",
                    command=on_toggle_pause,
                    variant="warning_outline" if self._hud_paused else "secondary",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=3,
                    height=26,
                    palette=pal,
                )
                self._hud_pause_btn.pack(side="right")
                self._hud_tooltips.append(ToolTip(self._hud_pause_btn, "Pause / Resume (Ctrl+Alt+P)"))
            else:
                self._hud_pause_btn = None

            self._hud_tooltips.append(ToolTip(self._hud_state_label, lambda: f"{goal} — {self._hud_last_summary}"))

            bind_drag(frame, _start_drag, _on_drag, _end_drag)

            self._fit_window(
                self.root,
                min_w=340,
                min_h=46,
                pos="top_right",
                explicit_pos=preserve_pos,
                pad_w=6,
                pad_h=4,
                allow_resize=False,
            )
            return

        # Full HUD View
        top_row = ctk.CTkFrame(frame, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(10, 6))

        grip_lbl = ModernLabel(
            top_row,
            text="⋮⋮",
            font=ui_font(11, "bold"),
            text_color=pal["border_strong"],
            cursor="fleur",
        )
        grip_lbl.pack(side="left", padx=(0, 8))

        goal_display = goal if len(goal) <= 24 else f"{goal[:21]}..."
        self._hud_goal_label = ModernLabel(  # type: ignore[assignment]
            top_row,
            text=goal_display,
            font=ui_font(13, "bold", display=True),
            text_color=pal["text"],
            anchor="w",
            cursor="fleur",
        )
        self._hud_goal_label.pack(side="left", fill="x", expand=True)
        self._hud_tooltips.append(ToolTip(self._hud_goal_label, f"Focus Goal: {goal}"))

        min_btn = _make_button(
            top_row,
            text="—",
            command=self.minimize_to_taskbar,
            variant="ghost",
            font=ui_font(10, "bold"),
            padx=6,
            pady=2,
            height=24,
            width=26,
            palette=pal,
        )
        min_btn.pack(side="right", padx=(2, 0))
        self._hud_tooltips.append(ToolTip(min_btn, "Minimize HUD to taskbar"))

        self._hud_mini_btn = _make_button(  # type: ignore[assignment]
            top_row,
            text="▾",
            command=self.toggle_mini_mode,
            variant="ghost",
            font=ui_font(10, "bold"),
            padx=6,
            pady=2,
            height=24,
            width=26,
            palette=pal,
        )
        self._hud_mini_btn.pack(side="right", padx=(2, 0))
        self._hud_tooltips.append(ToolTip(self._hud_mini_btn, "Collapse to compact Mini-Pill"))

        self._hud_tray_btn = _make_button(  # type: ignore[assignment]
            top_row,
            text="Tray",
            command=self.minimize_to_tray,
            variant="ghost",
            font=ui_font(9, "bold"),
            padx=7,
            pady=2,
            height=24,
            palette=pal,
        )
        self._hud_tray_btn.pack(side="right", padx=(2, 0))
        self._hud_tooltips.append(ToolTip(self._hud_tray_btn, "Hide HUD to system tray (Ctrl+Alt+H)"))

        def _apply_pin_visual() -> None:
            if self._hud_pin_btn is None or not self._hud_pin_btn.winfo_exists():
                return
            if self._hud_pinned:
                self._hud_pin_btn.configure(
                    text="Pinned",
                    fg_color=pal["primary_soft"],
                    hover_color=pal["primary_border"],
                    text_color=pal["primary"],
                    border_color=pal["primary_border"],
                    border_width=1,
                )
            else:
                self._hud_pin_btn.configure(
                    text="Unpinned",
                    fg_color=pal["surface_alt"],
                    hover_color=pal["surface_hover"],
                    text_color=pal["text_muted"],
                    border_color=pal["border"],
                    border_width=1,
                )

        def _toggle_pin() -> None:
            self._hud_pinned = not self._hud_pinned
            try:
                self.root.attributes("-topmost", self._hud_pinned)
            except Exception:
                pass
            _apply_pin_visual()

        self._hud_pin_btn = _make_button(  # type: ignore[assignment]
            top_row,
            text="Pinned" if self._hud_pinned else "Unpinned",
            command=_toggle_pin,
            variant="ghost",
            font=ui_font(9, "bold"),
            padx=8,
            pady=2,
            height=24,
            corner_radius=RADII["pill"],
            palette=pal,
        )
        _apply_pin_visual()
        self._hud_pin_btn.pack(side="right", padx=(4, 0))
        self._hud_tooltips.append(
            ToolTip(
                self._hud_pin_btn,
                lambda: "HUD stays above all windows (click to unpin)" if self._hud_pinned else "Click to pin HUD on top",
            )
        )

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        self._theme_btn = _make_button(  # type: ignore[assignment]
            top_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="ghost",
            font=ui_font(9, "bold"),
            padx=7,
            pady=2,
            height=24,
            palette=pal,
        )
        self._theme_btn.pack(side="right", padx=(4, 0))
        self._hud_tooltips.append(ToolTip(self._theme_btn, "Switch Light / Dark theme"))

        mode_text = f"[{mode.upper()}]"
        mode_bg = pal["primary_soft"] if mode == "normal" else pal["danger_soft"]
        mode_fg = pal["primary"] if mode == "normal" else pal["danger"]
        mode_bd = pal["primary_border"] if mode == "normal" else pal["danger_border"]
        self._hud_mode_label = StatusPill(  # type: ignore[assignment]
            top_row,
            text=mode_text,
            bg=mode_bg,
            fg=mode_fg,
            border=mode_bd,
            font=ui_font(9, "bold"),
            padx=7,
            pady=1,
        )
        self._hud_mode_label.pack(side="right", padx=(6, 0))

        # Middle status row: soft state pill + live activity summary
        status_row = ctk.CTkFrame(frame, fg_color="transparent")
        status_row.pack(fill="x", padx=14, pady=(2, 8))

        self._hud_state_label = StatusPill(  # type: ignore[assignment]
            status_row,
            text=pill_text,
            bg=pill_bg,
            fg=pill_fg,
            border=pill_bd,
            dot_color=pill_dot,
            font=ui_font(10, "bold"),
            padx=8,
            pady=2,
        )
        self._hud_state_label.pack(side="left", anchor="n")

        hud_summary_text = self._hud_pause_summary if self._hud_paused else self._hud_last_summary
        is_ai_unavail = "AI unavailable" in hud_summary_text
        summary_fg = pal["ai_unavailable"] if is_ai_unavail else pal["text_secondary"]
        summary_font = ui_font(11, "bold") if is_ai_unavail else ui_font(11)
        self._hud_summary_label = ModernLabel(  # type: ignore[assignment]
            status_row,
            text=hud_summary_text,
            font=summary_font,
            text_color=summary_fg,
            wraplength=self._s(310),
            justify="left",
            anchor="w",
        )
        self._hud_summary_label.pack(side="left", padx=(10, 0), fill="x", expand=True)

        self._hud_startup_bar = ModernProgressBar(  # type: ignore[assignment]
            frame,
            palette=pal,
            height=4,
            mode="indeterminate",
        )

        # Hairline divider
        tk.Frame(frame, bg=pal["border"], height=1).pack(fill="x", pady=(2, 0))

        # Compact Footer: Telemetry line + Action strip
        footer = ctk.CTkFrame(frame, fg_color=pal["surface_alt"], corner_radius=0)
        footer.pack(fill="x", side="bottom")

        footer_in = ctk.CTkFrame(footer, fg_color="transparent")
        footer_in.pack(fill="x", padx=14, pady=9)

        meta_row = ctk.CTkFrame(footer_in, fg_color="transparent")
        meta_row.pack(fill="x", pady=(0, 8))

        self._hud_timer_label = ModernLabel(  # type: ignore[assignment]
            meta_row,
            text="",
            font=mono_font(10, "bold"),
            text_color=pal["warning"] if self._hud_paused else pal["text"],
            anchor="w",
        )

        if self._hud_paused:
            p_mins, p_secs = divmod(max(0, int(math.ceil(self._hud_pause_remaining))), 60)
            self._hud_timer_label.configure(text=f"Paused {p_mins:02d}:{p_secs:02d}")
            self._hud_timer_label.pack(side="left", padx=(0, 12))
        elif self._hud_deadline is not None:
            self._hud_timer_label.pack(side="left", padx=(0, 12))
            self._tick_hud_timer()

        self._hud_ai_calls_label = ModernLabel(  # type: ignore[assignment]
            meta_row,
            text=f"AI calls: T1 {self._hud_t1_count} / T2 {self._hud_t2_count}",
            font=mono_font(10),
            text_color=pal["text_muted"],
            anchor="w",
        )
        self._hud_ai_calls_label.pack(side="left")

        ext_box = ctk.CTkFrame(meta_row, fg_color="transparent")
        ext_box.pack(side="right")

        ext_dot_color = pal["success"] if self._hud_ext_connected else pal["ext_off"]
        ext_text = "Ext: Live" if self._hud_ext_connected else "Ext: Off"
        ext_fg = pal["success"] if self._hud_ext_connected else pal["text_muted"]
        self._hud_ext_dot = ModernLabel(  # type: ignore[assignment]
            ext_box,
            text="●",
            font=ui_font(9),
            text_color=ext_dot_color,
        )
        self._hud_ext_dot.pack(side="left", padx=(0, 4))
        self._hud_ext_label = ModernLabel(  # type: ignore[assignment]
            ext_box,
            text=ext_text,
            font=ui_font(10, "bold") if self._hud_ext_connected else ui_font(10),
            text_color=ext_fg,
        )
        self._hud_ext_label.pack(side="left")
        ext_tip_fn = lambda: (
            "Browser extension connected on 127.0.0.1:8765"
            if self._hud_ext_connected
            else "Browser extension offline — load extension/ in Chrome or Edge"
        )
        self._hud_tooltips.append(ToolTip(self._hud_ext_label, ext_tip_fn))

        actions_row = ctk.CTkFrame(footer_in, fg_color="transparent")
        actions_row.pack(fill="x")

        if on_open_rules is not None:
            self._hud_rules_btn = _make_button(  # type: ignore[assignment]
                actions_row,
                text="Rules",
                command=on_open_rules,
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=10,
                pady=4,
                height=28,
                palette=pal,
            )
            self._hud_rules_btn.pack(side="left", padx=(0, 6))
            self._hud_tooltips.append(ToolTip(self._hud_rules_btn, "Edit allowed & blocked sites/apps (Ctrl+Alt+R)"))
        else:
            self._hud_rules_btn = None

        act_cmd = on_open_activity or (lambda: self.show_activity_drawer(self._activity_mark_relevant_cb))
        self._hud_activity_btn = _make_button(  # type: ignore[assignment]
            actions_row,
            text="Activity",
            command=act_cmd,
            variant="secondary",
            font=ui_font(10, "bold"),
            padx=10,
            pady=4,
            height=28,
            palette=pal,
        )
        self._hud_activity_btn.pack(side="left")
        self._hud_tooltips.append(ToolTip(self._hud_activity_btn, "Why was this flagged? View live AI checks"))

        end_btn = _make_button(
            actions_row,
            text="End Session",
            command=on_end_session,
            variant="danger_outline",
            font=ui_font(10, "bold"),
            padx=12,
            pady=4,
            height=28,
            palette=pal,
        )
        end_btn.pack(side="right")

        if on_toggle_pause is not None:
            pause_label = "Resume" if self._hud_paused else f"Pause {self._break_minutes}m"
            self._hud_pause_btn = _make_button(  # type: ignore[assignment]
                actions_row,
                text=pause_label,
                command=on_toggle_pause,
                variant="warning_outline" if self._hud_paused else "secondary",
                font=ui_font(10, "bold"),
                padx=10,
                pady=4,
                height=28,
                palette=pal,
            )
            self._hud_pause_btn.pack(side="right", padx=(0, 6))
            self._hud_tooltips.append(
                ToolTip(
                    self._hud_pause_btn,
                    lambda: "Resume focus monitoring (Ctrl+Alt+P)"
                    if self._hud_paused
                    else f"Take a {self._break_minutes}-minute break (Ctrl+Alt+P)",
                )
            )
        else:
            self._hud_pause_btn = None

        # Bind smooth drag + release persistence across all non-button HUD elements
        bind_drag(frame, _start_drag, _on_drag, _end_drag)

        self._fit_window(
            self.root,
            min_w=440,
            min_h=124,
            pos="top_right",
            explicit_pos=preserve_pos,
            pad_w=8,
            pad_h=4,
            allow_resize=False,
        )

    def reset_hud_timer(self, duration_seconds: float) -> None:
        """Reset HUD work interval timer (e.g. when starting the next Pomodoro work cycle)."""

        def _action() -> None:
            self._hud_deadline = time.monotonic() + max(1.0, float(duration_seconds))
            self._hud_session_remaining_on_pause = None
            if not self._hud_paused:
                self._tick_hud_timer()

        self.dispatch(_action)

    def _tick_hud_timer(self) -> None:
        """Update the HUD remaining-time countdown once per second while not paused."""
        self._hud_timer_after_id = None
        if self._closed or self._hud_deadline is None or self._hud_paused:
            return
        if self._hud_timer_label is None or not self._hud_timer_label.winfo_exists():
            return
        remaining = max(0, int(math.ceil(self._hud_deadline - time.monotonic())))
        mins, secs = divmod(remaining, 60)
        txt = f"{mins:02d}:{secs:02d}" if self._hud_mini_mode else f"{mins:02d}:{secs:02d} left"
        self._hud_timer_label.configure(text=txt, text_color=self._p("text"))
        if remaining > 0:
            try:
                self._hud_timer_after_id = self.root.after(1000, self._tick_hud_timer)
            except Exception:
                pass

    def update_grace_countdown(self, remaining_seconds: float | None) -> None:
        """Update live 'Grace: Xs' countdown on the HUD state pill during GRACE_PERIOD."""

        def _action() -> None:
            self._hud_grace_remaining = remaining_seconds
            if self._hud_paused or self._hud_last_state != "GRACE_PERIOD":
                return
            if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                badge_txt = self._format_state_badge_text().strip()
                if self._hud_mini_mode and not isinstance(self._hud_state_label, StatusPill):
                    badge_txt = f"● {badge_txt}"
                self._hud_state_label.configure(text=badge_txt)

        self.dispatch(_action)

    def update_ai_calls(self, t1_count: int, t2_count: int) -> None:
        """Update the small HUD line showing AI call counts (thread-safe)."""

        def _action() -> None:
            self._hud_t1_count = t1_count
            self._hud_t2_count = t2_count
            if (
                hasattr(self, "_hud_ai_calls_label")
                and self._hud_ai_calls_label is not None
                and self._hud_ai_calls_label.winfo_exists()
            ):
                self._hud_ai_calls_label.configure(text=f"AI calls: T1 {t1_count} / T2 {t2_count}")

        self.dispatch(_action)

    def update_extension_status(self, connected: bool) -> None:
        """Update the HUD extension connection indicator dot (thread-safe)."""

        def _action() -> None:
            self._hud_ext_connected = bool(connected)
            pal = self._active_palette()
            if self._hud_ext_dot is not None and self._hud_ext_dot.winfo_exists():
                self._hud_ext_dot.configure(text_color=pal["success"] if connected else pal["ext_off"])
            if self._hud_ext_label is not None and self._hud_ext_label.winfo_exists():
                self._hud_ext_label.configure(
                    text="Ext: Live" if connected else "Ext: Off",
                    text_color=pal["success"] if connected else pal["text_muted"],
                    font=ui_font(10, "bold") if connected else ui_font(10),
                )

        self.dispatch(_action)

    def update_pause_status(
        self,
        paused: bool,
        remaining_seconds: float = 0.0,
        summary: str | None = None,
    ) -> None:
        """Update HUD controls and countdown for Pause/Resume state with synced hover colors."""

        def _action() -> None:
            pal = self._active_palette()
            was_paused = self._hud_paused
            self._hud_paused = bool(paused)
            self._hud_pause_remaining = max(0.0, float(remaining_seconds))

            if paused:
                if summary is not None and summary.strip():
                    self._hud_pause_summary = summary.strip()
                elif not was_paused:
                    self._hud_pause_summary = "Break active — monitoring paused."
                if not was_paused:
                    if self._hud_deadline is not None:
                        self._hud_session_remaining_on_pause = max(0.0, self._hud_deadline - time.monotonic())
                    if self._hud_timer_after_id is not None:
                        try:
                            self.root.after_cancel(self._hud_timer_after_id)
                        except Exception:
                            pass
                        self._hud_timer_after_id = None

                if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                    bg, fg, bd, dot = get_state_pill_style(self._theme, "PAUSED", paused=True)
                    lbl_txt = "PAUSED"
                    if isinstance(self._hud_state_label, StatusPill):
                        self._hud_state_label.set_pill(text=lbl_txt, bg=bg, fg=fg, border=bd, dot_color=dot)
                    else:
                        self._hud_state_label.configure(text="● PAUSED" if self._hud_mini_mode else "PAUSED", bg=bg, fg=fg)
                if self._hud_summary_label is not None and self._hud_summary_label.winfo_exists():
                    self._hud_summary_label.configure(
                        text=self._hud_pause_summary,
                        font=ui_font(11),
                        text_color=pal["text_secondary"],
                    )
                if self._hud_timer_label is not None and self._hud_timer_label.winfo_exists():
                    mins, secs = divmod(max(0, int(math.ceil(self._hud_pause_remaining))), 60)
                    t_str = f"{mins:02d}:{secs:02d}" if self._hud_mini_mode else f"Paused {mins:02d}:{secs:02d}"
                    self._hud_timer_label.configure(text=t_str, text_color=pal["warning"])
                    if not self._hud_mini_mode and not self._hud_timer_label.winfo_ismapped():
                        self._hud_timer_label.pack(side="left", padx=(0, 12))
                if self._hud_pause_btn is not None and self._hud_pause_btn.winfo_exists():
                    _set_button_variant(self._hud_pause_btn, "warning_outline", text="Resume", palette=pal)
            else:
                self._hud_pause_summary = "Break active — monitoring paused."
                if was_paused and self._hud_session_remaining_on_pause is not None:
                    self._hud_deadline = time.monotonic() + self._hud_session_remaining_on_pause
                    self._hud_session_remaining_on_pause = None
                if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                    bg, fg, bd, dot = get_state_pill_style(self._theme, self._hud_last_state, paused=False)
                    lbl_txt = self._format_state_badge_text().strip()
                    if isinstance(self._hud_state_label, StatusPill):
                        self._hud_state_label.set_pill(text=lbl_txt, bg=bg, fg=fg, border=bd, dot_color=dot)
                    else:
                        self._hud_state_label.configure(text=f"● {lbl_txt}" if self._hud_mini_mode else lbl_txt, bg=bg, fg=fg)
                if self._hud_pause_btn is not None and self._hud_pause_btn.winfo_exists():
                    p_txt = "Pause" if self._hud_mini_mode else f"Pause {self._break_minutes}m"
                    _set_button_variant(self._hud_pause_btn, "secondary", text=p_txt, palette=pal)
                if self._hud_deadline is not None:
                    self._tick_hud_timer()
                elif self._hud_timer_label is not None and self._hud_timer_label.winfo_exists():
                    self._hud_timer_label.configure(text="")

            if self._tray is not None:
                self._tray.update_state(self._hud_last_state, paused=self._hud_paused)

        self.dispatch(_action)

    def update_hud_status(self, state: AgentStateName, summary: str) -> None:
        """Update the HUD state badge and latest activity summary."""

        def _action() -> None:
            pal = self._active_palette()
            if state != "GRACE_PERIOD":
                self._hud_grace_remaining = None
            self._hud_last_state = state
            self._hud_last_summary = summary

            # Hide startup progress bar once monitoring is active
            if self._hud_startup_bar is not None and self._hud_startup_bar.winfo_exists():
                if self._hud_startup_bar.winfo_ismapped() and "starting" not in summary.lower() and "generating" not in summary.lower():
                    try:
                        self._hud_startup_bar.stop()
                        self._hud_startup_bar.pack_forget()
                    except Exception:
                        pass

            if not self._hud_paused:
                if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                    bg, fg, bd, dot = get_state_pill_style(self._theme, state, paused=False)
                    lbl_txt = self._format_state_badge_text().strip()
                    if isinstance(self._hud_state_label, StatusPill):
                        self._hud_state_label.set_pill(text=lbl_txt, bg=bg, fg=fg, border=bd, dot_color=dot)
                    else:
                        self._hud_state_label.configure(text=f"● {lbl_txt}" if self._hud_mini_mode else lbl_txt, bg=bg, fg=fg)

                if self._hud_summary_label is not None and self._hud_summary_label.winfo_exists():
                    display_summary = summary if len(summary) <= 85 else f"{summary[:82]}..."
                    if "AI unavailable" in summary:
                        self._hud_summary_label.configure(
                            text=display_summary,
                            font=ui_font(11, "bold"),
                            text_color=pal["ai_unavailable"],
                        )
                    else:
                        self._hud_summary_label.configure(
                            text=display_summary,
                            font=ui_font(11),
                            text_color=pal["text_secondary"],
                        )

                    if not self._hud_mini_mode:
                        try:
                            self.root.update_idletasks()
                            req_h = self.root.winfo_reqheight()
                            cur_w = self.root.winfo_width()
                            cur_h = self.root.winfo_height()
                            if req_h > cur_h and cur_w > 100:
                                x = self.root.winfo_x()
                                y = self.root.winfo_y()
                                new_h = min(self._s(240), req_h + 6)
                                self.root.geometry(f"{cur_w}x{new_h}+{x}+{y}")
                        except Exception:
                            pass

            if self._tray is not None:
                self._tray.update_state(state, paused=self._hud_paused, tooltip=f"Anti-Distractor — {state}")

        self.dispatch(_action)

    # ------------------------------------------------------------------
    # Live Activity Feed & Secondary Dialogs (History, Settings)
    # ------------------------------------------------------------------
    def clear_activity_items(self) -> None:
        def _action() -> None:
            self._activity_items.clear()
            if self._activity_drawer is not None and self._activity_drawer.win.winfo_exists():
                self._activity_drawer.refresh(self._activity_items)

        self.dispatch(_action)

    def append_activity_item(
        self,
        item: ActivityFeedItem,
        *,
        on_mark_relevant: Callable[[ActivityFeedItem], None] | None = None,
    ) -> None:
        def _action() -> None:
            if on_mark_relevant is not None:
                self._activity_mark_relevant_cb = on_mark_relevant
            self._activity_items.append(item)
            if len(self._activity_items) > 20:
                self._activity_items = self._activity_items[-20:]
            if self._activity_drawer is not None and self._activity_drawer.win.winfo_exists():
                self._activity_drawer.refresh(self._activity_items)

        self.dispatch(_action)

    def mark_activity_overridden(self, log_id: int | None, source_key: str = "") -> None:
        def _action() -> None:
            for it in self._activity_items:
                if (log_id is not None and it.log_id == log_id) or (
                    source_key and it.source_key.lower() == source_key.lower() and not it.on_task
                ):
                    it.overridden = True
            if self._activity_drawer is not None and self._activity_drawer.win.winfo_exists():
                self._activity_drawer.refresh(self._activity_items)

        self.dispatch(_action)

    def show_activity_drawer(
        self,
        on_mark_relevant: Callable[[ActivityFeedItem], None] | None = None,
    ) -> None:
        def _action() -> None:
            if on_mark_relevant is not None:
                self._activity_mark_relevant_cb = on_mark_relevant
            if self._activity_drawer is not None:
                try:
                    if self._activity_drawer.win.winfo_exists():
                        self._activity_drawer.win.destroy()
                except Exception:
                    pass
                self._activity_drawer = None

            self._activity_drawer = ActivityDrawerDialog(
                self.root,
                palette=self._active_palette(),
                dark=(self._theme == "dark"),
                scale_fn=self._s,
                fit_fn=self._fit_window,
                make_btn_fn=_make_button,
                items=self._activity_items,
                on_mark_relevant=self._activity_mark_relevant_cb,
                on_close=lambda: setattr(self, "_activity_drawer", None),
            )

        self.dispatch(_action)

    def show_history_dialog(
        self,
        stats: dict[str, Any],
        sessions: list[dict[str, Any]],
        *,
        on_read_review: Callable[[dict[str, Any]], None] | None = None,
        on_repeat_session: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        def _action() -> None:
            if self._history_dialog is not None:
                try:
                    if self._history_dialog.win.winfo_exists():
                        self._history_dialog.win.destroy()
                except Exception:
                    pass
                self._history_dialog = None

            self._history_dialog = HistoryAnalyticsDialog(
                self.root,
                palette=self._active_palette(),
                dark=(self._theme == "dark"),
                scale_fn=self._s,
                fit_fn=self._fit_window,
                make_btn_fn=_make_button,
                stats=stats,
                sessions=sessions,
                on_read_review=on_read_review,
                on_repeat_session=on_repeat_session,
                on_close=lambda: setattr(self, "_history_dialog", None),
            )

        self.dispatch(_action)

    def update_setup_api_key_status(self, api_key_status: tuple[bool, str]) -> None:
        """Refresh the Setup screen's API Key status pill immediately when the key changes."""
        self._setup_extra_kwargs["api_key_status"] = api_key_status

        def _action() -> None:
            if self._active_screen != "setup" or self._setup_api_key_btn is None:
                return
            try:
                if not self._setup_api_key_btn.winfo_exists():
                    return
            except Exception:
                return
            pal = self._active_palette()
            configured, status_str = api_key_status
            _set_button_variant(
                self._setup_api_key_btn,
                "success_outline" if configured else "warning_outline",
                text="Key ✓" if configured else "Set API Key",
                palette=pal,
            )
            if self._setup_api_key_tooltip is not None:
                self._setup_api_key_tooltip.text = (
                    f"Gemini API Key: {status_str} — Click to view or change"
                    if configured
                    else "No Gemini API key configured — Click to add your key"
                )

        self.dispatch(_action)

    def show_settings_dialog(
        self,
        current_settings: AppSettings,
        api_key_status: tuple[bool, str],
        *,
        on_save_settings: Callable[[AppSettings], None],
        on_save_api_key: Callable[[str], tuple[bool, str]] | None = None,
        on_test_api_key: Callable[[str, Callable[[bool, str], None]], None] | None = None,
        on_clear_api_key: Callable[[], tuple[bool, str]] | None = None,
        focus_api_key: bool = False,
    ) -> None:
        def _action() -> None:
            if self._settings_dialog is not None:
                try:
                    if self._settings_dialog.win.winfo_exists():
                        self._settings_dialog.win.destroy()
                except Exception:
                    pass
                self._settings_dialog = None

            self._settings_dialog = SettingsDialog(
                self.root,
                palette=self._active_palette(),
                dark=(self._theme == "dark"),
                scale_fn=self._s,
                fit_fn=self._fit_window,
                make_btn_fn=_make_button,
                current_settings=current_settings,
                api_key_status=api_key_status,
                on_save_settings=on_save_settings,
                on_save_api_key=on_save_api_key,
                on_test_api_key=on_test_api_key,
                on_clear_api_key=on_clear_api_key,
                focus_api_key=focus_api_key,
                url_opener=self._url_opener,
                on_close=lambda: setattr(self, "_settings_dialog", None),
            )

        self.dispatch(_action)

    # ------------------------------------------------------------------
    # Smarter Editable Rules Dialog
    # ------------------------------------------------------------------
    def show_rules_editor(
        self,
        profile: GoalProfile,
        on_save: Callable[[GoalProfile], None],
        *,
        on_ai_suggest: Callable[[str, Callable[[GoalProfile], None]], None] | None = None,
        active_target: tuple[Literal["domain", "app"], str] | None = None,
    ) -> None:
        """Open a conflict-preventing, DPI-aware dialog to edit allow/borderline/block rules live."""

        def _action() -> None:
            pal = self._active_palette()
            if self._rules_editor_win is not None:
                try:
                    if self._rules_editor_win.winfo_exists():
                        self._rules_editor_win.destroy()
                except Exception:
                    pass
                self._rules_editor_win = None

            win = tk.Toplevel(self.root)
            win.title(f"Anti-Distractor — Focus Rules ({profile.goal})")
            win.configure(bg=pal["bg"])
            win.attributes("-topmost", True)
            apply_brand_icon(self.root, win)
            apply_dark_titlebar(win, dark=(self._theme == "dark"))
            self._rules_editor_win = win

            current_topics: list[str] = list(profile.on_task_topics)

            outer = tk.Frame(win, bg=pal["bg"], padx=20, pady=16)
            outer.pack(fill="both", expand=True)

            ModernLabel(
                outer,
                text="Focus rules",
                font=ui_font(16, "bold", display=True),
                text_color=pal["text"],
            ).pack(anchor="w")

            ModernLabel(
                outer,
                text=f"Goal: {profile.goal} — changes apply immediately without restarting.",
                font=ui_font(11),
                text_color=pal["text_secondary"],
                wraplength=self._s(520),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(2, 10))

            # Optional mid-session 1-click "Current Site/App" shortcut banner
            quick_allow_btn: Any = None
            quick_block_btn: Any = None
            if active_target is not None and active_target[1]:
                t_kind, t_val = active_target
                banner = SurfaceCard(
                    outer,
                    palette=pal,
                    fg_color=pal["surface"],
                    border_color=pal["border_strong"],
                    corner_radius=RADII["md"],
                    border_width=1,
                )
                banner.pack(fill="x", pady=(0, 10))
                banner_in = ctk.CTkFrame(banner, fg_color="transparent")
                banner_in.pack(fill="x", padx=12, pady=8)

                ModernLabel(
                    banner_in,
                    text=f"Current {t_kind}: {t_val}",
                    font=ui_font(11, "bold"),
                    text_color=pal["text"],
                ).pack(side="left")

                def _quick_add(allow: bool) -> None:
                    if t_kind == "domain":
                        dest = "allowed_domains" if allow else "blocked_domains"
                        _show_tab("websites")
                    else:
                        dest = "allowed_apps" if allow else "blocked_apps"
                        _show_tab("apps")
                    _insert_with_dedup(dest, t_val, is_domain=(t_kind == "domain"))

                quick_block_btn = _make_button(
                    banner_in,
                    text="Block",
                    command=lambda: _quick_add(False),
                    variant="danger_outline",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=3,
                    height=26,
                    palette=pal,
                )
                quick_block_btn.pack(side="right")

                quick_allow_btn = _make_button(
                    banner_in,
                    text="+ Allow",
                    command=lambda: _quick_add(True),
                    variant="secondary",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=3,
                    height=26,
                    palette=pal,
                )
                quick_allow_btn.pack(side="right", padx=(0, 6))

            # Tab switcher bar + optional pre-session AI Suggest Rules button
            tab_bar = ctk.CTkFrame(outer, fg_color="transparent")
            tab_bar.pack(fill="x", pady=(0, 10))

            footer = ctk.CTkFrame(outer, fg_color="transparent")
            footer.pack(fill="x", side="bottom", pady=(10, 0))

            status_note = ModernLabel(
                outer,
                text="Tip: Select any entry and press Delete or Backspace to remove it.",
                font=ui_font(10),
                text_color=pal["text_muted"],
                anchor="w",
            )
            status_note.pack(fill="x", side="bottom", pady=(8, 0))

            content_holder = SurfaceCard(
                outer,
                palette=pal,
                fg_color=pal["surface"],
                border_color=pal["border_strong"],
                corner_radius=RADII["lg"],
                border_width=1,
            )
            content_holder.pack(fill="both", expand=True)

            websites_frame = ctk.CTkFrame(content_holder, fg_color="transparent")
            apps_frame = ctk.CTkFrame(content_holder, fg_color="transparent")

            listboxes: dict[str, tk.Listbox] = {}
            entries: dict[str, Any] = {}
            add_btns: dict[str, Any] = {}
            remove_btns: dict[str, Any] = {}

            section_labels = {
                "allowed_domains": "Allowed Websites",
                "borderline_domains": "Borderline Websites",
                "blocked_domains": "Blocked Websites",
                "allowed_apps": "Allowed Apps",
                "blocked_apps": "Blocked Apps",
            }
            domain_keys = ("allowed_domains", "borderline_domains", "blocked_domains")
            app_keys = ("allowed_apps", "blocked_apps")

            def _insert_with_dedup(target_key: str, raw_val: str, *, is_domain: bool) -> bool:
                norm = normalize_domain(raw_val) if is_domain else raw_val.strip().lower()
                if not norm:
                    status_note.configure(text="Enter a valid domain (e.g. github.com) or .exe name.", text_color=pal["danger"])
                    return False

                siblings = domain_keys if is_domain else app_keys
                moved_from: list[str] = []
                for sib in siblings:
                    if sib == target_key:
                        continue
                    sib_lb = listboxes[sib]
                    sib_items = list(sib_lb.get(0, "end"))
                    for idx in reversed(range(len(sib_items))):
                        if sib_items[idx].lower() == norm.lower():
                            sib_lb.delete(idx)
                            moved_from.append(section_labels[sib])

                target_lb = listboxes[target_key]
                existing = {x.lower() for x in target_lb.get(0, "end")}
                if norm.lower() not in existing:
                    target_lb.insert("end", norm)

                if moved_from:
                    status_note.configure(
                        text=f"Moved '{norm}' from {', '.join(moved_from)} → {section_labels[target_key]}.",
                        text_color=pal["primary"],
                    )
                else:
                    status_note.configure(
                        text=f"Added '{norm}' to {section_labels[target_key]}.",
                        text_color=pal["text_secondary"],
                    )
                return True

            def _make_section(
                parent: tk.Misc,
                key: str,
                heading: str,
                accent: str,
                items: list[str],
                is_domain: bool,
                placeholder: str,
            ) -> None:
                sec = ctk.CTkFrame(parent, fg_color="transparent")
                sec.pack(fill="both", expand=True, padx=14, pady=(8, 6))

                ModernLabel(
                    sec,
                    text=heading,
                    font=ui_font(10, "bold"),
                    text_color=accent,
                ).pack(anchor="w", pady=(0, 4))

                lb_wrap = tk.Frame(sec, bg=pal["surface"])
                lb_wrap.pack(fill="both", expand=True)

                lb = tk.Listbox(
                    lb_wrap,
                    height=4,
                    font=ui_font(10),
                    bg=pal["surface_alt"],
                    fg=pal["text"],
                    selectbackground=pal["primary"],
                    selectforeground="#FFFFFF",
                    relief="flat",
                    bd=0,
                    highlightthickness=1,
                    highlightbackground=pal["border"],
                )
                sb = ctk.CTkScrollbar(
                    lb_wrap,
                    orientation="vertical",
                    command=lb.yview,
                    fg_color="transparent",
                    button_color=pal["border_strong"],
                    button_hover_color=pal["text_muted"],
                    width=10,
                )
                lb.configure(yscrollcommand=sb.set)
                lb.pack(side="left", fill="both", expand=True)
                sb.pack(side="right", fill="y", padx=(2, 0))

                for item in items:
                    if item:
                        lb.insert("end", item)
                listboxes[key] = lb

                ctrl = ctk.CTkFrame(sec, fg_color="transparent")
                ctrl.pack(fill="x", pady=(6, 0))

                ent_var = tk.StringVar()
                ent = ModernEntry(ctrl, palette=pal, textvariable=ent_var, font=ui_font(10), height=30)
                ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
                entries[key] = ent

                def _add() -> None:
                    raw = ent_var.get().strip()
                    if not raw:
                        return
                    if _insert_with_dedup(key, raw, is_domain=is_domain):
                        ent_var.set("")

                def _remove(_event: tk.Event | None = None) -> None:
                    sel = list(lb.curselection())
                    for idx in reversed(sel):
                        lb.delete(idx)

                ent.bind("<Return>", lambda _e: _add())
                lb.bind("<Delete>", _remove)
                lb.bind("<BackSpace>", _remove)

                add_btn = _make_button(
                    ctrl,
                    text=f"+ Add {placeholder}",
                    command=_add,
                    variant="secondary",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=4,
                    height=30,
                    palette=pal,
                )
                add_btn.pack(side="left", padx=(0, 4))
                add_btns[key] = add_btn

                rem_btn = _make_button(
                    ctrl,
                    text="Remove",
                    command=_remove,
                    variant="danger_outline",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=4,
                    height=30,
                    palette=pal,
                )
                rem_btn.pack(side="left")
                remove_btns[key] = rem_btn

            _make_section(
                websites_frame,
                "allowed_domains",
                "Allowed websites (always on-task)",
                pal["success"],
                list(profile.allowed_domains),
                is_domain=True,
                placeholder="Domain",
            )
            _make_section(
                websites_frame,
                "borderline_domains",
                "Borderline websites (inspect title / screen)",
                pal["warning"],
                list(profile.borderline_domains),
                is_domain=True,
                placeholder="Domain",
            )
            _make_section(
                websites_frame,
                "blocked_domains",
                "Blocked websites (instant distraction)",
                pal["danger"],
                list(profile.blocked_domains),
                is_domain=True,
                placeholder="Domain",
            )

            _make_section(
                apps_frame,
                "allowed_apps",
                "Allowed desktop apps (e.g. code.exe)",
                pal["success"],
                list(profile.allowed_apps),
                is_domain=False,
                placeholder="App",
            )
            _make_section(
                apps_frame,
                "blocked_apps",
                "Blocked desktop apps (e.g. spotify.exe)",
                pal["danger"],
                list(profile.blocked_apps),
                is_domain=False,
                placeholder="App",
            )

            websites_frame.pack(fill="both", expand=True)

            def _show_tab(which: str) -> None:
                if which == "apps":
                    websites_frame.pack_forget()
                    apps_frame.pack(fill="both", expand=True)
                    _set_button_variant(tab_web_btn, "secondary", palette=pal)
                    _set_button_variant(tab_app_btn, "primary", palette=pal)
                else:
                    apps_frame.pack_forget()
                    websites_frame.pack(fill="both", expand=True)
                    _set_button_variant(tab_web_btn, "primary", palette=pal)
                    _set_button_variant(tab_app_btn, "secondary", palette=pal)

            tab_web_btn = _make_button(
                tab_bar,
                text="Websites",
                command=lambda: _show_tab("websites"),
                variant="primary",
                font=ui_font(11, "bold"),
                padx=14,
                pady=5,
                height=30,
                palette=pal,
            )
            tab_web_btn.pack(side="left", padx=(0, 6))

            tab_app_btn = _make_button(
                tab_bar,
                text="Desktop Apps",
                command=lambda: _show_tab("apps"),
                variant="secondary",
                font=ui_font(11, "bold"),
                padx=14,
                pady=5,
                height=30,
                palette=pal,
            )
            tab_app_btn.pack(side="left")

            ai_suggest_btn: Any = None
            if on_ai_suggest is not None:
                def _handle_ai_suggest() -> None:
                    if ai_suggest_btn is not None and ai_suggest_btn.winfo_exists():
                        ai_suggest_btn.configure(state="disabled", text="Suggesting...")
                    status_note.configure(text="Generating AI rule suggestions...", text_color=pal["primary"])

                    def _on_suggested(suggested: GoalProfile) -> None:
                        def _apply_ui() -> None:
                            if not win.winfo_exists():
                                return
                            current_topics.clear()
                            current_topics.extend(suggested.on_task_topics or [profile.goal])
                            mapping = {
                                "allowed_domains": suggested.allowed_domains,
                                "borderline_domains": suggested.borderline_domains,
                                "blocked_domains": suggested.blocked_domains,
                                "allowed_apps": suggested.allowed_apps,
                                "blocked_apps": suggested.blocked_apps,
                            }
                            for k, vals in mapping.items():
                                lb_k = listboxes[k]
                                lb_k.delete(0, "end")
                                seen_k: set[str] = set()
                                for v in vals:
                                    if v and v.lower() not in seen_k:
                                        lb_k.insert("end", v)
                                        seen_k.add(v.lower())
                            if ai_suggest_btn is not None and ai_suggest_btn.winfo_exists():
                                ai_suggest_btn.configure(state="normal", text="✨ AI Suggest Rules")
                            status_note.configure(
                                text="AI suggestions applied — review and click Save Rules.",
                                text_color=pal["success"],
                            )

                        self.dispatch(_apply_ui)

                    on_ai_suggest(profile.goal, _on_suggested)

                ai_suggest_btn = _make_button(
                    tab_bar,
                    text="✨ AI Suggest Rules",
                    command=_handle_ai_suggest,
                    variant="secondary",
                    font=ui_font(10, "bold"),
                    padx=12,
                    pady=5,
                    height=30,
                    palette=pal,
                )
                ai_suggest_btn.pack(side="right")

            def _close() -> None:
                if self._rules_editor_win is win:
                    self._rules_editor_win = None
                try:
                    if win.winfo_exists():
                        win.destroy()
                except Exception:
                    pass

            def _save() -> None:
                updated = GoalProfile(
                    goal=profile.goal,
                    on_task_topics=list(current_topics),
                    allowed_domains=list(listboxes["allowed_domains"].get(0, "end")),
                    borderline_domains=list(listboxes["borderline_domains"].get(0, "end")),
                    blocked_domains=list(listboxes["blocked_domains"].get(0, "end")),
                    allowed_apps=list(listboxes["allowed_apps"].get(0, "end")),
                    blocked_apps=list(listboxes["blocked_apps"].get(0, "end")),
                )
                _close()
                try:
                    on_save(updated)
                except Exception:
                    logger.exception("Error saving edited GoalProfile")

            _make_button(
                footer,
                text="Cancel",
                command=_close,
                variant="secondary",
                font=ui_font(11, "bold"),
                padx=14,
                pady=6,
                height=34,
                palette=pal,
            ).pack(side="left")

            save_btn = _make_button(
                footer,
                text="Save Rules",
                command=_save,
                variant="primary",
                font=ui_font(11, "bold"),
                padx=16,
                pady=6,
                height=34,
                palette=pal,
            )
            save_btn.pack(side="right")

            self._rules_editor_widgets = {
                "win": win,
                "listboxes": listboxes,
                "entries": entries,
                "add_btns": add_btns,
                "remove_btns": remove_btns,
                "save_btn": save_btn,
                "show_tab": _show_tab,
                "status_lbl": status_note,
                "ai_suggest_btn": ai_suggest_btn,
                "quick_allow_btn": quick_allow_btn,
                "quick_block_btn": quick_block_btn,
            }

            win.bind("<Escape>", lambda _e: _close())
            win.protocol("WM_DELETE_WINDOW", _close)
            self._fit_window(win, min_w=550, min_h=550, pos="center", pad_w=14, pad_h=14, allow_resize=True)

        self.dispatch(_action)

    # ------------------------------------------------------------------
    # Review Screen (KPI Cards, Animated Progress, Tab Vault & .md Export)
    # ------------------------------------------------------------------
    def _render_markdown_into_text(self, txt: tk.Text, review_text: str) -> None:
        """Render lightweight Markdown (headings, bold, bullets) into a Tk Text widget."""
        txt.config(state="normal")
        txt.delete("1.0", "end")

        def _insert_inline(line_text: str, base_tag: str | None = None) -> None:
            pos = 0
            for match in re.finditer(r"\*\*(.+?)\*\*", line_text):
                if match.start() > pos:
                    tags = (base_tag,) if base_tag else ()
                    txt.insert("end", line_text[pos : match.start()], tags)
                tags = ("bold", base_tag) if base_tag else ("bold",)
                txt.insert("end", match.group(1), tags)
                pos = match.end()
            if pos < len(line_text):
                tags = (base_tag,) if base_tag else ()
                txt.insert("end", line_text[pos:], tags)

        for raw_line in review_text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("### "):
                txt.insert("end", stripped[4:] + "\n", ("h3",))
            elif stripped.startswith("## "):
                txt.insert("end", stripped[3:] + "\n", ("h2",))
            elif stripped.startswith("# "):
                txt.insert("end", stripped[2:] + "\n", ("h1",))
            elif stripped.startswith("- ") or stripped.startswith("* "):
                txt.insert("end", "• ", ("bullet",))
                _insert_inline(stripped[2:], base_tag="bullet")
                txt.insert("end", "\n", ("bullet",))
            else:
                _insert_inline(raw_line)
                txt.insert("end", "\n")

        txt.config(state="disabled")

    def _populate_review_kpis(self, holder: tk.Frame, kpis: SessionKPIs | None) -> None:
        for child in holder.winfo_children():
            child.destroy()
        if kpis is None:
            holder.pack_forget()
            return

        pal = self._active_palette()
        score = int(kpis.focus_score_pct)
        score_color = pal["success"] if score >= 75 else (pal["warning"] if score >= 50 else pal["danger"])
        mins_str = f"{kpis.focused_minutes:.1f}m" if kpis.focused_minutes < 10 else f"{int(round(kpis.focused_minutes))}m"

        metrics = [
            ("Focus score", f"{score}%", score_color),
            ("Focused time", mins_str, pal["primary"]),
            ("Distractions", str(kpis.distractions_count), pal["danger"] if kpis.distractions_count > 0 else pal["text"]),
            ("Overrides", str(kpis.overrides_count), pal["text"]),
        ]
        for idx, (label, val, accent) in enumerate(metrics):
            holder.columnconfigure(idx, weight=1, uniform="kpi")
            card = SurfaceCard(
                holder,
                palette=pal,
                fg_color=pal["surface"],
                border_color=pal["border"],
                corner_radius=10,
                border_width=1,
            )
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 8, 0))
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=14, pady=10)
            ModernLabel(
                inner,
                text=label,
                font=ui_font(10, "bold"),
                text_color=pal["text_muted"],
            ).pack(anchor="w")
            ModernLabel(
                inner,
                text=val,
                font=ui_font(19, "bold", display=True),
                text_color=accent,
            ).pack(anchor="w", pady=(2, 0))

    def _populate_review_vault(self, holder: tk.Frame, closed_tabs: list[ClosedTabEntry]) -> None:
        for child in holder.winfo_children():
            child.destroy()
        self._review_vault_reopen_btns: list[Any] = []
        self._review_vault_toggle_btn: Any = None
        self._review_vault_reopen_all_btn: Any = None

        if not closed_tabs:
            holder.pack_forget()
            return

        pal = self._active_palette()
        bar = SurfaceCard(
            holder,
            palette=pal,
            fg_color=pal["surface"],
            border_color=pal["border"],
            corner_radius=RADII["md"],
            border_width=1,
        )
        bar.pack(fill="x")

        bar_in = ctk.CTkFrame(bar, fg_color="transparent")
        bar_in.pack(fill="x", padx=12, pady=6)

        items_frame = SurfaceCard(
            holder,
            palette=pal,
            fg_color=pal["surface_alt"],
            border_color=pal["border"],
            corner_radius=RADII["md"],
            border_width=1,
        )
        expanded = {"open": False}

        def _toggle_vault() -> None:
            expanded["open"] = not expanded["open"]
            if expanded["open"]:
                items_frame.pack(fill="x", pady=(4, 0))
                toggle_btn.configure(text=f"▾ Closed Tabs Vault ({len(closed_tabs)})")
            else:
                items_frame.pack_forget()
                toggle_btn.configure(text=f"▸ Closed Tabs Vault ({len(closed_tabs)})")

        toggle_btn = _make_button(
            bar_in,
            text=f"▸ Closed Tabs Vault ({len(closed_tabs)})",
            command=_toggle_vault,
            variant="ghost",
            font=ui_font(11, "bold"),
            padx=6,
            pady=2,
            height=26,
            palette=pal,
        )
        toggle_btn.pack(side="left")
        self._review_vault_toggle_btn = toggle_btn

        def _reopen_all() -> None:
            for entry in closed_tabs:
                if entry.url:
                    try:
                        self._url_opener(entry.url)
                    except Exception:
                        logger.exception("Failed reopening URL %s", entry.url)

        reopen_all_btn = _make_button(
            bar_in,
            text="Reopen All",
            command=_reopen_all,
            variant="secondary",
            font=ui_font(10, "bold"),
            padx=10,
            pady=3,
            height=26,
            palette=pal,
        )
        reopen_all_btn.pack(side="right")
        self._review_vault_reopen_all_btn = reopen_all_btn

        for entry in closed_tabs[:12]:
            row = ctk.CTkFrame(items_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=4)
            label_str = entry.title or entry.domain or entry.url
            if len(label_str) > 54:
                label_str = label_str[:51] + "..."
            ModernLabel(
                row,
                text=f"{label_str}  ({entry.domain})",
                font=ui_font(10),
                text_color=pal["text_secondary"],
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

            r_btn = _make_button(
                row,
                text="Reopen",
                command=lambda u=entry.url: self._url_opener(u),
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=10,
                pady=2,
                height=24,
                palette=pal,
            )
            r_btn.pack(side="right", padx=(6, 0))
            self._review_vault_reopen_btns.append(r_btn)

    def show_review(
        self,
        review_text: str,
        on_close: Callable[[], None],
        on_new_session: Callable[[], None] | None = None,
        *,
        kpis: SessionKPIs | None = None,
        closed_tabs: list[ClosedTabEntry] | None = None,
    ) -> None:
        """Display the end-of-session AI review screen (updating in-place if already open)."""
        self.dispatch(
            lambda: self._render_review_now(
                review_text,
                on_close,
                on_new_session=on_new_session,
                kpis=kpis,
                closed_tabs=closed_tabs,
            )
        )

    def _render_review_now(
        self,
        review_text: str,
        on_close: Callable[[], None],
        on_new_session: Callable[[], None] | None = None,
        *,
        kpis: SessionKPIs | None = None,
        closed_tabs: list[ClosedTabEntry] | None = None,
    ) -> None:
        pal = self._active_palette()
        self._review_callbacks = {
            "on_close": on_close,
            "on_new_session": on_new_session,
        }
        if kpis is not None:
            self._review_kpis = kpis
        if closed_tabs is not None:
            self._review_closed_tabs = list(closed_tabs)

        is_loading = review_text.strip().lower().startswith("generating")

        # Smooth in-place refresh when async review arrives after "Generating your review..."
        if (
            self._active_screen == "review"
            and self._review_text_widget is not None
            and self._review_text_widget.winfo_exists()
            and self._current_frame is not None
            and self._current_frame.winfo_exists()
        ):
            self._review_raw_text = review_text
            self._render_markdown_into_text(self._review_text_widget, review_text)
            if self._review_sub_label is not None and self._review_sub_label.winfo_exists():
                self._review_sub_label.configure(
                    text=(
                        "Compiling your focus metrics and session summary..."
                        if is_loading
                        else "Here is your focus summary, highlights, and model breakdown:"
                    )
                )
            if self._review_progress_bar is not None and self._review_progress_bar.winfo_exists():
                if is_loading:
                    self._review_progress_bar.start()
                else:
                    self._review_progress_bar.stop()
                    self._review_progress_bar.destroy()
                    self._review_progress_bar = None
            txt_container = getattr(self._review_text_widget, "frame", self._review_text_widget)
            if kpis is not None and self._review_kpi_holder is not None and self._review_kpi_holder.winfo_exists():
                self._populate_review_kpis(self._review_kpi_holder, kpis)
                if self._review_kpi_holder.winfo_manager() != "pack":
                    self._review_kpi_holder.pack(fill="x", pady=(0, 10), before=txt_container)
            if (
                closed_tabs is not None
                and self._review_vault_holder is not None
                and self._review_vault_holder.winfo_exists()
            ):
                self._populate_review_vault(self._review_vault_holder, self._review_closed_tabs)
                if self._review_closed_tabs and self._review_vault_holder.winfo_manager() != "pack":
                    self._review_vault_holder.pack(fill="x", side="bottom", pady=(8, 0), before=txt_container)
            return

        self._stop_tray()
        self._hide_popups_and_overlay_now()
        self._clear_root_widgets()
        self._active_screen = "review"
        if kpis is None:
            self._review_kpis = None
        if closed_tabs is None:
            self._review_closed_tabs = []
        gen = self._screen_gen

        self.root.title("Anti-Distractor — Session Review")
        self.root.configure(bg=pal["bg"])
        was_withdrawn = self._reset_window_state(
            self.root,
            topmost=False,
            resizable=(True, True),
            deiconify=True,
        )
        if not was_withdrawn:
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                logger.exception("Failed to raise review window")

        frame = tk.Frame(self.root, bg=pal["bg"], padx=24, pady=20)
        frame.pack(fill="both", expand=True)
        self._current_frame = frame

        # Header row + theme toggle
        hdr_row = ctk.CTkFrame(frame, fg_color="transparent")
        hdr_row.pack(fill="x", pady=(0, 4))

        ModernLabel(
            hdr_row,
            text="Focus Session Review",
            font=ui_font(19, "bold", display=True),
            text_color=pal["text"],
        ).pack(side="left")

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        self._theme_btn = _make_button(  # type: ignore[assignment]
            hdr_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="ghost",
            font=ui_font(10, "bold"),
            padx=10,
            pady=4,
            height=28,
            palette=pal,
        )
        self._theme_btn.pack(side="right")

        sub_lbl = ModernLabel(
            frame,
            text=(
                "Compiling your focus metrics and session summary..."
                if is_loading
                else "Here is your focus summary, highlights, and model breakdown:"
            ),
            font=ui_font(11),
            text_color=pal["text_secondary"],
            anchor="w",
        )
        sub_lbl.pack(anchor="w", pady=(0, 12))
        self._review_sub_label = sub_lbl  # type: ignore[assignment]

        if is_loading:
            pbar = ModernProgressBar(frame, palette=pal, height=5, mode="indeterminate")
            pbar.pack(fill="x", pady=(0, 12))
            pbar.start()
            self._review_progress_bar = pbar  # type: ignore[assignment]

        # KPI Summary Strip
        kpi_holder = tk.Frame(frame, bg=pal["bg"])
        self._review_kpi_holder = kpi_holder
        if self._review_kpis is not None:
            kpi_holder.pack(fill="x", pady=(0, 12))
            self._populate_review_kpis(kpi_holder, self._review_kpis)

        # Footer buttons packed side="bottom" first so they can NEVER be pushed off-screen
        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom", pady=(14, 0))

        vault_holder = tk.Frame(frame, bg=pal["bg"])
        self._review_vault_holder = vault_holder
        if self._review_closed_tabs:
            vault_holder.pack(fill="x", side="bottom", pady=(10, 0))
            self._populate_review_vault(vault_holder, self._review_closed_tabs)

        self._review_raw_text = review_text

        def _handle_copy() -> None:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(self._review_raw_text)
                copy_btn.configure(text="Copied!")
                self.root.after(
                    1500,
                    lambda: copy_btn.configure(text="Copy Summary") if copy_btn.winfo_exists() else None,
                )
            except Exception:
                logger.exception("Failed to copy review summary")

        copy_btn = _make_button(
            btn_row,
            text="Copy Summary",
            command=_handle_copy,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=7,
            height=34,
            palette=pal,
        )
        copy_btn.pack(side="left")
        self._review_copy_btn = copy_btn  # type: ignore[assignment]

        def _handle_export_md() -> None:
            try:
                target_path = filedialog.asksaveasfilename(
                    parent=self.root,
                    title="Save Session Review as Markdown",
                    defaultextension=".md",
                    initialfile="focus-session-review.md",
                    filetypes=[("Markdown files", "*.md"), ("Text files", "*.txt"), ("All files", "*.*")],
                )
                if not target_path:
                    return
                parts: list[str] = []
                if self._review_kpis is not None:
                    k = self._review_kpis
                    mins_s = (
                        f"{k.focused_minutes:.1f}m"
                        if k.focused_minutes < 10
                        else f"{int(round(k.focused_minutes))}m"
                    )
                    parts.append(
                        "## Session KPIs\n"
                        f"- **Focus Score:** {k.focus_score_pct}%\n"
                        f"- **Focused Time:** {mins_s}\n"
                        f"- **Distractions:** {k.distractions_count}\n"
                        f"- **Overrides:** {k.overrides_count}"
                    )
                if self._review_raw_text.strip():
                    parts.append(self._review_raw_text.rstrip())
                if self._review_closed_tabs:
                    vault_lines = ["## Closed Tabs Vault"]
                    for entry in self._review_closed_tabs:
                        safe_title = (entry.title or entry.domain or entry.url).replace("[", "(").replace("]", ")")
                        vault_lines.append(f"- [{safe_title}]({entry.url})")
                    parts.append("\n".join(vault_lines))
                export_md = "\n\n".join(parts) + "\n"
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(export_md)
                export_btn.configure(text="Saved!")
                self.root.after(
                    1500,
                    lambda: export_btn.configure(text="Save .md") if export_btn.winfo_exists() else None,
                )
            except Exception:
                logger.exception("Failed exporting review markdown")

        export_btn = _make_button(
            btn_row,
            text="Save .md",
            command=_handle_export_md,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=12,
            pady=7,
            height=34,
            palette=pal,
        )
        export_btn.pack(side="left", padx=(8, 0))
        self._review_export_btn = export_btn  # type: ignore[assignment]

        close_btn = _make_button(
            btn_row,
            text="Done / Close",
            command=on_close,
            variant="primary",
            font=ui_font(11, "bold"),
            padx=16,
            pady=7,
            height=34,
            palette=pal,
        )
        close_btn.pack(side="right")

        if on_new_session is not None:
            new_btn = _make_button(
                btn_row,
                text="New Session",
                command=on_new_session,
                variant="secondary",
                font=ui_font(11, "bold"),
                padx=14,
                pady=7,
                height=34,
                palette=pal,
            )
            new_btn.pack(side="right", padx=(0, 8))

        # Scrollable review card (direct child of _current_frame so tk.Text stays within 2 levels)
        txt_wrap = tk.Frame(
            frame,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border_strong"],
        )
        txt_wrap.pack(fill="both", expand=True)

        txt = tk.Text(
            txt_wrap,
            wrap="word",
            width=66,
            height=15,
            font=mono_font(10) if "```" in review_text else ui_font(11),
            bg=pal["surface"],
            fg=pal["text"],
            insertbackground=pal["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=20,
            pady=16,
            spacing1=3,
            spacing2=4,
            spacing3=3,
        )
        sb = ctk.CTkScrollbar(
            txt_wrap,
            orientation="vertical",
            command=txt.yview,
            fg_color="transparent",
            button_color=pal["border_strong"],
            button_hover_color=pal["text_muted"],
            width=10,
        )
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y", padx=(0, 3), pady=4)
        setattr(txt, "frame", txt_wrap)
        txt.tag_configure("h1", font=ui_font(15, "bold", display=True), foreground=pal["h1"], spacing1=10, spacing3=5)
        txt.tag_configure("h2", font=ui_font(12, "bold", display=True), foreground=pal["h2"], spacing1=10, spacing3=4)
        txt.tag_configure("h3", font=ui_font(11, "bold"), foreground=pal["text"], spacing1=7, spacing3=3)
        txt.tag_configure("bold", font=ui_font(11, "bold"), foreground=pal["text"])
        txt.tag_configure("bullet", lmargin1=16, lmargin2=30, spacing1=3, spacing3=2)

        self._render_markdown_into_text(txt, review_text)
        self._review_text_widget = txt

        self._fit_window(self.root, min_w=660, min_h=540, pos="center", pad_w=12, pad_h=12, allow_resize=True)

        if not was_withdrawn:
            def _clear_topmost() -> None:
                if self._screen_gen == gen:
                    try:
                        self.root.attributes("-topmost", False)
                    except Exception:
                        pass

            self.root.after(150, _clear_topmost)

    def _hide_popups_and_overlay_now(self) -> None:
        """Synchronously close active popups and strict banner on the Tk main thread."""
        if self._strict_countdown_after_id is not None and self._strict_overlay_win is not None:
            try:
                self._strict_overlay_win.after_cancel(self._strict_countdown_after_id)
            except Exception:
                pass
            self._strict_countdown_after_id = None

        for seq in ("<Escape>", "<F12>"):
            bind_id = getattr(self, f"_strict_root_bind_{seq}", None)
            if bind_id:
                try:
                    self.root.unbind(seq, bind_id)
                except Exception:
                    pass
                setattr(self, f"_strict_root_bind_{seq}", None)

        for popup in list(self._active_popups):
            try:
                for attr in ("_auto_close_id", "_countdown_id"):
                    aid = getattr(popup, attr, None)
                    if aid is not None:
                        try:
                            popup.after_cancel(aid)
                        except Exception:
                            pass
                if popup.winfo_exists():
                    popup.destroy()
            except Exception:
                pass
        self._active_popups.clear()
        self._tier0_popup = None

        if self._strict_overlay_win is not None:
            try:
                if self._strict_overlay_win.winfo_exists():
                    self._strict_overlay_win.destroy()
            except Exception:
                pass
            self._strict_overlay_win = None

    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant", "allow_domain"]], None],
        *,
        source_label: str | None = None,
    ) -> None:
        """Display non-focus-stealing Layer 1 nudge on the active monitor's bottom-right work area."""

        def _action() -> None:
            pal = self._active_palette()
            self._hide_popups_and_overlay_now()

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — Quick Check")
            win.overrideredirect(True)
            win.configure(bg=pal["surface"])
            win.attributes("-topmost", True)
            win.resizable(False, False)
            apply_brand_icon(self.root, win)

            shell = tk.Frame(win, bg=pal["surface"])
            shell.pack(fill="both", expand=True)

            card = SurfaceCard(
                shell,
                palette=pal,
                fg_color=pal["surface"],
                border_color=pal["border_strong"],
                corner_radius=0,
                border_width=1,
            )
            card.pack(fill="both", expand=True)

            # Left 3px semantic amber rail
            tk.Frame(card, bg=pal["warning"], width=4).pack(side="left", fill="y")

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="both", expand=True, padx=16, pady=14)

            hdr = ctk.CTkFrame(body, fg_color="transparent")
            hdr.pack(fill="x", pady=(0, 8))

            ModernLabel(
                hdr,
                text="Quick focus check",
                font=ui_font(13, "bold", display=True),
                text_color=pal["text"],
            ).pack(side="left")

            StatusPill(
                hdr,
                text="Check-in",
                bg=pal["warning_soft"],
                fg=pal["warning"],
                border=pal["warning_border"],
                dot_color=pal["warning"],
                font=ui_font(9, "bold"),
                padx=7,
                pady=1,
            ).pack(side="right")

            ModernLabel(
                body,
                text=f"Goal: {goal}",
                font=ui_font(11, "bold"),
                text_color=pal["text"],
                wraplength=self._s(370),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 4))

            ModernLabel(
                body,
                text=reason or "Current activity looks off-task.",
                font=ui_font(10),
                text_color=pal["text_secondary"],
                wraplength=self._s(370),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 14))

            def _choose(choice: Literal["back_to_work", "relevant", "allow_domain"]) -> None:
                self._hide_popups_and_overlay_now()
                try:
                    on_choice(choice)
                except Exception:
                    logger.exception("Error in Layer 1 on_choice callback")

            if source_label and source_label.strip():
                clean_src = source_label.strip()
                allow_btn = _make_button(
                    body,
                    text=f"Always allow {clean_src} for this session",
                    command=lambda: _choose("allow_domain"),
                    variant="ghost",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=4,
                    height=28,
                    palette=pal,
                )
                allow_btn.pack(fill="x", side="bottom", pady=(6, 0))
                setattr(win, "_allow_domain_btn", allow_btn)

            btn_row = ctk.CTkFrame(body, fg_color="transparent")
            btn_row.pack(fill="x", side="bottom")
            btn_row.columnconfigure(0, weight=1, uniform="l1")
            btn_row.columnconfigure(1, weight=1, uniform="l1")

            back_btn = _make_button(
                btn_row,
                text="Back to work",
                command=lambda: _choose("back_to_work"),
                variant="primary",
                font=ui_font(11, "bold"),
                padx=12,
                pady=7,
                height=34,
                palette=pal,
            )
            back_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

            rel_btn = _make_button(
                btn_row,
                text="This is relevant",
                command=lambda: _choose("relevant"),
                variant="secondary",
                font=ui_font(11, "bold"),
                padx=12,
                pady=7,
                height=34,
                palette=pal,
            )
            rel_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

            win.bind("<Escape>", lambda _e: self._hide_popups_and_overlay_now())
            win.protocol("WM_DELETE_WINDOW", self._hide_popups_and_overlay_now)
            self._active_popups.append(win)

            mon_rect = self._get_hud_monitor_work_area()
            self._fit_window(
                win,
                min_w=430,
                min_h=175,
                pos="bottom_right",
                monitor_rect=mon_rect,
                pad_w=8,
                pad_h=8,
                allow_resize=False,
            )
            apply_rounded_corners(win)
            apply_noactivate_topmost(win)
            fade_in_window(win)

        self.dispatch(_action)

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        """Display non-focus-stealing Layer 2 Normal-mode warning popup on the HUD's monitor."""

        def _action() -> None:
            pal = self._active_palette()
            self._hide_popups_and_overlay_now()

            if not self._mute_sounds:
                try:
                    self.root.bell()
                except Exception:
                    pass

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — OFF-TASK WARNING")
            win.overrideredirect(True)
            win.configure(bg=pal["surface"])
            win.attributes("-topmost", True)
            win.resizable(False, False)
            apply_brand_icon(self.root, win)

            shell = tk.Frame(win, bg=pal["surface"])
            shell.pack(fill="both", expand=True)

            card = SurfaceCard(
                shell,
                palette=pal,
                fg_color=pal["surface"],
                border_color=pal["danger_border"],
                corner_radius=0,
                border_width=1,
            )
            card.pack(fill="both", expand=True)

            tk.Frame(card, bg=pal["danger_solid"], width=4).pack(side="left", fill="y")

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="both", expand=True, padx=16, pady=14)

            hdr = ctk.CTkFrame(body, fg_color="transparent")
            hdr.pack(fill="x", pady=(0, 8))

            ModernLabel(
                hdr,
                text="Off-task warning",
                font=ui_font(13, "bold", display=True),
                text_color=pal["danger"],
            ).pack(side="left")

            StatusPill(
                hdr,
                text="Alert",
                bg=pal["danger_soft"],
                fg=pal["danger"],
                border=pal["danger_border"],
                dot_color=pal["danger_solid"],
                font=ui_font(9, "bold"),
                padx=7,
                pady=1,
            ).pack(side="right")

            ModernLabel(
                body,
                text=f"Return to your goal: {goal}",
                font=ui_font(11, "bold"),
                text_color=pal["text"],
                wraplength=self._s(400),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 4))

            ModernLabel(
                body,
                text=reason or "Distraction detected.",
                font=ui_font(10),
                text_color=pal["text_secondary"],
                wraplength=self._s(400),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 14))

            footer = ctk.CTkFrame(body, fg_color="transparent")
            footer.pack(fill="x", side="bottom")

            countdown_lbl = ModernLabel(
                footer,
                text="Auto-closes in 12s",
                font=mono_font(10),
                text_color=pal["text_muted"],
            )
            countdown_lbl.pack(side="left")

            dismiss_btn = _make_button(
                footer,
                text="Dismiss — Back to Work",
                command=self._hide_popups_and_overlay_now,
                variant="danger",
                font=ui_font(11, "bold"),
                padx=14,
                pady=6,
                height=32,
                palette=pal,
            )
            dismiss_btn.pack(side="right")

            def _auto_close() -> None:
                try:
                    if win.winfo_exists():
                        if win in self._active_popups:
                            self._active_popups.remove(win)
                        win.destroy()
                except Exception:
                    pass

            close_deadline = time.monotonic() + 12.0

            def _tick_countdown() -> None:
                try:
                    if not win.winfo_exists():
                        return
                    secs = max(0, int(math.ceil(close_deadline - time.monotonic())))
                    countdown_lbl.configure(text=f"Auto-closes in {secs}s")
                    if secs > 0:
                        win._countdown_id = win.after(1000, _tick_countdown)  # type: ignore[attr-defined]
                except Exception:
                    pass

            win._auto_close_id = win.after(12000, _auto_close)  # type: ignore[attr-defined]
            win._countdown_id = win.after(1000, _tick_countdown)  # type: ignore[attr-defined]
            win.bind("<Escape>", lambda _e: self._hide_popups_and_overlay_now())
            win.protocol("WM_DELETE_WINDOW", self._hide_popups_and_overlay_now)
            self._active_popups.append(win)

            mon_rect = self._get_hud_monitor_work_area()
            self._fit_window(
                win,
                min_w=450,
                min_h=175,
                pos="bottom_right",
                monitor_rect=mon_rect,
                pad_w=8,
                pad_h=8,
                allow_resize=False,
            )
            apply_rounded_corners(win)
            apply_noactivate_topmost(win)
            fade_in_window(win)

        self.dispatch(_action)

    def show_tier0_shift_popup(self, message: str) -> None:
        """Display a non-focus-stealing notice that AI is offline and Tier 0 rules are active."""

        def _action() -> None:
            pal = self._active_palette()
            if self._tier0_popup is not None:
                try:
                    if self._tier0_popup in self._active_popups:
                        self._active_popups.remove(self._tier0_popup)
                    if self._tier0_popup.winfo_exists():
                        self._tier0_popup.destroy()
                except Exception:
                    pass
                self._tier0_popup = None

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — AI Shift Notice")
            win.overrideredirect(True)
            win.configure(bg=pal["surface"])
            win.attributes("-topmost", True)
            win.resizable(False, False)
            apply_brand_icon(self.root, win)

            shell = tk.Frame(win, bg=pal["surface"])
            shell.pack(fill="both", expand=True)

            card = SurfaceCard(
                shell,
                palette=pal,
                fg_color=pal["surface"],
                border_color=pal["danger_border"],
                corner_radius=0,
                border_width=1,
            )
            card.pack(fill="both", expand=True)

            tk.Frame(card, bg=pal["danger_solid"], width=4).pack(side="left", fill="y")

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="both", expand=True, padx=16, pady=14)

            hdr = ctk.CTkFrame(body, fg_color="transparent")
            hdr.pack(fill="x", pady=(0, 8))

            ModernLabel(
                hdr,
                text="AI unavailable — Local rules active",
                font=ui_font(12, "bold", display=True),
                text_color=pal["danger"],
            ).pack(side="left")

            StatusPill(
                hdr,
                text="Tier 0",
                bg=pal["danger_soft"],
                fg=pal["danger"],
                border=pal["danger_border"],
                dot_color=pal["danger_solid"],
                font=ui_font(9, "bold"),
                padx=7,
                pady=1,
            ).pack(side="right")

            ModernLabel(
                body,
                text=(
                    message
                    or "All AI models are currently unreachable or rate-limited. "
                    "Anti-Distractor shifted to Tier 0 local rules (domain and app allow/block lists) "
                    "so your focus protection continues uninterrupted."
                ),
                font=ui_font(10),
                text_color=pal["text_secondary"],
                wraplength=self._s(410),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 14))

            def _close_tier0() -> None:
                try:
                    if win in self._active_popups:
                        self._active_popups.remove(win)
                    if self._tier0_popup is win:
                        self._tier0_popup = None
                    if win.winfo_exists():
                        win.destroy()
                except Exception:
                    pass

            btn_row = ctk.CTkFrame(body, fg_color="transparent")
            btn_row.pack(fill="x", side="bottom")

            btn = _make_button(
                btn_row,
                text="Understood",
                command=_close_tier0,
                variant="primary",
                font=ui_font(11, "bold"),
                padx=14,
                pady=6,
                height=32,
                palette=pal,
            )
            btn.pack(side="right")

            win.bind("<Escape>", lambda _e: _close_tier0())
            win.protocol("WM_DELETE_WINDOW", _close_tier0)
            self._tier0_popup = win
            self._active_popups.append(win)

            mon_rect = self._get_hud_monitor_work_area()
            self._fit_window(
                win,
                min_w=460,
                min_h=175,
                pos="bottom_right",
                monitor_rect=mon_rect,
                pad_w=8,
                pad_h=8,
                allow_resize=False,
            )
            apply_rounded_corners(win)
            apply_noactivate_topmost(win)
            fade_in_window(win)

        self.dispatch(_action)

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        """Display compact, non-focus-stealing Strict-mode banner on the active monitor (not full screen)."""

        def _action() -> None:
            pal = DARK_PALETTE
            self._hide_popups_and_overlay_now()

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — Strict Banner")
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.resizable(False, False)
            win.configure(bg=pal["surface"])
            self._strict_overlay_win = win

            shell = tk.Frame(win, bg=pal["surface"])
            shell.pack(fill="both", expand=True)

            card = SurfaceCard(
                shell,
                palette=pal,
                fg_color=pal["surface"],
                border_color=pal["danger_border"],
                corner_radius=0,
                border_width=1,
            )
            card.pack(fill="both", expand=True)

            tk.Frame(card, bg=pal["danger_solid"], width=4).pack(side="left", fill="y")

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="both", expand=True, padx=16, pady=14)

            hdr = ctk.CTkFrame(body, fg_color="transparent")
            hdr.pack(fill="x", pady=(0, 8))

            StatusPill(
                hdr,
                text="Strict lockout",
                bg=pal["danger_soft"],
                fg=pal["danger"],
                border=pal["danger_border"],
                dot_color=pal["danger_solid"],
                font=ui_font(10, "bold"),
                padx=8,
                pady=2,
            ).pack(side="left")

            countdown_lbl = ModernLabel(
                hdr,
                text="",
                font=mono_font(14, "bold"),
                text_color=pal["warning"],
            )
            countdown_lbl.pack(side="right")

            ModernLabel(
                body,
                text=f"Return to {goal}",
                font=ui_font(13, "bold", display=True),
                text_color=pal["text"],
                wraplength=self._s(410),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 4))

            ModernLabel(
                body,
                text=reason or "Distracting tab closed — stay locked on your focus goal.",
                font=ui_font(10),
                text_color=pal["text_secondary"],
                wraplength=self._s(410),
                justify="left",
                anchor="w",
            ).pack(anchor="w", pady=(0, 12))

            progress = ModernProgressBar(
                body,
                palette=pal,
                progress_color=pal["warning"],
                height=5,
                mode="determinate",
            )
            progress.set(1.0)
            progress.pack(fill="x", pady=(0, 12))

            completed = False

            def _finish_lockout() -> None:
                nonlocal completed
                if completed:
                    return
                completed = True
                self._hide_popups_and_overlay_now()
                try:
                    on_lockout_done()
                except Exception:
                    logger.exception("Error in strict banner on_lockout_done callback")

            footer = ctk.CTkFrame(body, fg_color="transparent")
            footer.pack(fill="x")

            ModernLabel(
                footer,
                text="Press Esc or F12 to dismiss",
                font=ui_font(10),
                text_color=pal["text_muted"],
            ).pack(side="left")

            escape_btn = _make_button(
                footer,
                text="Dismiss (Esc / F12)",
                command=_finish_lockout,
                variant="secondary",
                font=ui_font(10, "bold"),
                padx=12,
                pady=5,
                height=30,
                palette=pal,
            )
            escape_btn.pack(side="right")

            win.bind("<Escape>", lambda _event: _finish_lockout())
            win.bind("<F12>", lambda _event: _finish_lockout())
            try:
                setattr(self, "_strict_root_bind_<Escape>", self.root.bind("<Escape>", lambda _e: _finish_lockout(), add="+"))
                setattr(self, "_strict_root_bind_<F12>", self.root.bind("<F12>", lambda _e: _finish_lockout(), add="+"))
            except Exception:
                pass
            win.protocol("WM_DELETE_WINDOW", _finish_lockout)

            mon_rect = self._get_hud_monitor_work_area()
            self._fit_window(
                win,
                min_w=460,
                min_h=170,
                pos="bottom_right",
                monitor_rect=mon_rect,
                pad_w=8,
                pad_h=8,
                allow_resize=False,
            )
            apply_rounded_corners(win)
            apply_noactivate_topmost(win)
            fade_in_window(win)

            total_secs = max(0.1, float(lockout_seconds))
            deadline = time.monotonic() + max(0.0, float(lockout_seconds))

            def _tick() -> None:
                if completed or not win.winfo_exists():
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    _finish_lockout()
                    return
                secs = math.ceil(remaining)
                countdown_lbl.configure(text=f"{secs}s")
                ratio = max(0.0, min(1.0, remaining / total_secs))
                try:
                    progress.set(ratio)
                except Exception:
                    pass
                self._strict_countdown_after_id = win.after(150, _tick)

            _tick()

        self.dispatch(_action)

    def hide_popups_and_overlay(self) -> None:
        """Dismiss active Layer 1/2 popups or strict-mode overlay (thread-safe)."""
        self.dispatch(self._hide_popups_and_overlay_now)


if __name__ == "__main__":
    ui = TkinterUI()

    def on_end():
        sample_review = (
            "## Session Review: DSA Study\n\n"
            "- **On-Task Score:** 88% (22/25 checks on-task)\n"
            "- **Highlights:** Solid focus on LeetCode dynamic programming and tree traversals.\n"
            "- **Distractions:** 1 brief YouTube distraction intercepted and closed promptly.\n"
        )
        ui.show_review(sample_review, on_close=ui.close, on_new_session=lambda: ui.show_setup(on_start=on_start))

    def on_start(goal: str, duration: int, mode: ModeType):
        print(f"Session started: Goal={goal!r}, Duration={duration}m, Mode={mode!r}")
        ui.show_monitoring_hud(goal, mode, on_end_session=on_end, duration_minutes=duration)
        ui.root.after(1500, lambda: ui.update_hud_status("GRACE_PERIOD", "YouTube tab detected"))
        ui.root.after(3500, lambda: ui.update_hud_status("ON_TASK", "Back to LeetCode"))

    ui.show_setup(on_start=on_start)
    ui.run()
