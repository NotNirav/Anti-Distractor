"""Theme-aware secondary dialogs and hover ToolTip helper for Anti-Distractor."""

from __future__ import annotations

import ctypes
from datetime import datetime
import logging
import sys
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable
import webbrowser

import customtkinter as ctk

from app.models import ActivityFeedItem
from app.settings import AppSettings
from app.theme import (
    RADII,
    ModernEntry,
    ModernLabel,
    StatusPill,
    SurfaceCard,
    mono_font,
    ui_font,
)
from app.win32_ui import (
    apply_brand_icon,
    apply_dark_titlebar,
    apply_noactivate_topmost,
    apply_rounded_corners,
)

logger = logging.getLogger(__name__)


def _virtual_bounds_for_widget(widget: tk.Misc) -> tuple[int, int, int, int]:
    """Return (vx, vy, vw, vh) virtual desktop bounds for clamping tooltips."""
    try:
        sw = max(640, int(widget.winfo_screenwidth()))
        sh = max(480, int(widget.winfo_screenheight()))
    except Exception:
        sw, sh = 1920, 1080
    if sys.platform == "win32":
        try:
            u32 = ctypes.windll.user32
            vx = int(u32.GetSystemMetrics(76))
            vy = int(u32.GetSystemMetrics(77))
            vw = int(u32.GetSystemMetrics(78))
            vh = int(u32.GetSystemMetrics(79))
            if vw > 0 and vh > 0:
                return vx, vy, vw, vh
        except Exception:
            pass
    return 0, 0, sw, sh


class ToolTip:
    """Non-focus-stealing hover tooltip with configurable delay and multi-monitor clamping."""

    def __init__(
        self,
        widget: tk.Widget,
        text: str | Callable[[], str],
        *,
        delay_ms: int = 450,
    ) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip_win: tk.Toplevel | None = None

        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def resolve_text(self) -> str:
        try:
            return self.text() if callable(self.text) else str(self.text)
        except Exception:
            return ""

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        self._cancel_timer()
        try:
            self._after_id = self.widget.after(self.delay_ms, self.show_now)
        except Exception:
            self._after_id = None

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self.hide_now()

    def _on_destroy(self, _event: tk.Event | None = None) -> None:
        self.hide_now()

    def _cancel_timer(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def show_now(self, *, force_headless: bool = False) -> tk.Toplevel | None:
        """Render the tooltip window immediately (used after hover delay or in tests)."""
        self._cancel_timer()
        self.hide_now()

        msg = self.resolve_text().strip()
        if not msg:
            return None
        try:
            if not self.widget.winfo_exists():
                return None
            top = self.widget.winfo_toplevel()
            if not force_headless and top.state() == "withdrawn":
                return None
        except Exception:
            return None

        try:
            tip = tk.Toplevel(self.widget)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tip.configure(bg="#09090B")

            lbl = tk.Label(
                tip,
                text=msg,
                font=ui_font(10),
                bg="#09090B",
                fg="#FAFAFA",
                padx=10,
                pady=5,
                justify="left",
                wraplength=320,
                highlightthickness=1,
                highlightbackground="#27272A",
            )
            lbl.pack()
            tip.update_idletasks()

            tw = max(40, tip.winfo_reqwidth())
            th = max(20, tip.winfo_reqheight())
            wx = int(self.widget.winfo_rootx())
            wy = int(self.widget.winfo_rooty()) + int(self.widget.winfo_height()) + 6

            vx, vy, vw, vh = _virtual_bounds_for_widget(self.widget)
            cx = max(vx + 4, min(vx + vw - tw - 4, wx))
            cy = max(vy + 4, min(vy + vh - th - 4, wy))
            tip.geometry(f"{tw}x{th}+{cx}+{cy}")
            apply_rounded_corners(tip, small=True)
            apply_noactivate_topmost(tip)
            self._tip_win = tip
            return tip
        except Exception:
            logger.debug("Failed showing tooltip", exc_info=True)
            return None

    def hide_now(self) -> None:
        """Cancel timer and destroy any visible tooltip window."""
        self._cancel_timer()
        if self._tip_win is not None:
            try:
                if self._tip_win.winfo_exists():
                    self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None


class ActivityDrawerDialog:
    """Live explainability feed showing recent checks, tiers/models, reasons, and 1-click Allow."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        palette: dict[str, str],
        dark: bool,
        scale_fn: Callable[[int], int],
        fit_fn: Callable[..., Any],
        make_btn_fn: Callable[..., tk.Button],
        items: list[ActivityFeedItem],
        on_mark_relevant: Callable[[ActivityFeedItem], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.pal = palette
        self.dark = dark
        self._s = scale_fn
        self._fit = fit_fn
        self._make_btn = make_btn_fn
        self._on_mark_relevant = on_mark_relevant
        self._on_close = on_close
        self.relevant_buttons: list[tuple[ActivityFeedItem, tk.Button]] = []

        win = tk.Toplevel(root)
        win.title("Anti-Distractor — Live Activity Feed")
        win.configure(bg=palette["bg"])
        win.attributes("-topmost", True)
        apply_brand_icon(root, win)
        apply_dark_titlebar(win, dark)
        self.win = win

        outer = tk.Frame(win, bg=palette["bg"], padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 4))

        ModernLabel(
            hdr,
            text="Live Activity & Explainability",
            font=ui_font(16, "bold", display=True),
            text_color=palette["text"],
        ).pack(side="left")

        ModernLabel(
            outer,
            text="Recent checks (newest first) — inspect why an app or tab was passed or flagged.",
            font=ui_font(11),
            text_color=palette["text_secondary"],
            anchor="w",
        ).pack(fill="x", pady=(0, 12))

        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        self._make_btn(
            footer,
            text="Close",
            command=self.close,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=6,
            palette=palette,
        ).pack(side="right")

        # Scrollable feed container
        container = SurfaceCard(
            outer,
            palette=palette,
            fg_color=palette["surface"],
            border_color=palette["border_strong"],
            corner_radius=RADII["lg"],
            border_width=1,
        )
        container.pack(fill="both", expand=True)

        scroll_host = ctk.CTkScrollableFrame(
            container,
            fg_color="transparent",
            corner_radius=RADII["md"],
        )
        scroll_host.pack(fill="both", expand=True, padx=6, pady=6)
        self._rows_frame = scroll_host

        self.refresh(items)

        win.bind("<Escape>", lambda _e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._fit(win, min_w=580, min_h=450, pos="center", pad_w=12, pad_h=12, allow_resize=True)
        apply_dark_titlebar(win, dark)

    def refresh(self, items: list[ActivityFeedItem]) -> None:
        """Rebuild the visible activity rows in-place."""
        if not self.win.winfo_exists():
            return
        pal = self.pal
        self.relevant_buttons.clear()
        for child in self._rows_frame.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        recent = list(reversed(items[-15:]))
        if not recent:
            ModernLabel(
                self._rows_frame,
                text="No evaluations recorded in this session yet.",
                font=ui_font(11),
                text_color=pal["text_muted"],
            ).pack(fill="x", pady=24)
            return

        for item in recent:
            row = SurfaceCard(
                self._rows_frame,
                palette=pal,
                fg_color=pal["surface_alt"],
                border_color=pal["border"],
                corner_radius=RADII["md"],
                border_width=1,
            )
            row.pack(fill="x", pady=(0, 8), padx=4)

            row_in = ctk.CTkFrame(row, fg_color="transparent")
            row_in.pack(fill="x", padx=12, pady=10)

            top = ctk.CTkFrame(row_in, fg_color="transparent")
            top.pack(fill="x")

            ts_str = time.strftime("%H:%M:%S", time.localtime(item.timestamp or time.time()))
            ModernLabel(
                top,
                text=ts_str,
                font=mono_font(10),
                text_color=pal["text_muted"],
            ).pack(side="left", padx=(0, 8))

            if item.overridden:
                badge_txt, badge_bg, badge_fg, badge_bd = "Overridden", pal["success_soft"], pal["success"], pal["success_border"]
            elif item.on_task:
                badge_txt, badge_bg, badge_fg, badge_bd = "On-task", pal["success_soft"], pal["success"], pal["success_border"]
            else:
                badge_txt, badge_bg, badge_fg, badge_bd = "Flagged", pal["danger_soft"], pal["danger"], pal["danger_border"]

            StatusPill(
                top,
                text=badge_txt,
                bg=badge_bg,
                fg=badge_fg,
                border=badge_bd,
                dot_color=badge_fg,
                font=ui_font(9, "bold"),
                padx=7,
                pady=1,
            ).pack(side="left", padx=(0, 8))

            src_display = item.source_key or item.title or "Activity"
            if len(src_display) > 34:
                src_display = src_display[:31] + "..."
            ModernLabel(
                top,
                text=src_display,
                font=ui_font(11, "bold"),
                text_color=pal["text"],
            ).pack(side="left")

            model_short = item.model_used or ("Rules" if item.tier_used == 0 else f"Tier {item.tier_used}")
            conf_pct = int(round(max(0.0, min(1.0, float(item.confidence))) * 100))
            tier_txt = f"T{item.tier_used} · {model_short} · {conf_pct}%"
            ModernLabel(
                top,
                text=tier_txt,
                font=mono_font(9),
                text_color=pal["text_muted"],
            ).pack(side="right")

            bottom = ctk.CTkFrame(row_in, fg_color="transparent")
            bottom.pack(fill="x", pady=(6, 0))

            reason_txt = item.reason or ("On-task" if item.on_task else "Off-task detected")
            ModernLabel(
                bottom,
                text=reason_txt,
                font=ui_font(10),
                text_color=pal["text_secondary"],
                wraplength=self._s(370),
                justify="left",
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

            if not item.on_task and not item.overridden and self._on_mark_relevant is not None:
                cb = self._on_mark_relevant
                btn = self._make_btn(
                    bottom,
                    text="Mark Relevant / Allow",
                    command=lambda it=item: cb(it),
                    variant="secondary",
                    font=ui_font(10, "bold"),
                    padx=10,
                    pady=3,
                    palette=pal,
                )
                btn.pack(side="right", padx=(8, 0))
                self.relevant_buttons.append((item, btn))

    def close(self) -> None:
        try:
            if self.win.winfo_exists():
                self.win.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass


class HistoryAnalyticsDialog:
    """Past sessions history, daily focus streak, deep-work hours, and top distractions view."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        palette: dict[str, str],
        dark: bool,
        scale_fn: Callable[[int], int],
        fit_fn: Callable[..., Any],
        make_btn_fn: Callable[..., tk.Button],
        stats: dict[str, Any],
        sessions: list[dict[str, Any]],
        on_read_review: Callable[[dict[str, Any]], None] | None = None,
        on_repeat_session: Callable[[dict[str, Any]], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.pal = palette
        self._s = scale_fn
        self._fit = fit_fn
        self._make_btn = make_btn_fn
        self.sessions = list(sessions)
        self._on_read_review = on_read_review
        self._on_repeat_session = on_repeat_session
        self._on_close = on_close
        self.stat_labels: dict[str, tk.Label] = {}

        win = tk.Toplevel(root)
        win.title("Anti-Distractor — History & Focus Stats")
        win.configure(bg=palette["bg"])
        win.attributes("-topmost", True)
        apply_brand_icon(root, win)
        apply_dark_titlebar(win, dark)
        self.win = win

        outer = tk.Frame(win, bg=palette["bg"], padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        ModernLabel(
            outer,
            text="Session History & Focus Stats",
            font=ui_font(17, "bold", display=True),
            text_color=palette["text"],
        ).pack(anchor="w")

        ModernLabel(
            outer,
            text="Track your daily streak, deep-work hours, top distractions, and past reviews.",
            font=ui_font(11),
            text_color=palette["text_secondary"],
        ).pack(anchor="w", pady=(2, 12))

        # 4 KPI cards
        kpi_row = tk.Frame(outer, bg=palette["bg"])
        kpi_row.pack(fill="x", pady=(0, 10))

        streak_days = int(stats.get("current_streak_days", 0))
        total_hours = float(stats.get("total_focus_hours", 0.0))
        completed_cnt = int(stats.get("completed_sessions", 0))
        avg_score = int(stats.get("avg_focus_score_pct", 0))

        cards = [
            ("streak", "Daily streak", f"{streak_days}d", palette["primary"]),
            ("hours", "Deep-work hours", f"{total_hours:.1f}h", palette["success"]),
            ("sessions", "Sessions", f"{completed_cnt}", palette["text"]),
            ("avg_focus", "Avg focus", f"{avg_score}%", palette["success"] if avg_score >= 75 else palette["warning"]),
        ]
        for idx, (key, title, val, accent) in enumerate(cards):
            kpi_row.columnconfigure(idx, weight=1, uniform="hist_kpi")
            c = SurfaceCard(
                kpi_row,
                palette=palette,
                fg_color=palette["surface"],
                border_color=palette["border"],
                corner_radius=10,
                border_width=1,
            )
            c.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 8, 0))
            c_in = ctk.CTkFrame(c, fg_color="transparent")
            c_in.pack(fill="both", expand=True, padx=14, pady=10)
            ModernLabel(
                c_in,
                text=title,
                font=ui_font(10, "bold"),
                text_color=palette["text_muted"],
            ).pack(anchor="w")
            val_lbl = ModernLabel(
                c_in,
                text=val,
                font=ui_font(19, "bold", display=True),
                text_color=accent,
            )
            val_lbl.pack(anchor="w", pady=(2, 0))
            self.stat_labels[key] = val_lbl  # type: ignore[assignment]

        # Top distractions strip
        dist_card = SurfaceCard(
            outer,
            palette=palette,
            fg_color=palette["surface"],
            border_color=palette["border"],
            corner_radius=10,
            border_width=1,
        )
        dist_card.pack(fill="x", pady=(0, 10))
        dist_in = ctk.CTkFrame(dist_card, fg_color="transparent")
        dist_in.pack(fill="x", padx=14, pady=10)

        ModernLabel(
            dist_in,
            text="Top distracting sites / apps",
            font=ui_font(10, "bold"),
            text_color=palette["text_muted"],
        ).pack(anchor="w")

        top_dist: list[tuple[str, int]] = list(stats.get("top_distractions", []))
        if top_dist:
            dist_text = "   ·   ".join(f"{name} ({cnt})" for name, cnt in top_dist[:5])
        else:
            dist_text = "No distractions logged yet — clean sheet!"
        self.top_distractions_label = ModernLabel(
            dist_in,
            text=dist_text,
            font=ui_font(11, "bold" if top_dist else "normal"),
            text_color=palette["danger"] if top_dist else palette["text_secondary"],
            anchor="w",
            wraplength=self._s(540),
            justify="left",
        )
        self.top_distractions_label.pack(anchor="w", pady=(3, 0))

        # Footer buttons packed before expandable list so they never clip
        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.pack(fill="x", side="bottom", pady=(12, 0))

        self._make_btn(
            footer,
            text="Close",
            command=self.close,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=6,
            palette=palette,
        ).pack(side="left")

        self.repeat_btn = self._make_btn(
            footer,
            text="Repeat Goal",
            command=self._handle_repeat,
            variant="primary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=6,
            palette=palette,
        )
        self.repeat_btn.pack(side="right")

        self.read_btn = self._make_btn(
            footer,
            text="Read Review",
            command=self._handle_read_review,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=6,
            palette=palette,
        )
        self.read_btn.pack(side="right", padx=(0, 8))

        # Past sessions list
        list_card = SurfaceCard(
            outer,
            palette=palette,
            fg_color=palette["surface"],
            border_color=palette["border_strong"],
            corner_radius=RADII["lg"],
            border_width=1,
        )
        list_card.pack(fill="both", expand=True)

        list_in = ctk.CTkFrame(list_card, fg_color="transparent")
        list_in.pack(fill="both", expand=True, padx=14, pady=12)

        ModernLabel(
            list_in,
            text="Past sessions",
            font=ui_font(10, "bold"),
            text_color=palette["text_muted"],
        ).pack(anchor="w", pady=(0, 6))

        lb_wrap = tk.Frame(list_in, bg=palette["surface"])
        lb_wrap.pack(fill="both", expand=True)

        lb = tk.Listbox(
            lb_wrap,
            height=8,
            font=ui_font(10),
            bg=palette["surface_alt"],
            fg=palette["text"],
            selectbackground=palette["primary"],
            selectforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["border"],
        )
        sb = tk.Scrollbar(lb_wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox = lb

        for s in self.sessions:
            dt_str = datetime.fromtimestamp(float(s.get("started_at") or time.time())).strftime("%Y-%m-%d %H:%M")
            goal_str = str(s.get("goal", "Focus"))
            if len(goal_str) > 32:
                goal_str = goal_str[:29] + "..."
            dur = int(s.get("duration_minutes", 25))
            mode_str = str(s.get("mode", "strict")).upper()
            score = int(s.get("focus_score_pct", 100))
            status_tag = "" if s.get("ended_at") else " [incomplete]"
            lb.insert("end", f"{dt_str}  |  {goal_str}  ({dur}m · {mode_str} · {score}% focus){status_tag}")

        if self.sessions:
            lb.selection_set(0)
            lb.bind("<Double-Button-1>", lambda _e: self._handle_read_review())
        else:
            self.read_btn.configure(state="disabled")
            self.repeat_btn.configure(state="disabled")

        win.bind("<Escape>", lambda _e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._fit(win, min_w=640, min_h=500, pos="center", pad_w=12, pad_h=12, allow_resize=True)
        apply_dark_titlebar(win, dark)

    def _selected_session(self) -> dict[str, Any] | None:
        sel = self.listbox.curselection()
        if not sel:
            return self.sessions[0] if self.sessions else None
        idx = int(sel[0])
        if 0 <= idx < len(self.sessions):
            return self.sessions[idx]
        return None

    def _handle_read_review(self) -> None:
        sess = self._selected_session()
        if sess is not None and self._on_read_review is not None:
            self.close()
            self._on_read_review(sess)

    def _handle_repeat(self) -> None:
        sess = self._selected_session()
        if sess is not None and self._on_repeat_session is not None:
            self.close()
            self._on_repeat_session(sess)

    def close(self) -> None:
        try:
            if self.win.winfo_exists():
                self.win.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass


class SettingsDialog:
    """Compact Settings dialog for break duration, mute, sensitivity, hotkeys, Pomodoro, and API key."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        palette: dict[str, str],
        dark: bool,
        scale_fn: Callable[[int], int],
        fit_fn: Callable[..., Any],
        make_btn_fn: Callable[..., tk.Button],
        current_settings: AppSettings,
        api_key_status: tuple[bool, str],
        on_save_settings: Callable[[AppSettings], None],
        on_save_api_key: Callable[[str], tuple[bool, str]] | None = None,
        on_test_api_key: Callable[[str, Callable[[bool, str], None]], None] | None = None,
        on_clear_api_key: Callable[[], tuple[bool, str]] | None = None,
        focus_api_key: bool = False,
        url_opener: Callable[[str], Any] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.pal = palette
        self._s = scale_fn
        self._fit = fit_fn
        self._make_btn = make_btn_fn
        self._initial_settings = current_settings
        self._on_save_settings = on_save_settings
        self._on_save_api_key = on_save_api_key
        self._on_test_api_key = on_test_api_key
        self._on_clear_api_key = on_clear_api_key
        self._url_opener: Callable[[str], Any] = url_opener or webbrowser.open
        self._on_close = on_close
        self._show_key = False

        win = tk.Toplevel(root)
        win.title("Anti-Distractor — Settings")
        win.configure(bg=palette["bg"])
        win.attributes("-topmost", True)
        apply_brand_icon(root, win)
        apply_dark_titlebar(win, dark)
        self.win = win

        outer = tk.Frame(win, bg=palette["bg"], padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        ModernLabel(
            outer,
            text="Preferences & API Configuration",
            font=ui_font(17, "bold", display=True),
            text_color=palette["text"],
        ).pack(anchor="w")

        ModernLabel(
            outer,
            text="Customize break durations, detection timings, global shortcuts, and your Gemini API key.",
            font=ui_font(11),
            text_color=palette["text_secondary"],
        ).pack(anchor="w", pady=(2, 12))

        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.pack(fill="x", side="bottom", pady=(12, 0))

        self._make_btn(
            footer,
            text="Cancel",
            command=self.close,
            variant="secondary",
            font=ui_font(11, "bold"),
            padx=14,
            pady=6,
            palette=palette,
        ).pack(side="left")

        self.save_btn = self._make_btn(
            footer,
            text="Save Settings",
            command=self._handle_save,
            variant="primary",
            font=ui_font(11, "bold"),
            padx=16,
            pady=6,
            palette=palette,
        )
        self.save_btn.pack(side="right")

        card_shell = SurfaceCard(
            outer,
            palette=palette,
            fg_color=palette["surface"],
            border_color=palette["border_strong"],
            corner_radius=RADII["lg"],
            border_width=1,
        )
        card_shell.pack(fill="both", expand=True)
        card = ctk.CTkFrame(card_shell, fg_color="transparent")
        card.pack(fill="both", expand=True, padx=18, pady=16)

        # 1. Pause / Break length
        ModernLabel(
            card,
            text="Pause / break duration",
            font=ui_font(10, "bold"),
            text_color=palette["text_muted"],
        ).pack(anchor="w", pady=(0, 6))

        self.break_var = tk.IntVar(value=current_settings.break_minutes)
        brk_row = ctk.CTkFrame(card, fg_color="transparent")
        brk_row.pack(fill="x", pady=(0, 14))
        for mins in (5, 10, 15):
            ctk.CTkRadioButton(
                brk_row,
                text=f"{mins} minutes",
                variable=self.break_var,
                value=mins,
                font=ui_font(11),
                text_color=palette["text"],
                fg_color=palette["primary"],
                hover_color=palette["primary_hover"],
                border_color=palette["border_strong"],
                radiobutton_width=16,
                radiobutton_height=16,
            ).pack(side="left", padx=(0, 16))

        # 2. Sensitivity / Timing Preset
        ModernLabel(
            card,
            text="Detection sensitivity preset",
            font=ui_font(10, "bold"),
            text_color=palette["text_muted"],
        ).pack(anchor="w", pady=(0, 6))

        self.timing_var = tk.StringVar(value=current_settings.timing_preset)
        tim_row = ctk.CTkFrame(card, fg_color="transparent")
        tim_row.pack(fill="x", pady=(0, 14))
        for val, label in (
            ("dev", "Fast / Responsive (5s grace)"),
            ("demo", "Relaxed / Realistic (25s grace)"),
        ):
            ctk.CTkRadioButton(
                tim_row,
                text=label,
                variable=self.timing_var,
                value=val,
                font=ui_font(11),
                text_color=palette["text"],
                fg_color=palette["primary"],
                hover_color=palette["primary_hover"],
                border_color=palette["border_strong"],
                radiobutton_width=16,
                radiobutton_height=16,
            ).pack(side="left", padx=(0, 16))

        # 3. Pomodoro Cycles
        ModernLabel(
            card,
            text="Pomodoro cycles",
            font=ui_font(10, "bold"),
            text_color=palette["text_muted"],
        ).pack(anchor="w", pady=(0, 6))

        pomo_row = ctk.CTkFrame(card, fg_color="transparent")
        pomo_row.pack(fill="x", pady=(0, 12))
        self.pomodoro_var = tk.BooleanVar(value=current_settings.pomodoro_enabled)
        ctk.CTkCheckBox(
            pomo_row,
            text="Enable Pomodoro work/break loop by default   |   Cycles:",
            variable=self.pomodoro_var,
            font=ui_font(11),
            text_color=palette["text"],
            fg_color=palette["primary"],
            hover_color=palette["primary_hover"],
            border_color=palette["border_strong"],
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        ).pack(side="left")

        self.cycles_var = tk.StringVar(value=str(current_settings.pomodoro_cycles))
        ModernEntry(
            pomo_row,
            palette=palette,
            textvariable=self.cycles_var,
            width=48,
            height=28,
            font=mono_font(11, "bold"),
            justify="center",
        ).pack(side="left", padx=(8, 0))

        # 4. Mute & Global Hotkeys
        self.mute_var = tk.BooleanVar(value=current_settings.mute_sounds)
        ctk.CTkCheckBox(
            card,
            text="Mute alert chimes (Layer 2 warnings & break transitions)",
            variable=self.mute_var,
            font=ui_font(11),
            text_color=palette["text"],
            fg_color=palette["primary"],
            hover_color=palette["primary_hover"],
            border_color=palette["border_strong"],
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        ).pack(anchor="w", pady=(0, 8))

        self.hotkeys_var = tk.BooleanVar(value=current_settings.hotkeys_enabled)
        ctk.CTkCheckBox(
            card,
            text="Enable Global Hotkeys: Ctrl+Alt+P (Pause) · Ctrl+Alt+H (HUD) · Ctrl+Alt+R (Rules)",
            variable=self.hotkeys_var,
            font=ui_font(11),
            text_color=palette["text"],
            fg_color=palette["primary"],
            hover_color=palette["primary_hover"],
            border_color=palette["border_strong"],
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        ).pack(anchor="w", pady=(0, 14))

        tk.Frame(card, bg=palette["border"], height=1).pack(fill="x", pady=(0, 12))

        # 5. Gemini API Key section
        key_hdr = ctk.CTkFrame(card, fg_color="transparent")
        key_hdr.pack(fill="x", pady=(0, 6))
        ModernLabel(
            key_hdr,
            text="Gemini API key (.env)",
            font=ui_font(10, "bold"),
            text_color=palette["text_muted"],
        ).pack(side="left")

        get_key_link = ModernLabel(
            key_hdr,
            text="Get Key ↗",
            font=ui_font(10, "bold"),
            text_color=palette["primary"],
            cursor="hand2",
        )
        get_key_link.pack(side="left", padx=(8, 0))
        get_key_link.bind("<Button-1>", lambda _e: self._url_opener("https://aistudio.google.com/apikey"))

        configured, status_str = api_key_status
        self.key_status_lbl = ModernLabel(
            key_hdr,
            text=status_str,
            font=ui_font(10, "bold"),
            text_color=palette["success"] if configured else palette["warning"],
        )
        self.key_status_lbl.pack(side="right")

        key_row = ctk.CTkFrame(card, fg_color="transparent")
        key_row.pack(fill="x")

        self.key_var = tk.StringVar(value="")
        self.key_entry = ModernEntry(
            key_row,
            palette=palette,
            textvariable=self.key_var,
            show="•",
            font=ui_font(11),
            height=34,
        )
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.toggle_key_btn = self._make_btn(
            key_row,
            text="Show",
            command=self._handle_toggle_show_key,
            variant="secondary",
            font=ui_font(10, "bold"),
            padx=10,
            pady=5,
            palette=palette,
        )
        self.toggle_key_btn.pack(side="left", padx=(0, 4))

        self.save_key_btn = self._make_btn(
            key_row,
            text="Save Key",
            command=self._handle_save_key,
            variant="secondary",
            font=ui_font(10, "bold"),
            padx=10,
            pady=5,
            palette=palette,
        )
        self.save_key_btn.pack(side="left", padx=(0, 4))

        self.test_key_btn = self._make_btn(
            key_row,
            text="Test Connection",
            command=self._handle_test_key,
            variant="secondary",
            font=ui_font(10, "bold"),
            padx=10,
            pady=5,
            palette=palette,
        )
        self.test_key_btn.pack(side="left", padx=(0, 4))

        self.remove_key_btn: Any = None
        if on_clear_api_key is not None:
            self.remove_key_btn = self._make_btn(
                key_row,
                text="Remove Key",
                command=self._handle_clear_key,
                variant="danger_outline",
                font=ui_font(10, "bold"),
                padx=10,
                pady=5,
                palette=palette,
            )
            if not configured:
                self.remove_key_btn.configure(state="disabled")
            self.remove_key_btn.pack(side="left")

        self.key_feedback_lbl = ModernLabel(
            card,
            text="Paste a new key to update .env locally (never shared or logged).",
            font=ui_font(10),
            text_color=palette["text_muted"],
            anchor="w",
        )
        self.key_feedback_lbl.pack(fill="x", pady=(6, 0))

        win.bind("<Escape>", lambda _e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._fit(win, min_w=640, min_h=500, pos="center", pad_w=12, pad_h=12, allow_resize=True)
        apply_dark_titlebar(win, dark)
        if focus_api_key:
            try:
                self.key_entry.focus_set()
            except Exception:
                pass

    def _handle_toggle_show_key(self) -> None:
        self._show_key = not self._show_key
        self.key_entry.configure(show="" if self._show_key else "•")
        self.toggle_key_btn.configure(text="Hide" if self._show_key else "Show")

    def _handle_save_key(self) -> None:
        raw = self.key_var.get().strip()
        if not raw:
            self.key_feedback_lbl.configure(text="Enter a non-empty API key first.", fg=self.pal["danger"])
            return
        if self._on_save_api_key is not None:
            ok, masked_msg = self._on_save_api_key(raw)
            if ok:
                self.key_var.set("")
            self.key_status_lbl.configure(
                text=masked_msg if ok else self.key_status_lbl.cget("text"),
                fg=self.pal["success"] if ok else self.pal["warning"],
            )
            if self.remove_key_btn is not None:
                self.remove_key_btn.configure(state="normal" if ok else self.remove_key_btn.cget("state"))
            self.key_feedback_lbl.configure(
                text="Saved GEMINI_API_KEY to .env." if ok else masked_msg,
                fg=self.pal["success"] if ok else self.pal["danger"],
            )

    def _handle_clear_key(self) -> None:
        if self._on_clear_api_key is None:
            return
        ok, status_msg = self._on_clear_api_key()
        self.key_var.set("")
        self.key_status_lbl.configure(
            text=status_msg,
            fg=self.pal["success"] if ok else self.pal["warning"],
        )
        if self.remove_key_btn is not None:
            self.remove_key_btn.configure(state="disabled")
        self.key_feedback_lbl.configure(
            text="Removed GEMINI_API_KEY from .env.",
            fg=self.pal["text_secondary"],
        )

    def _handle_test_key(self) -> None:
        if self._on_test_api_key is None:
            return
        candidate = self.key_var.get().strip()
        self.test_key_btn.configure(state="disabled", text="Testing...")
        self.key_feedback_lbl.configure(text="Testing Gemini API connection...", fg=self.pal["text_secondary"])

        def _done(ok: bool, msg: str) -> None:
            if not self.win.winfo_exists():
                return
            self.test_key_btn.configure(state="normal", text="Test Connection")
            self.key_feedback_lbl.configure(
                text=msg,
                fg=self.pal["success"] if ok else self.pal["danger"],
            )

        self._on_test_api_key(candidate, _done)

    def _handle_save(self) -> None:
        # If user typed an API key and clicked Save Settings directly, save the key too
        pending_key = self.key_var.get().strip()
        if pending_key and self._on_save_api_key is not None:
            ok, _msg = self._on_save_api_key(pending_key)
            if ok:
                self.key_var.set("")

        try:
            cyc = int(self.cycles_var.get().strip())
        except Exception:
            cyc = 4
        new_settings = AppSettings(
            break_minutes=int(self.break_var.get()),
            mute_sounds=bool(self.mute_var.get()),
            timing_preset="demo" if self.timing_var.get() == "demo" else "dev",
            hotkeys_enabled=bool(self.hotkeys_var.get()),
            pomodoro_enabled=bool(self.pomodoro_var.get()),
            pomodoro_cycles=cyc,
            onboarding_skipped=bool(self._initial_settings.onboarding_skipped),
        ).sanitized()
        self.close()
        self._on_save_settings(new_settings)

    def close(self) -> None:
        try:
            if self.win.winfo_exists():
                self.win.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass
