"""Capture PNG screenshots of every Anti-Distractor UI screen/popup into ui_shots/."""

from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import time
import tkinter as tk

from PIL import Image, ImageGrab

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ui import TkinterUI
from scripts.ui_demo import render_demo_screen

OUT_DIR = REPO_ROOT / "ui_shots"


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def _flush(ui: TkinterUI, wait_ms: int = 120) -> None:
    end = time.monotonic() + (wait_ms / 1000.0)
    while True:
        ui.drain_queue()
        ui.root.update_idletasks()
        ui.root.update()
        if time.monotonic() >= end:
            break
        time.sleep(0.015)


def _print_window_fallback(win: tk.Misc) -> Image.Image:
    hwnd = int(win.winfo_id())
    try:
        top_hwnd = ctypes.windll.user32.GetParent(hwnd) or hwnd
    except Exception:
        top_hwnd = hwnd

    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.GetWindowRect(top_hwnd, ctypes.byref(rect))
    w = max(1, rect[2] - rect[0])
    h = max(1, rect[3] - rect[1])

    hdc_win = ctypes.windll.user32.GetWindowDC(top_hwnd)
    hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_win)
    hbmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    ctypes.windll.gdi32.SelectObject(hdc_mem, hbmp)
    ctypes.windll.user32.PrintWindow(top_hwnd, hdc_mem, 2)

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    ctypes.windll.gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    ctypes.windll.gdi32.DeleteObject(hbmp)
    ctypes.windll.gdi32.DeleteDC(hdc_mem)
    ctypes.windll.user32.ReleaseDC(top_hwnd, hdc_win)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)


def capture_window(ui: TkinterUI, win: tk.Misc, filename: str) -> Path:
    """Capture a live window cleanly with ImageGrab (with PrintWindow fallback)."""
    _flush(ui, wait_ms=140)
    try:
        win.attributes("-alpha", 1.0)
    except Exception:
        pass
    try:
        win.lift()
    except Exception:
        pass
    _flush(ui, wait_ms=40)

    out_path = OUT_DIR / filename
    try:
        x1 = int(win.winfo_rootx())
        y1 = int(win.winfo_rooty())
        x2 = x1 + max(10, int(win.winfo_width()))
        y2 = y1 + max(10, int(win.winfo_height()))
        # Include outer titlebar/border when decorated window is used
        if not bool(win.overrideredirect()):
            img = _print_window_fallback(win)
        else:
            img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True)
    except Exception:
        img = _print_window_fallback(win)

    img.save(out_path)
    return out_path


def main() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ui = TkinterUI(initial_theme="dark")
    shots: list[Path] = []

    try:
        # 1. Onboarding (Dark)
        render_demo_screen(ui, "onboarding")
        shots.append(capture_window(ui, ui.root, "01_onboarding_dark.png"))

        # 2. Setup (Dark & Light)
        render_demo_screen(ui, "setup")
        shots.append(capture_window(ui, ui.root, "02_setup_dark.png"))

        ui.set_theme("light")
        shots.append(capture_window(ui, ui.root, "03_setup_light.png"))
        ui.set_theme("dark")

        # 3. HUD states (Live Dark, AI Unavailable Dark, Mini-Pill Dark)
        render_demo_screen(ui, "hud")
        shots.append(capture_window(ui, ui.root, "04_hud_dark.png"))

        render_demo_screen(ui, "hud-offline")
        shots.append(capture_window(ui, ui.root, "05_hud_ai_unavailable_dark.png"))

        render_demo_screen(ui, "hud-mini")
        shots.append(capture_window(ui, ui.root, "06_hud_mini_dark.png"))

        ui.set_mini_mode(False)

        # 4. Popups & Strict Banner (Dark)
        render_demo_screen(ui, "layer1")
        _flush(ui, 120)
        assert ui._active_popups
        shots.append(capture_window(ui, ui._active_popups[0], "07_layer1_popup_dark.png"))

        render_demo_screen(ui, "layer2")
        _flush(ui, 120)
        assert ui._active_popups
        shots.append(capture_window(ui, ui._active_popups[0], "08_layer2_warning_dark.png"))

        render_demo_screen(ui, "strict")
        _flush(ui, 120)
        assert ui._strict_overlay_win is not None
        shots.append(capture_window(ui, ui._strict_overlay_win, "09_strict_banner_dark.png"))

        render_demo_screen(ui, "tier0")
        _flush(ui, 120)
        assert ui._tier0_popup is not None
        shots.append(capture_window(ui, ui._tier0_popup, "10_tier0_popup_dark.png"))

        ui.hide_popups_and_overlay()

        # 5. Review (Loading Dark, Complete Dark, Complete Light)
        render_demo_screen(ui, "review-loading")
        shots.append(capture_window(ui, ui.root, "11_review_loading_dark.png"))

        render_demo_screen(ui, "review")
        shots.append(capture_window(ui, ui.root, "12_review_complete_dark.png"))

        ui.set_theme("light")
        shots.append(capture_window(ui, ui.root, "13_review_complete_light.png"))
        ui.set_theme("dark")

        # 6. Settings Dialog (Dark)
        render_demo_screen(ui, "settings")
        _flush(ui, 120)
        assert ui._settings_dialog is not None
        shots.append(capture_window(ui, ui._settings_dialog.win, "14_settings_dark.png"))
    finally:
        ui.close()

    print(f"CAPTURED {len(shots)} SHOTS IN {OUT_DIR}")


if __name__ == "__main__":
    main()
