"""Render the audiobookmaker CLI reference into docs/CLI.md.

Usage
-----
    python scripts/render_cli_help.py            # rewrite docs/CLI.md in-place
    python scripts/render_cli_help.py --check    # exit 1 if docs/CLI.md is stale
    python scripts/render_cli_help.py --stdout   # print the rendered block, do not touch the file

The rendered block replaces (or fills) the marker section in docs/CLI.md:

    <!-- BEGIN_GENERATED_REFERENCE -->
    ...
    <!-- END_GENERATED_REFERENCE -->

If the markers are missing the script prints a clear error and exits 1 with
instructions for adding them — it never crashes with a KeyError.

Pre-commit hook integration
---------------------------
Add the following lines to .git/hooks/pre-commit (or scripts/pre-commit)
BEFORE the doc-only shortcut that exits early for markdown-only commits,
so the check runs even when only docs/CLI.md is staged:

    echo "Checking docs/CLI.md is in sync with CLI parsers..."
    if ! py -3 scripts/render_cli_help.py --check; then
        echo ""
        echo "docs/CLI.md is out of sync with the CLI parsers."
        echo "Run: python scripts/render_cli_help.py"
        exit 1
    fi

The script is stdlib-only (no external dependencies).
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_MD = _REPO_ROOT / "docs" / "CLI.md"

_BEGIN_MARKER = "<!-- BEGIN_GENERATED_REFERENCE -->"
_END_MARKER = "<!-- END_GENERATED_REFERENCE -->"

# One canonical example per subcommand. Keys are the breadcrumb path
# (the same list that becomes `audiobookmaker-cli <crumb...>`). Every
# leaf parser surfaced by `_leaf_parsers` must have an entry — the
# renderer test `test_every_subcommand_has_example` enforces this so
# that adding a new subcommand without an example fails the suite.
_EXAMPLES: dict[tuple[str, ...], str] = {
    ("convert",): 'audiobookmaker-cli convert mybook.pdf --engine edge --language en',
    ("sample",): 'audiobookmaker-cli sample mybook.epub --engine chatterbox_grandmom --language fi',
    ("preview",): 'audiobookmaker-cli preview "This is a quick preview."',
    ("voices", "list"): 'audiobookmaker-cli voices list --language fi',
    ("engines", "list"): 'audiobookmaker-cli engines list --installed-only',
    ("engines", "install"): 'audiobookmaker-cli engines install chatterbox_grandmom',
    ("engines", "repair"): 'audiobookmaker-cli engines repair chatterbox_grandmom',
    ("engines", "remove"): 'audiobookmaker-cli engines remove piper --yes',
    ("engines", "check"): 'audiobookmaker-cli engines check chatterbox_grandmom',
    ("packs", "list"): 'audiobookmaker-cli packs list',
    ("packs", "import"): 'audiobookmaker-cli packs import ./mypack/',
    ("packs", "export"): 'audiobookmaker-cli packs export mypack --out ./mypack.abvpack.zip',
    ("packs", "remove"): 'audiobookmaker-cli packs remove mypack --yes',
    ("packs", "info"): 'audiobookmaker-cli packs info mypack',
    ("config", "show"): 'audiobookmaker-cli config show engine_id',
    ("config", "set"): 'audiobookmaker-cli config set engine_id piper',
    ("config", "reset"): 'audiobookmaker-cli config reset engine_id',
    ("config", "path"): 'audiobookmaker-cli config path',
    ("update", "check"): 'audiobookmaker-cli update check',
    ("update", "apply"): 'audiobookmaker-cli update apply --yes',
    ("doctor",): 'audiobookmaker-cli doctor',
    ("report-bug",): 'audiobookmaker-cli report-bug --print',
}

# ---------------------------------------------------------------------------
# Parser introspection helpers
# ---------------------------------------------------------------------------


def _action_metavar(action: argparse.Action) -> str:
    """Return a short usage token for a flag (e.g. ``--engine ID``)."""
    if action.option_strings:
        name = max(action.option_strings, key=len)
        if action.metavar:
            return f"`{name} {action.metavar}`"
        if action.nargs == 0 or isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            return f"`{name}`"
        return f"`{name}`"
    # Positional
    mv = action.metavar or action.dest.upper()
    return f"`{mv}`"


def _flag_table_rows(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """Return (flag, description) pairs for all non-help actions."""
    rows = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        token = _action_metavar(action)
        desc = (action.help or "").replace("\n", " ").strip()
        rows.append((token, desc))
    return rows


def _render_flag_table(parser: argparse.ArgumentParser) -> str:
    """Return a markdown table of flags, or an empty string if none."""
    rows = _flag_table_rows(parser)
    if not rows:
        return ""
    lines = ["| Flag | Description |", "|------|-------------|"]
    for token, desc in rows:
        lines.append(f"| {token} | {desc} |")
    return "\n".join(lines) + "\n"


def _render_example(crumb: list[str]) -> str:
    """Return a fenced bash code block with the canonical example, or
    an empty string if no example is registered for this subcommand."""
    cmd = _EXAMPLES.get(tuple(crumb))
    if not cmd:
        return ""
    return "**Example:**\n\n```bash\n" + cmd + "\n```\n"


def _leaf_parsers(
    parser: argparse.ArgumentParser,
    breadcrumb: list[str],
) -> list[tuple[list[str], argparse.ArgumentParser, list[str]]]:
    """Recursively collect ``(breadcrumb, parser, aliases)`` for every
    leaf command.

    A leaf command is one that has no further sub-parsers (i.e. it can
    actually be invoked).  Top-level commands that only exist to group
    sub-commands (like ``engines``) are skipped in favour of their
    leaves.

    When a subcommand is registered with ``aliases=[...]`` (e.g.
    ``add_parser("convert", aliases=["c"])``), argparse stores every
    alias as a separate key in ``subparsers.choices`` mapping to the
    same parser instance.  Iterating naively would emit duplicate
    documentation entries.  This implementation dedupes by parser
    identity: the first name encountered (insertion-order = canonical
    name) wins, the alias keys are skipped, and the alias names are
    returned alongside the parser so the renderer can mention them
    under the canonical heading.
    """
    sub_action = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub_action = action
            break

    if sub_action is None:
        # This is a leaf; no aliases at the leaf level.
        return [(breadcrumb, parser, [])]

    # Build a parser-id -> list-of-names map so we can collect aliases
    # for each canonical entry.
    names_by_parser_id: dict[int, list[str]] = {}
    for choice_name, choice_parser in sub_action.choices.items():
        names_by_parser_id.setdefault(id(choice_parser), []).append(choice_name)

    leaves: list[tuple[list[str], argparse.ArgumentParser, list[str]]] = []
    seen_parser_ids: set[int] = set()
    for choice_name, choice_parser in sub_action.choices.items():
        if id(choice_parser) in seen_parser_ids:
            continue
        seen_parser_ids.add(id(choice_parser))
        # All names that point at this parser; the first is canonical,
        # the rest are aliases.
        all_names = names_by_parser_id[id(choice_parser)]
        canonical = all_names[0]
        aliases = all_names[1:]

        child_crumb = breadcrumb + [canonical]
        # Recurse into the child parser.  Aliases declared at THIS level
        # only attach to a leaf that lives directly at this level — i.e.
        # the recursion returned without descending further.  We detect
        # that by comparing the returned breadcrumb's length against
        # `child_crumb`: same length ⇒ the recursion hit the leaf
        # branch and returned the same breadcrumb (no further nesting).
        # The length check is more robust than `sub_crumb == child_crumb`
        # because it survives any future normalization of breadcrumbs
        # (e.g. tuple-isation) without silently swallowing aliases.
        #
        # Known limitation: aliases declared on a non-leaf parent (e.g.
        # if `engines` itself ever got `aliases=["e"]`) would not
        # propagate down to `engines list`, `engines install`, etc. —
        # those leaves would still be tagged with empty alias lists.
        # No subcommand in this codebase uses nested aliases today, so
        # the gap is theoretical.  If it ever matters, switch to
        # passing parent-aliases down into the recursion explicitly.
        target_depth = len(child_crumb)
        for sub_crumb, sub_parser, sub_aliases in _leaf_parsers(
            choice_parser, child_crumb
        ):
            if len(sub_crumb) == target_depth:
                leaves.append((sub_crumb, sub_parser, aliases))
            else:
                leaves.append((sub_crumb, sub_parser, sub_aliases))
    return leaves


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _render_block(root_parser: argparse.ArgumentParser) -> str:
    """Render the full reference block (content between markers)."""
    leaves = _leaf_parsers(root_parser, [])
    sections: list[str] = []

    for crumb, parser, aliases in leaves:
        heading_words = ["audiobookmaker-cli"] + crumb
        heading = "### `" + " ".join(heading_words) + "`"

        # One-line description: prefer the parser's description, fall back
        # to the help text registered in the parent subparsers.
        description = (parser.description or "").strip()
        # The description often has a multi-line exit-code block appended
        # with two newlines.  We only want the first paragraph.
        first_para = description.split("\n\n")[0].replace("\n", " ").strip()

        table = _render_flag_table(parser)

        parts = [heading, ""]
        if aliases:
            # Surface short aliases below the canonical heading so readers
            # of the static reference (not just `--help`) discover them.
            alias_list = ", ".join(f"`{a}`" for a in aliases)
            parts.append(f"**Aliases:** {alias_list}")
            parts.append("")
        if first_para:
            parts.append(first_para)
            parts.append("")
        if table:
            parts.append(table)
        else:
            parts.append("*(no options)*")
            parts.append("")

        example = _render_example(crumb)
        if example:
            parts.append(example)

        sections.append("\n".join(parts))

    return "\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _load_cli_md(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _inject(original: str, rendered: str) -> str | None:
    """Return the file content with the generated block replaced.

    The output format between the markers is:

        <!-- BEGIN_GENERATED_REFERENCE -->
        <!-- This block is auto-generated ... -->
        <rendered content>
        <!-- END_GENERATED_REFERENCE -->

    Returns None if either marker is missing.
    """
    begin_idx = original.find(_BEGIN_MARKER)
    end_idx = original.find(_END_MARKER)
    if begin_idx == -1 or end_idx == -1:
        return None

    # Everything up to and including the BEGIN_MARKER line.
    before = original[: begin_idx + len(_BEGIN_MARKER)]
    # Everything from END_MARKER onward.
    after = original[end_idx:]

    _AUTOGEN_COMMENT = (
        "\n<!-- This block is auto-generated by scripts/render_cli_help.py.\n"
        "     Do not edit by hand; edit the argparse parsers and re-run the\n"
        "     renderer (or the pre-commit hook will re-run it for you). -->\n"
    )

    return before + _AUTOGEN_COMMENT + rendered.strip() + "\n" + after


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render_cli_help.py",
        description=__doc__.splitlines()[0],
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Exit 1 if docs/CLI.md is stale; exit 0 if it is up to date.",
    )
    mode.add_argument(
        "--stdout",
        action="store_true",
        default=False,
        help="Print the rendered block to stdout; do not modify any file.",
    )
    p.add_argument(
        "--doc",
        metavar="PATH",
        default=str(_CLI_MD),
        help="Path to docs/CLI.md (default: auto-detected from repo root).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    doc_path = Path(args.doc)

    # Add the repo root to sys.path so ``src`` is importable when invoked
    # directly as ``python scripts/render_cli_help.py`` from any cwd.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from src.cli.__main__ import _build_parser as build_cli_parser
    except ImportError as exc:
        print(
            f"Error: cannot import src.cli.__main__._build_parser: {exc}\n"
            "Make sure you are running from the repo root or that the repo root "
            "is on sys.path.",
            file=sys.stderr,
        )
        return 1

    try:
        root_parser = build_cli_parser()
    except Exception as exc:
        print(f"Error: _build_parser() raised: {exc}", file=sys.stderr)
        return 1

    rendered = _render_block(root_parser)

    # --stdout: just print and exit.
    if args.stdout:
        print(rendered)
        return 0

    # Read the current file.
    if not doc_path.exists():
        print(f"Error: {doc_path} does not exist.", file=sys.stderr)
        return 1

    original = _load_cli_md(doc_path)
    new_content = _inject(original, rendered)

    if new_content is None:
        print(
            f"Error: {doc_path} is missing the marker block.\n\n"
            "Add these two lines to docs/CLI.md where you want the auto-generated\n"
            "reference to appear:\n\n"
            "    <!-- BEGIN_GENERATED_REFERENCE -->\n"
            "    <!-- END_GENERATED_REFERENCE -->\n\n"
            "Then re-run this script.",
            file=sys.stderr,
        )
        return 1

    # --check: compare without writing.
    if args.check:
        if new_content == original:
            return 0
        print(
            "docs/CLI.md is out of sync with the CLI parsers.\n"
            "Run: python scripts/render_cli_help.py",
            file=sys.stderr,
        )
        return 1

    # Default: rewrite in place.
    if new_content != original:
        doc_path.write_text(new_content, encoding="utf-8")
        print(f"Updated {doc_path}")
    else:
        print(f"{doc_path} is already up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
