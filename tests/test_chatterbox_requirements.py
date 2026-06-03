"""Parity + pinning guard for the Chatterbox engine dependency set.

The Chatterbox venv is installed by two separate code paths:

  * the in-app "Install engines" button  -> src/engine_installer.py
  * the Inno Setup wizard post-install    -> installer/post_install_chatterbox.py

Both must install byte-identical, fully-pinned package specs, and both must
match the canonical installer/requirements-chatterbox.txt. A floating
dependency (notably transformers) is what produced the silent
"Could not import module 'LlamaModel'" engine-load failures, so these tests
exist to make that class of drift impossible to merge:

  * every spec is exact-pinned (``name==version``) — no floats;
  * all three sources name the same packages at the same versions;
  * torch / torchaudio stay OUT (they install via the CUDA wheel index).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.engine_installer import PIP_PACKAGES_MAIN as ENGINE_INSTALLER_PACKAGES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REQUIREMENTS_TXT = _REPO_ROOT / "installer" / "requirements-chatterbox.txt"
_POST_INSTALL_PY = _REPO_ROOT / "installer" / "post_install_chatterbox.py"


def _normalize(spec: str) -> str:
    """Normalize one ``name==version`` spec for comparison.

    pip treats package names case-insensitively and as equivalent under
    ``-``/``_``/``.`` separators, so ``huggingface_hub`` and
    ``huggingface-hub`` are the same project. Normalize the name part to a
    canonical lower-hyphen form; leave the version untouched.
    """
    name, sep, version = spec.partition("==")
    canon = re.sub(r"[-_.]+", "-", name.strip().lower())
    return f"{canon}=={version.strip()}" if sep else canon


def _parse_requirements_txt(path: Path) -> list[str]:
    """Return the ``name==version`` specs from a requirements file.

    Comment lines (``#``) and blank lines are skipped.
    """
    specs: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        specs.append(line)
    return specs


def _extract_list_literal(path: Path, name: str) -> list[str]:
    """Extract a module-level ``name = [ ... ]`` string list via AST.

    Parsed rather than imported so a script outside the package (with a
    ``__main__`` block) is never executed by the test.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        if not isinstance(node.value, ast.List):
            raise AssertionError(f"{name} in {path} is not a list literal")
        out: list[str] = []
        for elt in node.value.elts:
            if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                raise AssertionError(
                    f"{name} in {path} has a non-string entry: {ast.dump(elt)}"
                )
            out.append(elt.value)
        return out
    raise AssertionError(f"{name} not found in {path}")


@pytest.fixture(scope="module")
def requirements_specs() -> list[str]:
    return _parse_requirements_txt(_REQUIREMENTS_TXT)


@pytest.fixture(scope="module")
def post_install_packages() -> list[str]:
    return _extract_list_literal(_POST_INSTALL_PY, "PIP_PACKAGES_MAIN")


def test_requirements_file_exists() -> None:
    assert _REQUIREMENTS_TXT.is_file(), (
        f"Canonical pinned requirements file missing: {_REQUIREMENTS_TXT}"
    )


def test_every_requirement_is_exact_pinned(requirements_specs: list[str]) -> None:
    for spec in requirements_specs:
        assert "==" in spec, f"requirements-chatterbox.txt entry not pinned: {spec!r}"
        # Exactly one '==' and a non-empty version.
        name, _, version = spec.partition("==")
        assert name.strip(), f"empty package name in {spec!r}"
        assert version.strip(), f"empty version in {spec!r}"
        assert "," not in version and ">" not in version and "<" not in version, (
            f"non-exact version specifier in {spec!r}"
        )


def test_both_installers_are_exact_pinned(post_install_packages: list[str]) -> None:
    for spec in (*ENGINE_INSTALLER_PACKAGES, *post_install_packages):
        assert "==" in spec, f"installer package not pinned: {spec!r}"


def test_torch_is_not_in_the_main_package_set(
    requirements_specs: list[str], post_install_packages: list[str]
) -> None:
    # torch / torchaudio install separately via the CUDA wheel index; if they
    # leak into the main set they would drag in the CPU wheel.
    for source in (requirements_specs, ENGINE_INSTALLER_PACKAGES, post_install_packages):
        names = {_normalize(s).split("==")[0] for s in source}
        assert "torch" not in names, "torch must install via --index-url, not the main set"
        assert "torchaudio" not in names, (
            "torchaudio must install via --index-url, not the main set"
        )


def test_all_three_sources_are_identical(
    requirements_specs: list[str], post_install_packages: list[str]
) -> None:
    req = {_normalize(s) for s in requirements_specs}
    engine = {_normalize(s) for s in ENGINE_INSTALLER_PACKAGES}
    wizard = {_normalize(s) for s in post_install_packages}

    assert engine == req, (
        "src/engine_installer.py PIP_PACKAGES_MAIN drifted from "
        f"requirements-chatterbox.txt.\n  only in installer: {sorted(engine - req)}\n"
        f"  only in requirements: {sorted(req - engine)}"
    )
    assert wizard == req, (
        "installer/post_install_chatterbox.py PIP_PACKAGES_MAIN drifted from "
        f"requirements-chatterbox.txt.\n  only in wizard: {sorted(wizard - req)}\n"
        f"  only in requirements: {sorted(req - wizard)}"
    )


def test_critical_chain_versions_are_locked(requirements_specs: list[str]) -> None:
    # These three are the load-bearing pins: chatterbox-tts==0.1.7 expects this
    # exact transformers/tokenizers pair. The LlamaModel failure is what happens
    # when transformers floats above this.
    specs = {_normalize(s) for s in requirements_specs}
    for required in (
        "chatterbox-tts==0.1.7",
        "transformers==5.2.0",
        "tokenizers==0.22.2",
    ):
        assert _normalize(required) in specs, f"missing critical pin: {required}"
