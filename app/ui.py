"""Tkinter UI components: setup, monitoring HUD, Layer 1/2 popups, strict overlay, rules editor, tray, and review."""

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
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable, Literal, Protocol

from app.models import AgentStateName, GoalProfile, ModeType
from app.profile import normalize_domain
from app.tray import SystemTrayManager

logger = logging.getLogger(__name__)

ThemeType = Literal["light", "dark"]


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
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
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


# Unified Slate/Indigo/Semantic Light & Dark palettes matching extension/popup.html
LIGHT_PALETTE: dict[str, str] = {
    "bg": "#F1F5F9",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "text": "#0F172A",
    "text_secondary": "#475569",
    "text_muted": "#64748B",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "primary_soft": "#EFF6FF",
    "primary_border": "#BFDBFE",
    "danger": "#C62828",
    "danger_hover": "#B71C1C",
    "danger_soft": "#FEF2F2",
    "danger_border": "#FECACA",
    "warning": "#D97706",
    "warning_soft": "#FFFBEB",
    "warning_border": "#FDE68A",
    "success": "#16A34A",
    "success_soft": "#F0FDF4",
    "success_border": "#BBF7D0",
    "ext_off": "#94A3B8",
    "btn_secondary_bg": "#E2E8F0",
    "btn_secondary_hover": "#CBD5E1",
    "h1": "#1E3A8A",
    "h2": "#1D4ED8",
    "overlay_bg": "#0B1120",
    "overlay_card": "#1E293B",
    "overlay_border": "#334155",
    "overlay_text": "#F8FAFC",
    "overlay_reason": "#FCA5A5",
    "overlay_countdown": "#FBBF24",
}

DARK_PALETTE: dict[str, str] = {
    "bg": "#0F172A",
    "surface": "#1E293B",
    "surface_alt": "#162032",
    "border": "#334155",
    "border_strong": "#475569",
    "text": "#F8FAFC",
    "text_secondary": "#CBD5E1",
    "text_muted": "#94A3B8",
    "primary": "#3B82F6",
    "primary_hover": "#2563EB",
    "primary_soft": "#172554",
    "primary_border": "#1D4ED8",
    "danger": "#F87171",
    "danger_hover": "#EF4444",
    "danger_soft": "#450A0A",
    "danger_border": "#7F1D1D",
    "warning": "#F59E0B",
    "warning_soft": "#451A03",
    "warning_border": "#78350F",
    "success": "#22C55E",
    "success_soft": "#052E16",
    "success_border": "#14532D",
    "ext_off": "#64748B",
    "btn_secondary_bg": "#334155",
    "btn_secondary_hover": "#475569",
    "h1": "#93C5FD",
    "h2": "#60A5FA",
    "overlay_bg": "#0B1120",
    "overlay_card": "#1E293B",
    "overlay_border": "#334155",
    "overlay_text": "#F8FAFC",
    "overlay_reason": "#FCA5A5",
    "overlay_countdown": "#FBBF24",
}

PALETTE: dict[str, str] = LIGHT_PALETTE

STATE_COLORS: dict[AgentStateName, tuple[str, str]] = {
    "IDLE": ("#64748B", "#FFFFFF"),
    "ON_TASK": ("#15803D", "#FFFFFF"),
    "GRACE_PERIOD": ("#D97706", "#FFFFFF"),
    "LAYER_1_ASK": ("#EA580C", "#FFFFFF"),
    "LAYER_2_ULTIMATUM": ("#C62828", "#FFFFFF"),
    "ENDED": ("#334155", "#FFFFFF"),
}


def _bind_hover(
    widget: tk.Widget,
    normal_bg: str,
    hover_bg: str,
    normal_fg: str | None = None,
    hover_fg: str | None = None,
) -> None:
    """Attach clean hover color transitions to a flat tk.Button or tk.Label."""

    def _on_enter(_event: tk.Event) -> None:
        try:
            if str(widget.cget("state")) == "disabled":
                return
            widget.configure(bg=hover_bg)
            if hover_fg is not None:
                widget.configure(fg=hover_fg)
        except Exception:
            pass

    def _on_leave(_event: tk.Event) -> None:
        try:
            widget.configure(bg=normal_bg)
            if normal_fg is not None:
                widget.configure(fg=normal_fg)
        except Exception:
            pass

    widget.bind("<Enter>", _on_enter, add="+")
    widget.bind("<Leave>", _on_leave, add="+")


def _make_button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    *,
    variant: Literal["primary", "secondary", "danger", "danger_outline", "warning_outline", "ghost", "chip"] = "secondary",
    font: tuple[str, int] | tuple[str, int, str] = ("Segoe UI", 9, "bold"),
    padx: int = 14,
    pady: int = 7,
    bind_hover: bool = True,
    palette: dict[str, str] | None = None,
) -> tk.Button:
    """Create a consistently styled flat modern tk.Button with hover feedback."""
    pal = palette or PALETTE
    if variant == "primary":
        bg, hover_bg, fg = pal["primary"], pal["primary_hover"], "#FFFFFF"
        bd_color = pal["primary"]
    elif variant == "danger":
        bg, hover_bg, fg = pal["danger"], pal["danger_hover"], "#FFFFFF"
        bd_color = pal["danger"]
    elif variant == "danger_outline":
        bg, hover_bg, fg = pal["danger_soft"], pal["danger_border"], pal["danger"]
        bd_color = pal["danger_border"]
    elif variant == "warning_outline":
        bg, hover_bg, fg = pal["warning_soft"], pal["warning_border"], pal["warning"]
        bd_color = pal["warning_border"]
    elif variant == "ghost":
        bg, hover_bg, fg = pal["surface"], pal["btn_secondary_hover"], pal["text_secondary"]
        bd_color = pal["surface"]
    elif variant == "chip":
        bg, hover_bg, fg = pal["surface_alt"], pal["primary_soft"], pal["text_secondary"]
        bd_color = pal["border_strong"]
    else:  # secondary
        bg, hover_bg, fg = pal["btn_secondary_bg"], pal["btn_secondary_hover"], pal["text"]
        bd_color = pal["border_strong"]

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        font=font,
        bg=bg,
        fg=fg,
        activebackground=hover_bg,
        activeforeground=fg,
        disabledforeground="#E2E8F0" if variant in ("primary", "danger") else pal["text_muted"],
        relief="flat",
        bd=0,
        highlightthickness=1 if variant in ("secondary", "danger_outline", "warning_outline", "chip") else 0,
        highlightbackground=bd_color,
        highlightcolor=pal["primary"],
        padx=padx,
        pady=pady,
        cursor="hand2",
    )
    if bind_hover:
        _bind_hover(btn, bg, hover_bg)
    return btn


class TkinterUI:
    """Thread-safe Tkinter implementation of UIHooks running on the main thread."""

    def __init__(
        self,
        root: tk.Tk | None = None,
        tray_factory: Callable[..., SystemTrayManager] | None = None,
    ) -> None:
        self._owns_root = root is None
        self.root = root if root is not None else tk.Tk()
        self._main_thread_id = threading.get_ident()
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._tray_factory = tray_factory or SystemTrayManager
        self._tray: SystemTrayManager | None = None

        self._theme: ThemeType = "light"
        self.on_theme_changed: Callable[[ThemeType], None] | None = None

        self._configure_theme()

        self._active_screen: Literal["none", "setup", "hud", "review"] = "none"
        self._current_frame: tk.Frame | None = None
        self._theme_btn: tk.Button | None = None

        # Setup live state for seamless theme switches
        self._setup_on_start: Callable[[str, int, ModeType], None] | None = None
        self._setup_on_edit_rules: Callable[[str], None] | None = None
        self._setup_goal_var: tk.StringVar | None = None
        self._setup_dur_var: tk.StringVar | None = None
        self._setup_mode_var: tk.StringVar | None = None

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
        self._hud_tray_btn: tk.Button | None = None

        self._hud_pinned: bool = True
        self._hud_deadline: float | None = None
        self._hud_session_remaining_on_pause: float | None = None
        self._hud_timer_after_id: str | None = None
        self._hud_paused: bool = False
        self._hud_pause_remaining: float = 0.0
        self._hud_ext_connected: bool = False
        self._hud_last_goal: str = ""
        self._hud_last_mode: ModeType = "strict"
        self._hud_last_state: AgentStateName = "ON_TASK"
        self._hud_last_summary: str = "Monitoring active."
        self._hud_t1_count: int = 0
        self._hud_t2_count: int = 0
        self._hud_callbacks: dict[str, Any] = {}

        # Review live state & widgets
        self._review_text_widget: tk.Text | None = None
        self._review_sub_label: tk.Label | None = None
        self._review_copy_btn: tk.Button | None = None
        self._review_raw_text: str = ""
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

    def _configure_theme(self) -> None:
        """Apply consistent colors to root and ttk widgets for the active theme."""
        pal = self._active_palette()
        try:
            self.root.configure(bg=pal["bg"])
        except Exception:
            pass
        try:
            style = ttk.Style(self.root)
            if "clam" in style.theme_names():
                style.theme_use("clam")
            style.configure(
                "Modern.TEntry",
                fieldbackground=pal["surface"],
                foreground=pal["text"],
                insertcolor=pal["text"],
                bordercolor=pal["border_strong"],
                lightcolor=pal["border_strong"],
                darkcolor=pal["border_strong"],
                padding=(10, 7),
            )
            style.map(
                "Modern.TEntry",
                bordercolor=[("focus", pal["primary"])],
                lightcolor=[("focus", pal["primary"])],
                darkcolor=[("focus", pal["primary"])],
            )
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

            if self._active_screen == "setup" and self._setup_on_start is not None:
                cur_goal = self._setup_goal_var.get() if self._setup_goal_var is not None else None
                cur_dur_raw = self._setup_dur_var.get() if self._setup_dur_var is not None else None
                try:
                    cur_dur = int(cur_dur_raw.strip()) if cur_dur_raw else None
                except ValueError:
                    cur_dur = None
                cur_mode = self._setup_mode_var.get() if self._setup_mode_var is not None else None
                resolved_mode: ModeType | None = cur_mode if cur_mode in ("strict", "normal") else None  # type: ignore[assignment]
                self._render_setup_now(
                    self._setup_on_start,
                    initial_goal=cur_goal,
                    initial_duration=cur_dur,
                    initial_mode=resolved_mode,
                    on_edit_rules=self._setup_on_edit_rules,
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
        pad_w: int = 8,
        pad_h: int = 8,
        allow_resize: bool = True,
    ) -> tuple[int, int]:
        """Compute DPI-aware, content-fitting geometry so buttons/labels never clip."""
        win.update_idletasks()
        screen_w = max(640, win.winfo_screenwidth())
        screen_h = max(480, win.winfo_screenheight())

        req_w = win.winfo_reqwidth()
        req_h = win.winfo_reqheight()

        scaled_w = self._s(min_w)
        scaled_h = self._s(min_h)

        target_w = min(screen_w - 40, max(scaled_w, req_w + pad_w))
        target_h = min(screen_h - 80, max(scaled_h, req_h + pad_h))

        if explicit_pos is not None:
            x = max(0, min(screen_w - target_w, explicit_pos[0]))
            y = max(0, min(screen_h - target_h, explicit_pos[1]))
        else:
            margin = self._s(20)
            taskbar_pad = self._s(68)
            if pos == "top_right":
                x = max(12, screen_w - target_w - margin)
                y = margin
            elif pos == "bottom_right":
                x = max(12, screen_w - target_w - margin)
                y = max(12, screen_h - target_h - taskbar_pad)
            elif pos == "upper_center":
                x = max(12, (screen_w - target_w) // 2)
                y = max(32, (screen_h - target_h) // 3)
            else:  # center
                x = max(12, (screen_w - target_w) // 2)
                y = max(24, (screen_h - target_h) // 2 - self._s(16))

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

    def close(self) -> None:
        """Safely cancel timers, stop tray, destroy popups, and tear down Tk on the main thread."""

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
            if self._rules_editor_win is not None:
                try:
                    if self._rules_editor_win.winfo_exists():
                        self._rules_editor_win.destroy()
                except Exception:
                    pass
                self._rules_editor_win = None
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

    def _clear_root_widgets(self) -> None:
        """Remove all existing children from the root window and reset screen-specific refs."""
        self._cancel_hud_timer()
        self._screen_gen += 1
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self._current_frame = None
        self._theme_btn = None
        self._review_text_widget = None
        self._review_sub_label = None
        self._review_copy_btn = None

    def show_setup(
        self,
        on_start: Callable[[str, int, ModeType], None],
        initial_goal: str | None = None,
        initial_duration: int | None = None,
        initial_mode: ModeType | None = None,
        on_edit_rules: Callable[[str], None] | None = None,
    ) -> None:
        """Render the goal/duration/mode setup screen with 'I'm ready' action."""
        self.dispatch(
            lambda: self._render_setup_now(
                on_start,
                initial_goal=initial_goal,
                initial_duration=initial_duration,
                initial_mode=initial_mode,
                on_edit_rules=on_edit_rules,
            )
        )

    def _render_setup_now(
        self,
        on_start: Callable[[str, int, ModeType], None],
        initial_goal: str | None = None,
        initial_duration: int | None = None,
        initial_mode: ModeType | None = None,
        on_edit_rules: Callable[[str], None] | None = None,
    ) -> None:
        pal = self._active_palette()
        self._hide_popups_and_overlay_now()
        self._clear_root_widgets()
        self._active_screen = "setup"
        self._setup_on_start = on_start
        self._setup_on_edit_rules = on_edit_rules

        self.root.title("Anti-Distractor — Session Setup")
        self.root.configure(bg=pal["bg"])
        self._reset_window_state(self.root, topmost=False, resizable=(True, True))

        outer = tk.Frame(self.root, bg=pal["bg"], padx=22, pady=18)
        outer.pack(fill="both", expand=True)
        self._current_frame = outer

        # Header block
        header = tk.Frame(outer, bg=pal["bg"])
        header.pack(fill="x", pady=(0, 14))

        title_row = tk.Frame(header, bg=pal["bg"])
        title_row.pack(fill="x")

        tk.Label(
            title_row,
            text="Anti-Distractor",
            font=("Segoe UI", 17, "bold"),
            bg=pal["bg"],
            fg=pal["text"],
        ).pack(side="left")

        tk.Label(
            title_row,
            text="  FOCUS AGENT  ",
            font=("Segoe UI", 8, "bold"),
            bg=pal["primary_soft"],
            fg=pal["primary"],
            padx=6,
            pady=3,
        ).pack(side="left", padx=(10, 0), pady=(4, 0))

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        theme_btn = _make_button(
            title_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="secondary",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=3,
            palette=pal,
        )
        theme_btn.pack(side="right", pady=(2, 0))
        self._theme_btn = theme_btn

        tk.Label(
            header,
            text="Define your focus goal and enforcement style. Press Enter or click Start when ready.",
            font=("Segoe UI", 9),
            bg=pal["bg"],
            fg=pal["text_secondary"],
            wraplength=self._s(460),
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # Main card
        card = tk.Frame(
            outer,
            bg=pal["surface"],
            padx=20,
            pady=18,
            highlightthickness=1,
            highlightbackground=pal["border_strong"],
            highlightcolor=pal["border_strong"],
        )
        card.pack(fill="both", expand=True)

        # Goal input
        tk.Label(
            card,
            text="WHAT ARE YOU WORKING ON?",
            font=("Segoe UI", 8, "bold"),
            bg=pal["surface"],
            fg=pal["text_muted"],
        ).pack(anchor="w", pady=(0, 6))

        goal_val_default = initial_goal if initial_goal is not None else "Study Data Structures and Algorithms"
        goal_var = tk.StringVar(value=goal_val_default)
        self._setup_goal_var = goal_var
        goal_entry = ttk.Entry(card, textvariable=goal_var, font=("Segoe UI", 10), style="Modern.TEntry")
        goal_entry.pack(fill="x", pady=(0, 14))

        # Duration row + quick preset chips
        tk.Label(
            card,
            text="SESSION DURATION (MINUTES)",
            font=("Segoe UI", 8, "bold"),
            bg=pal["surface"],
            fg=pal["text_muted"],
        ).pack(anchor="w", pady=(0, 6))

        dur_row = tk.Frame(card, bg=pal["surface"])
        dur_row.pack(fill="x", pady=(0, 16))

        dur_val_default = str(initial_duration) if initial_duration is not None else "25"
        dur_var = tk.StringVar(value=dur_val_default)
        self._setup_dur_var = dur_var
        dur_entry = ttk.Entry(dur_row, textvariable=dur_var, width=7, font=("Segoe UI", 10), style="Modern.TEntry")
        dur_entry.pack(side="left")

        preset_btns: dict[int, tk.Button] = {}

        def _refresh_preset_styles(*_args: object) -> None:
            cur = dur_var.get().strip()
            for mins, pbtn in preset_btns.items():
                active = cur == str(mins)
                bg = pal["primary_soft"] if active else pal["surface_alt"]
                fg = pal["primary"] if active else pal["text_secondary"]
                bd = pal["primary"] if active else pal["border_strong"]
                pbtn.configure(bg=bg, fg=fg, highlightbackground=bd)

        def _select_preset(mins: int) -> None:
            dur_var.set(str(mins))
            _refresh_preset_styles()

        for mins in (15, 25, 45, 60):
            pbtn = _make_button(
                dur_row,
                text=f"{mins}m",
                command=lambda m=mins: _select_preset(m),
                variant="chip",
                font=("Segoe UI", 9, "bold"),
                padx=10,
                pady=4,
                bind_hover=False,
                palette=pal,
            )
            pbtn.bind("<Enter>", lambda _e, b=pbtn: b.configure(bg=pal["primary_soft"]))
            pbtn.bind("<Leave>", lambda _e: _refresh_preset_styles())
            pbtn.pack(side="left", padx=(8, 0))
            preset_btns[mins] = pbtn

        dur_var.trace_add("write", _refresh_preset_styles)
        _refresh_preset_styles()

        # Enforcement Mode selectable cards
        tk.Label(
            card,
            text="ENFORCEMENT MODE",
            font=("Segoe UI", 8, "bold"),
            bg=pal["surface"],
            fg=pal["text_muted"],
        ).pack(anchor="w", pady=(0, 8))

        mode_val_default = initial_mode if initial_mode in ("strict", "normal") else "strict"
        mode_var = tk.StringVar(value=mode_val_default)
        self._setup_mode_var = mode_var

        mode_cards: dict[str, tuple[tk.Frame, list[tk.Widget]]] = {}

        def _refresh_mode_cards(*_args: object) -> None:
            sel = mode_var.get()
            for m_val, (m_frame, children) in mode_cards.items():
                is_sel = m_val == sel
                if is_sel:
                    bg = pal["danger_soft"] if m_val == "strict" else pal["primary_soft"]
                    bd = pal["danger"] if m_val == "strict" else pal["primary"]
                else:
                    bg = pal["surface_alt"]
                    bd = pal["border"]
                m_frame.configure(bg=bg, highlightbackground=bd, highlightthickness=2 if is_sel else 1)
                for child in children:
                    try:
                        if not getattr(child, "_keep_bg", False):
                            child.configure(bg=bg)
                        if isinstance(child, tk.Radiobutton):
                            child.configure(activebackground=bg)
                    except Exception:
                        pass

        def _build_mode_card(
            value: ModeType,
            title: str,
            badge: str,
            badge_bg: str,
            badge_fg: str,
            desc: str,
        ) -> tk.Frame:
            m_card = tk.Frame(
                card,
                bg=pal["surface_alt"],
                padx=12,
                pady=10,
                highlightthickness=1,
                highlightbackground=pal["border"],
                cursor="hand2",
            )
            m_card.pack(fill="x", pady=(0, 8))

            top = tk.Frame(m_card, bg=pal["surface_alt"])
            top.pack(fill="x")

            rb = tk.Radiobutton(
                top,
                text=title,
                variable=mode_var,
                value=value,
                font=("Segoe UI", 10, "bold"),
                bg=pal["surface_alt"],
                fg=pal["text"],
                activebackground=pal["surface_alt"],
                activeforeground=pal["text"],
                selectcolor=pal["surface"],
                cursor="hand2",
            )
            rb.pack(side="left")

            tag = tk.Label(
                top,
                text=f" {badge} ",
                font=("Segoe UI", 7, "bold"),
                bg=badge_bg,
                fg=badge_fg,
                padx=5,
                pady=1,
            )
            tag._keep_bg = True  # type: ignore[attr-defined]
            tag.pack(side="right")

            desc_lbl = tk.Label(
                m_card,
                text=desc,
                font=("Segoe UI", 8),
                bg=pal["surface_alt"],
                fg=pal["text_secondary"],
                wraplength=self._s(420),
                justify="left",
            )
            desc_lbl.pack(anchor="w", padx=(22, 0), pady=(2, 0))

            def _pick(_e: tk.Event) -> None:
                mode_var.set(value)

            for w in (m_card, top, desc_lbl, tag):
                w.bind("<Button-1>", _pick)

            mode_cards[value] = (m_card, [top, rb, desc_lbl])
            return m_card

        _build_mode_card(
            "strict",
            "Strict Mode",
            "AUTO-ENFORCE",
            pal["danger_soft"],
            pal["danger"],
            "Closes off-task browser tabs, minimizes distracting apps, and locks screen briefly.",
        )
        _build_mode_card(
            "normal",
            "Normal Mode",
            "GENTLE NUDGES",
            pal["primary_soft"],
            pal["primary"],
            "Shows friendly check-in popups and timed warnings without closing tabs.",
        )

        mode_var.trace_add("write", _refresh_mode_cards)
        _refresh_mode_cards()

        # Footer inside card: inline validation status + optional Edit Rules + Start button
        status_row = tk.Frame(card, bg=pal["surface"])
        status_row.pack(fill="x", pady=(6, 10))

        status_lbl = tk.Label(
            status_row,
            text="Ready to begin your focus session.",
            font=("Segoe UI", 9),
            bg=pal["surface"],
            fg=pal["text_muted"],
            anchor="w",
            wraplength=self._s(340 if on_edit_rules is not None else 430),
            justify="left",
        )
        status_lbl.pack(side="left", fill="x", expand=True)
        self._setup_status_lbl = status_lbl

        if on_edit_rules is not None:
            setup_rules_btn = _make_button(
                status_row,
                text="Edit Rules",
                command=lambda: on_edit_rules(goal_var.get().strip()),
                variant="secondary",
                font=("Segoe UI", 8, "bold"),
                padx=10,
                pady=4,
                palette=pal,
            )
            setup_rules_btn.pack(side="right", padx=(8, 0))
            self._setup_rules_btn = setup_rules_btn
        else:
            self._setup_rules_btn = None

        def _set_status(msg: str, is_error: bool = False) -> None:
            status_lbl.configure(
                text=msg,
                fg=pal["danger"] if is_error else pal["text_muted"],
                font=("Segoe UI", 9, "bold") if is_error else ("Segoe UI", 9),
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
            start_btn.configure(state="disabled", text="Starting...", bg="#93C5FD")
            _set_status("Starting session and building your focus profile...")
            self.root.update_idletasks()
            try:
                on_start(goal_val, dur_val, selected_mode)
            except Exception as err:
                start_btn.configure(
                    state="normal",
                    text="I'm Ready — Start Session",
                    bg=pal["primary"],
                )
                _set_status(f"Failed to start: {err}", is_error=True)
                messagebox.showerror("Start Failed", f"Could not start session: {err}")

        start_btn = _make_button(
            card,
            text="I'm Ready — Start Session",
            command=_handle_start,
            variant="primary",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=10,
            palette=pal,
        )
        self._start_btn = start_btn
        self._setup_handle_start = _handle_start
        start_btn.pack(fill="x")

        goal_entry.bind("<Return>", lambda _e: _handle_start())
        dur_entry.bind("<Return>", lambda _e: _handle_start())
        goal_entry.focus_set()

        self._fit_window(self.root, min_w=500, min_h=500, pos="center", pad_w=12, pad_h=12, allow_resize=True)

    def show_monitoring_hud(
        self,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
        duration_minutes: int | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_open_rules: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        """Render the compact, non-maximizable floating monitoring HUD in the top-right corner."""
        self.dispatch(
            lambda: self._render_hud_now(
                goal=goal,
                mode=mode,
                on_end_session=on_end_session,
                duration_minutes=duration_minutes,
                on_toggle_pause=on_toggle_pause,
                on_open_rules=on_open_rules,
                on_quit=on_quit,
            )
        )

    def _render_hud_now(
        self,
        *,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
        duration_minutes: int | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_open_rules: Callable[[], None] | None = None,
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
            "on_quit": on_quit,
        }
        if not preserve_state:
            self._hud_pinned = True
            self._hud_paused = False
            self._hud_pause_remaining = 0.0
            self._hud_session_remaining_on_pause = None
            self._hud_last_state = "ON_TASK"
            self._hud_last_summary = "Monitoring active."
            self._hud_t1_count = 0
            self._hud_t2_count = 0

        self.root.title("Anti-Distractor — HUD")
        self.root.configure(bg=pal["border_strong"])

        # Always exit zoomed/fullscreen state and disable maximize so HUD never traps tabs
        self._reset_window_state(
            self.root,
            topmost=self._hud_pinned,
            resizable=(False, False),
        )

        frame = tk.Frame(
            self.root,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border_strong"],
        )
        frame.pack(fill="both", expand=True)
        self._current_frame = frame

        # Smooth dragging (root coords in real UI + widget coords in headless tests)
        self._drag_data: dict[str, int | None] = {
            "x": 0,
            "y": 0,
            "win_x": 0,
            "win_y": 0,
            "use_root": 0,
        }

        def _start_drag(event: tk.Event) -> None:
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
            screen_w = max(640, self.root.winfo_screenwidth())
            screen_h = max(480, self.root.winfo_screenheight())
            win_w = max(100, self.root.winfo_width())
            win_h = max(60, self.root.winfo_height())

            if self._drag_data.get("use_root"):
                dx = (getattr(event, "x_root", 0) or 0) - (self._drag_data["x"] or 0)
                dy = (getattr(event, "y_root", 0) or 0) - (self._drag_data["y"] or 0)
                new_x = (self._drag_data["win_x"] or 0) + dx
                new_y = (self._drag_data["win_y"] or 0) + dy
            else:
                new_x = self.root.winfo_x() + ((getattr(event, "x", 0) or 0) - (self._drag_data["x"] or 0))
                new_y = self.root.winfo_y() + ((getattr(event, "y", 0) or 0) - (self._drag_data["y"] or 0))

            clamped_x = max(0, min(screen_w - win_w, new_x))
            clamped_y = max(0, min(screen_h - win_h, new_y))
            self.root.geometry(f"+{clamped_x}+{clamped_y}")

        self._hud_start_drag = _start_drag
        self._hud_on_drag = _on_drag

        # Top header bar
        top_row = tk.Frame(frame, bg=pal["surface"], padx=12, pady=8)
        top_row.pack(fill="x")

        grip_lbl = tk.Label(
            top_row,
            text="⋮⋮",
            font=("Segoe UI", 9, "bold"),
            bg=pal["surface"],
            fg=pal["border_strong"],
            cursor="fleur",
        )
        grip_lbl.pack(side="left", padx=(0, 6))

        goal_display = goal if len(goal) <= 24 else f"{goal[:21]}..."
        self._hud_goal_label = tk.Label(
            top_row,
            text=goal_display,
            font=("Segoe UI", 10, "bold"),
            bg=pal["surface"],
            fg=pal["text"],
            anchor="w",
            cursor="fleur",
        )
        self._hud_goal_label.pack(side="left", fill="x", expand=True)

        min_btn = _make_button(
            top_row,
            text="—",
            command=lambda: self.root.iconify(),
            variant="ghost",
            font=("Segoe UI", 8, "bold"),
            padx=5,
            pady=1,
            palette=pal,
        )
        min_btn.pack(side="right", padx=(3, 0))

        self._hud_tray_btn = _make_button(
            top_row,
            text="Tray",
            command=self.minimize_to_tray,
            variant="ghost",
            font=("Segoe UI", 7, "bold"),
            padx=5,
            pady=2,
            palette=pal,
        )
        self._hud_tray_btn.pack(side="right", padx=(3, 0))

        def _toggle_pin() -> None:
            self._hud_pinned = not self._hud_pinned
            try:
                self.root.attributes("-topmost", self._hud_pinned)
            except Exception:
                pass
            if self._hud_pin_btn is not None and self._hud_pin_btn.winfo_exists():
                if self._hud_pinned:
                    self._hud_pin_btn.configure(
                        text="Pinned",
                        bg=pal["primary_soft"],
                        fg=pal["primary"],
                    )
                else:
                    self._hud_pin_btn.configure(
                        text="Unpinned",
                        bg=pal["surface_alt"],
                        fg=pal["text_muted"],
                    )

        pin_bg = pal["primary_soft"] if self._hud_pinned else pal["surface_alt"]
        pin_fg = pal["primary"] if self._hud_pinned else pal["text_muted"]
        self._hud_pin_btn = tk.Button(
            top_row,
            text="Pinned" if self._hud_pinned else "Unpinned",
            command=_toggle_pin,
            font=("Segoe UI", 7, "bold"),
            bg=pin_bg,
            fg=pin_fg,
            activebackground=pal["primary_border"],
            activeforeground=pal["primary"],
            relief="flat",
            bd=0,
            padx=6,
            pady=2,
            cursor="hand2",
        )
        self._hud_pin_btn.pack(side="right", padx=(4, 0))

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        self._theme_btn = _make_button(
            top_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="ghost",
            font=("Segoe UI", 7, "bold"),
            padx=5,
            pady=2,
            palette=pal,
        )
        self._theme_btn.pack(side="right", padx=(4, 0))

        mode_text = f"[{mode.upper()}]"
        mode_bg = pal["primary_soft"] if mode == "normal" else pal["danger_soft"]
        mode_fg = pal["primary"] if mode == "normal" else pal["danger"]
        self._hud_mode_label = tk.Label(
            top_row,
            text=mode_text,
            font=("Segoe UI", 8, "bold"),
            bg=mode_bg,
            fg=mode_fg,
            padx=6,
            pady=2,
        )
        self._hud_mode_label.pack(side="right", padx=(4, 0))

        # Middle status row: colored state pill + live activity summary
        status_row = tk.Frame(frame, bg=pal["surface"], padx=12, pady=4)
        status_row.pack(fill="x")

        if self._hud_paused:
            pill_text = " PAUSED "
            pill_bg, pill_fg = ("#D97706", "#FFFFFF")
        else:
            pill_text = f" {self._hud_last_state} "
            pill_bg, pill_fg = STATE_COLORS.get(self._hud_last_state, ("#64748B", "#FFFFFF"))

        self._hud_state_label = tk.Label(
            status_row,
            text=pill_text,
            font=("Segoe UI", 8, "bold"),
            bg=pill_bg,
            fg=pill_fg,
            padx=7,
            pady=3,
            relief="flat",
        )
        self._hud_state_label.pack(side="left", anchor="n")

        summary_fg = pal["danger"] if "AI unavailable" in self._hud_last_summary else pal["text_secondary"]
        summary_font = ("Segoe UI", 9, "bold") if "AI unavailable" in self._hud_last_summary else ("Segoe UI", 9)
        self._hud_summary_label = tk.Label(
            status_row,
            text=self._hud_last_summary,
            font=summary_font,
            bg=pal["surface"],
            fg=summary_fg,
            wraplength=self._s(310),
            justify="left",
            anchor="w",
        )
        self._hud_summary_label.pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Divider line
        tk.Frame(frame, bg=pal["border"], height=1).pack(fill="x", pady=(6, 0))

        # Compact Footer: Telemetry line (timer, AI calls, Ext dot) + Action strip (Rules, Pause 5m, End)
        footer = tk.Frame(frame, bg=pal["surface_alt"], padx=12, pady=7)
        footer.pack(fill="x", side="bottom")

        meta_row = tk.Frame(footer, bg=pal["surface_alt"])
        meta_row.pack(fill="x", pady=(0, 6))

        self._hud_timer_label = tk.Label(
            meta_row,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg=pal["surface_alt"],
            fg=pal["warning"] if self._hud_paused else pal["text"],
            anchor="w",
        )
        if saved_deadline is not None:
            self._hud_deadline = saved_deadline
        elif duration_minutes is not None and duration_minutes > 0:
            self._hud_deadline = time.monotonic() + duration_minutes * 60.0

        if self._hud_paused:
            p_mins, p_secs = divmod(max(0, int(math.ceil(self._hud_pause_remaining))), 60)
            self._hud_timer_label.configure(text=f"Paused {p_mins:02d}:{p_secs:02d}")
            self._hud_timer_label.pack(side="left", padx=(0, 10))
        elif self._hud_deadline is not None:
            self._hud_timer_label.pack(side="left", padx=(0, 10))
            self._tick_hud_timer()

        self._hud_ai_calls_label = tk.Label(
            meta_row,
            text=f"AI calls: T1 {self._hud_t1_count} / T2 {self._hud_t2_count}",
            font=("Segoe UI", 8),
            bg=pal["surface_alt"],
            fg=pal["text_muted"],
            anchor="w",
        )
        self._hud_ai_calls_label.pack(side="left")

        ext_box = tk.Frame(meta_row, bg=pal["surface_alt"])
        ext_box.pack(side="right")

        ext_dot_color = pal["success"] if self._hud_ext_connected else pal["ext_off"]
        ext_text = "Ext: Live" if self._hud_ext_connected else "Ext: Off"
        ext_fg = pal["success"] if self._hud_ext_connected else pal["text_muted"]
        self._hud_ext_dot = tk.Label(
            ext_box,
            text="●",
            font=("Segoe UI", 8),
            bg=pal["surface_alt"],
            fg=ext_dot_color,
        )
        self._hud_ext_dot.pack(side="left", padx=(0, 3))
        self._hud_ext_label = tk.Label(
            ext_box,
            text=ext_text,
            font=("Segoe UI", 8, "bold") if self._hud_ext_connected else ("Segoe UI", 8),
            bg=pal["surface_alt"],
            fg=ext_fg,
        )
        self._hud_ext_label.pack(side="left")

        actions_row = tk.Frame(footer, bg=pal["surface_alt"])
        actions_row.pack(fill="x")

        if on_open_rules is not None:
            self._hud_rules_btn = _make_button(
                actions_row,
                text="Rules",
                command=on_open_rules,
                variant="secondary",
                font=("Segoe UI", 8, "bold"),
                padx=10,
                pady=4,
                palette=pal,
            )
            self._hud_rules_btn.pack(side="left")
        else:
            self._hud_rules_btn = None

        end_btn = _make_button(
            actions_row,
            text="End Session",
            command=on_end_session,
            variant="danger_outline",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=4,
            palette=pal,
        )
        end_btn.pack(side="right")

        if on_toggle_pause is not None:
            self._hud_pause_btn = _make_button(
                actions_row,
                text="Resume" if self._hud_paused else "Pause 5m",
                command=on_toggle_pause,
                variant="warning_outline" if self._hud_paused else "secondary",
                font=("Segoe UI", 8, "bold"),
                padx=10,
                pady=4,
                palette=pal,
            )
            self._hud_pause_btn.pack(side="right", padx=(0, 6))
        else:
            self._hud_pause_btn = None

        # Bind smooth drag to all non-button HUD elements
        for w in (
            frame,
            top_row,
            grip_lbl,
            self._hud_goal_label,
            self._hud_mode_label,
            status_row,
            self._hud_state_label,
            self._hud_summary_label,
            footer,
            meta_row,
            self._hud_timer_label,
            self._hud_ai_calls_label,
            ext_box,
            self._hud_ext_dot,
            self._hud_ext_label,
            actions_row,
        ):
            w.bind("<Button-1>", _start_drag)
            w.bind("<B1-Motion>", _on_drag)

        self._fit_window(
            self.root,
            min_w=420,
            min_h=136,
            pos="top_right",
            explicit_pos=preserve_pos,
            pad_w=8,
            pad_h=6,
            allow_resize=False,
        )

    def _tick_hud_timer(self) -> None:
        """Update the HUD remaining-time countdown once per second while not paused."""
        self._hud_timer_after_id = None
        if self._closed or self._hud_deadline is None or self._hud_paused:
            return
        if self._hud_timer_label is None or not self._hud_timer_label.winfo_exists():
            return
        remaining = max(0, int(math.ceil(self._hud_deadline - time.monotonic())))
        mins, secs = divmod(remaining, 60)
        self._hud_timer_label.configure(text=f"{mins:02d}:{secs:02d} left", fg=self._p("text"))
        if remaining > 0:
            try:
                self._hud_timer_after_id = self.root.after(1000, self._tick_hud_timer)
            except Exception:
                pass

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
                self._hud_ai_calls_label.config(text=f"AI calls: T1 {t1_count} / T2 {t2_count}")

        self.dispatch(_action)

    def update_extension_status(self, connected: bool) -> None:
        """Update the HUD extension connection indicator dot (thread-safe)."""

        def _action() -> None:
            self._hud_ext_connected = bool(connected)
            pal = self._active_palette()
            if self._hud_ext_dot is not None and self._hud_ext_dot.winfo_exists():
                self._hud_ext_dot.configure(fg=pal["success"] if connected else pal["ext_off"])
            if self._hud_ext_label is not None and self._hud_ext_label.winfo_exists():
                self._hud_ext_label.configure(
                    text="Ext: Live" if connected else "Ext: Off",
                    fg=pal["success"] if connected else pal["text_muted"],
                    font=("Segoe UI", 8, "bold") if connected else ("Segoe UI", 8),
                )

        self.dispatch(_action)

    def update_pause_status(self, paused: bool, remaining_seconds: float = 0.0) -> None:
        """Update HUD controls and countdown for Pause/Resume state (thread-safe)."""

        def _action() -> None:
            pal = self._active_palette()
            was_paused = self._hud_paused
            self._hud_paused = bool(paused)
            self._hud_pause_remaining = max(0.0, float(remaining_seconds))

            if paused:
                if not was_paused:
                    # Snapshot remaining session time so session timer visibly pauses cleanly
                    if self._hud_deadline is not None:
                        self._hud_session_remaining_on_pause = max(0.0, self._hud_deadline - time.monotonic())
                    if self._hud_timer_after_id is not None:
                        try:
                            self.root.after_cancel(self._hud_timer_after_id)
                        except Exception:
                            pass
                        self._hud_timer_after_id = None

                if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                    self._hud_state_label.configure(text=" PAUSED ", bg="#D97706", fg="#FFFFFF")
                if self._hud_summary_label is not None and self._hud_summary_label.winfo_exists():
                    self._hud_summary_label.configure(
                        text="Break active — monitoring paused.",
                        font=("Segoe UI", 9),
                        fg=pal["text_secondary"],
                    )
                if self._hud_timer_label is not None and self._hud_timer_label.winfo_exists():
                    mins, secs = divmod(max(0, int(math.ceil(self._hud_pause_remaining))), 60)
                    self._hud_timer_label.configure(text=f"Paused {mins:02d}:{secs:02d}", fg=pal["warning"])
                    if not self._hud_timer_label.winfo_ismapped():
                        self._hud_timer_label.pack(side="left", padx=(0, 10))
                if self._hud_pause_btn is not None and self._hud_pause_btn.winfo_exists():
                    self._hud_pause_btn.configure(
                        text="Resume",
                        bg=pal["warning_soft"],
                        fg=pal["warning"],
                        highlightbackground=pal["warning_border"],
                    )
            else:
                if was_paused and self._hud_session_remaining_on_pause is not None:
                    self._hud_deadline = time.monotonic() + self._hud_session_remaining_on_pause
                    self._hud_session_remaining_on_pause = None
                if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                    bg, fg = STATE_COLORS.get(self._hud_last_state, ("#64748B", "#FFFFFF"))
                    self._hud_state_label.configure(text=f" {self._hud_last_state} ", bg=bg, fg=fg)
                if self._hud_pause_btn is not None and self._hud_pause_btn.winfo_exists():
                    self._hud_pause_btn.configure(
                        text="Pause 5m",
                        bg=pal["btn_secondary_bg"],
                        fg=pal["text"],
                        highlightbackground=pal["border_strong"],
                    )
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
            self._hud_last_state = state
            self._hud_last_summary = summary

            if not self._hud_paused:
                if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                    bg, fg = STATE_COLORS.get(state, ("#64748B", "#FFFFFF"))
                    self._hud_state_label.config(text=f" {state} ", bg=bg, fg=fg)

                if self._hud_summary_label is not None and self._hud_summary_label.winfo_exists():
                    display_summary = summary if len(summary) <= 85 else f"{summary[:82]}..."
                    self._hud_summary_label.config(text=display_summary)
                    if "AI unavailable" in summary:
                        self._hud_summary_label.config(font=("Segoe UI", 9, "bold"), foreground=pal["danger"])
                    else:
                        self._hud_summary_label.config(font=("Segoe UI", 9), foreground=pal["text_secondary"])

                    # Grow HUD height cleanly if summary wrapped onto 2 lines without shifting x/y
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

    def show_rules_editor(
        self,
        profile: GoalProfile,
        on_save: Callable[[GoalProfile], None],
    ) -> None:
        """Open a deduplicated, DPI-aware dialog to edit allow/borderline/block rules live."""

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
            self._rules_editor_win = win

            outer = tk.Frame(win, bg=pal["bg"], padx=18, pady=14)
            outer.pack(fill="both", expand=True)

            tk.Label(
                outer,
                text="Editable Domain & App Rules",
                font=("Segoe UI", 13, "bold"),
                bg=pal["bg"],
                fg=pal["text"],
            ).pack(anchor="w")

            tk.Label(
                outer,
                text=f"Goal: {profile.goal} — changes apply immediately without restarting.",
                font=("Segoe UI", 9),
                bg=pal["bg"],
                fg=pal["text_secondary"],
                wraplength=self._s(520),
                justify="left",
            ).pack(anchor="w", pady=(2, 10))

            # Tab switcher bar
            tab_bar = tk.Frame(outer, bg=pal["bg"])
            tab_bar.pack(fill="x", pady=(0, 8))

            content_holder = tk.Frame(
                outer,
                bg=pal["surface"],
                padx=14,
                pady=12,
                highlightthickness=1,
                highlightbackground=pal["border_strong"],
            )
            content_holder.pack(fill="both", expand=True)

            websites_frame = tk.Frame(content_holder, bg=pal["surface"])
            apps_frame = tk.Frame(content_holder, bg=pal["surface"])

            listboxes: dict[str, tk.Listbox] = {}
            entries: dict[str, ttk.Entry] = {}
            add_btns: dict[str, tk.Button] = {}
            remove_btns: dict[str, tk.Button] = {}

            def _make_section(
                parent: tk.Misc,
                key: str,
                heading: str,
                accent: str,
                items: list[str],
                is_domain: bool,
                placeholder: str,
            ) -> None:
                sec = tk.Frame(parent, bg=pal["surface"], pady=4)
                sec.pack(fill="both", expand=True, pady=(0, 6))

                tk.Label(
                    sec,
                    text=heading,
                    font=("Segoe UI", 9, "bold"),
                    bg=pal["surface"],
                    fg=accent,
                ).pack(anchor="w", pady=(0, 3))

                lb_wrap = tk.Frame(sec, bg=pal["surface"])
                lb_wrap.pack(fill="both", expand=True)

                lb = tk.Listbox(
                    lb_wrap,
                    height=4,
                    font=("Segoe UI", 9),
                    bg=pal["surface_alt"],
                    fg=pal["text"],
                    selectbackground=pal["primary"],
                    selectforeground="#FFFFFF",
                    relief="flat",
                    bd=0,
                    highlightthickness=1,
                    highlightbackground=pal["border"],
                )
                sb = tk.Scrollbar(lb_wrap, orient="vertical", command=lb.yview)
                lb.configure(yscrollcommand=sb.set)
                lb.pack(side="left", fill="both", expand=True)
                sb.pack(side="right", fill="y")

                for item in items:
                    if item:
                        lb.insert("end", item)
                listboxes[key] = lb

                ctrl = tk.Frame(sec, bg=pal["surface"], pady=4)
                ctrl.pack(fill="x")

                ent_var = tk.StringVar()
                ent = ttk.Entry(ctrl, textvariable=ent_var, font=("Segoe UI", 9), style="Modern.TEntry")
                ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
                entries[key] = ent

                def _add() -> None:
                    raw = ent_var.get().strip()
                    if not raw:
                        return
                    norm = normalize_domain(raw) if is_domain else raw.lower()
                    if not norm:
                        return
                    existing = set(lb.get(0, "end"))
                    if norm not in existing:
                        lb.insert("end", norm)
                    ent_var.set("")

                def _remove() -> None:
                    sel = list(lb.curselection())
                    for idx in reversed(sel):
                        lb.delete(idx)

                ent.bind("<Return>", lambda _e: _add())

                add_btn = _make_button(
                    ctrl,
                    text=f"+ Add {placeholder}",
                    command=_add,
                    variant="secondary",
                    font=("Segoe UI", 8, "bold"),
                    padx=8,
                    pady=4,
                    palette=pal,
                )
                add_btn.pack(side="left", padx=(0, 4))
                add_btns[key] = add_btn

                rem_btn = _make_button(
                    ctrl,
                    text="Remove",
                    command=_remove,
                    variant="danger_outline",
                    font=("Segoe UI", 8, "bold"),
                    padx=8,
                    pady=4,
                    palette=pal,
                )
                rem_btn.pack(side="left")
                remove_btns[key] = rem_btn

            _make_section(
                websites_frame,
                "allowed_domains",
                "ALLOWED WEBSITES (Always On-Task)",
                pal["success"],
                list(profile.allowed_domains),
                is_domain=True,
                placeholder="Domain",
            )
            _make_section(
                websites_frame,
                "borderline_domains",
                "BORDERLINE WEBSITES (Inspect Title / Screen)",
                pal["warning"],
                list(profile.borderline_domains),
                is_domain=True,
                placeholder="Domain",
            )
            _make_section(
                websites_frame,
                "blocked_domains",
                "BLOCKED WEBSITES (Instant Distraction)",
                pal["danger"],
                list(profile.blocked_domains),
                is_domain=True,
                placeholder="Domain",
            )

            _make_section(
                apps_frame,
                "allowed_apps",
                "ALLOWED DESKTOP APPS (e.g. code.exe)",
                pal["success"],
                list(profile.allowed_apps),
                is_domain=False,
                placeholder="App",
            )
            _make_section(
                apps_frame,
                "blocked_apps",
                "BLOCKED DESKTOP APPS (e.g. spotify.exe)",
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
                    tab_web_btn.configure(bg=pal["btn_secondary_bg"], fg=pal["text_secondary"])
                    tab_app_btn.configure(bg=pal["primary"], fg="#FFFFFF")
                else:
                    apps_frame.pack_forget()
                    websites_frame.pack(fill="both", expand=True)
                    tab_web_btn.configure(bg=pal["primary"], fg="#FFFFFF")
                    tab_app_btn.configure(bg=pal["btn_secondary_bg"], fg=pal["text_secondary"])

            tab_web_btn = _make_button(
                tab_bar,
                text="Websites",
                command=lambda: _show_tab("websites"),
                variant="primary",
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=5,
                bind_hover=False,
                palette=pal,
            )
            tab_web_btn.pack(side="left", padx=(0, 6))

            tab_app_btn = _make_button(
                tab_bar,
                text="Desktop Apps",
                command=lambda: _show_tab("apps"),
                variant="secondary",
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=5,
                bind_hover=False,
                palette=pal,
            )
            tab_app_btn.pack(side="left")

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
                    on_task_topics=list(profile.on_task_topics),
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

            footer = tk.Frame(outer, bg=pal["bg"])
            footer.pack(fill="x", side="bottom", pady=(10, 0))

            _make_button(
                footer,
                text="Cancel",
                command=_close,
                variant="secondary",
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=6,
                palette=pal,
            ).pack(side="left")

            save_btn = _make_button(
                footer,
                text="Save Rules",
                command=_save,
                variant="primary",
                font=("Segoe UI", 9, "bold"),
                padx=16,
                pady=6,
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
            }

            win.bind("<Escape>", lambda _e: _close())
            win.protocol("WM_DELETE_WINDOW", _close)
            self._fit_window(win, min_w=540, min_h=540, pos="center", pad_w=14, pad_h=14, allow_resize=True)

        self.dispatch(_action)

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

    def show_review(
        self,
        review_text: str,
        on_close: Callable[[], None],
        on_new_session: Callable[[], None] | None = None,
    ) -> None:
        """Display the end-of-session AI review screen (updating in-place if already open)."""
        self.dispatch(lambda: self._render_review_now(review_text, on_close, on_new_session=on_new_session))

    def _render_review_now(
        self,
        review_text: str,
        on_close: Callable[[], None],
        on_new_session: Callable[[], None] | None = None,
    ) -> None:
        pal = self._active_palette()
        self._review_callbacks = {
            "on_close": on_close,
            "on_new_session": on_new_session,
        }

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
                is_loading = review_text.strip().lower().startswith("generating")
                self._review_sub_label.configure(
                    text=(
                        "Compiling your focus metrics and session summary..."
                        if is_loading
                        else "Here is your focus summary, highlights, and model breakdown:"
                    )
                )
            return

        self._stop_tray()
        self._hide_popups_and_overlay_now()
        self._clear_root_widgets()
        self._active_screen = "review"
        gen = self._screen_gen

        self.root.title("Anti-Distractor — Session Review")
        self.root.configure(bg=pal["bg"])
        was_withdrawn = self._reset_window_state(
            self.root,
            topmost=False,
            resizable=(True, True),
            deiconify=True,
        )
        # Briefly raise above browser BEFORE _fit_window so Windows SetWindowPos
        # doesn't revert the centered geometry back to top-right HUD coordinates.
        if not was_withdrawn:
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                logger.exception("Failed to raise review window")

        frame = tk.Frame(self.root, bg=pal["bg"], padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        self._current_frame = frame

        # Header row + theme toggle
        hdr_row = tk.Frame(frame, bg=pal["bg"])
        hdr_row.pack(fill="x", pady=(0, 3))

        tk.Label(
            hdr_row,
            text="Focus Session Review",
            font=("Segoe UI", 16, "bold"),
            bg=pal["bg"],
            fg=pal["text"],
        ).pack(side="left")

        theme_lbl = "Light" if self._theme == "dark" else "Dark"
        self._theme_btn = _make_button(
            hdr_row,
            text=theme_lbl,
            command=self.toggle_theme,
            variant="secondary",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=3,
            palette=pal,
        )
        self._theme_btn.pack(side="right")

        is_loading = review_text.strip().lower().startswith("generating")
        sub_lbl = tk.Label(
            frame,
            text=(
                "Compiling your focus metrics and session summary..."
                if is_loading
                else "Here is your focus summary, highlights, and model breakdown:"
            ),
            font=("Segoe UI", 9),
            bg=pal["bg"],
            fg=pal["text_secondary"],
        )
        sub_lbl.pack(anchor="w", pady=(0, 12))
        self._review_sub_label = sub_lbl

        # Footer buttons packed side="bottom" first so they can NEVER be pushed off-screen
        btn_row = tk.Frame(frame, bg=pal["bg"])
        btn_row.pack(fill="x", side="bottom", pady=(12, 0))

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
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=7,
            palette=pal,
        )
        copy_btn.pack(side="left")
        self._review_copy_btn = copy_btn

        close_btn = _make_button(
            btn_row,
            text="Done / Close",
            command=on_close,
            variant="primary",
            font=("Segoe UI", 9, "bold"),
            padx=16,
            pady=7,
            palette=pal,
        )
        close_btn.pack(side="right")

        if on_new_session is not None:
            new_btn = _make_button(
                btn_row,
                text="New Session",
                command=on_new_session,
                variant="secondary",
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=7,
                palette=pal,
            )
            new_btn.pack(side="right", padx=(0, 8))

        # Scrollable review card (direct child of _current_frame so tk.Text stays within 2 levels)
        txt = scrolledtext.ScrolledText(
            frame,
            wrap="word",
            width=64,
            height=16,
            font=("Consolas" if "```" in review_text else "Segoe UI", 10),
            bg=pal["surface"],
            fg=pal["text"],
            insertbackground=pal["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=pal["border_strong"],
            padx=14,
            pady=12,
            spacing2=2,
        )
        txt.pack(fill="both", expand=True)
        txt.tag_configure("h1", font=("Segoe UI", 13, "bold"), foreground=pal["h1"], spacing1=8, spacing3=4)
        txt.tag_configure("h2", font=("Segoe UI", 11, "bold"), foreground=pal["h2"], spacing1=8, spacing3=3)
        txt.tag_configure("h3", font=("Segoe UI", 10, "bold"), foreground=pal["text"], spacing1=6, spacing3=2)
        txt.tag_configure("bold", font=("Segoe UI", 10, "bold"), foreground=pal["text"])
        txt.tag_configure("bullet", lmargin1=14, lmargin2=28, spacing1=2)

        self._render_markdown_into_text(txt, review_text)
        self._review_text_widget = txt

        self._fit_window(self.root, min_w=600, min_h=480, pos="center", pad_w=12, pad_h=12, allow_resize=True)

        if not was_withdrawn:
            def _clear_topmost() -> None:
                if self._screen_gen == gen:
                    try:
                        self.root.attributes("-topmost", False)
                    except Exception:
                        pass

            self.root.after(150, _clear_topmost)

    def _hide_popups_and_overlay_now(self) -> None:
        """Synchronously close active popups and strict overlay on the Tk main thread."""
        if self._strict_countdown_after_id is not None and self._strict_overlay_win is not None:
            try:
                self._strict_overlay_win.after_cancel(self._strict_countdown_after_id)
            except Exception:
                pass
            self._strict_countdown_after_id = None

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
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
    ) -> None:
        """Display Layer 1 'Are you studying <goal>?' popup (thread-safe; stays until answered)."""

        def _action() -> None:
            pal = self._active_palette()
            self._hide_popups_and_overlay_now()

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — Quick Check")
            win.configure(bg=pal["surface"])
            win.attributes("-topmost", True)
            win.resizable(False, False)

            tk.Frame(win, bg=pal["warning"], height=5).pack(fill="x")

            body = tk.Frame(win, bg=pal["surface"], padx=18, pady=14)
            body.pack(fill="both", expand=True)

            hdr = tk.Frame(body, bg=pal["surface"])
            hdr.pack(fill="x", pady=(0, 8))

            tk.Label(
                hdr,
                text="Quick Focus Check",
                font=("Segoe UI", 12, "bold"),
                bg=pal["surface"],
                fg=pal["warning"],
            ).pack(side="left")

            tk.Label(
                hdr,
                text="  NUDGE  ",
                font=("Segoe UI", 7, "bold"),
                bg=pal["warning_soft"],
                fg=pal["warning"],
                padx=5,
                pady=2,
            ).pack(side="right")

            tk.Label(
                body,
                text=f"Goal: {goal}",
                font=("Segoe UI", 10, "bold"),
                bg=pal["surface"],
                fg=pal["text"],
                wraplength=self._s(390),
                justify="left",
            ).pack(anchor="w", pady=(0, 4))

            tk.Label(
                body,
                text=reason or "Current activity looks off-task.",
                font=("Segoe UI", 9),
                bg=pal["surface"],
                fg=pal["text_secondary"],
                wraplength=self._s(390),
                justify="left",
            ).pack(anchor="w", pady=(0, 16))

            btn_row = tk.Frame(body, bg=pal["surface"])
            btn_row.pack(fill="x", side="bottom")

            def _choose(choice: Literal["back_to_work", "relevant"]) -> None:
                self._hide_popups_and_overlay_now()
                try:
                    on_choice(choice)
                except Exception:
                    logger.exception("Error in Layer 1 on_choice callback")

            back_btn = _make_button(
                btn_row,
                text="Back to work",
                command=lambda: _choose("back_to_work"),
                variant="primary",
                font=("Segoe UI", 9, "bold"),
                padx=12,
                pady=8,
                palette=pal,
            )
            back_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

            rel_btn = _make_button(
                btn_row,
                text="This is relevant",
                command=lambda: _choose("relevant"),
                variant="secondary",
                font=("Segoe UI", 9, "bold"),
                padx=12,
                pady=8,
                palette=pal,
            )
            rel_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))

            win.bind("<Escape>", lambda _e: self._hide_popups_and_overlay_now())
            win.protocol("WM_DELETE_WINDOW", self._hide_popups_and_overlay_now)
            self._active_popups.append(win)

            self._fit_window(win, min_w=430, min_h=190, pos="bottom_right", pad_w=10, pad_h=10, allow_resize=False)

        self.dispatch(_action)

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        """Display Layer 2 Normal-mode loud warning popup with live 12s auto-close countdown."""

        def _action() -> None:
            pal = self._active_palette()
            self._hide_popups_and_overlay_now()

            try:
                self.root.bell()
            except Exception:
                pass

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — OFF-TASK WARNING")
            win.configure(bg=pal["surface"])
            win.attributes("-topmost", True)
            win.resizable(False, False)

            banner_bg = "#C62828"
            banner = tk.Frame(win, bg=banner_bg, padx=18, pady=12)
            banner.pack(fill="x")
            tk.Label(
                banner,
                text="OFF-TASK WARNING",
                font=("Segoe UI", 13, "bold"),
                bg=banner_bg,
                fg="#FFFFFF",
            ).pack(anchor="w")

            body = tk.Frame(win, bg=pal["surface"], padx=18, pady=14)
            body.pack(fill="both", expand=True)

            tk.Label(
                body,
                text=f"Return to your goal: {goal}",
                font=("Segoe UI", 10, "bold"),
                bg=pal["surface"],
                fg=pal["text"],
                wraplength=self._s(420),
                justify="left",
            ).pack(anchor="w", pady=(0, 6))

            tk.Label(
                body,
                text=reason or "Distraction detected.",
                font=("Segoe UI", 9),
                bg=pal["surface"],
                fg=pal["text_secondary"],
                wraplength=self._s(420),
                justify="left",
            ).pack(anchor="w", pady=(0, 16))

            footer = tk.Frame(body, bg=pal["surface"])
            footer.pack(fill="x", side="bottom")

            countdown_lbl = tk.Label(
                footer,
                text="Auto-closes in 12s",
                font=("Segoe UI", 8),
                bg=pal["surface"],
                fg=pal["text_muted"],
            )
            countdown_lbl.pack(side="left")

            dismiss_btn = _make_button(
                footer,
                text="Dismiss — Back to Work",
                command=self._hide_popups_and_overlay_now,
                variant="danger",
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=7,
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
                        win._countdown_id = win.after(1000, _tick_countdown)
                except Exception:
                    pass

            win._auto_close_id = win.after(12000, _auto_close)
            win._countdown_id = win.after(1000, _tick_countdown)
            win.bind("<Escape>", lambda _e: self._hide_popups_and_overlay_now())
            win.protocol("WM_DELETE_WINDOW", self._hide_popups_and_overlay_now)
            self._active_popups.append(win)

            self._fit_window(win, min_w=460, min_h=215, pos="upper_center", pad_w=10, pad_h=10, allow_resize=False)

        self.dispatch(_action)

    def show_tier0_shift_popup(self, message: str) -> None:
        """Display a warning dialog that AI models are unavailable and shifting to Tier 0 rules only."""

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
            win.configure(bg=pal["surface"])
            win.attributes("-topmost", True)
            win.resizable(False, False)

            banner_bg = "#C62828"
            banner = tk.Frame(win, bg=banner_bg, padx=18, pady=12)
            banner.pack(fill="x")
            tk.Label(
                banner,
                text="AI Models Offline — Tier 0 Rules Active",
                font=("Segoe UI", 11, "bold"),
                bg=banner_bg,
                fg="#FFFFFF",
            ).pack(anchor="w")

            body = tk.Frame(win, bg=pal["surface"], padx=18, pady=14)
            body.pack(fill="both", expand=True)

            tk.Label(
                body,
                text=(
                    message
                    or "All AI models (Gemini 3.5 Flash-Lite, Gemma 31B, Gemma 4 26B A4B) are currently experiencing "
                    "quota limits or high demand.\n\n"
                    "Anti-Distractor has shifted to Tier 0 local rules only (allowed/blocked domain and app lists) "
                    "to maintain your focus protection until an AI model becomes available."
                ),
                font=("Segoe UI", 9),
                bg=pal["surface"],
                fg=pal["text_secondary"],
                wraplength=self._s(440),
                justify="left",
            ).pack(anchor="w", pady=(0, 16))

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

            btn_row = tk.Frame(body, bg=pal["surface"])
            btn_row.pack(fill="x", side="bottom")

            btn = _make_button(
                btn_row,
                text="Understood (Continue with Tier 0 Rules)",
                command=_close_tier0,
                variant="primary",
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=7,
                palette=pal,
            )
            btn.pack(side="right")

            win.bind("<Escape>", lambda _e: _close_tier0())
            win.protocol("WM_DELETE_WINDOW", _close_tier0)
            self._tier0_popup = win
            self._active_popups.append(win)

            self._fit_window(win, min_w=480, min_h=225, pos="upper_center", pad_w=12, pad_h=12, allow_resize=False)

        self.dispatch(_action)

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        """Display Strict-mode multi-monitor topmost overlay with countdown, progress bar, and Esc/F12 exit."""

        def _action() -> None:
            pal = LIGHT_PALETTE  # Strict overlay intentionally stays high-contrast dark
            self._hide_popups_and_overlay_now()

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — Strict Lockout")
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=pal["overlay_bg"])

            vx, vy, vw, vh, primary_w, primary_h = self._get_virtual_screen_bounds()
            win.geometry(f"{vw}x{vh}+{vx}+{vy}")
            self._strict_overlay_win = win

            card = tk.Frame(
                win,
                bg=pal["overlay_card"],
                padx=40,
                pady=34,
                highlightthickness=1,
                highlightbackground=pal["overlay_border"],
            )
            # Place card cleanly centered on the primary monitor (even when secondary monitors extend left/up)
            center_x = max(240, (-vx) + (primary_w // 2))
            center_y = max(180, (-vy) + (primary_h // 2))
            card.place(x=center_x, y=center_y, anchor="center")

            tk.Label(
                card,
                text="  STRICT FOCUS LOCKOUT  ",
                font=("Segoe UI", 9, "bold"),
                bg="#C62828",
                fg="#FFFFFF",
                padx=8,
                pady=3,
            ).pack(pady=(0, 16))

            max_wrap = max(360, min(self._s(640), primary_w - 160))
            tk.Label(
                card,
                text=f"Start {goal} again",
                font=("Segoe UI", 24, "bold"),
                bg=pal["overlay_card"],
                fg=pal["overlay_text"],
                wraplength=max_wrap,
                justify="center",
            ).pack(pady=(0, 10))

            tk.Label(
                card,
                text=reason or "Strict mode lockout active.",
                font=("Segoe UI", 12),
                bg=pal["overlay_card"],
                fg=pal["overlay_reason"],
                wraplength=max_wrap,
                justify="center",
            ).pack(pady=(0, 22))

            countdown_lbl = tk.Label(
                card,
                text="",
                font=("Segoe UI", 16, "bold"),
                bg=pal["overlay_card"],
                fg=pal["overlay_countdown"],
            )
            countdown_lbl.pack(pady=(0, 12))

            bar_w = self._s(340)
            bar_h = self._s(8)
            progress = tk.Canvas(
                card,
                width=bar_w,
                height=bar_h,
                bg="#0F172A",
                highlightthickness=0,
            )
            progress.pack(pady=(0, 26))
            bar_fill = progress.create_rectangle(0, 0, bar_w, bar_h, fill=pal["overlay_countdown"], width=0)

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
                    logger.exception("Error in strict overlay on_lockout_done callback")

            escape_btn = tk.Button(
                card,
                text="Emergency Exit (Esc / F12)",
                font=("Segoe UI", 9, "bold"),
                bg="#334155",
                fg="#E2E8F0",
                activebackground="#475569",
                activeforeground="#FFFFFF",
                relief="flat",
                bd=0,
                padx=16,
                pady=8,
                cursor="hand2",
                command=_finish_lockout,
            )
            _bind_hover(escape_btn, "#334155", "#475569")
            escape_btn.pack()

            win.bind("<Escape>", lambda _event: _finish_lockout())
            win.bind("<F12>", lambda _event: _finish_lockout())
            win.protocol("WM_DELETE_WINDOW", lambda: None)
            try:
                win.focus_force()
            except Exception:
                pass

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
                countdown_lbl.config(text=f"Lockout ends in {secs}s")
                ratio = max(0.0, min(1.0, remaining / total_secs))
                try:
                    progress.coords(bar_fill, 0, 0, int(bar_w * ratio), bar_h)
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
