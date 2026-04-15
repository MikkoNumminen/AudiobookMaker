"""Regression test: PyInstaller specs must bundle every transitively-imported
sibling that the Chatterbox subprocess might reach at runtime.

The Chatterbox subprocess imports `src.tts_engine`, which imports sibling
modules at module load time, plus `src.tts_normalizer` (the dispatcher),
which lazily imports per-language modules (`tts_normalizer_fi`,
`tts_normalizer_en`) inside `normalize_text`. If a new sibling lands in
either layer and the .spec files don't list it, the installed app crashes
with ModuleNotFoundError.

This test parses both `src/tts_engine.py` and `src/tts_normalizer.py` to
discover the full sibling set and asserts each one is registered in both
spec files.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TTS_ENGINE = _REPO_ROOT / "src" / "tts_engine.py"
_TTS_NORMALIZER = _REPO_ROOT / "src" / "tts_normalizer.py"
_TTS_NORMALIZER_EN = _REPO_ROOT / "src" / "tts_normalizer_en.py"
_APP_SPEC = _REPO_ROOT / "audiobookmaker.spec"
_LAUNCHER_SPEC = _REPO_ROOT / "audiobookmaker_launcher.spec"


def _src_siblings_imported_by(path: Path) -> set[str]:
    """Return the set of `src.X` module basenames imported by `path`.

    Walks both top-level `from src.X import …` statements and lazy imports
    nested inside function bodies — the dispatcher uses the latter to keep
    the FI and EN normalizers from being eagerly loaded.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    self_module = f"src.{path.stem}"
    siblings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("src.") and node.module != self_module:
                siblings.add(node.module.removeprefix("src."))
    return siblings


def _src_siblings_imported_by_tts_engine() -> set[str]:
    return (
        _src_siblings_imported_by(_TTS_ENGINE)
        | _src_siblings_imported_by(_TTS_NORMALIZER)
        | _src_siblings_imported_by(_TTS_NORMALIZER_EN)
    )


@pytest.mark.parametrize("sibling", sorted(_src_siblings_imported_by_tts_engine()))
def test_audiobookmaker_spec_bundles_sibling(sibling: str) -> None:
    """Each src.X imported by tts_engine.py must appear in audiobookmaker.spec datas."""
    spec_text = _APP_SPEC.read_text(encoding="utf-8")
    needle = f"{sibling}.py"
    assert needle in spec_text, (
        f"audiobookmaker.spec is missing a datas entry for src/{needle}; "
        f"the Chatterbox subprocess will crash with ModuleNotFoundError."
    )


@pytest.mark.parametrize("sibling", sorted(_src_siblings_imported_by_tts_engine()))
def test_launcher_spec_declares_sibling_hidden_import(sibling: str) -> None:
    """Each src.X imported by tts_engine.py must appear in audiobookmaker_launcher.spec hidden_imports."""
    spec_text = _LAUNCHER_SPEC.read_text(encoding="utf-8")
    needle = f'"src.{sibling}"'
    assert needle in spec_text, (
        f"audiobookmaker_launcher.spec is missing hidden_imports entry "
        f"for src.{sibling}; PyInstaller will not freeze it into the launcher."
    )
