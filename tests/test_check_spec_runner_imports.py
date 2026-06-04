"""Unit tests for scripts/check_spec_runner_imports.py.

The guard walks the FULL transitive closure of the Chatterbox runner's
``src`` imports and checks it against the datas lists of both shipped specs
(audiobookmaker.spec + audiobookmaker_cli.spec). These tests cover the
import parser, the closure walk, the spec parser, and an end-to-end run
against a synthetic source tree.
"""
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
_src_imports_in_text = _mod._src_imports_in_text
_collect_spec_bundled = _mod._collect_spec_bundled
_transitive_src_closure = _mod._transitive_src_closure


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# _src_imports_in_text
# ===========================================================================


def test_from_src_module_import_detected() -> None:
    assert _src_imports_in_text("from src.foo import bar\n") == {"foo"}


def test_import_src_module_detected() -> None:
    assert _src_imports_in_text("import src.foo\n") == {"foo"}


def test_multiple_imports_all_collected() -> None:
    assert _src_imports_in_text("from src.foo import x\nfrom src.bar import y\n") == {
        "foo",
        "bar",
    }


def test_comment_lines_ignored() -> None:
    assert _src_imports_in_text("# from src.fake import nothing\n") == set()


def test_indented_import_inside_function_detected() -> None:
    assert _src_imports_in_text("def load():\n    from src.foo import bar\n") == {"foo"}


def test_empty_text_returns_empty_set() -> None:
    assert _src_imports_in_text("") == set()


def test_from_src_import_bare_name() -> None:
    assert _src_imports_in_text("from src import foo\n") == {"foo"}


def test_from_src_import_multiple_names() -> None:
    assert _src_imports_in_text("from src import foo, bar, baz\n") == {"foo", "bar", "baz"}


def test_from_src_import_with_alias() -> None:
    assert _src_imports_in_text("from src import foo as f\n") == {"foo"}


def test_from_src_import_with_trailing_comment() -> None:
    assert _src_imports_in_text("from src import foo  # bundled separately\n") == {"foo"}


# ===========================================================================
# _transitive_src_closure
# ===========================================================================


def test_closure_follows_chain(tmp_path: Path, monkeypatch) -> None:
    """runner -> a -> b -> c: the whole chain is returned, not just `a`."""
    src = tmp_path / "src"
    src.mkdir()
    _write(src, "a.py", "from src.b import x\n")
    _write(src, "b.py", "from src.c import y\n")
    _write(src, "c.py", "z = 1\n")
    runner = _write(tmp_path, "runner.py", "from src.a import q\n")
    monkeypatch.setattr(_mod, "_SRC_DIR", src)
    assert _transitive_src_closure(runner) == {"a", "b", "c"}


def test_closure_terminates_on_cycle(tmp_path: Path, monkeypatch) -> None:
    """a <-> b import each other; the walk must not loop forever."""
    src = tmp_path / "src"
    src.mkdir()
    _write(src, "a.py", "from src.b import x\n")
    _write(src, "b.py", "from src.a import y\n")
    runner = _write(tmp_path, "runner.py", "from src.a import q\n")
    monkeypatch.setattr(_mod, "_SRC_DIR", src)
    assert _transitive_src_closure(runner) == {"a", "b"}


def test_closure_recurses_into_subpackage_init(tmp_path: Path, monkeypatch) -> None:
    """A `src.pkg` import resolves to src/pkg/__init__.py and its imports."""
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    _write(src / "pkg", "__init__.py", "from src.leaf import z\n")
    _write(src, "leaf.py", "w = 1\n")
    runner = _write(tmp_path, "runner.py", "from src.pkg import thing\n")
    monkeypatch.setattr(_mod, "_SRC_DIR", src)
    assert _transitive_src_closure(runner) == {"pkg", "leaf"}


# ===========================================================================
# _collect_spec_bundled
# ===========================================================================


def test_single_quote_spec_entry(tmp_path: Path) -> None:
    spec = _write(tmp_path, "app.spec", "datas = [(os.path.join('src', 'foo.py'), 'src')]\n")
    assert _collect_spec_bundled(spec) == {"foo"}


def test_double_quote_spec_entry(tmp_path: Path) -> None:
    spec = _write(tmp_path, "app.spec", 'datas = [(os.path.join("src", "bar.py"), \'src\')]\n')
    assert _collect_spec_bundled(spec) == {"bar"}


def test_mixed_quote_spec_entries(tmp_path: Path) -> None:
    spec = _write(
        tmp_path,
        "app.spec",
        "(os.path.join('src', 'foo.py'), 'src'),\n"
        '(os.path.join("src", "bar.py"), \'src\'),\n',
    )
    assert _collect_spec_bundled(spec) == {"foo", "bar"}


def test_empty_spec_returns_empty_set(tmp_path: Path) -> None:
    spec = _write(tmp_path, "app.spec", "")
    assert _collect_spec_bundled(spec) == set()


def test_commented_spec_entry_is_still_counted_as_bundled(tmp_path: Path) -> None:
    """A commented-out entry still registers — the regex runs on raw text by
    design, so the guard keeps firing until the line is fully deleted, not
    merely commented out (that is the state that breaks the frozen build)."""
    spec = _write(
        tmp_path,
        "app.spec",
        "# (os.path.join('src', 'old.py'), 'src'),\n"
        "(os.path.join('src', 'foo.py'), 'src'),\n",
    )
    assert _collect_spec_bundled(spec) == {"old", "foo"}


# ===========================================================================
# End-to-end run against a synthetic source tree + both specs
# ===========================================================================


def _run_e2e(
    tmp_path: Path,
    runner_text: str,
    src_files: dict[str, str],
    app_spec: str,
    cli_spec: str,
) -> subprocess.CompletedProcess:
    """Lay out a fake repo (scripts/runner, src/*.py, both specs) under
    tmp_path and run a copy of the guard so it resolves paths to tmp_path."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "generate_chatterbox_audiobook.py").write_text(runner_text, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    for name, content in src_files.items():
        (src / name).write_text(content, encoding="utf-8")
    (tmp_path / "audiobookmaker.spec").write_text(app_spec, encoding="utf-8")
    (tmp_path / "audiobookmaker_cli.spec").write_text(cli_spec, encoding="utf-8")
    guard = scripts_dir / "check_spec_runner_imports.py"
    shutil.copy(_SCRIPT, guard)
    return subprocess.run([sys.executable, str(guard)], capture_output=True, text=True)


def test_clean_state_passes(tmp_path: Path) -> None:
    """Runner import bundled in both specs → exit 0, no output."""
    entry = "(os.path.join('src', 'foo.py'), 'src'),\n"
    result = _run_e2e(
        tmp_path,
        runner_text="from src.foo import bar\n",
        src_files={"foo.py": "x = 1\n"},
        app_spec=entry,
        cli_spec=entry,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_transitively_reached_module_is_required(tmp_path: Path) -> None:
    """runner -> foo -> bar; bar bundled in CLI but missing in app spec → the
    app spec is flagged for bar even though the runner never imports it
    directly. This is the exact bug class that shipped in 3.15.0."""
    result = _run_e2e(
        tmp_path,
        runner_text="from src.foo import a\n",
        src_files={"foo.py": "from src.bar import b\n", "bar.py": "c = 1\n"},
        app_spec="(os.path.join('src', 'foo.py'), 'src'),\n",  # bar MISSING
        cli_spec="(os.path.join('src', 'foo.py'), 'src'),\n(os.path.join('src', 'bar.py'), 'src'),\n",
    )
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "bar" in combined
    assert "audiobookmaker.spec" in combined


def test_missing_in_cli_spec_alone_fails(tmp_path: Path) -> None:
    """A module bundled in the app spec but not the CLI spec still fails —
    both shipped specs are checked."""
    result = _run_e2e(
        tmp_path,
        runner_text="from src.foo import a\n",
        src_files={"foo.py": "x = 1\n"},
        app_spec="(os.path.join('src', 'foo.py'), 'src'),\n",
        cli_spec="# CLI bundles nothing\n",
    )
    assert result.returncode == 1
    assert "audiobookmaker_cli.spec" in (result.stdout + result.stderr)


def test_empty_runner_exits_zero(tmp_path: Path) -> None:
    result = _run_e2e(tmp_path, runner_text="", src_files={}, app_spec="", cli_spec="")
    assert result.returncode == 0


# ===========================================================================
# Real-repo smoke test (live canary)
# ===========================================================================


def test_real_repo_exits_zero() -> None:
    """The guard passes against the actual repo files. If someone adds a
    src dependency reachable from the runner without bundling it in both
    shipped specs, this fails in CI before the PyInstaller build starts."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, (
        "check_spec_runner_imports.py found a mismatch in the real repo:\n"
        + result.stderr
        + result.stdout
    )
