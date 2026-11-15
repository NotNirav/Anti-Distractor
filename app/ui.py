"""Tkinter UI components: setup, monitoring HUD, Layer 1/2 popups, strict overlay, and review."""

from __future__ import annotations

from typing import Callable, Literal, Protocol

from app.models import AgentStateName, ModeType


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


class TkinterUI:
    """Thread-safe Tkinter implementation of UIHooks running on the main thread."""

    def __init__(self) -> None:
        # TODO(T5.2/T5.3): Initialize tk.Tk(), thread-safe command queue, and root.after pump.
        pass

    def run(self) -> None:
        """Enter the Tkinter mainloop on the main thread."""
        raise NotImplementedError("TODO(T5.2): Implement TkinterUI.run")

    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None:
        """Render the goal/duration/mode setup screen with 'I'm ready' action."""
        raise NotImplementedError("TODO(T5.2): Implement TkinterUI.show_setup")

    def show_monitoring_hud(
        self,
        goal: str,
        mode: ModeType,
        on_end_session: Callable[[], None],
    ) -> None:
        """Render the compact floating monitoring HUD."""
        raise NotImplementedError("TODO(T5.2): Implement TkinterUI.show_monitoring_hud")

    def update_hud_status(self, state: AgentStateName, summary: str) -> None:
        """Update the HUD state badge and latest activity summary."""
        raise NotImplementedError("TODO(T5.2): Implement TkinterUI.update_hud_status")

    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
    ) -> None:
        """Display the Layer 1 'Are you studying <goal>?' popup."""
        raise NotImplementedError("TODO(T5.3): Implement TkinterUI.show_layer1_popup")

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        """Display the Layer 2 Normal-mode loud warning popup."""
        raise NotImplementedError("TODO(T5.3): Implement TkinterUI.show_layer2_warning")

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        """Display the Strict-mode full-screen topmost blank overlay."""
        raise NotImplementedError("TODO(T5.3): Implement TkinterUI.show_strict_overlay")

    def hide_popups_and_overlay(self) -> None:
        """Dismiss any active Layer 1/2 popups or strict-mode overlay."""
        raise NotImplementedError("TODO(T5.3): Implement TkinterUI.hide_popups_and_overlay")

    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None:
        """Display the end-of-session AI review screen."""
        raise NotImplementedError("TODO(T5.2): Implement TkinterUI.show_review")
