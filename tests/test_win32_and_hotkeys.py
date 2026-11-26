"""Unit tests for Win32/DWM window chrome helpers and GlobalHotkeyManager."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock, patch

from app.hotkeys import (
    HK_ID_HUD,
    HK_ID_PAUSE,
    HK_ID_RULES,
    WM_HOTKEY,
    WM_QUIT,
    GlobalHotkeyManager,
)
from app.win32_ui import (
    DWMWA_USE_IMMERSIVE_DARK_MODE,
    DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY,
    GWL_EXSTYLE,
    WS_EX_NOACTIVATE,
    WS_EX_TOPMOST,
    apply_brand_icon,
    apply_dark_titlebar,
    apply_noactivate_topmost,
    get_monitor_work_area,
)


def test_apply_dark_titlebar_and_legacy_fallback() -> None:
    fake_win = MagicMock()
    fake_win.winfo_id.return_value = 1001

    fake_u32 = MagicMock()
    fake_u32.GetParent.return_value = 2002

    fake_dwm = MagicMock()
    # First call (attr 20) succeeds -> no fallback call
    fake_dwm.DwmSetWindowAttribute.return_value = 0

    with (
        patch("app.win32_ui.sys.platform", "win32"),
        patch("app.win32_ui._get_user32", return_value=fake_u32),
        patch("app.win32_ui._get_dwmapi", return_value=fake_dwm),
    ):
        apply_dark_titlebar(fake_win, dark=True)
        assert fake_dwm.DwmSetWindowAttribute.call_count == 1
        args = fake_dwm.DwmSetWindowAttribute.call_args[0]
        assert args[1] == DWMWA_USE_IMMERSIVE_DARK_MODE

        # Now simulate older Win10 build where attr 20 returns non-zero -> falls back to attr 19
        fake_dwm.reset_mock()
        fake_dwm.DwmSetWindowAttribute.side_effect = [1, 0]
        apply_dark_titlebar(fake_win, dark=False)
        assert fake_dwm.DwmSetWindowAttribute.call_count == 2
        assert fake_dwm.DwmSetWindowAttribute.call_args_list[0][0][1] == DWMWA_USE_IMMERSIVE_DARK_MODE
        assert fake_dwm.DwmSetWindowAttribute.call_args_list[1][0][1] == DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY


def test_apply_noactivate_topmost_sets_ws_ex_noactivate_bitmask() -> None:
    fake_win = MagicMock()
    fake_win.winfo_id.return_value = 5000

    fake_u32 = MagicMock()
    fake_u32.GetParent.return_value = 6000
    fake_u32.GetWindowLongPtrW.return_value = 0x00000100

    with (
        patch("app.win32_ui.sys.platform", "win32"),
        patch("app.win32_ui._get_user32", return_value=fake_u32),
    ):
        apply_noactivate_topmost(fake_win)

    assert fake_u32.SetWindowLongPtrW.call_count == 1
    set_args = fake_u32.SetWindowLongPtrW.call_args[0]
    assert set_args[1] == GWL_EXSTYLE
    new_style = set_args[2]
    assert (new_style & WS_EX_NOACTIVATE) == WS_EX_NOACTIVATE
    assert (new_style & WS_EX_TOPMOST) == WS_EX_TOPMOST
    assert (new_style & 0x00000100) == 0x00000100
    assert fake_u32.SetWindowPos.call_count == 1


def test_get_monitor_work_area_multimonitor_and_fallback() -> None:
    fake_u32 = MagicMock()
    fake_u32.MonitorFromPoint.return_value = 99999

    def _fill_monitor_info(_hmon, info_ptr):
        info = info_ptr._obj
        info.rcWork.left = -1920
        info.rcWork.top = 0
        info.rcWork.right = 0
        info.rcWork.bottom = 1040
        return 1

    fake_u32.GetMonitorInfoW.side_effect = _fill_monitor_info

    with (
        patch("app.win32_ui.sys.platform", "win32"),
        patch("app.win32_ui._get_user32", return_value=fake_u32),
    ):
        area = get_monitor_work_area(-900, 120, fallback=(0, 0, 1920, 1080))
        assert area == (-1920, 0, 0, 1040)

        # Fallback when MonitorFromPoint returns 0
        fake_u32.MonitorFromPoint.return_value = 0
        area_fb = get_monitor_work_area(50, 50, fallback=(0, 0, 1920, 1080))
        assert area_fb == (0, 0, 1920, 1080)


def test_apply_brand_icon_caches_photoimage_on_root() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        apply_brand_icon(root)
        photo1 = getattr(root, "_brand_icon_photo", None)
        assert photo1 is not None

        child = tk.Toplevel(root)
        child.withdraw()
        apply_brand_icon(root, child)
        assert getattr(root, "_brand_icon_photo", None) is photo1
        child.destroy()
    finally:
        root.destroy()


def test_global_hotkey_manager_dispatch_and_mocked_win32_loop() -> None:
    events: list[str] = []

    # 1. Deterministic non-OS mode
    mgr = GlobalHotkeyManager(
        on_toggle_pause=lambda: events.append("pause"),
        on_toggle_hud=lambda: events.append("hud"),
        on_open_rules=lambda: events.append("rules"),
        enable_os_registration=False,
    )
    assert mgr.start() is True
    assert mgr.is_running is True
    assert mgr.trigger("toggle_pause") is True
    assert mgr.trigger("toggle_hud") is True
    assert mgr.trigger("open_rules") is True
    assert events == ["pause", "hud", "rules"]
    mgr.stop()
    assert mgr.is_running is False

    # 2. Mocked Win32 message loop mode verifying RegisterHotKey -> WM_HOTKEY -> WM_QUIT -> UnregisterHotKey
    loop_events: list[str] = []
    fake_u32 = MagicMock()
    fake_k32 = MagicMock()
    fake_k32.GetCurrentThreadId.return_value = 4242
    fake_u32.RegisterHotKey.return_value = 1

    queued_msgs = [
        (1, WM_HOTKEY, HK_ID_PAUSE),
        (1, WM_HOTKEY, HK_ID_HUD),
        (1, WM_HOTKEY, HK_ID_RULES),
        (0, WM_QUIT, 0),
    ]

    def _fake_get_msg(msg_ptr, _hwnd, _min, _max):
        if not queued_msgs:
            return 0
        ret, message, wparam = queued_msgs.pop(0)
        msg_ptr._obj.message = message
        msg_ptr._obj.wParam = wparam
        return ret

    fake_u32.GetMessageW.side_effect = _fake_get_msg

    with (
        patch("app.hotkeys.sys.platform", "win32"),
        patch("app.hotkeys._get_user32", return_value=fake_u32),
        patch("app.hotkeys._get_kernel32", return_value=fake_k32),
    ):
        os_mgr = GlobalHotkeyManager(
            on_toggle_pause=lambda: loop_events.append("pause"),
            on_toggle_hud=lambda: loop_events.append("hud"),
            on_open_rules=lambda: loop_events.append("rules"),
            enable_os_registration=True,
        )
        os_mgr.start()
        os_mgr.stop()

    assert loop_events == ["pause", "hud", "rules"]
    assert fake_u32.RegisterHotKey.call_count == 3
    assert fake_u32.UnregisterHotKey.call_count == 3
