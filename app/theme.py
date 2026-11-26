"""Linear / Vercel design system, palette tokens, typography, and CustomTkinter primitives.

DESIGN BRIEF
------------
1. Aesthetic Direction: Linear / Vercel desktop utility (2026) — quiet, intentional, spacious.
   - Monochrome Zinc/Slate base (`#09090B` / `#111114` / `#18181B` dark; `#FAFAFA` / `#FFFFFF` /
     `#F4F4F5` light) so content and live telemetry take center stage.
   - Single Indigo accent (`#6366F1` dark / `#4F46E5` light) reserved strictly for primary CTAs,
     focused inputs, and active selection states.
   - Semantic colors (emerald success, amber warning, crimson danger) kept soft and tinted inside
     pills/rails except when flagging urgent alerts or "AI unavailable - rules only".
2. Typography Hierarchy:
   - UI font stack: Inter (if installed) -> Segoe UI Variable Display / Text -> Segoe UI.
   - Monospace telemetry/countdowns: Cascadia Mono -> Consolas.
   - Sentence-case headings, clear contrast separation between primary text, secondary copy, and
     muted metadata captions.
3. Elevation & Surfaces:
   - Rounded surfaces (`12px` cards, `8px` inputs/buttons, `999px` status pills) paired with subtle
     `1px` hairline borders (`#27272A` dark / `#E4E4E7` light) and native Windows 11 DWM rounded
     corners + OS shadow on popups.
4. Single DPI Strategy:
   - Windows process DPI awareness (`SetProcessDpiAwareness(1)`) in `app/main.py` is the single
     scaling authority. CustomTkinter's automatic DPI scaling is explicitly deactivated
     (`deactivate_automatic_dpi_awareness()` + scaling pinned to `1.0`) so pixel math and font
     sizes never double-scale across CustomTkinter and standard Tk widgets.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any, Callable, Literal

import customtkinter as ctk

from app.models import AgentStateName

logger = logging.getLogger(__name__)

# Single DPI strategy: disable CTk's automatic DPI scaling so Windows process DPI awareness
# (configured in app/main.py) remains the sole scaling authority without double-scaling.
try:
    ctk.deactivate_automatic_dpi_awareness()
    ctk.set_widget_scaling(1.0)
    ctk.set_window_scaling(1.0)
except Exception:
    logger.debug("Could not deactivate CTk DPI scaling", exc_info=True)

ThemeType = Literal["light", "dark"]
ButtonVariant = Literal[
    "primary",
    "secondary",
    "danger",
    "danger_outline",
    "warning_outline",
    "success_outline",
    "ghost",
    "chip",
]

SPACING: dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 20,
    "xxl": 24,
}

RADII: dict[str, int] = {
    "sm": 6,
    "md": 8,
    "lg": 12,
    "pill": 999,
}

LIGHT_PALETTE: dict[str, str] = {
    "bg": "#FAFAFA",
    "surface": "#FFFFFF",
    "surface_alt": "#F4F4F5",
    "surface_hover": "#E4E4E7",
    "border": "#E4E4E7",
    "border_strong": "#D4D4D8",
    "text": "#09090B",
    "text_secondary": "#52525B",
    "text_muted": "#71717A",
    "primary": "#4F46E5",
    "primary_hover": "#4338CA",
    "primary_soft": "#EEF2FF",
    "primary_border": "#C7D2FE",
    "accent": "#4F46E5",
    "danger": "#C62828",
    "danger_hover": "#991B1B",
    "danger_soft": "#FEF2F2",
    "danger_border": "#FECACA",
    "danger_solid": "#C62828",
    "ai_unavailable": "#C62828",
    "warning": "#B45309",
    "warning_soft": "#FFFBEB",
    "warning_border": "#FDE68A",
    "success": "#15803D",
    "success_soft": "#F0FDF4",
    "success_border": "#BBF7D0",
    "ext_off": "#A1A1AA",
    "btn_secondary_bg": "#F4F4F5",
    "btn_secondary_hover": "#E4E4E7",
    "h1": "#09090B",
    "h2": "#18181B",
    "overlay_bg": "#09090B",
    "overlay_card": "#111114",
    "overlay_border": "#27272A",
    "overlay_text": "#FAFAFA",
    "overlay_reason": "#FCA5A5",
    "overlay_countdown": "#FBBF24",
}

DARK_PALETTE: dict[str, str] = {
    "bg": "#09090B",
    "surface": "#111114",
    "surface_alt": "#18181B",
    "surface_hover": "#202024",
    "border": "#27272A",
    "border_strong": "#3F3F46",
    "text": "#FAFAFA",
    "text_secondary": "#A1A1AA",
    "text_muted": "#71717A",
    "primary": "#6366F1",
    "primary_hover": "#4F46E5",
    "primary_soft": "#1E1B4B",
    "primary_border": "#4338CA",
    "accent": "#6366F1",
    "danger": "#F87171",
    "danger_hover": "#DC2626",
    "danger_soft": "#2A0D10",
    "danger_border": "#991B1B",
    "danger_solid": "#DC2626",
    "ai_unavailable": "#F87171",
    "warning": "#FBBF24",
    "warning_soft": "#2E1B05",
    "warning_border": "#92400E",
    "success": "#34D399",
    "success_soft": "#052E1C",
    "success_border": "#065F46",
    "ext_off": "#71717A",
    "btn_secondary_bg": "#18181B",
    "btn_secondary_hover": "#27272A",
    "h1": "#FAFAFA",
    "h2": "#E4E4E7",
    "overlay_bg": "#09090B",
    "overlay_card": "#111114",
    "overlay_border": "#27272A",
    "overlay_text": "#FAFAFA",
    "overlay_reason": "#FCA5A5",
    "overlay_countdown": "#FBBF24",
}

PALETTE: dict[str, str] = LIGHT_PALETTE

# Legacy mapping kept for backwards compatibility
STATE_COLORS: dict[AgentStateName, tuple[str, str]] = {
    "IDLE": ("#52525B", "#FAFAFA"),
    "ON_TASK": ("#059669", "#FAFAFA"),
    "GRACE_PERIOD": ("#D97706", "#FAFAFA"),
    "LAYER_1_ASK": ("#EA580C", "#FAFAFA"),
    "LAYER_2_ULTIMATUM": ("#DC2626", "#FAFAFA"),
    "ENDED": ("#27272A", "#FAFAFA"),
}

# Theme-aware soft pill tokens: (bg, fg, border, dot)
STATE_PILL_STYLES: dict[ThemeType, dict[str, tuple[str, str, str, str]]] = {
    "dark": {
        "IDLE": ("#18181B", "#A1A1AA", "#27272A", "#71717A"),
        "ON_TASK": ("#052E1C", "#34D399", "#065F46", "#10B981"),
        "GRACE_PERIOD": ("#2E1B05", "#FBBF24", "#92400E", "#F59E0B"),
        "LAYER_1_ASK": ("#331507", "#FB923C", "#9A3412", "#F97316"),
        "LAYER_2_ULTIMATUM": ("#2A0D10", "#F87171", "#991B1B", "#EF4444"),
        "PAUSED": ("#2E1B05", "#FBBF24", "#92400E", "#F59E0B"),
        "ENDED": ("#18181B", "#A1A1AA", "#27272A", "#71717A"),
    },
    "light": {
        "IDLE": ("#F4F4F5", "#52525B", "#E4E4E7", "#71717A"),
        "ON_TASK": ("#ECFDF5", "#047857", "#A7F3D0", "#10B981"),
        "GRACE_PERIOD": ("#FFFBEB", "#B45309", "#FDE68A", "#D97706"),
        "LAYER_1_ASK": ("#FFF7ED", "#C2410C", "#FED7AA", "#EA580C"),
        "LAYER_2_ULTIMATUM": ("#FEF2F2", "#B91C1C", "#FECACA", "#DC2626"),
        "PAUSED": ("#FFFBEB", "#B45309", "#FDE68A", "#D97706"),
        "ENDED": ("#F4F4F5", "#52525B", "#E4E4E7", "#71717A"),
    },
}


def get_state_pill_style(theme: ThemeType, state: str, *, paused: bool = False) -> tuple[str, str, str, str]:
    """Return (bg, fg, border, dot) colors for a state badge in the active theme."""
    key = "PAUSED" if paused else state
    table = STATE_PILL_STYLES["dark" if theme == "dark" else "light"]
    return table.get(key, table["IDLE"])


def detect_system_theme() -> ThemeType:
    """Detect Windows AppsUseLightTheme setting, defaulting safely to 'dark'."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if int(val) == 1 else "dark"
        except Exception:
            pass
    return "dark"


_RESOLVED_FONTS: dict[str, str] = {}


def _resolve_font_families(root: tk.Misc | None = None) -> tuple[str, str, str]:
    """Resolve best available (display, body, mono) font families cleanly once."""
    if _RESOLVED_FONTS:
        return (
            _RESOLVED_FONTS["display"],
            _RESOLVED_FONTS["body"],
            _RESOLVED_FONTS["mono"],
        )

    available_lower: dict[str, str] = {}
    try:
        for fam in tkfont.families(root=root):
            available_lower[fam.lower()] = fam
    except Exception:
        pass

    def _pick(candidates: tuple[str, ...], default: str) -> str:
        for cand in candidates:
            hit = available_lower.get(cand.lower())
            if hit:
                return hit
        return default

    display = _pick(("Inter", "Segoe UI Variable Display", "Segoe UI"), "Segoe UI")
    body = _pick(("Inter", "Segoe UI Variable Text", "Segoe UI"), "Segoe UI")
    mono = _pick(("Cascadia Mono", "Consolas"), "Consolas")

    _RESOLVED_FONTS["display"] = display
    _RESOLVED_FONTS["body"] = body
    _RESOLVED_FONTS["mono"] = mono
    return display, body, mono


_DPI_SCALE: float | None = None


def _get_dpi_scale() -> float:
    """Return the system DPI scale factor relative to 96 DPI (clamped 1.0..2.5)."""
    global _DPI_SCALE
    if _DPI_SCALE is not None:
        return _DPI_SCALE
    scale = 1.0
    if sys.platform == "win32":
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if dpi > 0:
                scale = max(1.0, min(2.5, dpi / 96.0))
        except Exception:
            scale = 1.0
    _DPI_SCALE = scale
    return scale


def _ctk_px(size: int) -> int:
    """Convert a nominal UI size to DPI-compensated pixel size for CustomTkinter."""
    # CTk converts positive font tuple integers into negative pixel sizes when
    # widget scaling is 1.0; compensate so text matches standard Windows pt sizing.
    return max(size, int(round(size * 1.15 * _get_dpi_scale())))


def ui_font(
    size: int,
    weight: Literal["normal", "bold"] = "normal",
    *,
    display: bool = False,
) -> tuple[str, int] | tuple[str, int, str]:
    """Return a crisp UI font tuple for CTk widgets (DPI-compensated px)."""
    disp_fam, body_fam, _ = _resolve_font_families()
    fam = disp_fam if display else body_fam
    px = _ctk_px(size)
    if weight == "bold":
        return (fam, px, "bold")
    return (fam, px)


def mono_font(
    size: int,
    weight: Literal["normal", "bold"] = "normal",
) -> tuple[str, int] | tuple[str, int, str]:
    """Return a crisp monospace font tuple for CTk widgets (DPI-compensated px)."""
    _, _, mono_fam = _resolve_font_families()
    px = _ctk_px(size)
    if weight == "bold":
        return (mono_fam, px, "bold")
    return (mono_fam, px)


def tk_ui_font(
    pt_size: int,
    weight: Literal["normal", "bold"] = "normal",
    *,
    display: bool = False,
) -> tuple[str, int] | tuple[str, int, str]:
    """Return a native Tk point-size UI font tuple for plain tk widgets."""
    disp_fam, body_fam, _ = _resolve_font_families()
    fam = disp_fam if display else body_fam
    if weight == "bold":
        return (fam, pt_size, "bold")
    return (fam, pt_size)


def tk_mono_font(
    pt_size: int,
    weight: Literal["normal", "bold"] = "normal",
) -> tuple[str, int] | tuple[str, int, str]:
    """Return a native Tk point-size monospace font tuple for plain tk widgets."""
    _, _, mono_fam = _resolve_font_families()
    if weight == "bold":
        return (mono_fam, pt_size, "bold")
    return (mono_fam, pt_size)


def resolve_variant_colors(
    variant: ButtonVariant,
    pal: dict[str, str],
) -> tuple[str, str, str, str, int]:
    """Return (bg, hover_bg, fg, bd_color, border_width) for a button variant."""
    if variant == "primary":
        return pal["primary"], pal["primary_hover"], "#FFFFFF", pal["primary"], 0
    if variant == "danger":
        bg = pal.get("danger_solid", pal["danger"])
        return bg, pal["danger_hover"], "#FFFFFF", bg, 0
    if variant == "danger_outline":
        return pal["danger_soft"], pal["danger_border"], pal["danger"], pal["danger_border"], 1
    if variant == "warning_outline":
        return pal["warning_soft"], pal["warning_border"], pal["warning"], pal["warning_border"], 1
    if variant == "success_outline":
        return pal["success_soft"], pal["success_border"], pal["success"], pal["success_border"], 1
    if variant == "ghost":
        return "transparent", pal["btn_secondary_hover"], pal["text_secondary"], pal["surface"], 0
    if variant == "chip":
        return pal["surface_alt"], pal["surface_hover"], pal["text_secondary"], pal["border_strong"], 1
    return pal["btn_secondary_bg"], pal["btn_secondary_hover"], pal["text"], pal["border_strong"], 1


class ModernButton(ctk.CTkButton):
    """CustomTkinter rounded button preserving `.invoke()` and hover metadata compatibility."""

    def __init__(
        self,
        master: Any,
        *,
        text: str = "",
        command: Callable[[], Any] | None = None,
        variant: ButtonVariant = "secondary",
        palette: dict[str, str] | None = None,
        font: Any = None,
        padx: int = 12,
        pady: int = 6,
        corner_radius: int = RADII["md"],
        width: int = 0,
        height: int = 0,
        **kwargs: Any,
    ) -> None:
        pal = palette or PALETTE
        self._variant: ButtonVariant = variant
        self._palette: dict[str, str] = pal
        self._user_command = command

        bg, hover_bg, fg, bd_color, bd_w = resolve_variant_colors(variant, pal)
        font_tuple = font if font is not None else ui_font(12, "bold")
        font_px = font_tuple[1] if isinstance(font_tuple, tuple) and len(font_tuple) >= 2 else _ctk_px(12)
        approx_char_px = max(8, int(font_px * 0.60))
        calc_w = max(width, font_px + padx * 2) if width > 0 else max(38, len(text) * approx_char_px + padx * 2)
        calc_h = max(height, font_px + pady * 2 + 4)

        super().__init__(
            master,
            text=text,
            command=command,
            width=calc_w,
            height=calc_h,
            corner_radius=corner_radius,
            border_width=bd_w,
            border_color=bd_color,
            fg_color=bg,
            hover_color=hover_bg,
            text_color=fg,
            text_color_disabled="#A1A1AA" if variant in ("primary", "danger") else pal["text_muted"],
            font=font_tuple,
            cursor="hand2",
            **kwargs,
        )
        self._hover_cfg = {
            "normal_bg": bg,
            "hover_bg": hover_bg,
            "normal_fg": fg,
            "hover_fg": fg,
        }

    def set_variant(
        self,
        variant: ButtonVariant,
        *,
        text: str | None = None,
        palette: dict[str, str] | None = None,
    ) -> None:
        """Update button variant + hover metadata cleanly in one atomic call."""
        pal = palette or self._palette or PALETTE
        self._variant = variant
        self._palette = pal
        bg, hover_bg, fg, bd_color, bd_w = resolve_variant_colors(variant, pal)
        cfg: dict[str, Any] = {
            "fg_color": bg,
            "hover_color": hover_bg,
            "text_color": fg,
            "border_width": bd_w,
            "border_color": bd_color,
        }
        if text is not None:
            cfg["text"] = text
        super().configure(**cfg)
        self._hover_cfg = {
            "normal_bg": bg,
            "hover_bg": hover_bg,
            "normal_fg": fg,
            "hover_fg": fg,
        }

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "bg" in kwargs:
            kwargs["fg_color"] = kwargs.pop("bg")
        if "fg" in kwargs:
            kwargs["text_color"] = kwargs.pop("fg")
        super().configure(require_redraw=require_redraw, **kwargs)

    config = configure

    def invoke(self) -> Any:
        """Trigger command when enabled (compatible with Tk test calls)."""
        if str(self.cget("state")) == "disabled":
            return None
        if self._user_command is not None:
            return self._user_command()
        return super().invoke()


class ModernLabel(ctk.CTkLabel):
    """CTkLabel wrapper preserving Tk-compatible `fg` / `foreground` attributes."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        if "fg" in kwargs:
            kwargs.setdefault("text_color", kwargs.pop("fg"))
        if "foreground" in kwargs:
            kwargs.setdefault("text_color", kwargs.pop("foreground"))
        if "bg" in kwargs:
            kwargs.setdefault("fg_color", kwargs.pop("bg"))
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "fg" in kwargs:
            kwargs["text_color"] = kwargs.pop("fg")
        if "foreground" in kwargs:
            kwargs["text_color"] = kwargs.pop("foreground")
        if "bg" in kwargs:
            kwargs["fg_color"] = kwargs.pop("bg")
        super().configure(require_redraw=require_redraw, **kwargs)

    config = configure

    def cget(self, attribute_name: str) -> Any:
        if attribute_name in ("fg", "foreground"):
            return super().cget("text_color")
        if attribute_name == "bg":
            return super().cget("fg_color")
        return super().cget(attribute_name)


class SurfaceCard(ctk.CTkFrame):
    """Elevated rounded card with subtle 1px border."""

    def __init__(
        self,
        master: Any,
        *,
        palette: dict[str, str] | None = None,
        fg_color: str | None = None,
        border_color: str | None = None,
        corner_radius: int = RADII["lg"],
        border_width: int = 1,
        **kwargs: Any,
    ) -> None:
        pal = palette or PALETTE
        super().__init__(
            master,
            fg_color=fg_color or pal["surface"],
            border_color=border_color or pal["border"],
            corner_radius=corner_radius,
            border_width=border_width,
            **kwargs,
        )


class StatusPill(ctk.CTkFrame):
    """Compact rounded status badge with optional indicator dot and crisp text."""

    def __init__(
        self,
        master: Any,
        *,
        text: str,
        bg: str,
        fg: str,
        border: str,
        dot_color: str | None = None,
        font: Any = None,
        padx: int = 8,
        pady: int = 3,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            fg_color=bg,
            border_color=border,
            border_width=1,
            corner_radius=RADII["pill"],
            **kwargs,
        )
        self._dot_lbl: ctk.CTkLabel | None = None
        if dot_color is not None:
            self._dot_lbl = ctk.CTkLabel(
                self,
                text="●",
                font=ui_font(9),
                text_color=dot_color,
                fg_color="transparent",
                width=0,
                height=0,
            )
            self._dot_lbl.pack(side="left", padx=(padx, 2), pady=pady)
            text_pad = (2, padx)
        else:
            text_pad = (padx, padx)

        self._text_lbl = ModernLabel(
            self,
            text=text.strip(),
            font=font or ui_font(10, "bold"),
            text_color=fg,
            fg_color="transparent",
            height=0,
        )
        self._text_lbl.pack(side="left", padx=text_pad, pady=pady)

    def set_pill(
        self,
        *,
        text: str,
        bg: str,
        fg: str,
        border: str,
        dot_color: str | None = None,
    ) -> None:
        self.configure(fg_color=bg, border_color=border)
        self._text_lbl.configure(text=text.strip(), text_color=fg)
        if self._dot_lbl is not None and dot_color is not None:
            self._dot_lbl.configure(text_color=dot_color)

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "text" in kwargs:
            self._text_lbl.configure(text=str(kwargs.pop("text")).strip())
        if "bg" in kwargs:
            kwargs["fg_color"] = kwargs.pop("bg")
        if "fg" in kwargs:
            self._text_lbl.configure(text_color=kwargs.pop("fg"))
        if kwargs:
            super().configure(require_redraw=require_redraw, **kwargs)

    config = configure

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "text":
            return self._text_lbl.cget("text")
        return super().cget(attribute_name)


class ModernEntry(ctk.CTkEntry):
    """Sleek rounded entry with focus accent ring matching Linear / Vercel inputs."""

    def __init__(
        self,
        master: Any,
        *,
        palette: dict[str, str] | None = None,
        font: Any = None,
        height: int = 36,
        corner_radius: int = RADII["md"],
        **kwargs: Any,
    ) -> None:
        pal = palette or PALETTE
        font_tuple = font or ui_font(12)
        font_px = font_tuple[1] if isinstance(font_tuple, tuple) and len(font_tuple) >= 2 else _ctk_px(12)
        super().__init__(
            master,
            fg_color=pal["surface_alt"],
            border_color=pal["border_strong"],
            border_width=1,
            corner_radius=corner_radius,
            height=max(height, font_px + 18),
            text_color=pal["text"],
            placeholder_text_color=pal["text_muted"],
            font=font_tuple,
            **kwargs,
        )
        self._pal = pal
        self.bind("<FocusIn>", lambda _e: self._set_focus_border(True), add="+")
        self.bind("<FocusOut>", lambda _e: self._set_focus_border(False), add="+")

    def _set_focus_border(self, focused: bool) -> None:
        try:
            self.configure(border_color=self._pal["primary"] if focused else self._pal["border_strong"])
        except Exception:
            pass


class ModernProgressBar(ctk.CTkProgressBar):
    """Slim rounded progress bar for indeterminate loading and strict countdowns."""

    def __init__(
        self,
        master: Any,
        *,
        palette: dict[str, str] | None = None,
        progress_color: str | None = None,
        height: int = 5,
        mode: Literal["determinate", "indeterminate"] = "indeterminate",
        **kwargs: Any,
    ) -> None:
        pal = palette or PALETTE
        super().__init__(
            master,
            height=height,
            corner_radius=RADII["pill"],
            border_width=0,
            fg_color=pal["surface_alt"],
            progress_color=progress_color or pal["primary"],
            mode=mode,
            indeterminate_speed=0.75,
            **kwargs,
        )

    def start(self, interval: Any = None) -> None:  # type: ignore[override]
        """Start indeterminate animation (accepting optional ttk-compatible interval arg)."""
        super().start()


def make_button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    *,
    variant: ButtonVariant = "secondary",
    font: Any = None,
    padx: int = 12,
    pady: int = 6,
    bind_hover: bool = True,
    palette: dict[str, str] | None = None,
    corner_radius: int = RADII["md"],
    width: int = 0,
    height: int = 0,
) -> ModernButton:
    """Create a Linear/Vercel styled ModernButton."""
    return ModernButton(
        parent,
        text=text,
        command=command,
        variant=variant,
        font=font,
        padx=padx,
        pady=pady,
        palette=palette,
        corner_radius=corner_radius,
        width=width,
        height=height,
    )


def set_button_variant(
    btn: Any,
    variant: ButtonVariant,
    *,
    text: str | None = None,
    palette: dict[str, str] | None = None,
) -> None:
    """Update a ModernButton (or fallback tk.Button) variant + hover state cleanly."""
    if isinstance(btn, ModernButton):
        btn.set_variant(variant, text=text, palette=palette)
        return
    pal = palette or PALETTE
    bg, hover_bg, fg, bd_color, bd_w = resolve_variant_colors(variant, pal)
    kwargs: dict[str, Any] = {
        "bg": bg,
        "fg": fg,
        "activebackground": hover_bg,
        "activeforeground": fg,
        "highlightthickness": bd_w,
        "highlightbackground": bd_color,
    }
    if text is not None:
        kwargs["text"] = text
    btn.configure(**kwargs)


def bind_drag(
    widget: tk.Misc,
    on_start: Callable[[tk.Event], None],
    on_move: Callable[[tk.Event], None],
    on_end: Callable[[tk.Event | None], None],
) -> None:
    """Bind drag handlers to widget and internal CTk canvas/label subwidgets."""
    seen: set[int] = set()

    def _walk(w: tk.Misc) -> None:
        if id(w) in seen:
            return
        seen.add(id(w))
        try:
            w.bind("<Button-1>", on_start, add="+")
            w.bind("<B1-Motion>", on_move, add="+")
            w.bind("<ButtonRelease-1>", on_end, add="+")
        except Exception:
            pass
        for child in w.winfo_children():
            # Only descend into internal primitive display widgets, never buttons
            if not isinstance(child, (ModernButton, ctk.CTkButton, tk.Button)):
                _walk(child)

    _walk(widget)


def fade_in_window(
    win: tk.Misc,
    *,
    target_alpha: float = 1.0,
    steps: int = 5,
    step_ms: int = 14,
) -> None:
    """Subtle fade-in animation on popup Toplevels; safely skipped when root is withdrawn."""
    try:
        master = win.winfo_toplevel()
        parent = getattr(win, "master", None)
        if parent is not None and parent.winfo_toplevel().state() == "withdrawn":
            return
        if master.state() == "withdrawn":
            return
    except Exception:
        return

    try:
        win.attributes("-alpha", 0.0)
    except Exception:
        return

    def _step(idx: int) -> None:
        try:
            if not win.winfo_exists():
                return
            alpha = min(target_alpha, target_alpha * (idx / max(1, steps)))
            win.attributes("-alpha", alpha)
            if idx < steps:
                win.after(step_ms, lambda: _step(idx + 1))
        except Exception:
            pass

    _step(1)


def apply_ttk_theme(root: tk.Misc, pal: dict[str, str]) -> None:
    """Configure minimal ttk styles (scrollbars, fallback entries) to harmonize with Zinc/Indigo."""
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "Modern.TEntry",
            fieldbackground=pal["surface_alt"],
            foreground=pal["text"],
            insertcolor=pal["text"],
            bordercolor=pal["border_strong"],
            lightcolor=pal["border_strong"],
            darkcolor=pal["border_strong"],
            padding=(10, 7),
        )
        style.map(
            "Modern.TEntry",
            bordercolor=[("focus", pal["primary"])],
            lightcolor=[("focus", pal["primary"])],
            darkcolor=[("focus", pal["primary"])],
        )
        style.configure(
            "Brand.Horizontal.TProgressbar",
            troughcolor=pal["surface_alt"],
            background=pal["primary"],
            bordercolor=pal["border"],
            lightcolor=pal["primary"],
            darkcolor=pal["primary"],
            thickness=5,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=pal["border_strong"],
            troughcolor=pal["surface"],
            bordercolor=pal["surface"],
            arrowcolor=pal["text_muted"],
            relief="flat",
        )
    except Exception:
        logger.debug("apply_ttk_theme skipped", exc_info=True)
