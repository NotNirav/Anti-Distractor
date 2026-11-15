"""In-memory screenshot capture and downscaling using mss and Pillow."""

from __future__ import annotations

import ctypes
import io
import mss
from PIL import Image


def _ensure_default_desktop() -> None:
    """Ensure the calling thread is attached to the interactive Windows default desktop."""
    try:
        user32 = ctypes.windll.user32
        h_desk = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)
            user32.CloseDesktop(h_desk)
    except Exception:
        pass


def capture_screenshot_bytes(max_width: int = 1024, jpeg_quality: int = 75) -> bytes:
    """Capture the primary monitor in memory via mss and return downscaled JPEG bytes.

    All processing occurs in memory without writing any image files to disk
    to strictly adhere to the privacy contract.
    """
    _ensure_default_desktop()

    mss_cls = getattr(mss, "MSS", mss.mss)
    with mss_cls() as sct:
        # Monitor 1 is the primary monitor in mss (monitor 0 is all monitors combined)
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        sct_img = sct.grab(monitor)

        # Convert raw RGB buffer to Pillow Image
        img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)

        # Downscale proportionally if wider than max_width
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        # Encode to JPEG bytes in-memory
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
        return buffer.getvalue()
