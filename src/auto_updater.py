"""Auto-update module for AudiobookMaker.

Checks GitHub Releases for new versions, downloads the installer,
and launches a silent update.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_VERSION = "3.11.0"
GITHUB_REPO = "MikkoNumminen/AudiobookMaker"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DIR = Path(tempfile.gettempdir()) / "audiobookmaker-update"
# Per-user dir (not system-wide temp): on a shared Windows machine another
# local user can write files into %TEMP% and could plant a fake marker that
# triggers a bogus "update failed" dialog. Keeping the marker under the
# user's home directory closes that tampering vector.
_USER_DIR = Path.home() / ".audiobookmaker"
PENDING_MARKER = _USER_DIR / "update_pending.json"
# One-time migration: the marker used to live in the system temp dir. Old
# markers at this path are read once (for self-heal on the very next launch
# after the upgrade) and then removed. Safe to delete this constant and the
# migration branch in read_pending_marker() a couple of releases from now.
_LEGACY_PENDING_MARKER = Path(tempfile.gettempdir()) / "audiobookmaker_update_pending.json"

CHUNK_SIZE = 256 * 1024  # 256 KB
API_TIMEOUT = 10  # seconds
# Sidecar SHA-256 files are tiny (~80 bytes) but still go over GitHub's
# releases CDN, which can stall. 30 s is generous enough for a slow mobile
# network but bounded so the update flow never hangs indefinitely.
SIDECAR_TIMEOUT = 30  # seconds

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
    sha256: str  # expected SHA-256 hex digest ("" if not provided in release notes)


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


def _find_exe_asset(assets: list[dict]) -> dict | None:
    """Return the first .exe asset from a GitHub release asset list."""
    for asset in assets:
        name: str = asset.get("name", "")
        content_type: str = asset.get("content_type", "")
        if name.endswith(".exe") or content_type == "application/x-msdownload":
            return asset
    return None


def _find_sha256_sidecar_asset(
    assets: list[dict], exe_name: str
) -> dict | None:
    """Return the ``.exe.sha256`` sidecar asset matching ``exe_name``, if any.

    The release pipeline uploads a sidecar text file alongside the installer
    so that auto-update can recover when (a) someone published a release with
    no SHA-256 line in the notes, or (b) GitHub's release-notes propagation
    is briefly stale right after publish.
    """
    target = exe_name + ".sha256"
    for asset in assets:
        if asset.get("name") == target:
            return asset
    return None


def _fetch_sidecar_sha256(
    asset: dict, current_version: str
) -> str | None:
    """Download a tiny `.sha256` sidecar asset and return the hex digest.

    Sidecar format mirrors `sha256sum`: ``<hex>  <filename>`` on one line.
    Any parse failure or network error returns None — the caller falls back
    to the existing 'no SHA-256 published' behaviour.
    """
    url = asset.get("browser_download_url")
    if not url:
        return None
    try:
        req = Request(url)
        req.add_header("User-Agent", f"AudiobookMaker/{current_version}")
        with urlopen(req, timeout=SIDECAR_TIMEOUT) as resp:
            payload = resp.read(512).decode("ascii", errors="replace")
    except (URLError, OSError) as exc:
        logger.debug("Sidecar SHA-256 fetch failed: %s", exc)
        return None
    match = re.search(r"\b([0-9a-fA-F]{64})\b", payload)
    if not match:
        logger.debug("Sidecar payload had no 64-hex token: %r", payload[:80])
        return None
    return match.group(1).lower()


# Characters that would break the Windows relaunch batch script if present
# in a substituted path. `"` ends a quoted string, `%` starts a variable
# expansion, `^` is the cmd.exe escape character, `&` chains commands, and
# CR/LF terminate a line. Today all substituted paths come from
# Path.home() / tempfile.gettempdir() / sys.executable so this is a defense
# in depth — but we want to fail loud if that assumption ever slips.
_BAT_UNSAFE_CHARS = ('"', '%', '^', '&', '\r', '\n')


def _assert_bat_safe_path(path: Path, label: str) -> None:
    """Raise ValueError if *path* contains characters that break a .bat script.

    The relaunch batch script built in :func:`apply_update` substitutes
    several paths via f-strings into ``set "VAR=..."`` lines and quoted
    command invocations. If any substituted path contains a batch
    metacharacter the script is malformed and the silent update flow
    silently corrupts (or worse, executes the wrong command). Paths we
    control today are safe, but this assertion makes the invariant loud.
    """
    s = str(path)
    for ch in _BAT_UNSAFE_CHARS:
        if ch in s:
            raise ValueError(
                f"{label} contains batch-unsafe character {ch!r}: {s!r}. "
                "Refusing to build relaunch .bat — would be malformed."
            )


def _no_update(current_version: str) -> UpdateInfo:
    """Return an UpdateInfo indicating no update is available."""
    return UpdateInfo(
        available=False,
        current_version=current_version,
        latest_version=current_version,
        download_url="",
        release_notes="",
        asset_size_bytes=0,
        sha256="",
    )


def _extract_sha256(release_notes: str) -> str | None:
    """Extract a SHA-256 hash from the release notes body.

    Looks for a line like:
        SHA-256: abc123...
    or:
        `abc123...` (64 hex chars on their own)
    """
    # Pattern: "SHA-256: <hex>" or "sha256: <hex>"  (with optional backticks)
    match = re.search(r"(?i)sha-?256:\s*`?([0-9a-fA-F]{64})`?", release_notes)
    if match:
        return match.group(1).lower()
    return None


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

        sha256 = _extract_sha256(data.get("body", ""))
        # Fallback: the release author may have published the sidecar
        # `.exe.sha256` asset without (or before) editing the body. Both
        # paths are equally trustworthy because the release author
        # authenticates either edit.
        if not sha256:
            sidecar = _find_sha256_sidecar_asset(
                data.get("assets", []), asset.get("name", "")
            )
            if sidecar is not None:
                sha256 = _fetch_sidecar_sha256(sidecar, current_version)
                if sha256:
                    logger.info(
                        "Recovered SHA-256 from sidecar asset (release notes lacked one)"
                    )

        return UpdateInfo(
            available=True,
            current_version=current_version,
            latest_version=latest_version,
            download_url=asset["browser_download_url"],
            release_notes=data.get("body", ""),
            asset_size_bytes=asset.get("size", 0),
            sha256=sha256 or "",
        )

    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        logger.debug("Update check failed: %s", exc)
        return _no_update(current_version)


def download_update(
    update: UpdateInfo,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
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
    if not update.sha256:
        raise RuntimeError(
            "No SHA-256 hash published for this release. "
            "Auto-update is blocked for security reasons. "
            "Use the 'Lataa selaimella' / 'Download in browser' button "
            "to install the new version manually."
        )

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

    # Verify integrity — SHA-256 is mandatory (checked at function entry).
    file_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
    if file_hash != update.sha256:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"Integrity check failed: expected SHA-256 {update.sha256[:16]}…, "
            f"got {file_hash[:16]}…. Download may be corrupted."
        )
    logger.info("SHA-256 verified: %s", file_hash[:16])

    return dest


def _write_pending_marker(expected_version: str, installer_path: Path) -> None:
    """Record that an update is in flight so the next launch can verify it."""
    import time
    try:
        PENDING_MARKER.parent.mkdir(parents=True, exist_ok=True)
        PENDING_MARKER.write_text(json.dumps({
            "expected_version": expected_version,
            "installer_path": str(installer_path),
            "started_at": time.time(),
        }), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not write pending marker: %s", exc)


def read_pending_marker() -> dict | None:
    """Return the pending-update marker dict, or None if no update is pending."""
    if not PENDING_MARKER.exists():
        # One-time migration from the old system-temp location. If a marker
        # was written by a previous version of the app, honor it once so the
        # user still gets the self-heal flow, then remove it.
        if _LEGACY_PENDING_MARKER.exists():
            try:
                data = json.loads(
                    _LEGACY_PENDING_MARKER.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                data = None
            try:
                _LEGACY_PENDING_MARKER.unlink(missing_ok=True)
            except OSError:
                pass
            return data
        return None
    try:
        return json.loads(PENDING_MARKER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_pending_marker() -> None:
    """Remove the pending-update marker (after verifying success or giving up)."""
    try:
        PENDING_MARKER.unlink(missing_ok=True)
    except OSError:
        pass


def is_post_update_launch(current_version: str) -> bool:
    """Return True iff this app launch was triggered by a successful auto-update.

    Peeks at the pending-update marker without modifying it. ``True`` means
    the user clicked "Päivitä nyt" recently, the silent install ran, and
    we are now the freshly-installed binary at ``current_version`` matching
    or exceeding the marker's expected version. Used by the GUI to bring
    the relaunched window to the foreground (the user clicked an action
    minutes ago and expects to see the result, not have it open behind a
    browser tab).

    Safe to call before or after ``verify_pending_update`` — does not
    clear the marker either way.
    """
    marker = read_pending_marker()
    if marker is None:
        return False
    expected = marker.get("expected_version", "")
    if not expected:
        return False
    return _parse_version(current_version) >= _parse_version(expected)


def verify_pending_update(current_version: str) -> dict | None:
    """Return the pending marker if the update FAILED, else clear and return None.

    Called on app launch. If the current version matches the expected
    version in the marker, the update succeeded — remove the marker.
    Otherwise the silent install didn't take effect; return the marker
    so the GUI can offer a visible-installer fallback.
    """
    marker = read_pending_marker()
    if marker is None:
        return None

    expected = marker.get("expected_version", "")
    if expected and _parse_version(current_version) >= _parse_version(expected):
        # Update succeeded.
        clear_pending_marker()
        return None

    # Ignore stale markers older than 24h — something went very wrong
    # and the user has since done something else.
    import time
    started = marker.get("started_at", 0)
    if started and (time.time() - started) > 24 * 3600:
        clear_pending_marker()
        return None

    return marker


def run_installer_visibly(installer_path: Path) -> None:
    """Launch the installer via Windows' default handler (os.startfile).

    Used as a fallback when the silent batch approach fails. Opens the
    installer the same way double-clicking it does — handles UAC, file
    associations, and anything else the OS needs to do.

    The caller must exit immediately after this returns so the installer
    can replace the running .exe.
    """
    from src.single_instance import release as release_mutex
    release_mutex()

    try:
        os.startfile(str(installer_path))  # type: ignore[attr-defined]
    except OSError as exc:
        logger.error("os.startfile failed: %s", exc)
        raise


def apply_update(installer_path: Path, expected_version: str = "") -> None:
    """Launch the installer and restart the application.

    The sequence is:
      1. Write a pending-update marker so the next launch can verify the
         installer actually took effect (self-healing).
      2. Release the single-instance mutex so Inno Setup's AppMutex check
         doesn't silently abort the installer (/VERYSILENT + AppMutex = exit 11).
      3. Write a helper batch script that:
         a. Waits for this process to exit.
         b. Runs the installer with /VERYSILENT.
         c. Relaunches the app.
      4. Launch the batch script in a hidden console window.
      5. Immediately terminate this process.

    If the silent install fails (file lock, permission, etc.), the marker
    written in step 1 will be detected on the next launch and the app will
    offer a visible-installer fallback.
    """
    from src.single_instance import release as release_mutex

    app_exe = str(Path(sys.executable).resolve())
    current_install_dir = str(Path(sys.executable).parent)
    my_pid = os.getpid()

    if expected_version:
        _write_pending_marker(expected_version, installer_path)

    release_mutex()

    log_file = Path(tempfile.gettempdir()) / "audiobookmaker_update.log"
    relaunch_bat = Path(tempfile.gettempdir()) / "audiobookmaker_relaunch.bat"

    # Write the batch script using binary mode to prevent any shell layer
    # (MSYS2/Git Bash) from mangling Windows-specific syntax like ">NUL".
    #
    # The script waits 3 seconds for the app to exit, then runs the Inno
    # Setup installer silently.  We use os._exit(0) below which terminates
    # the process in milliseconds, so a fixed delay is simpler and more
    # reliable than PID polling (which requires pipe commands that can fail
    # without a visible console).
    #
    # "waitfor" is used for the delay because "timeout" and "ping" both
    # fail to delay when cmd.exe runs without a visible console window
    # (CREATE_NO_WINDOW).  "waitfor /t 3 <signal>" waits up to 3 seconds
    # for a signal that never arrives, providing a reliable sleep.
    # Splash script: borderless WinForms window with the goat icon centered
    # on screen, auto-closes after 25 s (safety cap — usually the installer
    # + new-app launch is done in 10-15 s and the relaunched app's own
    # PyInstaller splash takes over seamlessly).
    splash_ps1 = Path(tempfile.gettempdir()) / "audiobookmaker_splash.ps1"
    icon_png = Path(current_install_dir) / "_internal" / "assets" / "icon.png"
    if not icon_png.is_file():
        # Fallback: try alongside the exe (legacy onefile layouts).
        icon_png = Path(current_install_dir) / "assets" / "icon.png"
    splash_ps1.write_text(
        'Add-Type -AssemblyName System.Windows.Forms, System.Drawing\n'
        '$form = New-Object System.Windows.Forms.Form\n'
        '$form.Text = "AudiobookMaker"\n'
        '$form.Width = 280\n'
        '$form.Height = 280\n'
        '$form.StartPosition = "CenterScreen"\n'
        '$form.FormBorderStyle = "None"\n'
        '$form.BackColor = [System.Drawing.Color]::White\n'
        '$form.TopMost = $true\n'
        '$form.ControlBox = $false\n'
        'try {\n'
        f'  $img = [System.Drawing.Image]::FromFile("{icon_png}")\n'
        '  $pic = New-Object System.Windows.Forms.PictureBox\n'
        '  $pic.Image = $img\n'
        '  $pic.SizeMode = "Zoom"\n'
        '  $pic.Dock = "Fill"\n'
        '  $form.Controls.Add($pic)\n'
        '} catch {}\n'
        '$timer = New-Object System.Windows.Forms.Timer\n'
        '$timer.Interval = 25000\n'
        '$timer.Add_Tick({ $form.Close() })\n'
        '$timer.Start()\n'
        '$form.ShowDialog() | Out-Null\n',
        encoding="utf-8",
    )

    # Guard the f-string substitutions below — any batch metacharacter in
    # one of these paths would silently corrupt the relaunch script.
    _assert_bat_safe_path(installer_path, "installer_path")
    _assert_bat_safe_path(Path(app_exe), "app_exe")
    _assert_bat_safe_path(Path(current_install_dir), "current_install_dir")
    _assert_bat_safe_path(log_file, "log_file")
    _assert_bat_safe_path(splash_ps1, "splash_ps1")

    lines = [
        "@echo off",
        f'set "INSTALLER={installer_path}"',
        f'set "APPEXE={app_exe}"',
        f'set "APPDIR={current_install_dir}"',
        f'set "LOG={log_file}"',
        f'set "SPLASH={splash_ps1}"',
        "",
        'echo [%date% %time%] Update script started >> "%LOG%"',
        # Bring up the splash immediately (fire-and-forget — has its own
        # 25 s self-destruct timer so it can never zombie-persist).
        'start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%SPLASH%"',
        "waitfor /t 3 AudiobookMakerDummy 2>NUL",
        'echo [%date% %time%] Running installer... >> "%LOG%"',
        '"%INSTALLER%" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES /DIR="%APPDIR%"',
        'echo [%date% %time%] Installer exit code: %ERRORLEVEL% >> "%LOG%"',
        'echo [%date% %time%] Launching app... >> "%LOG%"',
        'start "" "%APPEXE%"',
        'echo [%date% %time%] Done. >> "%LOG%"',
        'del "%~f0"',
    ]
    relaunch_bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))

    subprocess.Popen(
        ["cmd.exe", "/c", str(relaunch_bat)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Grant the next process (the relaunched app) the right to call
    # SetForegroundWindow. Without this Windows blocks the relaunched
    # exe from popping itself to the front because the user has
    # presumably clicked elsewhere during the ~10-15 s install. The
    # relaunched app calls SetForegroundWindow / lift / focus_force on
    # its main window during init when it detects a post-update launch.
    if sys.platform == "win32":
        try:
            import ctypes
            # ASFW_ANY = -1 — allow any process to take foreground next.
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except (OSError, AttributeError):
            pass  # best-effort; nothing breaks if this fails

    # Use os._exit() for immediate termination. sys.exit() raises SystemExit
    # which can be delayed by Tkinter cleanup, thread joining, etc.
    os._exit(0)
