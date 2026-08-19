"""Auto-update module for AudiobookMaker.

Checks GitHub Releases for new versions, downloads the installer,
and launches a silent update.
"""

import errno
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_VERSION = "3.23.0"
GITHUB_REPO = "MikkoNumminen/AudiobookMaker"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DIR = Path(tempfile.gettempdir()) / "audiobookmaker-update"
# Per-user dir (not system-wide temp): on a shared Windows machine another
# local user can write files into %TEMP% and could plant a fake marker that
# triggers a bogus "update failed" dialog. Keeping the marker under the
# user's home directory closes that tampering vector.
_USER_DIR = Path.home() / ".audiobookmaker"
PENDING_MARKER = _USER_DIR / "update_pending.json"
# The relaunch .bat writes the silent installer's exit code here (overwriting
# any previous run) so the next launch can tell a *failed* silent install from
# a successful one. Without this, an install that exits non-zero but still
# swapped the .exe would pass the version check and clear the marker, leaving
# the user on a silently-broken build with no recovery offered. Lives next to
# the marker under the per-user dir for the same anti-tampering reason.
UPDATE_RESULT = _USER_DIR / "update_result.json"
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


class IntegrityError(RuntimeError):
    """Raised when a downloaded installer fails its SHA-256 check.

    Existing callers catching ``RuntimeError`` keep working unchanged.
    Callers that want to distinguish integrity failures from generic
    download errors — most notably ``src.cli.update`` mapping this to
    the project's existential exit code 2 — can catch this subclass
    specifically instead of string-matching the error message.
    """


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
        logger.warning(
            "Sidecar asset %r has no download URL; treating as no SHA-256",
            asset.get("name"),
        )
        return None
    try:
        req = Request(url)
        req.add_header("User-Agent", f"AudiobookMaker/{current_version}")
        with urlopen(req, timeout=SIDECAR_TIMEOUT) as resp:
            raw = resp.read(512)
    except (URLError, OSError) as exc:
        logger.warning("Sidecar SHA-256 fetch failed (network): %s", exc)
        return None
    try:
        payload = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        logger.warning(
            "Sidecar SHA-256 payload was not ASCII; refusing to substitute "
            "garbage characters (%s). Treating as no sidecar.", exc
        )
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

# Characters that would break the PowerShell splash script if present in a
# substituted path. `"` closes a double-quoted string (letting a path
# smuggle in PowerShell code), `` ` `` is the PowerShell escape character,
# `$` starts variable interpolation, and CR/LF terminate statements. Same
# defense-in-depth model as _BAT_UNSAFE_CHARS above.
_PS_UNSAFE_CHARS = ('"', '`', '$', '\r', '\n')


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


def _assert_ps_safe_path(path: Path, label: str) -> None:
    """Raise ValueError if *path* contains characters that break a PowerShell script.

    The splash script built in :func:`apply_update` interpolates the icon
    path into a ``[System.Drawing.Image]::FromFile("...")`` call. An
    unescaped ``"`` or ``$`` would turn that literal into a code-execution
    sink. Same fail-loud posture as :func:`_assert_bat_safe_path`.
    """
    s = str(path)
    for ch in _PS_UNSAFE_CHARS:
        if ch in s:
            raise ValueError(
                f"{label} contains PowerShell-unsafe character {ch!r}: {s!r}. "
                "Refusing to build splash .ps1 — would be malformed."
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
    # A GitHub release body can be JSON null (-> None here) or, defensively,
    # any non-string. Guard so a malformed response yields "no SHA found"
    # rather than a TypeError that would escape check_for_update's
    # never-raises contract.
    if not isinstance(release_notes, str):
        return None
    # Pattern: "SHA-256: <hex>" or "sha256: <hex>"  (with optional backticks)
    match = re.search(r"(?i)sha-?256:\s*`?([0-9a-fA-F]{64})`?", release_notes)
    if match:
        return match.group(1).lower()
    return None


# The pipeline closes the news with this sentinel, so the boundary between
# what the user reads and the machine tail is stated rather than guessed.
# build-release.yml emits it; keep the two in step.
WHATS_NEW_END_MARKER = "<!-- /whats-new -->"
_WHATS_NEW_END_RE = re.compile(r"^<!--\s*/whats-new\s*-->$")

# Fallback for bodies published before the sentinel existed. These match the
# pipeline's OWN technical headings exactly rather than any heading starting
# with the same word. The earlier prefix form (`(?:installation|cli)\b`) also
# matched news headings: "### CLI gets a resume flag" is a plausible section
# for a project that ships a CLI, and it silently truncated the banner there,
# dropping every section after it.
#
# Exact matching is safe for legacy bodies because they are fixed content and
# were checked: v3.18.0 through v3.23.0 all use exactly these two headings and
# a `---` rule before the hash block.
# The pipeline's own tail headings, matched exactly. These end the news
# whatever else the body contains: a sentinel that was moved or lost must not
# turn install steps and hashes into banner text.
_TAIL_HEADING_RE = re.compile(
    r"(?i)^(?:"
    r"#{1,6}\s*installation\s*$"
    r"|#{1,6}\s*cli\s*\(command-line interface\)\s*$"
    r")"
)

# Weaker markers, trusted only for bodies with no sentinel. A `---` rule and a
# line opening with a hash are both legitimate inside release notes, so they
# may only end the news when nothing better says where it stops.
_LEGACY_STOP_RE = re.compile(r"(?i)^(?:(?:cli:\s*)?sha-?256:|-{3,}\s*$)")
_WHATS_NEW_HEADING_RE = re.compile(r"(?i)^#{1,6}\s*what['’]?s new\s*:?\s*$")
_RELEASE_TITLE_RE = re.compile(r"(?i)^##\s+audiobookmaker\b")


def extract_whats_new(release_notes: str) -> str:
    """Pull the human-readable "What's new" section out of a release body.

    The release pipeline writes a structured body (see build-release.yml): a
    ``## AudiobookMaker <ver>`` title, the news prose, then ``### Installation``,
    ``### CLI``, and machine-only ``SHA-256:`` lines. The update banner wants
    only the news, so return everything between the title and that tail.

    Capture must NOT stop at the first heading it meets. The news prose carries
    its own ``###`` sub-headings, so stopping at any heading truncated the
    banner to nothing the moment the notes grew sections.

    Where the news ends is taken from the ``<!-- /whats-new -->`` sentinel the
    pipeline emits, not inferred from wording. Bodies published before the
    sentinel fall back to matching the pipeline's exact technical headings.

    Older bodies put the news under a literal ``### What's new`` heading. That
    still works and takes precedence over the title as the starting point.
    Returns "" when neither marker is present (hand-written releases) so the
    caller can hide the expander rather than show a wall of markdown.
    """
    if not isinstance(release_notes, str) or not release_notes.strip():
        return ""
    lines = release_notes.splitlines()

    # Prefer an explicit "What's new" heading; fall back to the release title.
    start: int | None = None
    for i, line in enumerate(lines):
        if _WHATS_NEW_HEADING_RE.match(line.strip()):
            start = i + 1
            break
    if start is None:
        for i, line in enumerate(lines):
            if _RELEASE_TITLE_RE.match(line.strip()):
                start = i + 1
                break
    if start is None:
        return ""

    region = lines[start:]
    # When the body states where the news ends, that is the ONLY terminator.
    # Running the heuristics alongside it re-introduced the bug they exist to
    # avoid: `---` is ordinary markdown, and a horizontal rule or a setext
    # underline inside the notes cut the banner off mid-way. Presence is
    # tested within the captured region, so a stray sentinel above the start
    # marker cannot disable the fallback for a body that needs it.
    has_sentinel = any(_WHATS_NEW_END_RE.match(ln.strip()) for ln in region)

    out: list[str] = []
    for line in region:
        stripped = line.strip()
        if _WHATS_NEW_END_RE.match(stripped) or _TAIL_HEADING_RE.match(stripped):
            break
        if not has_sentinel and _LEGACY_STOP_RE.match(stripped):
            break
        out.append(line)
    # Drop leading/trailing blank lines so the banner text is tight.
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version() -> str:
    """Return the current application version."""
    return APP_VERSION


# Tiny file the PyInstaller spec writes into the bundle root, carrying the
# APP_VERSION this bundle was built from. See ``detect_inconsistent_install``.
BUILD_STAMP_NAME = "build_stamp.txt"


def installed_build_version() -> str | None:
    """Return the version recorded in the bundled build stamp, or None.

    Only meaningful in a frozen build; the spec writes ``build_stamp.txt``
    into ``_internal/`` (``sys._MEIPASS`` at runtime). Returns None in dev
    mode or when the stamp is missing/unreadable.
    """
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    try:
        text = (Path(meipass) / BUILD_STAMP_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    return text.strip() or None


def detect_inconsistent_install() -> str | None:
    """Return the stale bundled version if the install is internally inconsistent.

    A clean install has the bundle's build stamp equal to the ``APP_VERSION``
    compiled into the ``.exe``. After a *partial* update — the ``.exe`` was
    replaced but a bundled data file was skipped (a silent Inno install can
    leave a locked file at its old version and still report success) — the
    stamp and ``APP_VERSION`` disagree, meaning some bundled files are from a
    different build than the running binary.

    Returns the stamp's version string on mismatch so the GUI can tell the
    user to reinstall; returns None when consistent, in dev mode, or when the
    stamp is absent (so pre-stamp builds never false-alarm).
    """
    stamp = installed_build_version()
    if stamp is None:
        return None
    if stamp != APP_VERSION:
        return stamp
    return None


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
            release_data = json.loads(resp.read().decode("utf-8"))

        # The body is valid JSON but might not be the expected object — e.g.
        # GitHub's `{"message": "Not Found"}` error payload is a dict (handled
        # below by empty fields), but a rate-limit/list response could be a
        # non-dict. Bail cleanly rather than AttributeError on `.get`.
        if not isinstance(release_data, dict):
            logger.warning(
                "GitHub release response was not a JSON object (got %s)",
                type(release_data).__name__,
            )
            return _no_update(current_version)

        # `or ""` (not `.get(k, "")`): a present-but-null field returns None
        # from .get, and None.lstrip(...) would raise.
        tag: str = release_data.get("tag_name") or ""
        latest_version = tag.lstrip("vV").strip()
        if not latest_version:
            logger.warning("GitHub release has no tag_name")
            return _no_update(current_version)

        # Never offer a prerelease as a stable update. ``_parse_version``
        # drops the ``-beta`` / ``-rc1`` suffix, so a prerelease with a higher
        # base number (``3.21.0-rc1``) would otherwise be served to everyone on
        # the stable channel. Trust GitHub's ``prerelease`` flag first, and
        # treat a SemVer pre-release suffix (``-``) in the tag as a backstop.
        if release_data.get("prerelease") or "-" in latest_version:
            logger.debug("Skipping prerelease %r", latest_version)
            return _no_update(current_version)

        if _parse_version(latest_version) <= _parse_version(current_version):
            return _no_update(current_version)

        asset = _find_exe_asset(release_data.get("assets", []))
        if asset is None:
            logger.warning("No .exe asset found in latest release")
            return _no_update(current_version)

        sha256 = _extract_sha256(release_data.get("body") or "")
        # Fallback: the release author may have published the sidecar
        # `.exe.sha256` asset without (or before) editing the body. Both
        # paths are equally trustworthy because the release author
        # authenticates either edit.
        if not sha256:
            sidecar = _find_sha256_sidecar_asset(
                release_data.get("assets", []), asset.get("name", "")
            )
            if sidecar is not None:
                sha256 = _fetch_sidecar_sha256(sidecar, current_version)
                if sha256:
                    logger.info(
                        "Recovered SHA-256 from sidecar asset (release notes lacked one)"
                    )

        download_url = asset.get("browser_download_url")
        if not download_url:
            logger.warning(
                "Release asset %r has no browser_download_url; "
                "treating as no update",
                asset.get("name", "<unnamed>"),
            )
            return _no_update(current_version)

        return UpdateInfo(
            available=True,
            current_version=current_version,
            latest_version=latest_version,
            download_url=download_url,
            release_notes=release_data.get("body") or "",
            asset_size_bytes=asset.get("size") or 0,
            sha256=sha256 or "",
        )

    except HTTPError as exc:
        # HTTPError is a URLError subclass; catch it first so a 403 rate-limit
        # / 404 / 5xx is logged with its status code instead of vanishing into
        # the generic branch. Still degrades to "no update" (never raises).
        logger.warning("Update check HTTP %s: %s", exc.code, exc)
        return _no_update(current_version)
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        logger.debug("Update check failed: %s", exc)
        return _no_update(current_version)
    except Exception as exc:  # noqa: BLE001
        # Defensive net: check_for_update documents "never raises", and a
        # broken update check must never crash the app or its background
        # thread. Anything unforeseen (a surprise response shape, an
        # attribute/type error) degrades to "no update" with a loud log.
        logger.warning("Update check hit an unexpected error: %s", exc, exc_info=True)
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

    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create the update folder {UPDATE_DIR} "
            f"(check permissions / disk space): {exc}"
        ) from exc

    filename = f"AudiobookMaker-Setup-{update.latest_version}.exe"
    dest = UPDATE_DIR / filename
    # Download into a .part file and only rename it to the real .exe AFTER the
    # SHA-256 verifies, so a force-kill / power-loss mid-download can't leave a
    # truncated file that LOOKS like a complete installer (prune + retry paths
    # key off the .exe name).
    dest_tmp = UPDATE_DIR / (filename + ".part")
    # Clear any earlier downloads before fetching this one so the update dir
    # never accumulates stale installers or orphaned .part files; spare *dest*
    # in case a prior partial of the same version is being re-fetched.
    prune_old_installers(keep=dest)

    req = Request(update.download_url)
    req.add_header("User-Agent", f"AudiobookMaker/{update.current_version}")

    # Any exception in the body (network error, cancel, disk-full, even a
    # KeyboardInterrupt or BaseException subclass from a thread cancel) must
    # leave no .part behind. The outer try/except/BaseException ensures cleanup
    # happens before the exception propagates; the inner branches normalise
    # common I/O failures to a clear RuntimeError for the caller.
    try:
        try:
            with urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    # urlopen already raises HTTPError for 4xx/5xx; this guards
                    # an unexpected non-200 (e.g. a CDN error page served 2xx)
                    # so a non-installer body never reaches the hash step.
                    raise RuntimeError(
                        f"Server returned HTTP {status} for the installer download"
                    )
                total = update.asset_size_bytes or int(
                    resp.headers.get("Content-Length", 0)
                )
                done = 0

                with open(dest_tmp, "wb") as fp:
                    while True:
                        if cancel_event and cancel_event.is_set():
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
        except HTTPError as exc:
            # The asset URL returned an error status (404 if the asset was
            # removed, 5xx on a CDN hiccup). Surface the code/reason. Caught
            # before OSError because HTTPError is an OSError subclass and would
            # otherwise hit the disk-full branch with a meaningless errno.
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
        except OSError as exc:
            # Disk-full deserves a specific, actionable message.
            if exc.errno == errno.ENOSPC:
                raise RuntimeError(
                    f"Not enough disk space to download the update in {UPDATE_DIR}."
                ) from exc
            raise RuntimeError(f"Download failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Download failed: {exc}") from exc
    except BaseException:
        # Includes RuntimeError, KeyboardInterrupt, SystemExit, and any
        # thread-cancel exception. Delete the partial .part before re-raising
        # so we never leave a truncated download behind.
        dest_tmp.unlink(missing_ok=True)
        raise

    # Verify integrity — SHA-256 is mandatory (checked at function entry).
    try:
        file_hash = hashlib.sha256(dest_tmp.read_bytes()).hexdigest()
    except OSError as exc:
        # The file vanished or became unreadable between write and hash —
        # most often antivirus quarantining a freshly-written .exe.
        dest_tmp.unlink(missing_ok=True)
        raise RuntimeError(
            "Cannot read the downloaded installer to verify it "
            f"(antivirus may have quarantined it): {exc}"
        ) from exc
    if file_hash != update.sha256:
        dest_tmp.unlink(missing_ok=True)
        raise IntegrityError(
            f"Integrity check failed: expected SHA-256 {update.sha256[:16]}…, "
            f"got {file_hash[:16]}…. Download may be corrupted."
        )
    logger.info("SHA-256 verified: %s", file_hash[:16])

    # Promote the verified .part to the real installer name (atomic on the
    # same filesystem). Only now does a complete, verified .exe exist.
    dest_tmp.replace(dest)
    return dest


def prune_old_installers(keep: Path | None = None) -> int:
    """Delete stale downloaded installers from :data:`UPDATE_DIR`.

    Every auto-update downloads a full ~170 MB ``AudiobookMaker-Setup-*.exe``
    into ``UPDATE_DIR`` and never needs it again once the silent install has
    run — but :func:`apply_update` ``os._exit``'s the process before it could
    clean up, so the installers pile up (field-observed: three stale files
    ≈ 520 MB). Call this on a clean launch and before each fresh download.
    *keep*, when given, spares that one file (the download in flight). Returns
    the number removed. Never raises — a cleanup failure must not break launch
    or the update flow.
    """
    try:
        if not UPDATE_DIR.exists():
            return 0
        keep_resolved = keep.resolve() if keep is not None else None
        removed = 0
        # Verified installers (*.exe) plus any orphaned in-progress downloads
        # (*.exe.part) left by a force-killed run — a verified file is always a
        # plain .exe, so .part files are never worth keeping.
        stale = list(UPDATE_DIR.glob("AudiobookMaker-Setup-*.exe")) + list(
            UPDATE_DIR.glob("AudiobookMaker-Setup-*.exe.part")
        )
        for exe in stale:
            try:
                if keep_resolved is not None and exe.resolve() == keep_resolved:
                    continue
                exe.unlink()
                removed += 1
            except OSError as exc:
                logger.debug("Could not remove stale installer %s: %s", exe, exc)
        if removed:
            logger.info("Pruned %d stale downloaded installer(s)", removed)
        return removed
    except OSError as exc:
        logger.debug("Installer prune skipped: %s", exc)
        return 0


def _write_pending_marker(expected_version: str, installer_path: Path) -> None:
    """Record that an update is in flight so the next launch can verify it."""
    import time
    # A new update attempt starts now — drop any exit-code result left over
    # from a previous attempt so verify_pending_update can't misread it as
    # this attempt's outcome.
    clear_update_result()
    try:
        PENDING_MARKER.parent.mkdir(parents=True, exist_ok=True)
        PENDING_MARKER.write_text(json.dumps({
            "expected_version": expected_version,
            "installer_path": str(installer_path),
            "started_at": time.time(),
        }), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not write pending marker: %s", exc)


def read_update_result() -> dict | None:
    """Return the silent installer's result dict, or None if absent/unreadable.

    Written by the relaunch .bat in :func:`apply_update` as
    ``{"exit_code": <int>}``. A missing or malformed file degrades to None so
    callers fall back to the version-only check (the pre-result behaviour).
    """
    if not UPDATE_RESULT.exists():
        return None
    try:
        return json.loads(UPDATE_RESULT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_update_result() -> None:
    """Remove the installer-result file (best-effort)."""
    try:
        UPDATE_RESULT.unlink(missing_ok=True)
    except OSError:
        pass


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
    """Remove the pending-update marker (after verifying success or giving up).

    Also drops the installer-result file so a later update can't read this
    attempt's exit code by mistake.
    """
    try:
        PENDING_MARKER.unlink(missing_ok=True)
    except OSError:
        pass
    clear_update_result()


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

    # A silent install that exited non-zero must be treated as failed even if
    # the .exe was swapped (version advanced): Inno can replace the binary and
    # still abort on a locked _internal file, leaving a half-updated build.
    # Keep the marker so the GUI offers the visible-installer fallback.
    result = read_update_result()
    installer_failed = bool(result) and result.get("exit_code", 0) != 0

    expected = marker.get("expected_version", "")
    version_ok = bool(expected) and _parse_version(current_version) >= _parse_version(expected)
    if version_ok and not installer_failed:
        # Update succeeded.
        clear_pending_marker()
        return None

    if installer_failed:
        logger.warning(
            "Silent install reported exit code %s; offering visible-installer "
            "recovery instead of treating the update as successful.",
            result.get("exit_code"),
        )

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
    app_exe_name = Path(app_exe).name  # e.g. "AudiobookMaker.exe"
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

    # Guard the PowerShell interpolation below — a `"`, `` ` ``, or `$` in
    # the icon path would let the path smuggle PowerShell code into the
    # splash script. Today icon_png is always under sys.executable's parent
    # (so this can't happen) but we fail loud the moment that assumption
    # slips.
    _assert_ps_safe_path(icon_png, "icon_png")

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
    _assert_bat_safe_path(UPDATE_RESULT, "update_result")

    # Ensure the per-user dir exists so the result-file redirect below can't
    # silently fail (it normally exists from _write_pending_marker, but be
    # defensive — a missing result file just degrades to the version check).
    try:
        UPDATE_RESULT.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("Could not create update-result dir: %s", exc)

    lines = [
        "@echo off",
        f'set "INSTALLER={installer_path}"',
        f'set "APPEXE={app_exe}"',
        f'set "APPDIR={current_install_dir}"',
        f'set "LOG={log_file}"',
        f'set "SPLASH={splash_ps1}"',
        f'set "RESULT={UPDATE_RESULT}"',
        "",
        'echo [%date% %time%] Update script started >> "%LOG%"',
        # Bring up the splash immediately (fire-and-forget — has its own
        # 25 s self-destruct timer so it can never zombie-persist).
        'start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%SPLASH%"',
        "waitfor /t 3 AudiobookMakerDummy 2>NUL",
        # Close any app instance still holding the install files open before we
        # overwrite them. This process already exited (os._exit below), but a
        # SECOND window — or an old app that a prior failed update relaunched —
        # would otherwise lock the files and abort the silent install with Inno
        # exit code 5, which then relaunches the old app and snowballs into a
        # multi-instance jam (observed in the field). Killing our own image by
        # name is safe here: the only thing that starts the app again is the
        # relaunch below, which runs after the install completes.
        f'taskkill /F /IM "{app_exe_name}" >NUL 2>&1',
        "waitfor /t 2 AudiobookMakerSettle 2>NUL",
        'echo [%date% %time%] Running installer... >> "%LOG%"',
        '"%INSTALLER%" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES /DIR="%APPDIR%"',
        # Capture the installer's exit code immediately (the next command would
        # overwrite %ERRORLEVEL%) and record it where the relaunched app reads
        # it. A non-zero code means the silent install failed — even if the
        # .exe was swapped — so the app must offer the visible-installer
        # fallback instead of pretending the update succeeded.
        'set "EXITCODE=%ERRORLEVEL%"',
        'echo [%date% %time%] Installer exit code: %EXITCODE% >> "%LOG%"',
        '>"%RESULT%" echo {"exit_code": %EXITCODE%}',
        'if not exist "%APPEXE%" echo [%date% %time%] ERROR: app exe missing after install >> "%LOG%"',
        'echo [%date% %time%] Launching app... >> "%LOG%"',
        'start "" "%APPEXE%"',
        'echo [%date% %time%] Done. >> "%LOG%"',
        'del "%~f0"',
    ]
    relaunch_bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))

    # If Popen raises (e.g. cmd.exe missing, OSError, permission denied),
    # the splash .ps1 and relaunch .bat we just wrote would leak into
    # %TEMP% forever. Clean both up before re-raising — on success they
    # self-delete via `del "%~f0"` in the .bat and the 25 s timer in the
    # .ps1.
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(relaunch_bat)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except BaseException:
        splash_ps1.unlink(missing_ok=True)
        relaunch_bat.unlink(missing_ok=True)
        raise

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
