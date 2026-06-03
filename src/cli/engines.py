"""engines subcommand — list, install, repair, remove, and check TTS engines.

Usage:
    audiobookmaker engines list             [--installed-only] [--json] [--quiet]
    audiobookmaker engines install <id>     [--yes] [--json] [--quiet]
    audiobookmaker engines repair  <id>     [--yes] [--json] [--quiet]
    audiobookmaker engines remove  <id>     [--yes] [--json] [--quiet]
    audiobookmaker engines check   <id>

Exit codes:
    0  success / engine available
    1  bad input (unknown engine id, engine not installed)
    2  install/check failure
    3  cancelled by user
    5  unexpected error
"""

from __future__ import annotations

import argparse
import json
import sys
import threading

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_CANCELLED,
    EXIT_INTERNAL,
    EXIT_MISSING_DEP,
    EXIT_OK,
    add_output_mode_flags,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "engines",
        help="List and manage TTS engines.",
        description="List, install, remove, and check TTS engines.",
    )
    sub = p.add_subparsers(dest="engines_cmd", metavar="CMD")
    sub.required = True

    # engines list
    lst = sub.add_parser("list", help="List all registered engines.")
    lst.add_argument(
        "--installed-only",
        action="store_true",
        default=False,
        help="Only show engines that are currently available.",
    )
    add_output_mode_flags(
        lst,
        json_help=(
            "Emit one engine object per line (NDJSON) with fields: "
            "id, display_name, available, reason."
        ),
        quiet_help="Print only engine ids, one per line.",
    )
    lst.set_defaults(func=_run_list)

    # engines install <id>
    ins = sub.add_parser("install", help="Download and install a TTS engine.")
    ins.add_argument("engine_id", metavar="ID", help="Engine id (e.g. piper, chatterbox_grandmom).")
    ins.add_argument("--yes", action="store_true", default=False, help="Skip prompts.")
    add_output_mode_flags(
        ins,
        json_help=(
            "Emit one progress object per line (NDJSON) with fields: "
            "kind, step, total_steps, step_label, percent, message, error, done."
        ),
        quiet_help="Suppress progress; print only the final result.",
    )
    ins.set_defaults(func=_run_install)

    # engines repair <id>
    rep = sub.add_parser(
        "repair",
        help="Repair a broken/drifted engine by force-reinstalling its pins.",
    )
    rep.add_argument("engine_id", metavar="ID", help="Engine id (e.g. chatterbox_grandmom).")
    rep.add_argument("--yes", action="store_true", default=False, help="Skip prompts.")
    add_output_mode_flags(
        rep,
        json_help=(
            "Emit one progress object per line (NDJSON) with fields: "
            "kind, step, total_steps, step_label, percent, message, error, done."
        ),
        quiet_help="Suppress progress; print only the final result.",
    )
    rep.set_defaults(func=_run_repair)

    # engines remove <id>
    rem = sub.add_parser("remove", help="Remove an installed TTS engine's assets.")
    rem.add_argument("engine_id", metavar="ID", help="Engine id to remove.")
    rem.add_argument("--yes", action="store_true", default=False, help="Skip confirmation.")
    add_output_mode_flags(
        rem,
        json_help=(
            "Emit a single result object with fields: ok, id. "
            "Also bypasses the interactive confirmation prompt."
        ),
        quiet_help="Suppress the removal confirmation message.",
    )
    rem.set_defaults(func=_run_remove)

    # engines check <id>
    chk = sub.add_parser("check", help="Check whether a TTS engine is available.")
    chk.add_argument("engine_id", metavar="ID", help="Engine id to check.")
    add_output_mode_flags(
        chk,
        json_help=(
            "Emit a single check object with fields: "
            "id, display_name, available, reason."
        ),
        quiet_help='Suppress detail; print only "available" or nothing (uses exit code).',
    )
    chk.set_defaults(func=_run_check)


def run(args: argparse.Namespace) -> int:
    return args.func(args)


def _run_list(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    installed_only: bool = getattr(args, "installed_only", False)

    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import list_engines
        engines = list_engines()
    except Exception as exc:
        print(f"Error loading engines: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    rows = []
    for eng in engines:
        try:
            status = eng.check_status()
            available = status.available
            reason = status.reason if not available else ""
        except Exception as exc:
            available = False
            reason = str(exc)

        if installed_only and not available:
            continue

        rows.append({
            "id": eng.id,
            "display_name": eng.display_name,
            "available": available,
            "reason": reason,
        })

    if json_mode:
        for row in rows:
            print(json.dumps(row), flush=True)
    elif quiet:
        for row in rows:
            print(row["id"], flush=True)
    else:
        _print_engines_table(rows)

    return EXIT_OK


def _print_engines_table(rows: list[dict]) -> None:
    if not rows:
        print("No engines found.")
        return
    print(f"  {'ID':<20}  {'Available':<10}  {'Name / Reason'}")
    print("  " + "-" * 72)
    for row in rows:
        avail = "yes" if row["available"] else "no"
        detail = row["display_name"] if row["available"] else row["reason"]
        print(f"  {row['id']:<20}  {avail:<10}  {detail}")


def _run_install(args: argparse.Namespace) -> int:
    return _run_install_or_repair(args, repair=False)


def _run_repair(args: argparse.Namespace) -> int:
    return _run_install_or_repair(args, repair=True)


def _run_install_or_repair(args: argparse.Namespace, *, repair: bool) -> int:
    """Shared install/repair driver.

    ``repair`` selects ``installer.force_reinstall`` (force the pinned
    versions back into a drifted venv) over ``installer.install`` and adjusts
    the user-facing verb. Exit codes and event shape are identical for both so
    scripts can treat them the same.
    """
    verb = "Repair" if repair else "Install"
    verb_ing = "Repairing" if repair else "Installing"
    engine_id: str = args.engine_id
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)

    try:
        from src.engine_installer import get_installer
    except Exception as exc:
        print(f"Error loading installer module: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    installer = get_installer(engine_id)
    if installer is None:
        print(f"Unknown engine id '{engine_id}'. Run 'engines list' to see valid ids.", file=sys.stderr)
        return EXIT_BAD_INPUT

    def _emit(message: str) -> None:
        if json_mode:
            print(json.dumps({"kind": "log", "message": message}), flush=True)
        elif not quiet:
            print(message, flush=True)

    try:
        issues = installer.check_prerequisites(ui_lang="en")
    except Exception as exc:
        # An exception from inside check_prerequisites is an internal
        # bug in the installer, not a missing dependency the user can
        # fix — return EXIT_INTERNAL so scripts can distinguish the two.
        print(f"Prerequisite check failed: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if issues:
        for issue in issues:
            print(f"Missing prerequisite: {issue}", file=sys.stderr)
        return EXIT_MISSING_DEP

    _emit(f"{verb_ing} engine '{engine_id}'...")

    error_holder: list[str] = []
    cancel_event = threading.Event()

    def _progress(prog) -> None:
        if prog.error:
            error_holder.append(prog.error)
        if json_mode:
            print(json.dumps({
                "kind": "progress",
                "step": prog.step,
                "total_steps": prog.total_steps,
                "step_label": prog.step_label,
                "percent": prog.percent,
                "message": prog.message,
                "error": prog.error,
                "done": prog.done,
            }), flush=True)
        elif not quiet:
            if prog.error:
                print(f"  Error: {prog.error}", flush=True)
            elif prog.done:
                print(f"  Done: {prog.step_label}", flush=True)
            elif prog.message:
                print(f"  {prog.message}", flush=True)

    runner = installer.force_reinstall if repair else installer.install
    try:
        runner(_progress, cancel_event)
    except InterruptedError:
        print(f"{verb} cancelled.", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as exc:
        print(f"{verb} failed: {exc}", file=sys.stderr)
        return EXIT_MISSING_DEP

    if error_holder:
        print(f"{verb} failed: {error_holder[-1]}", file=sys.stderr)
        return EXIT_MISSING_DEP

    _emit(f"Engine '{engine_id}' {'repaired' if repair else 'installed'} successfully.")
    return EXIT_OK


def _run_remove(args: argparse.Namespace) -> int:
    engine_id: str = args.engine_id
    yes: bool = getattr(args, "yes", False)
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)

    def _err(msg: str, code: int) -> int:
        if json_mode:
            print(json.dumps({"ok": False, "error": msg, "exit_code": code}), flush=True)
        else:
            print(msg, file=sys.stderr)
        return code

    try:
        from src.engine_installer import get_installer
    except Exception as exc:
        return _err(f"Error loading installer module: {exc}", EXIT_INTERNAL)

    installer = get_installer(engine_id)
    if installer is None:
        return _err(f"Unknown engine id '{engine_id}'.", EXIT_BAD_INPUT)

    if hasattr(installer, "is_installed") and not installer.is_installed():
        return _err(f"Engine '{engine_id}' is not installed.", EXIT_BAD_INPUT)

    # --json bypasses the prompt because JSON consumers can't answer y/N
    # interactively. --quiet alone does NOT bypass it — cosmetic flags must
    # not change destructive behaviour (lesson from M6 / packs remove fix).
    if not (json_mode or yes):
        # Route the prompt to stderr so it never leaks into a stdout
        # pipeline (lesson from N5 / packs remove + update apply prompt
        # fix in PR #49). Cancellation message also stays on stderr to
        # match packs remove / update apply.
        print(
            f"Remove engine '{engine_id}'? [y/N] ",
            end="", file=sys.stderr, flush=True,
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return EXIT_CANCELLED
        if answer not in ("y", "yes"):
            print("Cancelled.", file=sys.stderr)
            return EXIT_CANCELLED

    # Every installer in the registry implements remove() on its class
    # (PiperInstaller, ChatterboxInstaller); the CLI does not reach for
    # private layout details any more.
    try:
        if hasattr(installer, "remove"):
            removed_any = bool(installer.remove())
        else:
            # Defensive fallback for any installer that predates the public
            # remove() contract — surface as "not installed" rather than
            # silently doing nothing.
            removed_any = False
    except Exception as exc:
        return _err(f"Remove failed: {exc}", EXIT_INTERNAL)

    if not removed_any:
        return _err(f"Engine '{engine_id}' is not installed.", EXIT_BAD_INPUT)

    if json_mode:
        print(json.dumps({"ok": True, "id": engine_id}), flush=True)
    elif not quiet:
        print(f"Removed: {engine_id}")
    return EXIT_OK


def _run_check(args: argparse.Namespace) -> int:
    engine_id: str = args.engine_id
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)

    try:
        from src import engine_registry  # noqa: F401
        from src.tts_base import get_engine
    except Exception as exc:
        print(f"Error loading engines: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    engine = get_engine(engine_id)
    if engine is None:
        msg = f"Unknown engine id '{engine_id}'. Run 'engines list' to see valid ids."
        if json_mode:
            print(json.dumps({"available": False, "reason": msg}), flush=True)
        else:
            print(msg, file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        status = engine.check_status()
    except Exception as exc:
        msg = str(exc)
        if json_mode:
            print(json.dumps({"id": engine_id, "available": False, "reason": msg}), flush=True)
        else:
            print(f"check_status() failed: {msg}", file=sys.stderr)
        return EXIT_INTERNAL

    if json_mode:
        print(json.dumps({
            "id": engine_id,
            "display_name": engine.display_name,
            "available": status.available,
            "reason": status.reason,
        }), flush=True)
    elif not quiet:
        avail = "available" if status.available else "not available"
        detail = f"  {status.reason}" if status.reason else ""
        print(f"{engine_id}: {avail}{detail}")

    return EXIT_OK if status.available else EXIT_MISSING_DEP
