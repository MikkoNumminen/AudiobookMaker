"""Tests for src/gui_style.py — centralized visual tokens.

Split into two sections:
- Constant tests: assert on module-level values (colors, spacing, fonts).
  No Tk root needed — these run headlessly in CI.
- Tk-dependent tests: font measurements and apply_theme calls that need
  a live CTk root.  Gated on ``_shared_app``; skipped on headless CI
  via TclError catch in the fixture.
"""
from __future__ import annotations

import re

import pytest

ctk = pytest.importorskip("customtkinter")

from src import gui_style


HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

COLOR_TOKEN_NAMES = (
    "BG_APP", "BG_SURFACE_1", "BG_SURFACE_2", "BG_SURFACE_3",
    "BORDER_SUBTLE", "BORDER_FOCUS",
    "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED",
    "ACCENT", "ACCENT_HOVER", "ACCENT_PRESS",
    "BTN_SECONDARY_BG", "BTN_SECONDARY_HOVER",
    "SUCCESS", "WARNING", "DANGER", "INFO",
)


# ---------------------------------------------------------------------------
# Constant tests — no Tk required
# ---------------------------------------------------------------------------


class TestColorTokens:
    """gui_style color constants are well-formed (light, dark) hex pairs."""

    @pytest.mark.parametrize("name", COLOR_TOKEN_NAMES)
    def test_color_token_is_hex_pair(self, name: str) -> None:
        value = getattr(gui_style, name)
        assert isinstance(value, tuple) and len(value) == 2, f"{name} must be 2-tuple"
        for entry in value:
            assert isinstance(entry, str), f"{name} entries must be strings"
            assert HEX_RE.match(entry), f"{name} entry {entry!r} is not #RRGGBB"

    def test_status_dot_dict_shape(self) -> None:
        assert set(gui_style.STATUS_DOT) == {"ready", "needs_setup", "unavailable"}
        for key, value in gui_style.STATUS_DOT.items():
            assert isinstance(value, tuple) and len(value) == 2

    def test_status_strip_dict_shape(self) -> None:
        assert set(gui_style.STATUS_STRIP) == {"ready", "synthesizing", "done"}
        for key, value in gui_style.STATUS_STRIP.items():
            assert isinstance(value, tuple) and len(value) == 2


class TestSpacingAndRadii:
    """Spacing and radius constants are positive integers."""

    @pytest.mark.parametrize("name", [
        "PAD_XS", "PAD_SM", "PAD_MD", "PAD_LG", "PAD_XL",
        "RADIUS_SM", "RADIUS_MD", "RADIUS_LG",
    ])
    def test_spacing_and_radii_positive_int(self, name: str) -> None:
        value = getattr(gui_style, name)
        assert isinstance(value, int) and value > 0, f"{name} must be a positive int"


@pytest.mark.skipif(
    not gui_style.THEME_FILE.exists(),
    reason="theme not yet generated",
)
def test_theme_file_exists_in_dev_checkout() -> None:
    assert gui_style.THEME_FILE.is_file()


# ---------------------------------------------------------------------------
# Tk-dependent tests — require a real CTk root
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _shared_app():
    """Module-scoped: create one UnifiedApp for tests that need a real CTk root."""
    from src.tts_base import _REGISTRY
    from src.tts_edge import EdgeTTSEngine
    from src.tts_piper import PiperTTSEngine
    from src.gui_unified import UnifiedApp

    if "edge" not in _REGISTRY:
        _REGISTRY["edge"] = EdgeTTSEngine
    if "piper" not in _REGISTRY:
        _REGISTRY["piper"] = PiperTTSEngine

    try:
        instance = UnifiedApp()
    except Exception as exc:
        pytest.skip(f"No display / Tk init failed: {exc}")
        return
    instance.update_idletasks()
    yield instance
    instance.quit()
    instance.destroy()


class TestApplyTheme:
    """apply_theme() switches appearance mode without raising."""

    def test_apply_theme_dark(self, _shared_app) -> None:
        gui_style.apply_theme("dark")
        assert ctk.get_appearance_mode() == "Dark"

    def test_apply_theme_light(self, _shared_app) -> None:
        gui_style.apply_theme("light")
        # Restore dark for subsequent tests in the module.
        gui_style.apply_theme("dark")

    def test_apply_theme_missing_file_does_not_raise(self, monkeypatch, _shared_app) -> None:
        monkeypatch.setattr(
            gui_style, "THEME_FILE",
            gui_style.THEME_FILE.parent / "does_not_exist.json",
        )
        # Must not raise even when the custom theme is absent.
        gui_style.apply_theme("dark")


class TestFontHelpers:
    """Font helper functions return CTkFont objects of the expected size."""

    @pytest.mark.parametrize("fn_name,expected_size", [
        ("font_hero", 22),
        ("font_tagline", 11),
        ("font_label", 12),
        ("font_body", 13),
        ("font_button", 13),
        ("font_primary_button", 15),
        ("font_small", 11),
        ("font_log", 12),
    ])
    def test_font_helper_size(self, fn_name: str, expected_size: int, _shared_app) -> None:
        font = getattr(gui_style, fn_name)()
        assert isinstance(font, ctk.CTkFont)
        assert font.cget("size") == expected_size

    def test_first_available_falls_back(self, _shared_app) -> None:
        result = gui_style._first_available(
            ("NonexistentFontXYZ123",), fallback="TkDefaultFont",
        )
        assert result == "TkDefaultFont"


class TestIconHelper:
    """icon() helper returns None for missing icons (no Tk error)."""

    def test_icon_returns_none_for_missing(self, _shared_app) -> None:
        assert gui_style.icon("nonexistent_icon_name") is None
