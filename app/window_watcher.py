"""Windows foreground window watcher and safe window minimize helper using pywin32/psutil."""

from __future__ import annotations

from typing import Callable

from app.models import WindowInfo


def get_foreground_window() -> WindowInfo | None:
    """Return metadata for the currently focused Windows foreground window."""
    # TODO(T3.1): Query win32gui.GetForegroundWindow, PID via GetWindowThreadProcessId, process via psutil.
    raise NotImplementedError("TODO(T3.1): Implement get_foreground_window")


def list_open_window_titles(limit: int = 15) -> list[str]:
    """Return visible top-level window titles/process names for Tier 1/2 context."""
    # TODO(T3.1): Enumerate visible windows via win32gui.EnumWindows.
    raise NotImplementedError("TODO(T3.1): Implement list_open_window_titles")


def minimize_window(hwnd: int) -> bool:
    """Minimize a non-browser window safely without terminating the process."""
    # TODO(T3.1): Call win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE).
    raise NotImplementedError("TODO(T3.1): Implement minimize_window")


class WindowWatcher:
    """Background watcher polling foreground window changes and emitting callbacks."""

    def __init__(
        self,
        on_window_changed: Callable[[WindowInfo], None],
        poll_interval: float = 0.5,
    ) -> None:
        # TODO(T3.1): Store callback, poll interval, stop event, and last window signature.
        self.on_window_changed = on_window_changed
        self.poll_interval = poll_interval

    def start(self) -> None:
        """Start the daemon polling thread."""
        raise NotImplementedError("TODO(T3.1): Implement WindowWatcher.start")

    def stop(self) -> None:
        """Signal the watcher thread to stop."""
        raise NotImplementedError("TODO(T3.1): Implement WindowWatcher.stop")
