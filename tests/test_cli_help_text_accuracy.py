"""Tests that --help text for --json and --quiet is accurate per subcommand.

Two findings addressed:
  M8 — --json said "ProgressEvent shape" everywhere; only synthesis
       subcommands actually emit ProgressEvents.
  T3 — --quiet said "print only the final output path" everywhere;
       non-synthesis subcommands print voice ids, engine ids, etc.

Strategy: invoke each leaf's --help via the in-process parser (no
subprocess required) and assert on the argparse-generated help string.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Helper — build the full parser and capture --help text for a leaf
# ---------------------------------------------------------------------------

def _build_main_parser() -> argparse.ArgumentParser:
    """Build the same top-level parser that __main__.py uses."""
    from src.cli.__main__ import _build_parser
    return _build_parser()


def _help_for(argv: list[str]) -> str:
    """Return the --help output for the given argv (without the prog name).

    Adds '--help' at the end and captures the SystemExit/output that argparse
    raises.  Returns the output as a string.
    """
    parser = _build_main_parser()
    try:
        parser.parse_args(argv + ["--help"])
    except SystemExit:
        pass
    # argparse prints to stdout; capture via capsys is not available here,
    # so we re-use the format_help() path instead.
    # Walk subparsers to find the matching action.
    return _get_help_string(parser, argv)


def _get_help_string(parser: argparse.ArgumentParser, argv: list[str]) -> str:
    """Drill into nested subparsers to find the leaf parser and call format_help()."""
    current = parser
    for token in argv:
        found = False
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                if token in action.choices:
                    current = action.choices[token]
                    found = True
                    break
        if not found:
            break
    return current.format_help()


# ---------------------------------------------------------------------------
# Subcommands that DO emit ProgressEvents (synthesis path)
# ---------------------------------------------------------------------------

SYNTHESIS_SUBCOMMANDS = [
    ["convert"],
    ["sample"],
    ["preview"],
]

# ---------------------------------------------------------------------------
# Subcommands that do NOT emit ProgressEvents
# ---------------------------------------------------------------------------

NON_SYNTHESIS_SUBCOMMANDS = [
    ["voices", "list"],
    ["engines", "list"],
    ["engines", "install"],
    ["engines", "remove"],
    ["engines", "check"],
    ["packs", "list"],
    ["packs", "import"],
    ["packs", "remove"],
    ["packs", "info"],
    ["config", "show"],
    ["config", "set"],
    ["config", "reset"],
    ["config", "path"],
    ["update", "check"],
    ["update", "apply"],
    ["doctor"],
]


# ---------------------------------------------------------------------------
# M8: --json help must not say "ProgressEvent shape" on non-synthesis commands
# ---------------------------------------------------------------------------

class TestJsonHelpNoProgressEventOnNonSynthesis:
    """Non-synthesis subcommands must not claim to emit ProgressEvent shape."""

    @pytest.mark.parametrize("argv", NON_SYNTHESIS_SUBCOMMANDS)
    def test_no_progress_event_shape_phrase(self, argv):
        help_text = _get_help_string(_build_main_parser(), argv)
        assert "ProgressEvent shape" not in help_text, (
            f"{' '.join(argv)}: --json help should not say 'ProgressEvent shape'; "
            f"got: {help_text!r}"
        )

    @pytest.mark.parametrize("argv", SYNTHESIS_SUBCOMMANDS)
    def test_synthesis_commands_mention_progress_event(self, argv):
        help_text = _get_help_string(_build_main_parser(), argv)
        assert "ProgressEvent" in help_text, (
            f"{' '.join(argv)}: --json help should mention ProgressEvent; "
            f"got: {help_text!r}"
        )


# ---------------------------------------------------------------------------
# M8: --json help must describe the actual output shape per subcommand
# ---------------------------------------------------------------------------

class TestJsonHelpDescribesActualShape:
    def test_voices_list_mentions_voice_fields(self):
        h = _get_help_string(_build_main_parser(), ["voices", "list"])
        assert "engine" in h and "display_name" in h, (
            f"voices list --json help should describe voice fields; got: {h!r}"
        )

    def test_engines_list_mentions_engine_fields(self):
        h = _get_help_string(_build_main_parser(), ["engines", "list"])
        assert "available" in h and "reason" in h, (
            f"engines list --json help should describe engine fields; got: {h!r}"
        )

    def test_engines_check_mentions_available_field(self):
        h = _get_help_string(_build_main_parser(), ["engines", "check"])
        assert "available" in h, (
            f"engines check --json help should mention 'available'; got: {h!r}"
        )

    def test_packs_list_mentions_pack_fields(self):
        h = _get_help_string(_build_main_parser(), ["packs", "list"])
        assert "slug" in h, (
            f"packs list --json help should mention 'slug'; got: {h!r}"
        )

    def test_packs_import_mentions_ok_slug_path(self):
        h = _get_help_string(_build_main_parser(), ["packs", "import"])
        assert "ok" in h and "slug" in h and "path" in h, (
            f"packs import --json help should mention ok, slug, path; got: {h!r}"
        )

    def test_config_show_mentions_json_object(self):
        h = _get_help_string(_build_main_parser(), ["config", "show"])
        assert "JSON object" in h, (
            f"config show --json help should mention JSON object; got: {h!r}"
        )

    def test_config_set_mentions_key_value(self):
        h = _get_help_string(_build_main_parser(), ["config", "set"])
        assert "key" in h and "value" in h, (
            f"config set --json help should mention key and value; got: {h!r}"
        )

    def test_config_path_mentions_path_field(self):
        h = _get_help_string(_build_main_parser(), ["config", "path"])
        assert "path" in h, (
            f"config path --json help should mention 'path'; got: {h!r}"
        )

    def test_update_check_mentions_version_fields(self):
        h = _get_help_string(_build_main_parser(), ["update", "check"])
        assert "current_version" in h and "latest_version" in h, (
            f"update check --json help should mention version fields; got: {h!r}"
        )

    def test_update_apply_mentions_progress_and_result(self):
        h = _get_help_string(_build_main_parser(), ["update", "apply"])
        assert "progress" in h.lower() or "installer_path" in h, (
            f"update apply --json help should describe progress/result; got: {h!r}"
        )

    def test_doctor_mentions_check_object_fields(self):
        h = _get_help_string(_build_main_parser(), ["doctor"])
        assert "name" in h and "status" in h and "summary" in h, (
            f"doctor --json help should describe check and summary objects; got: {h!r}"
        )


# ---------------------------------------------------------------------------
# T3: --quiet help must not say "output path" on non-synthesis subcommands
# ---------------------------------------------------------------------------

class TestQuietHelpAccuracy:
    """Non-synthesis subcommands must not say --quiet prints an output path."""

    @pytest.mark.parametrize("argv", [
        ["voices", "list"],
        ["engines", "list"],
        ["packs", "list"],
    ])
    def test_list_commands_mention_ids_or_slugs(self, argv):
        h = _get_help_string(_build_main_parser(), argv)
        # Should mention what is printed (ids/slugs), not "output path"
        assert "ids" in h or "slugs" in h or "id" in h.lower(), (
            f"{' '.join(argv)}: --quiet help should mention ids/slugs; got: {h!r}"
        )

    def test_voices_list_quiet_mentions_voice_ids(self):
        h = _get_help_string(_build_main_parser(), ["voices", "list"])
        assert "voice id" in h.lower() or "voice ids" in h.lower(), (
            f"voices list --quiet help should mention voice ids; got: {h!r}"
        )

    def test_engines_list_quiet_mentions_engine_ids(self):
        h = _get_help_string(_build_main_parser(), ["engines", "list"])
        assert "engine id" in h.lower() or "engine ids" in h.lower(), (
            f"engines list --quiet help should mention engine ids; got: {h!r}"
        )

    def test_packs_list_quiet_mentions_slugs(self):
        h = _get_help_string(_build_main_parser(), ["packs", "list"])
        assert "slug" in h.lower(), (
            f"packs list --quiet help should mention slugs; got: {h!r}"
        )

    def test_config_show_quiet_mentions_key_value(self):
        h = _get_help_string(_build_main_parser(), ["config", "show"])
        assert "key=value" in h or "key=" in h, (
            f"config show --quiet help should mention key=value; got: {h!r}"
        )

    def test_doctor_quiet_mentions_ok_or_fail(self):
        h = _get_help_string(_build_main_parser(), ["doctor"])
        assert "doctor: OK" in h or "doctor: FAIL" in h, (
            f"doctor --quiet help should mention 'doctor: OK' or 'doctor: FAIL'; got: {h!r}"
        )

    @pytest.mark.parametrize("argv", SYNTHESIS_SUBCOMMANDS)
    def test_synthesis_quiet_mentions_output_path(self, argv):
        h = _get_help_string(_build_main_parser(), argv)
        assert "output path" in h.lower() or "tempfile path" in h.lower(), (
            f"{' '.join(argv)}: --quiet help should mention output/tempfile path; got: {h!r}"
        )


# ---------------------------------------------------------------------------
# Smoke: every leaf subcommand's --help exits 0 and prints something
# ---------------------------------------------------------------------------

ALL_LEAF_SUBCOMMANDS = SYNTHESIS_SUBCOMMANDS + NON_SYNTHESIS_SUBCOMMANDS


class TestHelpSmokeExits:
    """Every subcommand's --help should exit 0 and produce non-empty output."""

    @pytest.mark.parametrize("argv", ALL_LEAF_SUBCOMMANDS)
    def test_help_exits_0_and_nonempty(self, argv):
        result = subprocess.run(
            [
                sys.executable,
                "-m", "src.cli",
            ] + argv + ["--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"{' '.join(argv)} --help exited {result.returncode}; "
            f"stderr: {result.stderr!r}"
        )
        assert result.stdout.strip(), (
            f"{' '.join(argv)} --help produced no output"
        )
