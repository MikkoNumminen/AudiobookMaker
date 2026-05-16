"""doctor subcommand — system readiness checks.

Checks:
- ffmpeg presence
- NVIDIA GPU via nvidia-smi / WMI
- Free disk on the output drive
- Python version
- Which TTS engines are available

Exit codes:
    0  all checks passed (or warnings only)
    2  a required dependency is missing (ffmpeg absent, no available engine)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from typing import Any

from src.cli._common import EXIT_MISSING_DEP, EXIT_OK, add_output_mode_flags


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "doctor",
        help="Check system requirements (ffmpeg, GPU, disk, engines).",
        description=(
            "Run system health checks and report which engines are ready.\n\n"
            "Exit codes:\n"
            "  0  all required components present\n"
            "  2  a required dependency is missing\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_output_mode_flags(
        p,
        json_help=(
            "Emit one check object per line (NDJSON) with fields: "
            "name, status, required, detail; "
            "followed by a summary object with fields: "
            "kind, status, required_missing, exit_code."
        ),
        quiet_help='Print only "doctor: OK" or "doctor: FAIL — required components missing".',
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    json_mode: bool = getattr(args, "json", False)
    quiet: bool = getattr(args, "quiet", False)

    checks: list[dict[str, Any]] = []
    any_required_missing = False

    # ------------------------------------------------------------------
    # 1. ffmpeg
    # ------------------------------------------------------------------
    from src.ffmpeg_path import get_ffmpeg_exe
    ffmpeg = get_ffmpeg_exe()
    ffmpeg_ok = ffmpeg is not None
    if not ffmpeg_ok:
        any_required_missing = True
    checks.append({
        "name": "ffmpeg",
        "status": "ok" if ffmpeg_ok else "missing",
        "required": True,
        "detail": ffmpeg if ffmpeg_ok else "Not found — install ffmpeg and add to PATH.",
    })

    # ------------------------------------------------------------------
    # 2. GPU
    # ------------------------------------------------------------------
    gpu = None
    _gpu_err = ""
    try:
        from src.system_checks import detect_gpu
        gpu = detect_gpu()
    except Exception as exc:
        _gpu_err = str(exc)

    if gpu is not None and gpu.has_nvidia:
        gpu_status = "ok"
        gpu_detail = f"{gpu.gpu_name}, {gpu.vram_mb} MB VRAM, driver {gpu.driver_version}"
    elif gpu is not None:
        gpu_status = "not_found"
        gpu_detail = "No NVIDIA GPU detected (Chatterbox engine needs one)."
    else:
        gpu_status = "error"
        gpu_detail = _gpu_err or "GPU detection failed."
    checks.append({
        "name": "gpu",
        "status": gpu_status,
        "required": False,  # GPU is required only for Chatterbox, not the CLI
        "detail": gpu_detail,
    })

    # ------------------------------------------------------------------
    # 3. Disk space (output drive)
    # ------------------------------------------------------------------
    try:
        from src.synthesis_orchestrator import default_output_dir
        from src.system_checks import check_disk_space
        out_dir = default_output_dir()
        disk = check_disk_space(str(out_dir))
        disk_ok = disk.free_gb >= 1.0
        disk_detail = f"{disk.free_gb:.1f} GB free of {disk.total_gb:.1f} GB on {disk.path}"
        if not disk_ok:
            disk_detail += " (low — synthesis needs at least 1 GB free)"
    except Exception as exc:
        disk_ok = False
        disk_detail = f"Disk check failed: {exc}"
    checks.append({
        "name": "disk",
        "status": "ok" if disk_ok else "warning",
        "required": False,
        "detail": disk_detail,
    })

    # ------------------------------------------------------------------
    # 4. Python version
    # ------------------------------------------------------------------
    major, minor = sys.version_info.major, sys.version_info.minor
    py_ok = (major, minor) >= (3, 10)
    checks.append({
        "name": "python",
        "status": "ok" if py_ok else "warning",
        "required": False,
        "detail": f"Python {major}.{minor} ({sys.executable})",
    })

    # ------------------------------------------------------------------
    # 5. Engines
    # ------------------------------------------------------------------
    try:
        from src import engine_registry  # noqa: F401 — populates registry
        from src.tts_base import list_engines
        engines = list_engines()
    except Exception as exc:
        engines = []
        checks.append({
            "name": "engine_registry",
            "status": "error",
            "required": True,
            "detail": f"Could not load engine registry: {exc}",
        })
        any_required_missing = True

    available_count = 0
    for eng in engines:
        try:
            status = eng.check_status()
        except Exception as exc:
            status_str = "error"
            detail = str(exc)
        else:
            status_str = "ok" if status.available else "unavailable"
            detail = status.reason if not status.available else eng.display_name
            if status.available:
                available_count += 1
        checks.append({
            "name": f"engine:{eng.id}",
            "status": status_str,
            "required": False,
            "detail": detail,
        })

    if engines and available_count == 0:
        any_required_missing = True
        checks.append({
            "name": "engines_available",
            "status": "missing",
            "required": True,
            "detail": "No TTS engines are available — install at least one (e.g. pip install edge-tts).",
        })

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    exit_code = EXIT_MISSING_DEP if any_required_missing else EXIT_OK

    if json_mode:
        for check in checks:
            print(json.dumps(check), flush=True)
        # Emit a terminal summary line so consumers get pass/fail without
        # having to aggregate every per-check row themselves.
        required_missing = [
            c["name"]
            for c in checks
            if c.get("required") and c.get("status") in ("missing", "not_found", "unavailable", "error")
        ]
        summary = {
            "kind": "summary",
            "status": "fail" if any_required_missing else "pass",
            "required_missing": required_missing,
            "exit_code": exit_code,
        }
        print(json.dumps(summary), flush=True)
    elif not quiet:
        _print_table(checks)
    else:
        # quiet mode: print summary line only
        if any_required_missing:
            print("doctor: FAIL — required components missing", flush=True)
        else:
            print("doctor: OK", flush=True)

    return exit_code


def _print_table(checks: list[dict]) -> None:
    """Print a human-readable table of check results."""
    STATUS_ICON = {
        "ok": "OK ",
        "warning": "WRN",
        "missing": "ERR",
        "unavailable": "---",
        "error": "ERR",
        "not_found": "---",
    }
    print(f"{'Name':<30}  {'Status':<12}  Detail")
    print("-" * 80)
    for check in checks:
        icon = STATUS_ICON.get(check["status"], "???")
        print(f"  {check['name']:<28}  [{icon}]         {check['detail']}")
    print()
