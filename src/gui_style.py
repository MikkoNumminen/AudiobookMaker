"""Centralized visual tokens for the AudiobookMaker GUI.

All colors, fonts, spacing, and corner radii used by gui_unified.py live
here so the palette can be tweaked in one file. Follows the "Cold Forge"
dark-first design system — slate near-black surfaces with an electric
blue accent.

CTk color entries are ("light_mode", "dark_mode") 2-tuples so the same
constant works regardless of the user's appearance setting.
"""
from __future__ import annotations

import sys
from pathlib import Path
from tkinter import font as tkfont
from typing import Optional

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Asset paths (work in both frozen PyInstaller bundle and dev checkout)
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _ROOT = Path(sys._MEIPASS)
else:
    _ROOT = Path(__file__).resolve().parent.parent

THEME_FILE = _ROOT / "assets" / "themes" / "cold_forge.json"
ICON_DIR = _ROOT / "assets" / "icons"

# ---------------------------------------------------------------------------
# Color tokens — (light, dark) pairs
# ---------------------------------------------------------------------------

BG_APP = ("#F5F7FA", "#0E1217")
BG_SURFACE_1 = ("#FFFFFF", "#161B22")
BG_SURFACE_2 = ("#EDF1F6", "#1E252E")
BG_SURFACE_3 = ("#E1E7EF", "#2A3140")
BORDER_SUBTLE = ("#D0D7DE", "#2C333D")
BORDER_FOCUS = ("#0969DA", "#4F9EE8")

TEXT_PRIMARY = ("#1F2328", "#E6EDF3")
TEXT_SECONDARY = ("#59636E", "#9CA9B7")
TEXT_MUTED = ("#8C959F", "#6B7785")

ACCENT = ("#0969DA", "#4F9EE8")
ACCENT_HOVER = ("#0550AE", "#6FB3F2")
ACCENT_PRESS = ("#033D8B", "#3D7EBC")

BTN_SECONDARY_BG = ("#EDF1F6", "#2A3140")
BTN_SECONDARY_HOVER = ("#DCE3EB", "#363E4B")

SUCCESS = ("#1A7F37", "#3FB950")
WARNING = ("#9A6700", "#D29922")
DANGER = ("#CF222E", "#F85149")
INFO = ("#0969DA", "#4F9EE8")

# Engine-status dot (replaces the old _CLR_READY / _NEEDS_SETUP / _UNAVAILABLE
# literals in gui_unified.py:179-181).
STATUS_DOT = {
    "ready": SUCCESS,
    "needs_setup": WARNING,
    "unavailable": DANGER,
}

# Status strip bg (replaces _STATUS_STRIP_COLORS in gui_unified.py:968-972).
STATUS_STRIP = {
    "ready": INFO,
    "synthesizing": INFO,
    "done": SUCCESS,
}

# ---------------------------------------------------------------------------
# Spacing (8pt grid) and corner radii
# ---------------------------------------------------------------------------

PAD_XS = 4
PAD_SM = 8
PAD_MD = 12
PAD_LG = 16
PAD_XL = 24

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14

# ---------------------------------------------------------------------------
# Font resolution — walk a fallback chain, never pass a name CTk will drop.
# ---------------------------------------------------------------------------


def _first_available(candidates: tuple[str, ...], fallback: str) -> str:
    """Return the first installed font family from ``candidates``.

    Falls back to ``fallback`` if none are installed or if font
    enumeration fails (e.g. no Tk root yet). Comparison is
    case-insensitive because Windows reports "Segoe UI" but some tools
    lowercase names.
    """
    try:
        available = {f.lower() for f in tkfont.families()}
    except Exception:
        return fallback
    for candidate in candidates:
        if candidate.lower() in available:
            return candidate
    return fallback


_UI_FAMILY: Optional[str] = None
_MONO_FAMILY: Optional[str] = None


def _ui_family() -> str:
    global _UI_FAMILY
    if _UI_FAMILY is None:
        _UI_FAMILY = _first_available(
            ("Segoe UI Variable Display", "Segoe UI", "Inter"),
            fallback="TkDefaultFont",
        )
    return _UI_FAMILY


def _mono_family() -> str:
    global _MONO_FAMILY
    if _MONO_FAMILY is None:
        _MONO_FAMILY = _first_available(
            ("Cascadia Mono", "Cascadia Code", "Consolas"),
            fallback="TkFixedFont",
        )
    return _MONO_FAMILY


def font_hero() -> ctk.CTkFont:
    """22pt bold — app name in the hero header."""
    return ctk.CTkFont(family=_ui_family(), size=22, weight="bold")


def font_tagline() -> ctk.CTkFont:
    """11pt — header subtitle + version string."""
    return ctk.CTkFont(family=_ui_family(), size=11)


def font_label() -> ctk.CTkFont:
    """12pt — field labels (Language, Engine, Voice, …)."""
    return ctk.CTkFont(family=_ui_family(), size=12)


def font_body() -> ctk.CTkFont:
    """13pt — combobox text, entry text, default body copy."""
    return ctk.CTkFont(family=_ui_family(), size=13)


def font_button() -> ctk.CTkFont:
    """13pt bold — secondary buttons (Sample, Preview, Open folder)."""
    return ctk.CTkFont(family=_ui_family(), size=13, weight="bold")


def font_primary_button() -> ctk.CTkFont:
    """15pt bold — Convert button."""
    return ctk.CTkFont(family=_ui_family(), size=15, weight="bold")


def font_small() -> ctk.CTkFont:
    """11pt — ETA, voice count, status sublabel."""
    return ctk.CTkFont(family=_ui_family(), size=11)


def font_log() -> ctk.CTkFont:
    """12pt monospace — log textbox."""
    return ctk.CTkFont(family=_mono_family(), size=12)


# ---------------------------------------------------------------------------
# Icon loader — ``CTkImage`` with (light, dark) PNG pair.
# ---------------------------------------------------------------------------


def icon(name: str, size: int = 20) -> Optional[ctk.CTkImage]:
    """Return a ``CTkImage`` for the named icon, or ``None`` if unavailable.

    Looks up ``assets/icons/{name}-light.png`` and ``{name}-dark.png``.
    Returns ``None`` if Pillow is not installed, the files are missing,
    or decoding fails. Callers MUST fall back to a text-only button.

    Pillow is a transitive dependency of CustomTkinter; in dev and normal
    PyInstaller bundles it is available. The ``PIL`` exclude in
    ``audiobookmaker.spec`` must be removed before icon assets ship.
    """
    try:
        from PIL import Image  # noqa: WPS433 — local import, optional dep
    except ImportError:
        return None
    light = ICON_DIR / f"{name}-light.png"
    dark = ICON_DIR / f"{name}-dark.png"
    if not (light.exists() and dark.exists()):
        return None
    try:
        return ctk.CTkImage(
            light_image=Image.open(light),
            dark_image=Image.open(dark),
            size=(size, size),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Theme bootstrap — call this once at startup in place of
# ``ctk.set_appearance_mode`` + ``ctk.set_default_color_theme``.
# ---------------------------------------------------------------------------


def apply_theme(appearance: str = "dark") -> None:
    """Configure CTk appearance + color theme.

    Loads ``assets/themes/cold_forge.json`` if present; otherwise falls
    back to CTk's built-in ``"blue"`` theme. Never raises — styling is
    cosmetic and must not block app startup.

    Must be called BEFORE any CTk widget is instantiated. Safe to call
    multiple times (each call re-applies).
    """
    try:
        ctk.set_appearance_mode(appearance)
    except Exception:
        pass
    if THEME_FILE.exists():
        try:
            ctk.set_default_color_theme(str(THEME_FILE))
            return
        except Exception:
            pass
    try:
        ctk.set_default_color_theme("blue")
    except Exception:
        pass
