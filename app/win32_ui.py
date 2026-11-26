"""Native Windows (Win32 / DWM) window chrome, focus-safe topmost, and multi-monitor helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
import tkinter as tk
from typing import Any

from PIL import ImageTk

from app.tray import create_tray_icon_image

logger = logging.getLogger(__name__)

# DWM attributes
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

# Extended window style constants
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000

# SetWindowPos flags
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

# Monitor flags
MONITOR_DEFAULTTONEAREST = 2


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _get_user32() -> Any:
    return ctypes.windll.user32


def _get_dwmapi() -> Any:
    return ctypes.windll.dwmapi


def _resolve_outer_hwnd(win: tk.Misc, u32: Any | None = None) -> int:
    """Return the outer Win32 frame HWND for a Tk or Toplevel widget."""
    try:
        win.update_idletasks()
    except Exception:
        pass
    hwnd = int(win.winfo_id())
    if hwnd <= 0:
        return 0
    user32 = u32 if u32 is not None else _get_user32()
    try:
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
    except Exception:
        pass
    try:
        parent = user32.GetParent(wintypes.HWND(hwnd))
        if parent:
            return int(parent)
    except Exception:
        pass
    return hwnd


def apply_brand_icon(root: tk.Misc, win: tk.Misc | None = None) -> None:
    """Attach the branded rounded target icon to root and/or a child Toplevel."""
    target = win if win is not None else root
    try:
        photo = getattr(root, "_brand_icon_photo", None)
        if photo is None:
            img = create_tray_icon_image("#6366F1", 64)
            photo = ImageTk.PhotoImage(img, master=root)
            setattr(root, "_brand_icon_photo", photo)
        target.iconphoto(win is None, photo)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("Skipping brand icon on headless/mocked window", exc_info=True)


def apply_rounded_corners(win: tk.Misc, *, small: bool = False) -> None:
    """Enable native Windows 11 DWM rounded window corners (no-op on older Windows/other OS)."""
    if sys.platform != "win32":
        return
    try:
        u32 = _get_user32()
        dwmapi = _get_dwmapi()
        hwnd = _resolve_outer_hwnd(win, u32=u32)
        if hwnd <= 0:
            return
        pref = ctypes.c_int(DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref),
            ctypes.sizeof(pref),
        )
    except Exception:
        logger.debug("DWMWA_WINDOW_CORNER_PREFERENCE unavailable or failed", exc_info=True)


def apply_dark_titlebar(win: tk.Misc, dark: bool) -> None:
    """Toggle native Windows 10/11 immersive dark mode titlebar via DWM."""
    if sys.platform != "win32":
        return
    try:
        u32 = _get_user32()
        dwmapi = _get_dwmapi()
        hwnd = _resolve_outer_hwnd(win, u32=u32)
        if hwnd <= 0:
            return
        value = ctypes.c_int(1 if dark else 0)
        hr = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        if hr != 0:
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
    except Exception:
        logger.debug("DwmSetWindowAttribute unavailable or failed", exc_info=True)


def apply_noactivate_topmost(win: tk.Misc) -> None:
    """Apply WS_EX_NOACTIVATE | WS_EX_TOPMOST so popups float visibly without stealing focus."""
    if sys.platform != "win32":
        return
    try:
        u32 = _get_user32()
        hwnd = _resolve_outer_hwnd(win, u32=u32)
        if hwnd <= 0:
            return

        get_long = getattr(u32, "GetWindowLongPtrW", None) or getattr(u32, "GetWindowLongW")
        set_long = getattr(u32, "SetWindowLongPtrW", None) or getattr(u32, "SetWindowLongW")
        try:
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
        except Exception:
            pass

        cur_style = int(get_long(wintypes.HWND(hwnd), GWL_EXSTYLE) or 0)
        new_style = cur_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, new_style)

        try:
            u32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            u32.SetWindowPos.restype = wintypes.BOOL
        except Exception:
            pass

        u32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception:
        logger.debug("apply_noactivate_topmost failed", exc_info=True)


def get_monitor_work_area(
    x: int,
    y: int,
    fallback: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) work area of the monitor nearest (x, y)."""
    if sys.platform != "win32":
        return fallback
    try:
        u32 = _get_user32()
        try:
            u32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
            u32.MonitorFromPoint.restype = wintypes.HMONITOR
            u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
            u32.GetMonitorInfoW.restype = wintypes.BOOL
        except Exception:
            pass

        pt = _POINT(int(x), int(y))
        hmon = u32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return fallback

        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        ok = u32.GetMonitorInfoW(hmon, ctypes.byref(info))
        if not ok:
            return fallback

        left = int(info.rcWork.left)
        top = int(info.rcWork.top)
        right = int(info.rcWork.right)
        bottom = int(info.rcWork.bottom)
        if right > left and bottom > top:
            return left, top, right, bottom
    except Exception:
        logger.debug("get_monitor_work_area failed; using fallback", exc_info=True)
    return fallback
