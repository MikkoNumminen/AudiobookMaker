"""Regression test: PyInstaller specs must bundle every src module the
Chatterbox subprocess can reach.

The Chatterbox runner (``scripts/generate_chatterbox_audiobook.py``) is
executed by the separate ``.venv-chatterbox`` interpreter and imports
``from src.X import`` straight off the bundled ``_internal/src/`` tree —
PyInstaller's dependency analysis never runs for it. So every src module in
the runner's FULL TRANSITIVE import closure must have a ``datas`` entry in
BOTH shipped specs (``audiobookmaker.spec`` and ``audiobookmaker_cli.spec``).

A direct-imports-only check is not enough: ``tts_normalizer_fi_legal`` shipped
broken in 3.15.0 because it is reachable only transitively (runner →
``tts_normalizer`` → ``tts_normalizer_fi`` → ``tts_normalizer_fi_legal``). The
closure walk lives in ``scripts/check_spec_runner_imports.py`` and is reused
here so the CI guard and this test can never drift apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_spec_runner_imports as guard  # noqa: E402

_SRC = _REPO_ROOT / "src"
_APP_SPEC = _REPO_ROOT / "audiobookmaker.spec"
_CLI_SPEC = _REPO_ROOT / "audiobookmaker_cli.spec"

# Full transitive src closure of the Chatterbox runner — every flat src/X.py
# module it can reach directly or via a chain. Both shipped datas specs must
# bundle all of these.
_RUNNER_CLOSURE = sorted(
    m for m in guard._transitive_src_closure(guard._RUNNER)
    if (_SRC / f"{m}.py").exists()
)


@pytest.mark.parametrize("module", _RUNNER_CLOSURE)
def test_app_spec_bundles_runner_module(module: str) -> None:
    """Every src module the runner can reach must be a datas entry in
    audiobookmaker.spec, else the frozen Chatterbox subprocess crashes with
    ModuleNotFoundError (this is how tts_normalizer_fi_legal shipped broken
    in 3.15.0)."""
    assert module in guard._collect_spec_bundled(_APP_SPEC), (
        f"audiobookmaker.spec is missing a datas entry for src/{module}.py "
        f"(reached transitively by the Chatterbox runner)."
    )


@pytest.mark.parametrize("module", _RUNNER_CLOSURE)
def test_cli_spec_bundles_runner_module(module: str) -> None:
    """The same closure must be bundled in audiobookmaker_cli.spec — the CLI
    zip ships the same runner and reads src/*.py off disk the same way."""
    assert module in guard._collect_spec_bundled(_CLI_SPEC), (
        f"audiobookmaker_cli.spec is missing a datas entry for src/{module}.py "
        f"(reached transitively by the Chatterbox runner)."
    )


def test_runner_closure_includes_known_transitive_deps() -> None:
    """Canary: the closure walk must keep surfacing the DEEP transitive deps
    that historically shipped unbundled — tts_normalizer_fi_legal (runner →
    tts_normalizer → tts_normalizer_fi → it) and ocr_path (via pdf_parser).
    If the closure logic regresses, the per-module tests above would silently
    stop checking them, so assert their presence directly."""
    assert "tts_normalizer_fi_legal" in _RUNNER_CLOSURE
    assert "ocr_path" in _RUNNER_CLOSURE


# ---------------------------------------------------------------------------
# Cold Forge visual assets — theme JSON is loaded by src/gui_style.py at
# startup. A missing file falls back to CTk's built-in "blue" theme, but we
# still want CI to flag if the asset ever drops out of the bundle.
# ---------------------------------------------------------------------------


def test_audiobookmaker_spec_bundles_cold_forge_theme() -> None:
    """assets/themes/cold_forge.json must be listed in the app spec's datas."""
    spec_text = _APP_SPEC.read_text(encoding="utf-8")
    assert "cold_forge.json" in spec_text, (
        "audiobookmaker.spec is missing a datas entry for "
        "assets/themes/cold_forge.json; the frozen app will fall back to "
        "CTk's default blue theme instead of the Cold Forge palette."
    )


# ---------------------------------------------------------------------------
# Lucide icon bundle — every interactive button pulls its glyph from
# ``assets/icons/<name>-{light,dark}.png`` via gui_style.icon(). Missing
# files degrade silently (button loses its icon, keeps its text) so CI
# needs an explicit check that the glob keeps them shipped. Paired with
# scripts/generate_icons.py which generates the PNGs.
# ---------------------------------------------------------------------------


def test_audiobookmaker_spec_bundles_icon_set() -> None:
    """The spec must include a glob covering assets/icons/*.png."""
    spec_text = _APP_SPEC.read_text(encoding="utf-8")
    assert "assets/icons" in spec_text or "'assets', 'icons'" in spec_text, (
        "audiobookmaker.spec is missing a datas entry for assets/icons/*.png; "
        "run scripts/generate_icons.py and add a glob to the spec."
    )


def test_icon_set_fully_rendered() -> None:
    """All 12 icon names must exist on disk as light+dark PNG pairs.

    Anyone adding a new icon name to gui_style / button call sites needs
    to also re-run scripts/generate_icons.py so the bitmap lands in git.
    """
    icon_dir = _REPO_ROOT / "assets" / "icons"
    expected = {
        "play", "music", "x", "folder", "settings", "volume",
        "book", "text", "list", "download", "chevron-down", "mic",
    }
    missing: list[str] = []
    for name in sorted(expected):
        for variant in ("light", "dark"):
            p = icon_dir / f"{name}-{variant}.png"
            if not p.exists():
                missing.append(p.name)
    assert not missing, (
        f"Missing icon PNGs: {missing}. Re-run scripts/generate_icons.py "
        f"to regenerate the Cold Forge icon set."
    )
