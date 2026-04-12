"""System capability checks for AudiobookMaker.

Detects GPU hardware, available disk space, and Python 3.11 installation.
Used by the engine installer dialog to show system readiness and by
individual engine installers to validate prerequisites.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GpuInfo:
    """NVIDIA GPU detection result."""

    has_nvidia: bool = False
    gpu_name: str = ""
    driver_version: str = ""
    vram_mb: int = 0

    @property
    def driver_version_float(self) -> float:
        try:
            return float(self.driver_version)
        except (ValueError, TypeError):
            return 0.0


@dataclass
class DiskInfo:
    """Disk space on a given path."""

    path: str = ""
    free_gb: float = 0.0
    total_gb: float = 0.0


@dataclass
class PythonInfo:
    """Python 3.11 detection result."""

    found: bool = False
    path: Optional[Path] = None
    version: str = ""


@dataclass
class SystemReport:
    """Aggregate system check result."""

    gpu: GpuInfo = field(default_factory=GpuInfo)
    disk: DiskInfo = field(default_factory=DiskInfo)
    python311: PythonInfo = field(default_factory=PythonInfo)


def detect_gpu() -> GpuInfo:
    """Detect NVIDIA GPU via nvidia-smi, with WMI fallback.

    Returns a GpuInfo with has_nvidia=False if no NVIDIA GPU found.
    Never raises — detection failures return empty info.
    """
    # Try nvidia-smi first (most reliable).
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                return GpuInfo(
                    has_nvidia=True,
                    gpu_name=parts[0],
                    driver_version=parts[1],
                    vram_mb=int(float(parts[2])),
                )
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Fallback: PowerShell WMI (Windows only).
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | "
                    "Select-Object Name, DriverVersion, "
                    "AdapterRAM | ConvertTo-Json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json

                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    data = [data]
                for gpu in data:
                    name = gpu.get("Name", "")
                    if "NVIDIA" in name.upper():
                        ram_bytes = gpu.get("AdapterRAM", 0)
                        return GpuInfo(
                            has_nvidia=True,
                            gpu_name=name,
                            driver_version=gpu.get("DriverVersion", ""),
                            vram_mb=int(ram_bytes / (1024 * 1024))
                            if ram_bytes
                            else 0,
                        )
        except (
            FileNotFoundError,
            subprocess.TimeoutExpired,
            ValueError,
            Exception,
        ):
            pass

    return GpuInfo()


def check_disk_space(path: str = "") -> DiskInfo:
    """Check free disk space at the given path.

    If path is empty, checks the drive where the user profile lives.
    """
    if not path:
        path = str(Path.home())
    try:
        usage = shutil.disk_usage(path)
        return DiskInfo(
            path=path,
            free_gb=round(usage.free / (1024**3), 1),
            total_gb=round(usage.total / (1024**3), 1),
        )
    except OSError:
        return DiskInfo(path=path)


def find_python311() -> PythonInfo:
    """Detect whether Python 3.11 is installed.

    Checks py launcher, known install paths, and PATH in that order.
    """
    # 1. Windows py launcher.
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["py", "-3.11", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                p = Path(result.stdout.strip())
                if p.exists():
                    return PythonInfo(found=True, path=p, version="3.11")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 2. Known per-user install path (Windows).
    if sys.platform == "win32":
        known = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "python.exe"
        if known.exists():
            return PythonInfo(found=True, path=known, version="3.11")

    # 3. Known system install path (Windows).
    if sys.platform == "win32":
        system = Path(r"C:\Program Files\Python311\python.exe")
        if system.exists():
            return PythonInfo(found=True, path=system, version="3.11")

    # 4. python3.11 on PATH.
    which = shutil.which("python3.11")
    if which:
        return PythonInfo(found=True, path=Path(which), version="3.11")

    # 5. Bare python/python3 on PATH — check version.
    for candidate in ("python", "python3"):
        which = shutil.which(candidate)
        if not which:
            continue
        try:
            result = subprocess.run(
                [
                    which,
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip() == "3.11":
                return PythonInfo(found=True, path=Path(which), version="3.11")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return PythonInfo()


def run_full_check(disk_path: str = "") -> SystemReport:
    """Run all system checks and return an aggregate report."""
    return SystemReport(
        gpu=detect_gpu(),
        disk=check_disk_space(disk_path),
        python311=find_python311(),
    )
