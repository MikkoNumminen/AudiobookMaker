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

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Make `scripts/` importable as a package — matches the convention
# used by tests/test_render_cli_help.py for the analog CLI-render
# script.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_codegen_smells import (  # noqa: E402
    _extract_param_annotation,
    _walk_to_balanced,
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

    def test_at_owner_tagged_todo_skipped(self) -> None:
        # `@owner` style owner tag — common alternative to `(name, date)`.
        text = "# TODO @numminen: drop legacy alias\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert hits == []

    def test_at_owner_with_hyphen_skipped(self) -> None:
        # GitHub usernames allow hyphens. The owner-tag regex must
        # accept them — otherwise `@some-user` is treated as no owner
        # and the TODO is flagged.
        text = "# TODO @some-user: refactor\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert hits == []

    def test_bare_at_sign_is_not_an_owner(self) -> None:
        # A loose mutation that accepts `@\\w*` (zero-or-more) would
        # treat a bare `@` followed by no name as an owner — wrong.
        # The current regex requires `@[A-Za-z][\\w.\\-]*`, so this
        # case must still flag.
        text = "# TODO @ no name yet\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert len(hits) == 1

    def test_issue_link_todo_skipped(self) -> None:
        text = "# TODO drop legacy alias (#42)\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert hits == []

    def test_incidental_hash_number_does_not_count_as_link(self) -> None:
        # Prose like "step #1" inside a TODO must NOT silently mark
        # the TODO as legitimate — the link must follow whitespace or
        # `(`.
        text = "# TODO step#1 first\n"
        hits = scan_phantom_todos(text, _DUMMY)
        assert len(hits) == 1

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

    def test_comment_between_handler_and_pass_still_flagged(self) -> None:
        # Bypass shape from the post-merge audit: a comment between
        # `except:` and `pass` previously defeated the regex. Must
        # still flag.
        text = "try:\n    do()\nexcept:\n    # silence the warning\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_blank_line_between_handler_and_pass_still_flagged(self) -> None:
        text = "try:\n    do()\nexcept:\n\n    pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_multiple_comments_between_handler_and_pass_still_flagged(self) -> None:
        text = (
            "try:\n"
            "    do()\n"
            "except:\n"
            "    # reason 1\n"
            "    # reason 2\n"
            "    pass\n"
        )
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_single_line_except_pass_flagged(self) -> None:
        # Realistic one-line shape — `except: pass` on a single line.
        # The pre-fix regex required a newline after the handler
        # colon and missed this.
        text = "try:\n    do()\nexcept: pass\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1

    def test_single_line_baseexception_return_none_flagged(self) -> None:
        text = "try:\n    do()\nexcept BaseException: return None\n"
        hits = scan_swallowed_errors(text, _DUMMY)
        assert len(hits) == 1


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

    def test_dict_annotation_with_comma_not_misread(self) -> None:
        # Bypass shape from the post-merge audit: the old `[^,]+`
        # annotation extractor truncated at the first comma. A
        # `Dict[str, int]` annotation should be parsed as the full
        # type, not as `Dict[str` — and since the type does not admit
        # None, an actual `is None` guard on it IS a smell.
        text = "def f(m: Dict[str, int]) -> int:\n    if m is None:\n        return 0\n"
        hits = scan_defensive_none(text, _DUMMY)
        assert len(hits) == 1

    def test_callable_annotation_with_internal_commas_skipped(self) -> None:
        # A `Callable[[int, int], None]` annotation contains commas and
        # contains `None`. The annotation explicitly admits None, so
        # the guard is legitimate — must NOT be flagged.
        text = (
            "def f(cb: Callable[[int, int], None]) -> int:\n"
            "    if cb is None:\n"
            "        return 0\n"
        )
        hits = scan_defensive_none(text, _DUMMY)
        assert hits == []

    def test_tuple_annotation_with_ellipsis_not_misread(self) -> None:
        # `Tuple[int, ...]` has a comma but no None — guard should
        # still flag.
        text = "def f(t: Tuple[int, ...]) -> int:\n    if t is None:\n        return 0\n"
        hits = scan_defensive_none(text, _DUMMY)
        assert len(hits) == 1

    def test_two_level_nested_paren_default_not_misread(self) -> None:
        # `def f(x=foo(bar()))` has two-level nested parens in the
        # default value. The old regex-based capture only tolerated
        # one level; the bracket-walker handles arbitrary depth.
        text = (
            "def f(x: int = foo(bar())) -> int:\n"
            "    if x is None:\n"
            "        return 0\n"
        )
        hits = scan_defensive_none(text, _DUMMY)
        assert len(hits) == 1


class TestExtractParamAnnotation:
    """Direct unit tests for `_extract_param_annotation`.

    Test #6 / #8 in the bypass-shape class above use the scanner's
    end-to-end behavior, which can mask whether the underlying
    extractor returns the right annotation slice. These tests pin
    the extractor's contract by value, so a regression where the
    extractor returns a wrong-but-still-flagging slice (e.g.
    `Dict[str` for `m: Dict[str, int]`) is caught directly.
    """

    def test_simple_str_annotation(self) -> None:
        assert _extract_param_annotation("m: str", "m") == "str"

    def test_dict_with_internal_comma(self) -> None:
        assert _extract_param_annotation("m: Dict[str, int]", "m") == "Dict[str, int]"

    def test_callable_with_internal_commas(self) -> None:
        assert (
            _extract_param_annotation("cb: Callable[[int, int], None]", "cb")
            == "Callable[[int, int], None]"
        )

    def test_tuple_with_ellipsis(self) -> None:
        assert _extract_param_annotation("t: Tuple[int, ...]", "t") == "Tuple[int, ...]"

    def test_optional_annotation(self) -> None:
        assert _extract_param_annotation("x: Optional[str]", "x") == "Optional[str]"

    def test_union_annotation(self) -> None:
        assert _extract_param_annotation("x: str | None", "x") == "str | None"

    def test_unannotated_returns_none(self) -> None:
        assert _extract_param_annotation("x, y", "x") is None

    def test_arg_with_default(self) -> None:
        # The annotation stops at the top-level comma; default value
        # bleed-through ("int = 5") is acceptable — the scanner only
        # checks for "None" / "Optional" presence.
        result = _extract_param_annotation("x: int = 5", "x")
        assert result is not None
        assert result.strip().startswith("int")

    def test_multi_arg_stops_at_top_level_comma(self) -> None:
        assert _extract_param_annotation("x: int, y: str", "x") == "int"
        assert _extract_param_annotation("x: int, y: str", "y") == "str"


class TestWalkToBalanced:
    """Direct unit tests for `_walk_to_balanced`.

    Pins the bracket-counter contract so a regression that allows the
    outer paren span to drift across nested defaults is caught
    directly, not just at the scanner's downstream behavior.
    """

    def test_empty_parens(self) -> None:
        # text[0] = ')', starting after the implied '('
        assert _walk_to_balanced(")", 0) == 0

    def test_simple_arg(self) -> None:
        # f(x)  — after '(' at index 2, the matching ')' is at index 3
        assert _walk_to_balanced("f(x)", 2) == 3

    def test_one_nested(self) -> None:
        # f(g()) — after the outer '(' at index 2, the matching ')'
        # is at index 5
        assert _walk_to_balanced("f(g())", 2) == 5

    def test_two_nested(self) -> None:
        # f(g(h())) — after the outer '(' at index 2, the matching
        # ')' is at index 8. Old regex-based capture failed here.
        assert _walk_to_balanced("f(g(h()))", 2) == 8

    def test_unbalanced_returns_none(self) -> None:
        # f(x  — no closing paren
        assert _walk_to_balanced("f(x", 2) is None


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

    def test_my_typed_dict_class_NOT_flagged(self) -> None:
        # Bypass shape from the post-merge audit: a class whose name
        # ends in `TypedDict` (e.g. `MyTypedDict`) is a perfectly
        # ordinary domain class. Word-boundary anchoring means it must
        # not trip the `TypedDict` rule.
        text = "class MyTypedDict:\n    pass\n"
        hits = scan_over_typed(text, _DUMMY)
        assert hits == []

    def test_inline_comment_with_newtype_substring_NOT_flagged(self) -> None:
        # `# uses NewType( style` is prose, not over-typed Python.
        # Inline comments should be stripped before scanning.
        text = "x = 1  # uses NewType( style\n"
        hits = scan_over_typed(text, _DUMMY)
        assert hits == []


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
