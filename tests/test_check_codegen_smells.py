"""Unit tests for the scanner functions in scripts/check_codegen_smells.py.

The CI codegen-smell gate is only useful if its regex pattern surface
keeps working. The script is pure-stdlib and the scanners are pure
functions (text in, list[Finding] out), so they're trivially testable.
Each scanner gets a smell example (must flag) AND a legitimate example
(must not flag) — lifted from the SKILL.md's own check definitions so
the tests double as documentation of what the CI gate actually catches.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Make `scripts/` importable as a package — matches the convention
# used by tests/test_render_cli_help.py for the analog CLI-render
# script.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_codegen_smells import (  # noqa: E402
    scan_defensive_none,
    scan_over_typed,
    scan_phantom_todos,
    scan_swallowed_errors,
)


_DUMMY = Path("dummy.py")


# ---------------------------------------------------------------------------
# scan_phantom_todos
# ---------------------------------------------------------------------------


class TestScanPhantomTodos:
    """A bare TODO/FIXME/XXX/HACK without owner tag or issue link fires;
    one with either escape is silently accepted."""

    def test_bare_todo_flagged(self) -> None:
        text = "# TODO: handle unicode edge cases\nx = 1\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert len(hits) == 1
        assert hits[0].check == "phantom-todos"
        assert hits[0].line == 1

    def test_fixme_flagged(self) -> None:
        text = "# FIXME: this is wrong\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert len(hits) == 1

    def test_xxx_flagged(self) -> None:
        text = "# XXX revisit before release\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert len(hits) == 1

    def test_hack_flagged(self) -> None:
        text = "# HACK workaround for upstream bug\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert len(hits) == 1

    def test_owner_tagged_todo_skipped(self) -> None:
        text = "# TODO(numminen, 2026-Q3): drop legacy alias\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert hits == []

    def test_issue_link_todo_skipped(self) -> None:
        text = "# TODO drop legacy alias (#42)\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert hits == []

    def test_word_todo_in_running_text_not_flagged(self) -> None:
        # "todo" inside a sentence-shaped comment is not a phantom TODO
        # marker — the keyword must follow `#` directly.
        text = "x = 1  # we will do this when we have time\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert hits == []


# ---------------------------------------------------------------------------
# scan_swallowed_errors
# ---------------------------------------------------------------------------


class TestScanSwallowedErrors:
    """Bare except + pass/return None is gated. Typed handlers and the
    very common `except Exception: pass` pattern are deliberately NOT
    gated — see the script docstring for the rationale."""

    def test_bare_except_pass_flagged(self) -> None:
        text = "try:\n    do()\nexcept:\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_base_exception_pass_flagged(self) -> None:
        text = "try:\n    do()\nexcept BaseException:\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_base_exception_as_e_pass_flagged(self) -> None:
        text = "try:\n    do()\nexcept BaseException as e:\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_bare_except_return_none_flagged(self) -> None:
        text = "def f():\n    try:\n        do()\n    except:\n        return None\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_except_exception_pass_NOT_flagged(self) -> None:
        # Documented script policy: `except Exception: pass` is too
        # common in legitimate teardown/best-effort code to gate on.
        text = "try:\n    do()\nexcept Exception:\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert hits == []

    def test_typed_except_pass_NOT_flagged(self) -> None:
        text = "try:\n    do()\nexcept FileNotFoundError:\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert hits == []

    def test_bare_except_with_logging_NOT_flagged(self) -> None:
        # Only `pass` / `return None` qualify as a swallow. A log + raise
        # / log + recover handler still uses bare-except but is not what
        # this check targets.
        text = "try:\n    do()\nexcept:\n    log.exception('oops')\n    raise\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert hits == []


# ---------------------------------------------------------------------------
# scan_defensive_none
# ---------------------------------------------------------------------------


class TestScanDefensiveNone:
    """`if x is None` on a parameter whose annotation excludes None is
    flagged; the same check on a `T | None` or `Optional[T]` param is
    skipped; unannotated params are skipped (grep can't judge)."""

    def test_typed_str_with_none_check_flagged(self) -> None:
        text = 'def render(text: str) -> str:\n    if text is None:\n        return ""\n'
        hits = scan_defensive_none(text, _DUMMY)
        assert len(hits) == 1
        assert hits[0].check == "defensive-checks-for-impossible-cases"

    def test_typed_int_with_none_check_flagged(self) -> None:
        text = "def f(n: int) -> int:\n    if n is None:\n        return 0\n"
        hits = scan_defensive_none(text, _DUMMY)
        assert len(hits) == 1

    def test_typed_union_with_none_skipped(self) -> None:
        text = 'def render(text: str | None) -> str:\n    if text is None:\n        return ""\n'
        hits = scan_defensive_none(text, _DUMMY)
        assert hits == []

    def test_optional_annotation_skipped(self) -> None:
        text = 'def render(text: Optional[str]) -> str:\n    if text is None:\n        return ""\n'
        hits = scan_defensive_none(text, _DUMMY)
        assert hits == []

    def test_unannotated_param_skipped(self) -> None:
        # Documented limitation: unannotated params cannot be judged by
        # grep. The script chooses to skip rather than flag spuriously.
        text = 'def render(text):\n    if text is None:\n        return ""\n'
        hits = scan_defensive_none(text, _DUMMY)
        assert hits == []

    def test_docstring_before_guard_not_caught(self) -> None:
        # Documented limitation: the check only fires when `if x is None`
        # is the very first line of the function body. A docstring or
        # any other statement before the guard escapes detection.
        text = (
            'def render(text: str) -> str:\n'
            '    """Do the thing."""\n'
            '    if text is None:\n'
            '        return ""\n'
        )
        hits = scan_defensive_none(text, _DUMMY)
        assert hits == []


# ---------------------------------------------------------------------------
# scan_over_typed
# ---------------------------------------------------------------------------


class TestScanOverTyped:
    """`NewType(`, `Literal[`, and `TypedDict` substrings are flagged
    anywhere in a file EXCEPT the typing-import line itself, where these
    names legitimately appear once."""

    def test_newtype_flagged(self) -> None:
        text = 'EngineId = NewType("EngineId", str)\n'
        hits = scan_over_typed(text, _DUMMY)
        assert len(hits) == 1
        assert hits[0].check == "over-typed-primitives"

    def test_literal_flagged(self) -> None:
        text = 'x: Literal["a", "b"] = "a"\n'
        hits = scan_over_typed(text, _DUMMY)
        assert len(hits) == 1

    def test_typed_dict_flagged(self) -> None:
        text = "class Foo(TypedDict):\n    pass\n"
        hits = scan_over_typed(text, _DUMMY)
        assert len(hits) == 1

    def test_typing_import_from_skipped(self) -> None:
        text = "from typing import NewType, Literal, TypedDict\n"
        hits = scan_over_typed(text, _DUMMY)
        assert hits == []

    def test_import_typing_module_skipped(self) -> None:
        text = "import typing\nx: typing.Literal[1] = 1\n"
        # The `import typing` line should be exempt; the use on the
        # next line should still fire.
        hits = scan_over_typed(text, _DUMMY)
        assert len(hits) == 1
        assert hits[0].line == 2

    def test_one_line_one_finding(self) -> None:
        # Multiple matches on the same line still count as one finding
        # (the scanner breaks after the first match per line).
        text = 'A = NewType("A", str)  # NewType again here\n'
        hits = scan_over_typed(text, _DUMMY)
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# Integration smoke test — full run against this repo's src/
# ---------------------------------------------------------------------------


def test_full_run_against_src_is_clean() -> None:
    """The CI gate must be green on `src/` today. If a future commit
    adds a phantom-todo or bare-except-pass to src/, this test fails
    locally before pre-commit, not just in CI."""
    from scripts.check_codegen_smells import run_checks

    src = _REPO_ROOT / "src"
    assert src.is_dir(), f"src/ not found at {src}"
    results = run_checks(src)

    gating_failures = [
        (r.name, [(f.path, f.line) for f in r.findings])
        for r in results
        if r.gating and r.findings
    ]
    assert not gating_failures, (
        f"Gating-tier codegen-smell checks have hits in src/: "
        f"{gating_failures}"
    )
