"""System tray integration using pystray and in-memory Pillow icons."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PIL import Image, ImageDraw

try:
    import pystray
except Exception:  # pragma: no cover - safe fallback when pystray is absent
    pystray = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

TRAY_STATE_COLORS: dict[str, str] = {
    "ON_TASK": "#16A34A",
    "GRACE_PERIOD": "#D97706",
    "LAYER_1_ASK": "#EA580C",
    "LAYER_2_ULTIMATUM": "#DC2626",
    "PAUSED": "#D97706",
    "IDLE": "#2563EB",
    "ENDED": "#475569",
}


def create_tray_icon_image(color_hex: str = "#16A34A", size: int = 64) -> Image.Image:
    """Render a crisp 64x64 rounded target badge in memory for the Windows tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(2, size // 16)
    radius = max(8, size // 4)
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=radius,
        fill=color_hex,
    )

    c = size // 2
    outer_r = int(size * 0.28)
    inner_r = int(size * 0.14)
    ring_w = max(3, size // 12)
    draw.ellipse(
        (c - outer_r, c - outer_r, c + outer_r, c + outer_r),
        outline="#FFFFFF",
        width=ring_w,
    )
    draw.ellipse(
        (c - inner_r, c - inner_r, c + inner_r, c + inner_r),
        fill="#FFFFFF",
    )
    return img


class SystemTrayManager:
    """Manages an optional Windows system tray icon when the HUD is minimized to tray."""

    def __init__(
        self,
        on_restore: Callable[[], None],
        on_toggle_pause: Callable[[], None] | None = None,
        on_end_session: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        icon_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.on_restore = on_restore
        self.on_toggle_pause = on_toggle_pause
        self.on_end_session = on_end_session
        self.on_quit = on_quit
        self._icon_factory = icon_factory

        self._lock = threading.Lock()
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._running: bool = False

    @staticmethod
    def is_available() -> bool:
        """Return True if pystray is importable."""
        return pystray is not None

    def _safe_invoke(self, fn: Callable[[], None] | None, label: str) -> None:
        if fn is None:
            return
        try:
            fn()
        except Exception:
            logger.exception("System tray action '%s' failed", label)

    def _build_menu(self) -> Any:
        if pystray is None:
            return None
        items = [
            pystray.MenuItem(
                "Show HUD",
                lambda _icon, _item: self._safe_invoke(self.on_restore, "restore"),
                default=True,
            ),
        ]
        if self.on_toggle_pause is not None:
            items.append(
                pystray.MenuItem(
                    "Pause / Resume",
                    lambda _icon, _item: self._safe_invoke(self.on_toggle_pause, "toggle_pause"),
                )
            )
        if self.on_end_session is not None:
            items.append(
                pystray.MenuItem(
                    "End Session",
                    lambda _icon, _item: self._safe_invoke(self.on_end_session, "end_session"),
                )
            )
        if self.on_quit is not None:
            items.append(pystray.Menu.SEPARATOR)
            items.append(
                pystray.MenuItem(
                    "Quit",
                    lambda _icon, _item: self._safe_invoke(self.on_quit, "quit"),
                )
            )
        return pystray.Menu(*items)

    def start(self, tooltip: str = "Anti-Distractor", state: str = "ON_TASK") -> bool:
        """Start the tray icon idempotently on a daemon thread without blocking Tk."""
        with self._lock:
            if self._running and self._icon is not None:
                return True
            if self._icon_factory is None and pystray is None:
                return False

            color = TRAY_STATE_COLORS.get(state, "#2563EB")
            image = create_tray_icon_image(color)
            menu = self._build_menu()

            try:
                if self._icon_factory is not None:
                    icon = self._icon_factory(
                        name="Anti-Distractor",
                        icon=image,
                        title=tooltip,
                        menu=menu,
                    )
                else:
                    icon = pystray.Icon(
                        "Anti-Distractor",
                        image,
                        tooltip,
                        menu,
                    )
            except Exception:
                logger.exception("Failed to create system tray icon")
                return False

            self._icon = icon
            self._running = True

            if hasattr(icon, "run_detached") and self._icon_factory is not None:
                try:
                    icon.run_detached()
                    return True
                except Exception:
                    logger.exception("Failed to run tray icon detached")

            if hasattr(icon, "run"):
                t = threading.Thread(
                    target=self._run_icon,
                    args=(icon,),
                    daemon=True,
                    name="SystemTrayThread",
                )
                self._thread = t
                t.start()
            return True

    def _run_icon(self, icon: Any) -> None:
        try:
            icon.run()
        except Exception:
            logger.exception("System tray loop terminated with error")
        finally:
            with self._lock:
                if self._icon is icon:
                    self._running = False
                    self._icon = None

    def update_state(
        self,
        state: str,
        paused: bool = False,
        tooltip: str | None = None,
    ) -> None:
        """Update tray icon badge color and tooltip safely."""
        with self._lock:
            icon = self._icon
            running = self._running
        if not running or icon is None:
            return

        key = "PAUSED" if paused else state
        color = TRAY_STATE_COLORS.get(key, "#2563EB")
        try:
            icon.icon = create_tray_icon_image(color)
            if tooltip is not None:
                icon.title = tooltip
        except Exception:
            logger.exception("Failed to update tray icon state")

    def stop(self) -> None:
        """Stop the tray icon cleanly so no ghost icon lingers on exit."""
        with self._lock:
            icon = self._icon
            self._icon = None
            self._running = False
            thread = self._thread
            self._thread = None

        if icon is not None and hasattr(icon, "stop"):
            try:
                icon.stop()
            except Exception:
                logger.exception("Failed stopping system tray icon")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
