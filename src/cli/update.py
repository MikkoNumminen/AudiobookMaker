"""update subcommand — check for and apply application updates.

Usage:
    audiobookmaker update check          # check GitHub for a newer version
    audiobookmaker update apply [--yes]  # download + verify + install

Exit codes:
    0  success (up-to-date or update available — both are informational)
    2  SHA-256 mismatch — integrity check failed (critical)
    3  user cancelled the apply prompt
    4  network failure or download error
    5  unexpected internal error
"""

from __future__ import annotations

import argparse
import json
import sys

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_CANCELLED,
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_RUNTIME,
    add_output_mode_flags,
)

# Exit code 2 is re-used here for SHA-256 mismatch (integrity failure),
# matching the spec's "critical — this is the existential failure mode".
EXIT_SHA256_MISMATCH = 2


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "update",
        help="Check for and apply application updates.",
        description=(
            "Check GitHub Releases for a newer version and optionally install it.\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  2  SHA-256 mismatch (integrity check failed)\n"
            "  3  user cancelled\n"
            "  4  network or download failure\n"
            "  5  unexpected internal error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="update_cmd", metavar="CMD")
    sub.required = True

    # update check
    chk = sub.add_parser(
        "check",
        help="Check whether a newer version is available.",
        description="Query GitHub Releases and report whether this build is current.",
    )
    add_output_mode_flags(
        chk,
        json_help=(
            "Emit a single object with fields: "
            "current_version, latest_version, update_available, release_url."
        ),
        quiet_help=(
            "Suppress detail; print only the latest version when an update is "
            "available, or nothing when already up to date."
        ),
    )
    chk.set_defaults(func=_run_check)

    # update apply
    apl = sub.add_parser(
        "apply",
        help="Download, verify, and install the latest version.",
        description=(
            "Download the latest installer, verify its SHA-256, and run it.\n\n"
            "Only meaningful for the installed .exe build.  Running from source\n"
            "prints a manual-download notice and exits 0."
        ),
    )
    apl.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the confirmation prompt and apply immediately.",
    )
    add_output_mode_flags(
        apl,
        json_help=(
            "Emit one progress object per line; "
            "the final object reports current_version, latest_version, "
            "installer_path, release_url, and status."
        ),
        quiet_help="Suppress progress; print only the new version on success.",
    )
    apl.set_defaults(func=_run_apply)


def run(args: argparse.Namespace) -> int:
    return args.func(args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _release_url(latest_version: str) -> str:
    from src.auto_updater import GITHUB_REPO
    tag = f"v{latest_version}"
    return f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


# ---------------------------------------------------------------------------
# update check
# ---------------------------------------------------------------------------


def _run_check(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)

    try:
        from src.auto_updater import APP_VERSION, check_for_update
        info = check_for_update(APP_VERSION)
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        if json_mode:
            print(json.dumps({
                "kind": "error",
                "error": reason,
                "exit_code": EXIT_RUNTIME,
            }), flush=True)
        elif not quiet:
            print(f"update check failed: {reason}", file=sys.stderr)
        return EXIT_RUNTIME

    release_url = _release_url(info.latest_version) if info.available else ""

    if json_mode:
        print(json.dumps({
            "current_version": info.current_version,
            "latest_version": info.latest_version,
            "update_available": info.available,
            "release_url": release_url,
        }), flush=True)
        return EXIT_OK

    if info.available:
        print(
            f"Update available: v{info.latest_version}"
            f"  (current: v{info.current_version})"
        )
        print(f"  {release_url}")
    else:
        print(f"Already on latest (v{info.current_version})")

    return EXIT_OK


# ---------------------------------------------------------------------------
# update apply
# ---------------------------------------------------------------------------


def _run_apply(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    yes: bool = getattr(args, "yes", False)

    # Dev-mode guard — apply only makes sense for the installed .exe.
    if not _is_frozen():
        from src.auto_updater import APP_VERSION, GITHUB_REPO
        url = f"https://github.com/{GITHUB_REPO}/releases/latest"
        msg = (
            "update apply is only meaningful for installed builds; "
            "you appear to be running from source. "
            f"Download manually from {url} if you want to switch to the installed build."
        )
        if json_mode:
            print(json.dumps({
                "dev_mode": True,
                "message": msg,
                "release_url": url,
            }), flush=True)
        else:
            print(msg)
        return EXIT_OK

    # Check for available update first.
    try:
        from src.auto_updater import (
            APP_VERSION,
            IntegrityError,
            check_for_update,
            download_update,
            apply_update,
        )
        info = check_for_update(APP_VERSION)
    except Exception as exc:
        print(f"Error checking for update: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if not info.available:
        if json_mode:
            print(json.dumps({
                "current_version": info.current_version,
                "update_available": False,
                "message": f"Already on latest (v{info.current_version})",
            }), flush=True)
        else:
            print(f"Already on latest (v{info.current_version})")
        return EXIT_OK

    release_url = _release_url(info.latest_version)

    # Prompt unless --yes.
    if not yes:
        print(
            f"Download and install v{info.latest_version}? [y/N] ",
            end="", flush=True, file=sys.stderr,
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return EXIT_CANCELLED
        if answer not in ("y", "yes"):
            print("Cancelled.", file=sys.stderr)
            return EXIT_CANCELLED

    if not json_mode:
        print(f"Downloading v{info.latest_version}…")

    # Download and verify.
    try:
        installer_path = download_update(info)
    except IntegrityError as exc:
        # The dedicated exception class lets us exit with the project's
        # existential failure code without parsing the error message.
        print(f"Integrity check failed: {exc}", file=sys.stderr)
        return EXIT_SHA256_MISMATCH
    except RuntimeError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    # Apply (launches installer + exits the process).
    if json_mode:
        print(json.dumps({
            "current_version": info.current_version,
            "latest_version": info.latest_version,
            "installer_path": str(installer_path),
            "release_url": release_url,
            "status": "applying",
        }), flush=True)
    else:
        print(f"Installing v{info.latest_version}…")

    try:
        apply_update(installer_path, expected_version=info.latest_version)
    except Exception as exc:
        print(f"Failed to launch installer: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    # apply_update calls os._exit(0); reaching here means it returned
    # without exiting (only in tests / dry-run scenarios).
    return EXIT_OK
