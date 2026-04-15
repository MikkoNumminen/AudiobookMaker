"""Regression test: PyInstaller specs must bundle every src.tts_engine sibling.

The Chatterbox subprocess imports `src.tts_engine`, which in turn imports
sibling modules (`tts_normalizer_fi`, `tts_chunking`, `tts_audio`) at module
load time. If a future split adds another sibling and forgets to update the
.spec files, the installed app crashes with ModuleNotFoundError.

This test parses src/tts_engine.py to discover the actual sibling list and
asserts each sibling appears in both spec files.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TTS_ENGINE = _REPO_ROOT / "src" / "tts_engine.py"
_APP_SPEC = _REPO_ROOT / "audiobookmaker.spec"
_LAUNCHER_SPEC = _REPO_ROOT / "audiobookmaker_launcher.spec"


def _src_siblings_imported_by_tts_engine() -> set[str]:
    """Return the set of `src.X` module basenames imported by tts_engine.py."""
    tree = ast.parse(_TTS_ENGINE.read_text(encoding="utf-8"))
    siblings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("src.") and node.module != "src.tts_engine":
                siblings.add(node.module.removeprefix("src."))
    return siblings


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
