"""Auto-update module for AudiobookMaker.

Checks GitHub Releases for new versions, downloads the installer,
and launches a silent update.
"""

import json
import logging
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_VERSION = "2.0.0"
GITHUB_REPO = "MikkoNumminen/AudiobookMaker"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DIR = Path(tempfile.gettempdir()) / "audiobookmaker-update"

CHUNK_SIZE = 256 * 1024  # 256 KB
API_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class UpdateInfo:
    """Information about an available (or unavailable) update."""

    available: bool
    current_version: str
    latest_version: str
    download_url: str
    release_notes: str
    asset_size_bytes: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string like '2.1.0' into a comparable tuple."""
    cleaned = version_str.lstrip("vV").strip()
    parts: list[int] = []
    for part in cleaned.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _find_exe_asset(assets: list[dict]) -> Optional[dict]:
    """Return the first .exe asset from a GitHub release asset list."""
    for asset in assets:
        name: str = asset.get("name", "")
        content_type: str = asset.get("content_type", "")
        if name.endswith(".exe") or content_type == "application/x-msdownload":
            return asset
    return None


def _no_update(current_version: str) -> UpdateInfo:
    """Return an UpdateInfo indicating no update is available."""
    return UpdateInfo(
        available=False,
        current_version=current_version,
        latest_version=current_version,
        download_url="",
        release_notes="",
        asset_size_bytes=0,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version() -> str:
    """Return the current application version."""
    return APP_VERSION


def check_for_update(current_version: str) -> UpdateInfo:
    """Check GitHub Releases API for a newer version.

    Returns UpdateInfo with ``available=False`` when the app is up to date
    or when any error occurs.  Never raises.
    """
    try:
        req = Request(GITHUB_API_URL)
        req.add_header("User-Agent", f"AudiobookMaker/{current_version}")
        req.add_header("Accept", "application/vnd.github+json")

        with urlopen(req, timeout=API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag: str = data.get("tag_name", "")
        latest_version = tag.lstrip("vV").strip()
        if not latest_version:
            logger.warning("GitHub release has no tag_name")
            return _no_update(current_version)

        if _parse_version(latest_version) <= _parse_version(current_version):
            return _no_update(current_version)

        asset = _find_exe_asset(data.get("assets", []))
        if asset is None:
            logger.warning("No .exe asset found in latest release")
            return _no_update(current_version)

        return UpdateInfo(
            available=True,
            current_version=current_version,
            latest_version=latest_version,
            download_url=asset["browser_download_url"],
            release_notes=data.get("body", ""),
            asset_size_bytes=asset.get("size", 0),
        )

    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        logger.debug("Update check failed: %s", exc)
        return _no_update(current_version)


def download_update(
    update: UpdateInfo,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Download the installer .exe to a temporary directory.

    *progress_cb(bytes_done, bytes_total)* is called after every chunk.
    If *cancel_event* is set, the download is aborted and the partial file
    is removed.

    Returns the path to the downloaded installer.

    Raises
    ------
    RuntimeError
        On download failure or cancellation.
    """
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"AudiobookMaker-Setup-{update.latest_version}.exe"
    dest = UPDATE_DIR / filename

    req = Request(update.download_url)
    req.add_header("User-Agent", f"AudiobookMaker/{update.current_version}")

    try:
        with urlopen(req, timeout=60) as resp:
            total = update.asset_size_bytes or int(resp.headers.get("Content-Length", 0))
            done = 0

            with open(dest, "wb") as fp:
                while True:
                    if cancel_event and cancel_event.is_set():
                        fp.close()
                        dest.unlink(missing_ok=True)
                        raise RuntimeError("Download cancelled")

                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    fp.write(chunk)
                    done += len(chunk)

                    if progress_cb:
                        progress_cb(done, total)

    except RuntimeError:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {exc}") from exc

    return dest


def apply_update(installer_path: Path) -> None:
    """Launch the installer silently and exit the current application.

    The Inno Setup installer runs with ``/VERYSILENT`` so the user sees no
    UI.  Its ``[Run]`` section is expected to relaunch the new version after
    installation completes.

    Must be called from the main thread.
    """
    current_install_dir = str(Path(sys.executable).parent)

    subprocess.Popen([
        str(installer_path),
        "/VERYSILENT",
        "/NORESTART",
        "/SUPPRESSMSGBOXES",
        f"/DIR={current_install_dir}",
    ])

    sys.exit(0)
