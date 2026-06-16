"""Tests for the s3gen flow out-of-range token clamp.

``chatterbox/models/s3gen/flow.py::inference()`` logs out-of-range speech
tokens but then feeds them into an ``nn.Embedding`` lookup unclamped — an index
>= ``vocab_size`` is an out-of-bounds read -> CUDA device-side assert -> the
whole multi-hour synthesis run dies mid-book.

Two delivery points apply the SAME one-line clamp:

* the installer (``ChatterboxInstaller._patch_flow_token_clamp``) at
  install/repair, so a freshly built venv is correct before first synthesis;
* the runner (``_ensure_flow_token_clamp``) at startup, so an app auto-update —
  which ships a new runner but never touches the separate engine venv — still
  delivers the fix on the next synthesis with no Repair.

Both must apply the identical edit, be idempotent, and be fully graceful (a miss
must never break an install or a run).
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

from src.engine_installer import ChatterboxInstaller

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402

# The exact upstream line and its clamped replacement (indentation preserved by
# the substring replace, so the search strings carry none).
_UNPATCHED = "        token = self.input_embedding(token.long()) * mask"
_CLAMPED = (
    "        token = self.input_embedding("
    "token.clamp(0, self.vocab_size - 1).long()) * mask"
)


def _flow_body(line: str) -> str:
    return (
        "class CausalMaskedDiffWithXvec:\n"
        "    vocab_size = 6561\n"
        "    def inference(self, token, token_len):\n"
        "        if (token >= self.vocab_size).any():\n"
        "            logger.error('out-of-range special tokens found in flow')\n"
        f"{line}\n"
        "        return token\n"
    )


def _write_installer_flow(venv_root: Path, body: str) -> Path:
    p = (
        venv_root / "Lib" / "site-packages" / "chatterbox"
        / "models" / "s3gen" / "flow.py"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _write_runner_flow(monkeypatch, purelib: Path, body: str) -> Path:
    flow = purelib / "chatterbox" / "models" / "s3gen" / "flow.py"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text(body, encoding="utf-8")
    monkeypatch.setattr(
        sysconfig, "get_paths", lambda *a, **k: {"purelib": str(purelib)}
    )
    return flow


# --- installer patch --------------------------------------------------------


def test_installer_clamps_the_embedding_lookup(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    f = _write_installer_flow(inst._venv_path, _flow_body(_UNPATCHED))
    inst._patch_flow_token_clamp()
    text = f.read_text(encoding="utf-8")
    assert _CLAMPED in text
    assert _UNPATCHED not in text


def test_installer_is_idempotent(tmp_path) -> None:
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    f = _write_installer_flow(inst._venv_path, _flow_body(_CLAMPED))
    inst._patch_flow_token_clamp()
    assert f.read_text(encoding="utf-8") == _flow_body(_CLAMPED)


def test_installer_noop_when_shape_changed(tmp_path) -> None:
    # Upstream rewrote the line — refuse to blind-edit, leave it untouched.
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    drifted = _flow_body("        token = embed(token) * mask  # rewritten")
    f = _write_installer_flow(inst._venv_path, drifted)
    inst._patch_flow_token_clamp()
    assert f.read_text(encoding="utf-8") == drifted


def test_installer_graceful_when_file_missing(tmp_path) -> None:
    # No chatterbox in the venv — a no-op, never an exception.
    inst = ChatterboxInstaller(venv_path=tmp_path / "empty-venv")
    inst._patch_flow_token_clamp()  # must not raise


# --- runner self-heal -------------------------------------------------------


def test_runner_self_heal_clamps_before_import(monkeypatch, tmp_path) -> None:
    flow = _write_runner_flow(
        monkeypatch, tmp_path / "site-packages", _flow_body(_UNPATCHED)
    )
    gca._ensure_flow_token_clamp()
    text = flow.read_text(encoding="utf-8")
    assert _CLAMPED in text
    assert _UNPATCHED not in text


def test_runner_self_heal_is_idempotent(monkeypatch, tmp_path) -> None:
    flow = _write_runner_flow(
        monkeypatch, tmp_path / "site-packages", _flow_body(_CLAMPED)
    )
    gca._ensure_flow_token_clamp()
    assert flow.read_text(encoding="utf-8") == _flow_body(_CLAMPED)


def test_runner_self_heal_graceful_when_missing(monkeypatch, tmp_path) -> None:
    # purelib has no chatterbox tree — best-effort no-op, never raises.
    monkeypatch.setattr(
        sysconfig, "get_paths", lambda *a, **k: {"purelib": str(tmp_path)}
    )
    gca._ensure_flow_token_clamp()  # must not raise


# --- the two paths agree ----------------------------------------------------


def test_installer_and_runner_apply_identical_edit(monkeypatch, tmp_path) -> None:
    # Installer patches one copy; runner patches an identical copy. The results
    # must be byte-identical, and each must treat the other's output as already
    # clamped (no double-patch) — otherwise the two delivery points would fight.
    inst = ChatterboxInstaller(venv_path=tmp_path / "venv")
    inst_flow = _write_installer_flow(inst._venv_path, _flow_body(_UNPATCHED))
    inst._patch_flow_token_clamp()
    inst_result = inst_flow.read_text(encoding="utf-8")

    runner_flow = _write_runner_flow(
        monkeypatch, tmp_path / "site-packages", _flow_body(_UNPATCHED)
    )
    gca._ensure_flow_token_clamp()
    runner_result = runner_flow.read_text(encoding="utf-8")

    assert inst_result == runner_result
    # Cross-check idempotency: runner sees the installer's output as done.
    runner_on_installer = _write_runner_flow(
        monkeypatch, tmp_path / "sp2", inst_result
    )
    gca._ensure_flow_token_clamp()
    assert runner_on_installer.read_text(encoding="utf-8") == inst_result
