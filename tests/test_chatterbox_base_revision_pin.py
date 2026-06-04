"""Tests for the Chatterbox base-model revision pin.

The library's ChatterboxMultilingualTTS.from_pretrained hardcodes
revision="main"; the installer patches that to an immutable SHA so an upstream
rename of the base weight files can't break loading. The patch must be fully
graceful (never break the install) and the SHA must stay consistent across the
prefetch list, both install paths, and this constant.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from src.engine_installer import (
    CHATTERBOX_BASE_REVISION,
    HF_REPOS,
    ChatterboxInstaller,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_POST_INSTALL = _REPO_ROOT / "installer" / "post_install_chatterbox.py"
_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _write_mtl(venv_root: Path, body: str) -> Path:
    p = venv_root / "Lib" / "site-packages" / "chatterbox" / "mtl_tts.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# --- constant consistency ---------------------------------------------------


def test_base_revision_is_a_sha() -> None:
    assert _SHA_RE.fullmatch(CHATTERBOX_BASE_REVISION)


def test_base_revision_matches_prefetch() -> None:
    resemble = next(
        rev for repo, _allow, rev in HF_REPOS
        if "chatterbox" in repo.lower() and "finnish" not in repo.lower()
    )
    assert resemble == CHATTERBOX_BASE_REVISION


def test_post_install_constant_matches() -> None:
    tree = ast.parse(_POST_INSTALL.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CHATTERBOX_BASE_REVISION"
            for t in node.targets
        ):
            assert ast.literal_eval(node.value) == CHATTERBOX_BASE_REVISION
            return
    raise AssertionError("CHATTERBOX_BASE_REVISION not found in post_install")


# --- patch behaviour --------------------------------------------------------


def test_pins_revision_main(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    f = _write_mtl(
        inst._venv_path,
        'd = snapshot_download(repo_id=R, revision="main", token=t)\n',
    )
    inst._pin_base_model_revision()
    text = f.read_text(encoding="utf-8")
    assert f'revision="{CHATTERBOX_BASE_REVISION}"' in text
    assert 'revision="main"' not in text


def test_is_idempotent(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    f = _write_mtl(inst._venv_path, 'revision="main"\n')
    inst._pin_base_model_revision()
    once = f.read_text(encoding="utf-8")
    inst._pin_base_model_revision()
    assert f.read_text(encoding="utf-8") == once


def test_skips_when_no_revision_main(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    f = _write_mtl(inst._venv_path, "no pinnable line here\n")
    inst._pin_base_model_revision()  # must not raise
    assert f.read_text(encoding="utf-8") == "no pinnable line here\n"


def test_skips_when_ambiguous(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    body = 'a revision="main"\nb revision="main"\n'
    f = _write_mtl(inst._venv_path, body)
    inst._pin_base_model_revision()
    assert f.read_text(encoding="utf-8") == body  # unchanged — don't mis-pin


def test_no_file_is_noop(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    # No mtl_tts.py written — must not raise.
    inst._pin_base_model_revision()
