"""Windows foreground window watcher and safe window minimize helper using pywin32/psutil."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Callable

import psutil
import win32con
import win32gui
import win32process

from app.models import WindowInfo

logger = logging.getLogger(__name__)
_SELF_PID = os.getpid()

BROWSER_PROCESSES: set[str] = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "opera_gx.exe",
    "vivaldi.exe",
    "chromium.exe",
    "arc.exe",
}


def _ensure_default_desktop() -> None:
    """Attach the calling thread to the interactive Windows default desktop."""
    try:
        user32 = ctypes.windll.user32
        h_desk = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)
            user32.CloseDesktop(h_desk)
    except Exception:
        pass


def _get_process_name(pid: int) -> str:
    """Safely resolve an executable name from a process PID."""
    if pid <= 0:
        return ""
    try:
        proc = psutil.Process(pid)
        return proc.name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""
    except Exception:
        return ""


def get_foreground_window() -> WindowInfo | None:
    """Return metadata for the currently focused Windows foreground window."""
    _ensure_default_desktop()
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None

        title = win32gui.GetWindowText(hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return None

        process_name = _get_process_name(pid)
        is_browser = process_name in BROWSER_PROCESSES

        # UWP / Windows Store apps are hosted under ApplicationFrameHost.exe.
        # Find the actual child window hosting the UWP app to resolve real process/PID.
        if process_name == "applicationframehost.exe":
            child_pids: list[int] = []

            def _enum_child(child_hwnd: int, result: list[int]) -> bool:
                _, c_pid = win32process.GetWindowThreadProcessId(child_hwnd)
                if c_pid != pid and c_pid > 0:
                    result.append(c_pid)
                return True

            try:
                win32gui.EnumChildWindows(hwnd, _enum_child, child_pids)
                if child_pids:
                    real_pid = child_pids[0]
                    real_name = _get_process_name(real_pid)
                    if real_name:
                        process_name = real_name
                        pid = real_pid
                        is_browser = process_name in BROWSER_PROCESSES
            except Exception:
                pass

        return WindowInfo(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            pid=pid,
            is_browser=is_browser,
        )
    except Exception:
        return None


def list_open_window_titles(limit: int = 15) -> list[str]:
    """Return visible top-level window titles/process names for Tier 1/2 context."""
    _ensure_default_desktop()
    windows: list[str] = []

    def _enum_window(hwnd: int, _: None) -> bool:
        if len(windows) >= limit:
            return False
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title and title not in ("Program Manager", "Windows Shell Experience Host"):
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid == _SELF_PID:
                        return True
                    proc_name = _get_process_name(pid)
                    entry = f"{title} ({proc_name})" if proc_name else title
                    if entry not in windows:
                        windows.append(entry)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_enum_window, None)
    except Exception:
        pass

    return windows


def minimize_window(hwnd: int) -> bool:
    """Minimize a non-browser window safely without terminating the process."""
    if not hwnd or hwnd <= 0:
        return False
    _ensure_default_desktop()
    try:
        if win32gui.IsWindow(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == _SELF_PID:
                return False
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return True
        return False
    except Exception:
        return False


class WindowWatcher:
    """Background watcher polling foreground window changes and emitting callbacks."""

    def __init__(
        self,
        on_window_changed: Callable[[WindowInfo], None],
        poll_interval: float = 0.5,
    ) -> None:
        self.on_window_changed = on_window_changed
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hwnd: int | None = None
        self._last_title: str | None = None

    def start(self) -> None:
        """Start the daemon polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="WindowWatcherThread"
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the watcher thread to stop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        _ensure_default_desktop()
        while not self._stop_event.is_set():
            try:
                info = get_foreground_window()
                if info is not None:
                    # Ignore Anti-Distractor's own windows so dragging the HUD or
                    # answering popups never triggers a spurious off-task check.
                    if info.pid == _SELF_PID:
                        self._stop_event.wait(timeout=self.poll_interval)
                        continue
                    if info.hwnd != self._last_hwnd or info.title != self._last_title:
                        self._last_hwnd = info.hwnd
                        self._last_title = info.title
                        try:
                            self.on_window_changed(info)
                        except Exception:
                            logger.exception("Error in WindowWatcher on_window_changed callback")
            except Exception:
                logger.exception("Error polling foreground window in WindowWatcher")

            self._stop_event.wait(timeout=self.poll_interval)
