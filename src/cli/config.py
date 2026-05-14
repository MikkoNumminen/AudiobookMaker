"""config subcommand — show, set, or reset persistent user config.

Usage:
    audiobookmaker config show              # print all fields
    audiobookmaker config show <KEY>        # print one field value
    audiobookmaker config set <KEY> <VALUE> # set one field and save
    audiobookmaker config reset             # reset to defaults
    audiobookmaker config reset <KEY>       # reset one field to default
    audiobookmaker config path              # print the config file path

Exit codes:
    0  success
    1  bad input / validation failure
    5  unexpected internal error
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields as dataclass_fields

from src.cli._common import EXIT_BAD_INPUT, EXIT_INTERNAL, EXIT_OK, add_output_mode_flags


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "config",
        help="Show, set, or reset persistent user configuration.",
        description=(
            "Manage the user config stored at ~/.audiobookmaker/config.json.\n\n"
            "Exit codes:\n"
            "  0  success\n"
            "  1  bad input / validation failure\n"
            "  5  unexpected internal error\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="config_cmd", metavar="CMD")
    sub.required = True

    # config show [KEY]
    show_p = sub.add_parser("show", help="Print config (or one field).")
    show_p.add_argument("key", nargs="?", default=None, metavar="KEY",
                        help="Field name to show. Omit to show all fields.")
    add_output_mode_flags(show_p)
    show_p.set_defaults(func=_run_show)

    # config set KEY VALUE
    set_p = sub.add_parser("set", help="Set one config field and save.")
    set_p.add_argument("key", metavar="KEY", help="Field name.")
    set_p.add_argument("value", metavar="VALUE", help="New value.")
    add_output_mode_flags(set_p)
    set_p.set_defaults(func=_run_set)

    # config reset [KEY]
    reset_p = sub.add_parser("reset", help="Reset config (or one field) to defaults.")
    reset_p.add_argument("key", nargs="?", default=None, metavar="KEY",
                         help="Field name to reset. Omit to reset entire config.")
    add_output_mode_flags(reset_p)
    reset_p.set_defaults(func=_run_reset)

    # config path
    path_p = sub.add_parser("path", help="Print the config file path.")
    add_output_mode_flags(path_p)
    path_p.set_defaults(func=_run_path)


def run(args: argparse.Namespace) -> int:
    return args.func(args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_keys() -> list[str]:
    from src.app_config import UserConfig
    return list(UserConfig.__dataclass_fields__.keys())


def _check_key(key: str) -> int:
    """Print an error and return EXIT_BAD_INPUT if key is unknown, else EXIT_OK."""
    valid = _valid_keys()
    if key not in valid:
        print(
            f"Unknown config field: '{key}'. Valid fields: {', '.join(valid)}",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT
    return EXIT_OK


def _parse_bool(raw: str) -> bool | None:
    """Return parsed bool or None on failure."""
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    return None


def _coerce_value(key: str, raw: str):
    """Return the coerced value for key, or raise ValueError on bad input."""
    from src.app_config import UserConfig
    field_type = UserConfig.__dataclass_fields__[key].type
    # Resolve string annotations to actual types
    if field_type is bool or field_type == "bool":
        result = _parse_bool(raw)
        if result is None:
            raise ValueError(
                f"Field '{key}' expects a boolean. "
                "Accepted: true/false/1/0/yes/no (case-insensitive)."
            )
        return result
    # All other fields are str
    return raw


# ---------------------------------------------------------------------------
# Sub-subcommand handlers
# ---------------------------------------------------------------------------

def _run_show(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)
    key: str | None = args.key

    try:
        from src.app_config import load
        cfg = load()
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if key is not None:
        rc = _check_key(key)
        if rc != EXIT_OK:
            return rc
        value = getattr(cfg, key)
        if json_mode:
            print(json.dumps({key: value}), flush=True)
        else:
            # Single-field show is already minimal — quiet has nothing
            # extra to suppress, just print the bare value.
            print(value, flush=True)
        return EXIT_OK

    # Show all fields
    from dataclasses import asdict
    data = asdict(cfg)
    if json_mode:
        print(json.dumps(data), flush=True)
    elif quiet:
        # Quiet mode: one "key=value" line per field — script-friendly,
        # no human-readable column padding.
        for k, v in data.items():
            print(f"{k}={v}", flush=True)
    else:
        for k, v in data.items():
            print(f"{k}: {v}", flush=True)
    return EXIT_OK


def _run_set(args: argparse.Namespace) -> int:
    quiet: bool = getattr(args, "quiet", False)
    json_mode: bool = getattr(args, "json", False)
    key: str = args.key
    raw: str = args.value

    rc = _check_key(key)
    if rc != EXIT_OK:
        return rc

    try:
        value = _coerce_value(key, raw)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BAD_INPUT

    try:
        from src.app_config import load, save
        cfg = load()
        setattr(cfg, key, value)
        save(cfg)
    except Exception as exc:
        print(f"Failed to save config: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if not quiet:
        if json_mode:
            print(json.dumps({"key": key, "value": value}), flush=True)
        else:
            print(f"Set {key} = {value}", flush=True)
    return EXIT_OK


def _run_reset(args: argparse.Namespace) -> int:
    quiet: bool = getattr(args, "quiet", False)
    json_mode: bool = getattr(args, "json", False)
    key: str | None = args.key

    try:
        from src.app_config import UserConfig, save
        if key is None:
            save(UserConfig())
            if not quiet:
                msg = "Config reset to defaults."
                print(json.dumps({"status": msg}) if json_mode else msg, flush=True)
        else:
            rc = _check_key(key)
            if rc != EXIT_OK:
                return rc
            from src.app_config import load
            cfg = load()
            default_val = UserConfig.__dataclass_fields__[key].default
            setattr(cfg, key, default_val)
            save(cfg)
            if not quiet:
                if json_mode:
                    print(json.dumps({"key": key, "value": default_val}), flush=True)
                else:
                    print(f"Reset {key} = {default_val}", flush=True)
    except Exception as exc:
        print(f"Failed to reset config: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    return EXIT_OK


def _run_path(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    try:
        from src.app_config import CONFIG_FILE
        path_str = str(CONFIG_FILE)
        if json_mode:
            print(json.dumps({"path": path_str}), flush=True)
        else:
            print(path_str, flush=True)
    except Exception as exc:
        print(f"Failed to resolve config path: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    return EXIT_OK
