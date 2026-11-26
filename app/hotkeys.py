"""Optional global Windows shortcuts (Ctrl+Alt+P / H / R) via Win32 RegisterHotKey."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
import threading
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_P = 0x50
VK_H = 0x48
VK_R = 0x52

HK_ID_PAUSE = 101
HK_ID_HUD = 102
HK_ID_RULES = 103

HotkeyAction = Literal["toggle_pause", "toggle_hud", "open_rules"]


def _get_user32() -> Any:
    return ctypes.windll.user32


def _get_kernel32() -> Any:
    return ctypes.windll.kernel32


class GlobalHotkeyManager:
    """Manages optional system-wide Ctrl+Alt+P/H/R shortcuts on a lightweight Win32 message thread."""

    def __init__(
        self,
        on_toggle_pause: Callable[[], None] | None = None,
        on_toggle_hud: Callable[[], None] | None = None,
        on_open_rules: Callable[[], None] | None = None,
        *,
        enable_os_registration: bool = True,
    ) -> None:
        self.on_toggle_pause = on_toggle_pause
        self.on_toggle_hud = on_toggle_hud
        self.on_open_rules = on_open_rules
        self.enable_os_registration = enable_os_registration

        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._win_thread_id: int | None = None
        self._registered_ids: list[int] = []

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def trigger(self, action: HotkeyAction) -> bool:
        """Invoke a hotkey callback safely (used by OS message loop and unit tests)."""
        cb: Callable[[], None] | None = None
        if action == "toggle_pause":
            cb = self.on_toggle_pause
        elif action == "toggle_hud":
            cb = self.on_toggle_hud
        elif action == "open_rules":
            cb = self.on_open_rules

        if cb is None:
            return False
        try:
            cb()
            return True
        except Exception:
            logger.exception("Global hotkey callback '%s' failed", action)
            return False

    def start(self) -> bool:
        """Start listening for global shortcuts idempotently."""
        with self._lock:
            if self._running:
                return True
            if not self.enable_os_registration or sys.platform != "win32":
                self._running = True
                return True

            ready = threading.Event()
            t = threading.Thread(
                target=self._message_loop,
                args=(ready,),
                daemon=True,
                name="GlobalHotkeyThread",
            )
            self._thread = t
            self._running = True
            t.start()

        ready.wait(timeout=1.5)
        return self.is_running

    def _message_loop(self, ready: threading.Event) -> None:
        u32 = _get_user32()
        k32 = _get_kernel32()
        tid = int(k32.GetCurrentThreadId())
        registered: list[int] = []

        bindings: list[tuple[int, int, HotkeyAction, str]] = [
            (HK_ID_PAUSE, VK_P, "toggle_pause", "Ctrl+Alt+P"),
            (HK_ID_HUD, VK_H, "toggle_hud", "Ctrl+Alt+H"),
            (HK_ID_RULES, VK_R, "open_rules", "Ctrl+Alt+R"),
        ]
        id_to_action: dict[int, HotkeyAction] = {}
        mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT

        try:
            # Ensure thread message queue exists before PostThreadMessageW can be called
            msg = wintypes.MSG()
            u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

            for hk_id, vk, action, label in bindings:
                try:
                    ok = bool(u32.RegisterHotKey(None, hk_id, mods, vk))
                    if ok:
                        registered.append(hk_id)
                        id_to_action[hk_id] = action
                    else:
                        logger.debug("Global hotkey %s unavailable (already registered)", label)
                except Exception:
                    logger.debug("RegisterHotKey failed for %s", label, exc_info=True)

            with self._lock:
                self._win_thread_id = tid
                self._registered_ids = list(registered)
            ready.set()

            while True:
                with self._lock:
                    if not self._running:
                        break
                ret = int(u32.GetMessageW(ctypes.byref(msg), None, 0, 0))
                if ret <= 0:  # 0 = WM_QUIT, -1 = error
                    break
                if msg.message == WM_HOTKEY:
                    action_name = id_to_action.get(int(msg.wParam))
                    if action_name is not None:
                        self.trigger(action_name)
        except Exception:
            logger.exception("GlobalHotkeyThread terminated unexpectedly")
        finally:
            for hk_id in registered:
                try:
                    u32.UnregisterHotKey(None, hk_id)
                except Exception:
                    pass
            with self._lock:
                self._running = False
                self._win_thread_id = None
                self._registered_ids.clear()
            ready.set()

    def stop(self) -> None:
        """Post WM_QUIT to exit the message loop cleanly and unregister OS hotkeys."""
        with self._lock:
            was_running = self._running
            self._running = False
            tid = self._win_thread_id
            thread = self._thread
            self._thread = None

        if was_running and tid is not None and sys.platform == "win32":
            try:
                u32 = _get_user32()
                u32.PostThreadMessageW(wintypes.DWORD(tid), WM_QUIT, 0, 0)
            except Exception:
                logger.debug("PostThreadMessageW failed during hotkey stop", exc_info=True)

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
