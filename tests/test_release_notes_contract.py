"""The release body's shape is a contract the in-app update banner depends on.

Three separate pieces have to agree, and until now none of them were tested:

  .github/workflows/build-release.yml   writes the body
  docs/RELEASE_NOTES_NEXT.md            supplies the news
  src/auto_updater.py                   reads it back in the installed app

v3.23.0 shipped with an empty notes panel because the workflow stopped
emitting a heading the installed reader was looking for. The suite did not
notice, and could not have: the change was pure markdown plus YAML, so the
pre-commit hook's docs-only shortcut skipped the tests, and no test referenced
either file. These lock the parts that broke.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.auto_updater import WHATS_NEW_END_MARKER, extract_whats_new

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-release.yml"
NOTES = REPO_ROOT / "docs" / "RELEASE_NOTES_NEXT.md"


# ---------------------------------------------------------------------------
# The workflow must keep emitting what older readers need
# ---------------------------------------------------------------------------

def test_workflow_emits_the_whats_new_heading() -> None:
    """Versions up to 3.23.0 start reading at this exact heading.

    Drop it and every user still on an older build gets an update prompt with
    an empty notes panel — they cannot be fixed retroactively, because the
    reader ships inside the version they already have.
    """
    assert '"### What\'s new"' in WORKFLOW.read_text(encoding="utf-8"), (
        "build-release.yml no longer emits the '### What's new' heading; "
        "installed versions up to 3.23.0 will show an empty notes panel"
    )


def test_workflow_emits_the_end_sentinel_the_reader_expects() -> None:
    """The sentinel is what lets a news section be titled "CLI ..." safely."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert f'"{WHATS_NEW_END_MARKER}"' in text, (
        f"build-release.yml must emit {WHATS_NEW_END_MARKER!r} to close the "
        f"news section; auto_updater.WHATS_NEW_END_MARKER expects it"
    )


def test_workflow_closes_the_news_before_the_installation_section() -> None:
    """Order matters: a sentinel after Installation would not bound anything."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index(f'"{WHATS_NEW_END_MARKER}"') < text.index('"### Installation"')


# ---------------------------------------------------------------------------
# The notes file must stay readable by the OLD extractor
# ---------------------------------------------------------------------------

def test_notes_file_exists() -> None:
    """A missing file publishes a release with no news at all."""
    assert NOTES.is_file(), (
        "docs/RELEASE_NOTES_NEXT.md is missing; the next release would ship "
        "without a What's new section"
    )


def test_notes_open_with_flat_prose_not_a_heading() -> None:
    """Readers up to 3.23.0 stop at the first sub-heading under "What's new".

    So whatever sits between that heading and the notes' own first `###` is
    the ONLY thing they ever display. Start the file with a `###` and they get
    an empty panel, which is precisely the 3.23.0 defect.
    """
    first = next(
        (ln for ln in NOTES.read_text(encoding="utf-8").splitlines() if ln.strip()),
        "",
    )
    assert not first.lstrip().startswith("#"), (
        f"docs/RELEASE_NOTES_NEXT.md opens with a heading ({first!r}). It must "
        f"open with a flat summary paragraph, or installed versions up to "
        f"3.23.0 show an empty notes panel."
    )


def test_notes_do_not_smuggle_in_the_technical_tail() -> None:
    """The pipeline appends install text and hashes; the notes must not."""
    text = NOTES.read_text(encoding="utf-8")
    assert not re.search(r"(?im)^#{1,6}\s*installation\s*$", text)
    assert "SHA-256:" not in text
    assert WHATS_NEW_END_MARKER not in text


# ---------------------------------------------------------------------------
# End to end: assemble a body the way the workflow does, then read it back
# ---------------------------------------------------------------------------

def _assembled_body(news: list[str], version: str = "9.9.9") -> str:
    """Mirror the line order build-release.yml writes."""
    return "\n".join(
        [f"## AudiobookMaker {version}", "", "### What's new", ""]
        + news
        + [
            "",
            WHATS_NEW_END_MARKER,
            "",
            "### Installation",
            "",
            "1. Download it",
            "",
            "### CLI (command-line interface)",
            "",
            "Download the zip",
            "",
            "---",
            "SHA-256: " + "a" * 64,
            "CLI: SHA-256: " + "b" * 64,
        ]
    )


def test_the_real_notes_file_survives_the_round_trip() -> None:
    """Whatever is staged for the next release must actually render."""
    news = NOTES.read_text(encoding="utf-8").splitlines()
    out = extract_whats_new(_assembled_body(news))
    assert out, "the staged release notes extract to nothing"
    for leaked in ("### Installation", "SHA-256:", "Download the zip"):
        assert leaked not in out


def test_a_news_section_titled_cli_is_not_truncated() -> None:
    """Regression: the stop pattern used to match any heading starting "CLI".

    This project ships a CLI, so that is a section title a release will
    plausibly use, and matching it silently dropped every later section.
    """
    out = extract_whats_new(_assembled_body([
        "### Faster conversions",
        "Now quicker.",
        "",
        "### CLI gets a resume flag",
        "You can resume from the terminal.",
        "",
        "### Fixes",
        "- a fix",
    ]))
    assert "### CLI gets a resume flag" in out
    assert "resume from the terminal" in out
    assert "- a fix" in out, "sections after the CLI heading were dropped"


def test_a_news_section_titled_installation_is_not_truncated() -> None:
    """Same defect, the other keyword."""
    out = extract_whats_new(_assembled_body([
        "### Installation is faster now",
        "Half the download.",
        "",
        "### Fixes",
        "- another fix",
    ]))
    assert "Half the download." in out
    assert "- another fix" in out


def test_an_unexpected_technical_section_does_not_reach_the_banner() -> None:
    """The sentinel bounds the news, so a new tail section cannot leak in."""
    body = _assembled_body(["### Faster conversions", "Now quicker."]).replace(
        "### Installation",
        "### Verifying your download\n\nCompare the hash.\n\n### Installation",
        1,
    )
    out = extract_whats_new(body)
    assert "Verifying your download" not in out
    assert "Compare the hash." not in out


def test_a_body_published_before_the_sentinel_still_reads() -> None:
    """Every release up to 3.23.0 lacks the sentinel and must keep working."""
    legacy = "\n".join([
        "## AudiobookMaker 3.22.0",
        "",
        "### What's new",
        "- Cleaner Finnish narration pauses",
        "",
        "### Installation",
        "Run the installer.",
        "",
        "### CLI (command-line interface)",
        "Download the zip.",
        "",
        "---",
        "SHA-256: " + "c" * 64,
    ])
    out = extract_whats_new(legacy)
    assert out == "- Cleaner Finnish narration pauses"
