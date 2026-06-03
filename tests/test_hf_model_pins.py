"""Guard that HuggingFace model fetches stay pinned to immutable revisions.

A floating model fetch (default branch) is the same failure class as a rotated
download URL: an upstream rename/move/force-push silently breaks the install.
These tests assert every prefetch repo pins a 40-hex commit SHA, the two
install paths agree, and the Finnish prefetch revision matches the revision the
synthesis runner actually requests.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from src.engine_installer import HF_REPOS as ENGINE_HF_REPOS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_POST_INSTALL = _REPO_ROOT / "installer" / "post_install_chatterbox.py"

# Import the runner (lives under scripts/, not a package) for FINNISH_REVISION.
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402

_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _extract_hf_repos(path: Path):
    """literal_eval the module-level HF_REPOS list (all literal entries)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "HF_REPOS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"HF_REPOS not found in {path}")


def _rev_by_repo(repos) -> dict[str, str]:
    # Each entry is (repo_id, allow_patterns, revision).
    return {entry[0]: entry[2] for entry in repos}


def test_runner_finnish_revision_is_a_sha() -> None:
    assert _SHA_RE.fullmatch(gca.FINNISH_REVISION), (
        f"FINNISH_REVISION must be a 40-hex SHA, not {gca.FINNISH_REVISION!r}"
    )


def test_every_engine_prefetch_repo_pins_a_sha() -> None:
    for entry in ENGINE_HF_REPOS:
        assert len(entry) == 3, f"HF_REPOS entry must be (repo, allow, revision): {entry}"
        repo_id, _allow, revision = entry
        assert _SHA_RE.fullmatch(revision), (
            f"{repo_id} prefetch revision must be a 40-hex SHA, not {revision!r} "
            "(a branch like 'main' would defeat the pin)"
        )


def test_both_install_paths_pin_identical_revisions() -> None:
    engine = _rev_by_repo(ENGINE_HF_REPOS)
    wizard = _rev_by_repo(_extract_hf_repos(_POST_INSTALL))
    assert engine == wizard, (
        "HF_REPOS revisions drifted between src/engine_installer.py and "
        f"installer/post_install_chatterbox.py.\n  engine: {engine}\n  wizard: {wizard}"
    )


def test_finnish_prefetch_matches_runner_revision() -> None:
    engine = _rev_by_repo(ENGINE_HF_REPOS)
    finnish = next(rev for repo, rev in engine.items() if "Finnish" in repo)
    assert finnish == gca.FINNISH_REVISION, (
        "The Finnish prefetch revision must match FINNISH_REVISION in the runner "
        "so the install populates exactly the revision synthesis requests "
        f"(prefetch={finnish}, runner={gca.FINNISH_REVISION})"
    )


def _extract_const(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def test_dev_script_finnish_revision_matches_runner() -> None:
    # The dev helper duplicates the Finnish constants; keep its pin in sync.
    rev = _extract_const(_REPO_ROOT / "dev_chatterbox_fi.py", "FINNISH_REVISION")
    assert rev == gca.FINNISH_REVISION
