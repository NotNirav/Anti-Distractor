"""Tkinter UI components: setup, monitoring HUD, Layer 1/2 popups, strict overlay, and review."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable, Literal, Protocol

from app.models import AgentStateName, ModeType

logger = logging.getLogger(__name__)


class UIHooks(Protocol):
    """Thread-safe interface used by the app controller to drive Tkinter screens."""

    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None: ...
    def show_monitoring_hud(
        self,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
    ) -> None: ...
    def update_hud_status(self, state: AgentStateName, summary: str) -> None: ...
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
    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None: ...


STATE_COLORS: dict[AgentStateName, tuple[str, str]] = {
    "IDLE": ("#9E9E9E", "#FFFFFF"),
    "ON_TASK": ("#2E7D32", "#FFFFFF"),
    "GRACE_PERIOD": ("#F57C00", "#FFFFFF"),
    "LAYER_1_ASK": ("#E65100", "#FFFFFF"),
    "LAYER_2_ULTIMATUM": ("#C62828", "#FFFFFF"),
    "ENDED": ("#424242", "#FFFFFF"),
}


class TkinterUI:
    """Thread-safe Tkinter implementation of UIHooks running on the main thread."""

    def __init__(self, root: tk.Tk | None = None) -> None:
        self._owns_root = root is None
        self.root = root if root is not None else tk.Tk()
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()

        self._current_frame: ttk.Frame | None = None
        self._hud_state_label: tk.Label | None = None
        self._hud_summary_label: tk.Label | None = None
        self._hud_mode_label: tk.Label | None = None
        self._hud_goal_label: tk.Label | None = None

        self._active_popups: list[tk.Toplevel] = []
        self._strict_overlay_win: tk.Toplevel | None = None
        self._strict_countdown_after_id: str | None = None

        # Start the queue pump
        self._pump()

    def drain_queue(self) -> None:
        """Immediately execute all pending queue callbacks on the current thread."""
        while not self._queue.empty():
            try:
                callback = self._queue.get_nowait()
                try:
                    callback()
                except Exception:
                    logger.exception("Error executing UI queue callback")
            except queue.Empty:
                break

    def _pump(self) -> None:
        """Drain background-thread callbacks safely on the Tkinter main thread."""
        self.drain_queue()

        try:
            if self.root.winfo_exists():
                self.root.after(50, self._pump)
        except Exception:
            pass

    def dispatch(self, callback: Callable[[], None]) -> None:
        """Enqueue a callback to execute on the main GUI thread."""
        self._queue.put(callback)

    def run(self) -> None:
        """Enter the Tkinter mainloop on the main thread."""
        self.root.mainloop()

    def _clear_root_widgets(self) -> None:
        """Remove all existing children from the root window."""
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self._current_frame = None

    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None:
        """Render the goal/duration/mode setup screen with 'I'm ready' action."""

        def _action() -> None:
            self._clear_root_widgets()
            self.root.title("Anti-Distractor — Session Setup")
            self.root.geometry("460x380")
            self.root.minsize(420, 340)
            self.root.attributes("-topmost", False)

            frame = ttk.Frame(self.root, padding="20 20 20 20")
            frame.pack(fill="both", expand=True)
            self._current_frame = frame

            # Title
            title_lbl = ttk.Label(
                frame,
                text="🎯 Anti-Distractor",
                font=("Segoe UI", 16, "bold"),
            )
            title_lbl.pack(anchor="w", pady=(0, 4))

            subtitle_lbl = ttk.Label(
                frame,
                text="Set your goal and focus mode before starting your session.",
                font=("Segoe UI", 9),
            )
            subtitle_lbl.pack(anchor="w", pady=(0, 16))

            # Goal input
            goal_lbl = ttk.Label(frame, text="What is your goal?", font=("Segoe UI", 10, "bold"))
            goal_lbl.pack(anchor="w", pady=(0, 4))
            goal_var = tk.StringVar(value="Study Data Structures and Algorithms")
            goal_entry = ttk.Entry(frame, textvariable=goal_var, font=("Segoe UI", 10))
            goal_entry.pack(fill="x", pady=(0, 12))

            # Duration input
            dur_frame = ttk.Frame(frame)
            dur_frame.pack(fill="x", pady=(0, 12))
            dur_lbl = ttk.Label(dur_frame, text="Duration (minutes):", font=("Segoe UI", 10, "bold"))
            dur_lbl.pack(side="left")
            dur_var = tk.StringVar(value="25")
            dur_entry = ttk.Entry(dur_frame, textvariable=dur_var, width=8, font=("Segoe UI", 10))
            dur_entry.pack(side="left", padx=(10, 0))

            # Mode selection (Normal vs Strict)
            mode_lbl = ttk.Label(frame, text="Enforcement Mode:", font=("Segoe UI", 10, "bold"))
            mode_lbl.pack(anchor="w", pady=(0, 4))

            mode_var = tk.StringVar(value="strict")
            mode_frame = ttk.Frame(frame)
            mode_frame.pack(fill="x", pady=(0, 16))

            strict_rb = ttk.Radiobutton(
                mode_frame,
                text="Strict Mode (Closes distracting tabs, minimizes apps, full lockout)",
                variable=mode_var,
                value="strict",
            )
            strict_rb.pack(anchor="w", pady=(0, 4))

            normal_rb = ttk.Radiobutton(
                mode_frame,
                text="Normal Mode (Gentle nudges, popups & warning alerts only)",
                variable=mode_var,
                value="normal",
            )
            normal_rb.pack(anchor="w")

            status_lbl = ttk.Label(
                frame,
                text="Ready to begin.",
                font=("Segoe UI", 9, "italic"),
                foreground="#666666",
            )
            status_lbl.pack(anchor="w", pady=(0, 12))

            def _handle_start() -> None:
                goal_val = goal_var.get().strip()
                if not goal_val:
                    messagebox.showwarning("Missing Goal", "Please enter a focus goal.")
                    return

                try:
                    dur_val = int(dur_var.get().strip())
                    if dur_val <= 0:
                        raise ValueError()
                except ValueError:
                    messagebox.showwarning("Invalid Duration", "Duration must be a positive integer.")
                    return

                selected_mode: ModeType = "strict" if mode_var.get() == "strict" else "normal"
                status_lbl.config(text="Preparing profile and starting session...")
                on_start(goal_val, dur_val, selected_mode)

            start_btn = ttk.Button(
                frame,
                text="🚀 I'm Ready — Start Session",
                command=_handle_start,
            )
            start_btn.pack(fill="x", ipady=4)

        self.dispatch(_action)

    def show_monitoring_hud(
        self,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
    ) -> None:
        """Render the compact floating monitoring HUD."""

        def _action() -> None:
            self._clear_root_widgets()
            self.root.title("Anti-Distractor — HUD")
            self.root.geometry("380x140")
            self.root.minsize(360, 130)
            self.root.attributes("-topmost", True)

            frame = ttk.Frame(self.root, padding="10 10 10 10")
            frame.pack(fill="both", expand=True)
            self._current_frame = frame

            # Top row: Goal and Mode
            top_row = ttk.Frame(frame)
            top_row.pack(fill="x", pady=(0, 6))

            goal_display = f"🎯 {goal}" if len(goal) <= 32 else f"🎯 {goal[:29]}..."
            self._hud_goal_label = ttk.Label(
                top_row,
                text=goal_display,
                font=("Segoe UI", 10, "bold"),
            )
            self._hud_goal_label.pack(side="left")

            mode_text = f"[{mode.upper()}]"
            self._hud_mode_label = ttk.Label(
                top_row,
                text=mode_text,
                font=("Segoe UI", 9, "bold"),
                foreground="#1976D2" if mode == "normal" else "#D32F2F",
            )
            self._hud_mode_label.pack(side="right")

            # Middle row: Status badge
            status_row = ttk.Frame(frame)
            status_row.pack(fill="x", pady=(0, 6))

            bg, fg = STATE_COLORS["ON_TASK"]
            self._hud_state_label = tk.Label(
                status_row,
                text=" ON_TASK ",
                font=("Segoe UI", 9, "bold"),
                bg=bg,
                fg=fg,
                padx=6,
                pady=2,
                relief="flat",
            )
            self._hud_state_label.pack(side="left")

            self._hud_summary_label = ttk.Label(
                status_row,
                text="Monitoring active.",
                font=("Segoe UI", 9),
            )
            self._hud_summary_label.pack(side="left", padx=(8, 0))

            # Bottom row: End Session Button
            end_btn = ttk.Button(
                frame,
                text="⏹ End Session",
                command=on_end_session,
            )
            end_btn.pack(side="right", pady=(4, 0))

        self.dispatch(_action)

    def update_hud_status(self, state: AgentStateName, summary: str) -> None:
        """Update the HUD state badge and latest activity summary."""

        def _action() -> None:
            if self._hud_state_label is not None and self._hud_state_label.winfo_exists():
                bg, fg = STATE_COLORS.get(state, ("#757575", "#FFFFFF"))
                self._hud_state_label.config(text=f" {state} ", bg=bg, fg=fg)

            if self._hud_summary_label is not None and self._hud_summary_label.winfo_exists():
                display_summary = summary if len(summary) <= 40 else f"{summary[:37]}..."
                self._hud_summary_label.config(text=display_summary)

        self.dispatch(_action)

    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None:
        """Display the end-of-session AI review screen."""

        def _action() -> None:
            self.hide_popups_and_overlay()
            self._clear_root_widgets()

            self.root.title("Anti-Distractor — Session Review")
            self.root.geometry("540x440")
            self.root.minsize(480, 360)
            self.root.attributes("-topmost", False)

            frame = ttk.Frame(self.root, padding="16 16 16 16")
            frame.pack(fill="both", expand=True)
            self._current_frame = frame

            header_lbl = ttk.Label(
                frame,
                text="📊 Focus Session Review",
                font=("Segoe UI", 15, "bold"),
            )
            header_lbl.pack(anchor="w", pady=(0, 4))

            sub_lbl = ttk.Label(
                frame,
                text="Here is your AI-generated focus review and performance analysis:",
                font=("Segoe UI", 9),
            )
            sub_lbl.pack(anchor="w", pady=(0, 10))

            # Scrollable text area
            txt = scrolledtext.ScrolledText(
                frame,
                wrap="word",
                font=("Consolas" if "```" in review_text else "Segoe UI", 10),
                padx=8,
                pady=8,
            )
            txt.pack(fill="both", expand=True, pady=(0, 12))
            txt.insert("1.0", review_text)
            txt.config(state="disabled")

            def _handle_close() -> None:
                on_close()

            close_btn = ttk.Button(
                frame,
                text="Done / Close",
                command=_handle_close,
            )
            close_btn.pack(side="right")

        self.dispatch(_action)

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
                if popup.winfo_exists():
                    popup.destroy()
            except Exception:
                pass
        self._active_popups.clear()

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
        """Display Layer 1 'Are you studying <goal>?' popup (thread-safe)."""

        def _action() -> None:
            self._hide_popups_and_overlay_now()

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — Quick Check")
            win.attributes("-topmost", True)
            win.resizable(False, False)

            width, height = 420, 210
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(0, screen_w - width - 24)
            y = max(0, screen_h - height - 64)
            win.geometry(f"{width}x{height}+{x}+{y}")

            frame = ttk.Frame(win, padding="16 14 16 14")
            frame.pack(fill="both", expand=True)

            title_lbl = ttk.Label(
                frame,
                text="Quick Focus Check",
                font=("Segoe UI", 12, "bold"),
                foreground="#E65100",
            )
            title_lbl.pack(anchor="w", pady=(0, 6))

            goal_lbl = ttk.Label(
                frame,
                text=f"Goal: {goal}",
                font=("Segoe UI", 10, "bold"),
                wraplength=380,
                justify="left",
            )
            goal_lbl.pack(anchor="w", pady=(0, 4))

            reason_lbl = ttk.Label(
                frame,
                text=reason or "Current activity looks off-task.",
                font=("Segoe UI", 9),
                wraplength=380,
                justify="left",
            )
            reason_lbl.pack(anchor="w", pady=(0, 14))

            btn_row = ttk.Frame(frame)
            btn_row.pack(fill="x", side="bottom")

            def _choose(choice: Literal["back_to_work", "relevant"]) -> None:
                self._hide_popups_and_overlay_now()
                try:
                    on_choice(choice)
                except Exception:
                    logger.exception("Error in Layer 1 on_choice callback")

            back_btn = ttk.Button(
                btn_row,
                text="Back to work",
                command=lambda: _choose("back_to_work"),
            )
            back_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

            rel_btn = ttk.Button(
                btn_row,
                text="This is relevant",
                command=lambda: _choose("relevant"),
            )
            rel_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))

            win.protocol("WM_DELETE_WINDOW", self._hide_popups_and_overlay_now)
            self._active_popups.append(win)

        self.dispatch(_action)

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        """Display Layer 2 Normal-mode loud warning popup (thread-safe)."""

        def _action() -> None:
            self._hide_popups_and_overlay_now()

            try:
                self.root.bell()
            except Exception:
                pass

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — OFF-TASK WARNING")
            win.attributes("-topmost", True)
            win.resizable(False, False)

            width, height = 460, 230
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 3)
            win.geometry(f"{width}x{height}+{x}+{y}")

            banner = tk.Frame(win, bg="#C62828", padx=16, pady=12)
            banner.pack(fill="x")
            tk.Label(
                banner,
                text="⚠ OFF-TASK WARNING",
                font=("Segoe UI", 14, "bold"),
                bg="#C62828",
                fg="#FFFFFF",
            ).pack(anchor="w")

            body = ttk.Frame(win, padding="16 14 16 14")
            body.pack(fill="both", expand=True)

            ttk.Label(
                body,
                text=f"Return to your goal: {goal}",
                font=("Segoe UI", 10, "bold"),
                wraplength=420,
                justify="left",
            ).pack(anchor="w", pady=(0, 6))

            ttk.Label(
                body,
                text=reason or "Distraction detected.",
                font=("Segoe UI", 9),
                wraplength=420,
                justify="left",
            ).pack(anchor="w", pady=(0, 14))

            dismiss_btn = ttk.Button(
                body,
                text="Dismiss — Back to Work",
                command=self._hide_popups_and_overlay_now,
            )
            dismiss_btn.pack(side="right")

            win.protocol("WM_DELETE_WINDOW", self._hide_popups_and_overlay_now)
            self._active_popups.append(win)

        self.dispatch(_action)

    def show_tier0_shift_popup(self, message: str) -> None:
        """Display a warning dialog that AI models are unavailable and shifting to Tier 0 rules only."""

        def _action() -> None:
            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — AI Shift Notice")
            win.attributes("-topmost", True)
            win.resizable(False, False)

            width, height = 480, 240
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 3)
            win.geometry(f"{width}x{height}+{x}+{y}")

            banner = tk.Frame(win, bg="#D32F2F", padx=16, pady=10)
            banner.pack(fill="x")
            tk.Label(
                banner,
                text="⚠️ AI Models Offline — Tier 0 Active",
                font=("Segoe UI", 12, "bold"),
                bg="#D32F2F",
                fg="#FFFFFF",
            ).pack(anchor="w")

            body = ttk.Frame(win, padding="16 14 16 14")
            body.pack(fill="both", expand=True)

            ttk.Label(
                body,
                text=(
                    message
                    or "All AI models (Gemini 3.5 Flash-Lite, Gemma 31B, Gemma 24B) are currently experiencing "
                    "quota limits or high demand.\n\n"
                    "Anti-Distractor has shifted to Tier 0 local rules only (allowed/blocked domain and app lists) "
                    "to maintain your focus protection until an AI model becomes available."
                ),
                font=("Segoe UI", 9),
                wraplength=440,
                justify="left",
            ).pack(anchor="w", pady=(0, 14))

            btn = ttk.Button(
                body,
                text="Understood (Continue with Tier 0 Rules)",
                command=win.destroy,
            )
            btn.pack(side="right")

            self._active_popups.append(win)

        self.dispatch(_action)

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        """Display Strict-mode full-screen topmost overlay with countdown and demo escape hatch."""

        def _action() -> None:
            self._hide_popups_and_overlay_now()

            win = tk.Toplevel(self.root)
            win.title("Anti-Distractor — Strict Lockout")
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg="#121212")

            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            win.geometry(f"{screen_w}x{screen_h}+0+0")
            self._strict_overlay_win = win

            container = tk.Frame(win, bg="#121212")
            container.place(relx=0.5, rely=0.5, anchor="center")

            tk.Label(
                container,
                text=f"Start {goal} again",
                font=("Segoe UI", 28, "bold"),
                bg="#121212",
                fg="#FFFFFF",
                wraplength=max(400, screen_w - 120),
                justify="center",
            ).pack(pady=(0, 14))

            tk.Label(
                container,
                text=reason or "Strict mode lockout active.",
                font=("Segoe UI", 13),
                bg="#121212",
                fg="#EF9A9A",
                wraplength=max(400, screen_w - 160),
                justify="center",
            ).pack(pady=(0, 24))

            countdown_lbl = tk.Label(
                container,
                text="",
                font=("Segoe UI", 18, "bold"),
                bg="#121212",
                fg="#FFB74D",
            )
            countdown_lbl.pack(pady=(0, 28))

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
                container,
                text="Emergency Exit (Demo: Ctrl+Shift+Esc)",
                font=("Segoe UI", 10),
                bg="#2C2C2C",
                fg="#CCCCCC",
                activebackground="#424242",
                activeforeground="#FFFFFF",
                relief="flat",
                padx=12,
                pady=6,
                command=_finish_lockout,
            )
            escape_btn.pack()

            win.bind("<Control-Shift-Escape>", lambda _event: _finish_lockout())
            win.protocol("WM_DELETE_WINDOW", lambda: None)
            try:
                win.focus_force()
            except Exception:
                pass

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
                self._strict_countdown_after_id = win.after(200, _tick)

            _tick()

        self.dispatch(_action)

    def hide_popups_and_overlay(self) -> None:
        """Dismiss active Layer 1/2 popups or strict-mode overlay (thread-safe)."""
        self.dispatch(self._hide_popups_and_overlay_now)


if __name__ == "__main__":
    import time

    ui = TkinterUI()

    def on_end():
        sample_review = (
            "## Session Review: DSA Study\n\n"
            "- **On-Task Score:** 88% (22/25 checks on-task)\n"
            "- **Highlights:** Solid focus on LeetCode dynamic programming and tree traversals.\n"
            "- **Distractions:** 1 brief YouTube distraction intercepted and closed promptly.\n"
        )
        ui.show_review(sample_review, on_close=lambda: ui.root.quit())

    def on_start(goal: str, duration: int, mode: ModeType):
        print(f"Session started: Goal={goal!r}, Duration={duration}m, Mode={mode!r}")
        ui.show_monitoring_hud(goal, mode, on_end_session=on_end)
        # Simulate background status update
        ui.root.after(
            1500,
            lambda: ui.update_hud_status("GRACE_PERIOD", "YouTube tab detected"),
        )
        ui.root.after(
            3500,
            lambda: ui.update_hud_status("ON_TASK", "Back to LeetCode"),
        )

    ui.show_setup(on_start=on_start)
    ui.run()
