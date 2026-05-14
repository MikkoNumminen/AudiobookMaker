"""packs subcommand — manage installed voice packs.

Usage:
    audiobookmaker packs list                  [--json] [--quiet]
    audiobookmaker packs import <directory>    [--json] [--quiet]
    audiobookmaker packs remove <slug>         [--json] [--quiet] [--yes]
    audiobookmaker packs info <slug>           [--json] [--quiet]

Exit codes:
    0  success
    1  bad input (unknown slug, validation failed, missing directory)
    2  voice_pack module unavailable
    5  unexpected internal error
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from src.cli._common import (
    EXIT_BAD_INPUT,
    EXIT_INTERNAL,
    EXIT_MISSING_DEP,
    EXIT_OK,
    add_output_mode_flags,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "packs",
        help="Manage installed voice packs.",
        description="List, import, remove, and inspect voice packs.",
    )
    sub = p.add_subparsers(dest="packs_cmd", metavar="CMD")
    sub.required = True

    lst = sub.add_parser("list", help="List installed voice packs.")
    add_output_mode_flags(lst)
    lst.set_defaults(func=_run_list)

    imp = sub.add_parser("import", help="Validate and install a voice pack.")
    imp.add_argument("directory", metavar="DIRECTORY", help="Source pack directory.")
    add_output_mode_flags(imp)
    imp.set_defaults(func=_run_import)

    rem = sub.add_parser("remove", help="Delete an installed voice pack.")
    rem.add_argument("slug", metavar="SLUG", help="Pack slug (folder name).")
    rem.add_argument("--yes", action="store_true", default=False, help="Skip confirmation prompt.")
    add_output_mode_flags(rem)
    rem.set_defaults(func=_run_remove)

    inf = sub.add_parser("info", help="Print metadata for an installed voice pack.")
    inf.add_argument("slug", metavar="SLUG", help="Pack slug (folder name).")
    add_output_mode_flags(inf)
    inf.set_defaults(func=_run_info)


def run(args: argparse.Namespace) -> int:
    return args.func(args)


def _packs_dir() -> Path:
    """Return ~/.audiobookmaker/voice_packs/."""
    try:
        from src.voice_pack import default_voice_packs_root
        return default_voice_packs_root()
    except Exception:
        return Path.home() / ".audiobookmaker" / "voice_packs"


def _err(json_mode: bool, msg: str, code: int, *, extra: dict | None = None) -> int:
    if json_mode:
        obj: dict = {"ok": False, "error": msg}
        if extra:
            obj.update(extra)
        print(json.dumps(obj), flush=True)
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return code


def _run_list(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    try:
        from src.voice_pack import list_packs
    except Exception as exc:
        print(f"Error: voice_pack unavailable: {exc}", file=sys.stderr)
        return EXIT_MISSING_DEP
    try:
        packs = list_packs(_packs_dir())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    if json_mode:
        for p in packs:
            print(json.dumps({"slug": p.root.name, "name": p.meta.name,
                               "language": p.meta.language, "tier": p.meta.tier,
                               "path": str(p.root)}), flush=True)
    elif quiet:
        for p in packs:
            print(p.root.name, flush=True)
    else:
        if not packs:
            print("No voice packs installed.")
        else:
            print(f"  {'Slug':<24}  {'Language':<8}  {'Tier':<14}  Name")
            print("  " + "-" * 70)
            for p in packs:
                print(f"  {p.root.name:<24}  {p.meta.language:<8}  {p.meta.tier:<14}  {p.meta.name}")
    return EXIT_OK


def _run_import(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    source = args.directory
    try:
        from src.voice_pack import VoicePackError, install_pack, validate_pack_dir
    except Exception as exc:
        print(f"Error: voice_pack unavailable: {exc}", file=sys.stderr)
        return EXIT_MISSING_DEP
    issues = validate_pack_dir(source)
    if issues:
        if json_mode:
            print(json.dumps({"ok": False, "issues": issues}), flush=True)
        else:
            print(f"Validation failed for {source}:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        installed = install_pack(source, _packs_dir())
    except (FileExistsError, VoicePackError) as exc:
        return _err(json_mode, str(exc), EXIT_BAD_INPUT)
    except Exception as exc:
        print(f"Internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    path_str = str(installed.root)
    if json_mode:
        print(json.dumps({"ok": True, "slug": installed.root.name, "path": path_str}), flush=True)
    elif quiet:
        print(path_str, flush=True)
    else:
        print(f"Installed: {path_str}")
    return EXIT_OK


def _run_remove(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    slug: str = args.slug
    target = _packs_dir() / slug
    if not target.exists() or not target.is_dir():
        return _err(json_mode, f"Voice pack not found: {slug}", EXIT_BAD_INPUT)
    if not (json_mode or quiet or args.yes):
        try:
            answer = input(f"Remove voice pack '{slug}'? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return EXIT_BAD_INPUT
        if answer != "y":
            print("Cancelled.")
            return EXIT_OK
    try:
        shutil.rmtree(target)
    except Exception as exc:
        print(f"Error removing pack: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    if json_mode:
        print(json.dumps({"ok": True, "slug": slug}), flush=True)
    elif not quiet:
        print(f"Removed: {slug}")
    return EXIT_OK


def _run_info(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    slug: str = args.slug
    try:
        from src.voice_pack import VoicePackError, load_pack
    except Exception as exc:
        print(f"Error: voice_pack unavailable: {exc}", file=sys.stderr)
        return EXIT_MISSING_DEP
    pack_dir = _packs_dir() / slug
    if not pack_dir.exists():
        return _err(json_mode, f"Voice pack not found: {slug}", EXIT_BAD_INPUT)
    try:
        pack = load_pack(pack_dir)
    except (FileNotFoundError, VoicePackError) as exc:
        return _err(json_mode, str(exc), EXIT_BAD_INPUT)
    except Exception as exc:
        print(f"Internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    meta = pack.meta
    if json_mode:
        obj = meta.to_dict()
        obj["slug"] = slug
        obj["path"] = str(pack.root)
        print(json.dumps(obj), flush=True)
    else:
        fields = [
            ("Slug", slug), ("Name", meta.name), ("Language", meta.language),
            ("Tier", meta.tier), ("Tier reason", meta.tier_reason),
            ("Base model", meta.base_model), ("Format version", meta.format_version),
            ("Total source minutes", f"{meta.total_source_minutes:.1f}"),
        ]
        if meta.created_at:
            fields.append(("Created at", meta.created_at))
        if meta.notes:
            fields.append(("Notes", meta.notes))
        if meta.emotion_coverage:
            fields.append(("Emotion coverage", meta.emotion_coverage))
        fields.append(("Path", str(pack.root)))
        for label, value in fields:
            print(f"{label + ':':<22} {value}")
    return EXIT_OK
