"""
ocr_path.py
-----------
Resolves Tesseract and Ghostscript paths for the OCR-fallback flow in
pdf_parser.py.

Frozen .exe builds bundle tesseract.exe, eng.traineddata, fin.traineddata,
and gswin64c.exe next to ffmpeg.exe (see audiobookmaker.spec). Dev mode
expects them on PATH (user-installed). When unavailable, ``is_ocr_available()``
returns False and the OCR fallback degrades gracefully — scanned PDFs still
fail, but with the existing EmptyPDFError ("may be scanned — try OCR first")
rather than a hard crash.

Call ``setup_ocr_path()`` early in main.py alongside ``setup_ffmpeg_path()``
so subprocesses launched by ocrmypdf find the bundled binaries.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _candidate_dirs() -> list[str]:
    """Search order for bundled Tesseract / Ghostscript binaries.

    Mirrors ``ffmpeg_path._candidate_dirs`` shape: PyInstaller bundle root,
    exe sibling, dev tree, parent-of-repo (Chatterbox subprocess case).
    """
    dirs: list[str] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        dirs.append(sys._MEIPASS)  # type: ignore[attr-defined]
    dirs.append(str(Path(sys.executable).parent))
    repo_root = Path(__file__).resolve().parent.parent
    dirs.append(str(repo_root / "dist" / "ocr"))
    dirs.append(str(repo_root.parent))
    return dirs


def get_tesseract_exe() -> str | None:
    """Return absolute path to tesseract.exe / tesseract, or None."""
    name = "tesseract.exe" if sys.platform == "win32" else "tesseract"
    for d in _candidate_dirs():
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("tesseract")


def get_ghostscript_exe() -> str | None:
    """Return absolute path to gswin64c.exe / gs, or None."""
    if sys.platform == "win32":
        names = ("gswin64c.exe", "gswin32c.exe")
    else:
        names = ("gs",)
    for d in _candidate_dirs():
        for name in names:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate):
                return candidate
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def get_tessdata_dir() -> str | None:
    """Return absolute path to the directory holding *.traineddata files.

    Bundled layout puts them next to tesseract.exe; system installs vary
    but Tesseract honors TESSDATA_PREFIX when set, so locating the dir is
    enough — the caller exports it as an env var.
    """
    name = "tesseract.exe" if sys.platform == "win32" else "tesseract"
    for d in _candidate_dirs():
        if os.path.isfile(os.path.join(d, name)):
            # tessdata/ subdir or flat alongside the exe.
            for sub in ("tessdata", "."):
                cand = Path(d) / sub
                if (cand / "eng.traineddata").is_file():
                    return str(cand)
    return None


# Map AudiobookMaker's TTS language codes ("fi", "en") to Tesseract's
# three-letter codes ("fin", "eng"). Keep in sync with the language packs
# bundled by audiobookmaker.spec. Unknown / empty / other languages fall
# back to English — Tesseract is more forgiving of an English-on-Finnish
# OCR pass than the other way around (English letterforms are a superset
# of the diacriticless Latin alphabet).
_TTS_TO_TESSERACT = {
    "fi": "fin",
    "en": "eng",
}


def tesseract_lang_for(tts_lang: str) -> str:
    """Return the Tesseract --language code matching a TTS book-language."""
    return _TTS_TO_TESSERACT.get(tts_lang, "eng")


def is_ocr_available() -> bool:
    """True only when every piece OCR needs is reachable.

    Required: ocrmypdf module + tesseract binary + ghostscript binary.
    Trained-data files are nice to verify, but Tesseract's own search
    paths handle them when TESSDATA_PREFIX is set or the install is
    standard, so we don't gate on them here.
    """
    try:
        import ocrmypdf  # noqa: F401
    except ImportError:
        return False
    return get_tesseract_exe() is not None and get_ghostscript_exe() is not None


def setup_ocr_path() -> None:
    """Prepend bundled OCR binaries to PATH and export TESSDATA_PREFIX.

    Idempotent. Safe to call when nothing is bundled — silently no-ops.
    """
    tesseract = get_tesseract_exe()
    if tesseract is None:
        return

    bin_dir = str(Path(tesseract).parent)
    current_path = os.environ.get("PATH", "")
    if bin_dir not in current_path:
        os.environ["PATH"] = bin_dir + os.pathsep + current_path

    gs = get_ghostscript_exe()
    if gs is not None:
        gs_dir = str(Path(gs).parent)
        if gs_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = gs_dir + os.pathsep + os.environ.get("PATH", "")

    tessdata = get_tessdata_dir()
    if tessdata is not None and not os.environ.get("TESSDATA_PREFIX"):
        os.environ["TESSDATA_PREFIX"] = tessdata
