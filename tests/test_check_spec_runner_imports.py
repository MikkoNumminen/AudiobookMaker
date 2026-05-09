"""Unit tests for scripts/check_spec_runner_imports.py."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_spec_runner_imports.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_spec_runner_imports", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_mod = _load_script()
_collect_runner_imports = _mod._collect_runner_imports
_collect_spec_bundled = _mod._collect_spec_bundled


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# _collect_runner_imports
# ===========================================================================


def test_from_src_module_import_detected(tmp_path: Path) -> None:
    """'from src.foo import bar' is collected."""
    runner = _write(tmp_path, "runner.py", "from src.foo import bar\n")
    assert _collect_runner_imports(runner) == {"foo"}


def test_import_src_module_detected(tmp_path: Path) -> None:
    """'import src.foo' (bare import form) is collected."""
    runner = _write(tmp_path, "runner.py", "import src.foo\n")
    assert _collect_runner_imports(runner) == {"foo"}


def test_multiple_imports_all_collected(tmp_path: Path) -> None:
    """Multiple src imports from one file are all returned."""
    runner = _write(
        tmp_path,
        "runner.py",
        "from src.foo import x\nfrom src.bar import y\n",
    )
    assert _collect_runner_imports(runner) == {"foo", "bar"}


def test_comment_lines_ignored(tmp_path: Path) -> None:
    """A commented-out import does NOT count as a real import."""
    runner = _write(tmp_path, "runner.py", "# from src.fake import nothing\n")
    assert _collect_runner_imports(runner) == set()


def test_indented_import_inside_function(tmp_path: Path) -> None:
    """An import indented inside a function body is still detected."""
    runner = _write(
        tmp_path,
        "runner.py",
        "def load():\n    from src.foo import bar\n",
    )
    assert _collect_runner_imports(runner) == {"foo"}


def test_empty_runner_returns_empty_set(tmp_path: Path) -> None:
    """An empty runner file produces an empty import set."""
    runner = _write(tmp_path, "runner.py", "")
    assert _collect_runner_imports(runner) == set()


def test_from_src_import_foo_without_dotmodule(tmp_path: Path) -> None:
    """'from src import foo' (no dot-submodule) is NOT caught — locks in existing behavior."""
    runner = _write(tmp_path, "runner.py", "from src import foo\n")
    assert _collect_runner_imports(runner) == set()


# ===========================================================================
# _collect_spec_bundled
# ===========================================================================


def test_single_quote_spec_entry(tmp_path: Path) -> None:
    """os.path.join('src', 'foo.py') with single quotes is parsed."""
    spec = _write(
        tmp_path,
        "app.spec",
        "datas = [(os.path.join('src', 'foo.py'), 'src')]\n",
    )
    assert _collect_spec_bundled(spec) == {"foo"}


def test_double_quote_spec_entry(tmp_path: Path) -> None:
    """os.path.join(\"src\", \"bar.py\") with double quotes is parsed."""
    spec = _write(
        tmp_path,
        "app.spec",
        'datas = [(os.path.join("src", "bar.py"), \'src\')]\n',
    )
    assert _collect_spec_bundled(spec) == {"bar"}


def test_mixed_quote_spec_entries(tmp_path: Path) -> None:
    """Both quote styles in the same spec file are recognized."""
    spec = _write(
        tmp_path,
        "app.spec",
        "(os.path.join('src', 'foo.py'), 'src'),\n"
        '(os.path.join("src", "bar.py"), \'src\'),\n',
    )
    assert _collect_spec_bundled(spec) == {"foo", "bar"}


def test_empty_spec_returns_empty_set(tmp_path: Path) -> None:
    """An empty spec file produces an empty bundled set."""
    spec = _write(tmp_path, "app.spec", "")
    assert _collect_spec_bundled(spec) == set()


def test_init_py_recognized(tmp_path: Path) -> None:
    """src/__init__.py is a valid spec entry and is collected as '__init__'."""
    spec = _write(tmp_path, "app.spec", "(os.path.join('src', '__init__.py'), 'src'),\n")
    assert "__init__" in _collect_spec_bundled(spec)


# ===========================================================================
# End-to-end cross-check (clean / missing / multiple-missing)
# ===========================================================================


def _run_e2e(tmp_path: Path, runner_text: str, spec_text: str) -> subprocess.CompletedProcess:
    """Write synthetic runner + spec, copy the script so it resolves paths to tmp_path."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "generate_chatterbox_audiobook.py").write_text(runner_text, encoding="utf-8")
    (tmp_path / "audiobookmaker.spec").write_text(spec_text, encoding="utf-8")
    guard = scripts_dir / "check_spec_runner_imports.py"
    shutil.copy(_SCRIPT, guard)
    return subprocess.run([sys.executable, str(guard)], capture_output=True, text=True)


def test_clean_state_passes(tmp_path: Path) -> None:
    """Runner import matches spec bundle → exit 0 with no output."""
    result = _run_e2e(
        tmp_path,
        runner_text="from src.foo import bar\n",
        spec_text="(os.path.join('src', 'foo.py'), 'src'),\n",
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_missing_bundle_fails_with_actionable_message(tmp_path: Path) -> None:
    """Runner imports src.bar but spec only bundles foo → exit 1 with fix hint."""
    result = _run_e2e(
        tmp_path,
        runner_text="from src.bar import baz\n",
        spec_text="(os.path.join('src', 'foo.py'), 'src'),\n",
    )
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "bar" in combined
    assert "Fix: add the following line" in combined
    assert "os.path.join('src', 'bar.py'), 'src'" in combined


def test_multiple_missing_bundles_reported(tmp_path: Path) -> None:
    """Both missing modules are named when spec bundles neither."""
    result = _run_e2e(
        tmp_path,
        runner_text="from src.foo import x\nfrom src.bar import y\n",
        spec_text="# nothing bundled\n",
    )
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "foo" in combined
    assert "bar" in combined


def test_empty_runner_and_spec_exit_zero(tmp_path: Path) -> None:
    """No imports and no bundles → nothing to verify → exit 0."""
    result = _run_e2e(tmp_path, runner_text="", spec_text="")
    assert result.returncode == 0


# ===========================================================================
# Real-repo smoke test (live canary)
# ===========================================================================


def test_real_repo_exits_zero() -> None:
    """The script passes against the actual repo files.

    This is the live-canary test: if someone removes a real bundled file from
    the spec without updating the runner (or vice versa), this test fails in CI
    before the PyInstaller build even starts.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "check_spec_runner_imports.py found a mismatch in the real repo:\n"
        + result.stderr
        + result.stdout
    )
