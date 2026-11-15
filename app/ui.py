"""Tkinter UI components: setup, monitoring HUD, Layer 1/2 popups, strict overlay, and review."""

from __future__ import annotations

import logging
import queue
import threading
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

    # Placeholders for Task 5.3 (Layer 1, Layer 2, strict overlay)
    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
    ) -> None:
        """Display Layer 1 'Are you studying <goal>?' popup (T5.3)."""
        logger.info("show_layer1_popup requested: goal=%r reason=%r", goal, reason)

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        """Display Layer 2 Normal-mode loud warning popup (T5.3)."""
        logger.info("show_layer2_warning requested: goal=%r reason=%r", goal, reason)

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        """Display Strict-mode full-screen topmost blank overlay (T5.3)."""
        logger.info(
            "show_strict_overlay requested: goal=%r reason=%r lockout=%.1fs",
            goal,
            reason,
            lockout_seconds,
        )

    def hide_popups_and_overlay(self) -> None:
        """Dismiss active Layer 1/2 popups or strict-mode overlay (T5.3)."""
        for popup in list(self._active_popups):
            try:
                popup.destroy()
            except Exception:
                pass
        self._active_popups.clear()

        if self._strict_overlay_win is not None:
            try:
                self._strict_overlay_win.destroy()
            except Exception:
                pass
            self._strict_overlay_win = None


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
