"""Unified AudiobookMaker GUI — single window replacing gui.py and launcher.py.

Combines the simple launcher's one-click workflow with the advanced settings
panel from the original gui.py. All engines are dispatched through the same
queue-based event loop: Chatterbox runs as a subprocess via launcher_bridge,
while Edge-TTS / Piper / VoxCPM2 run in-process on a background thread.

Entry point::

    python -m src.gui_unified
    # or: from src.gui_unified import run; run()

Supports Finnish and English UI languages.
Uses CustomTkinter for a modern look with dark/light mode support.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Optional

import customtkinter as ctk

from src import app_config
from src.auto_updater import (
    check_for_update, download_update, apply_update,
    APP_VERSION, GITHUB_REPO, UpdateInfo,
    is_post_update_launch,
)
from src import gui_style
from src.gui_synth_mixin import SynthMixin
from src.gui_update_mixin import UpdateMixin
from src.ffmpeg_path import get_ffmpeg_dir, setup_ffmpeg_path
from src.launcher_bridge import ChatterboxRunner, ProgressEvent, resolve_chatterbox_python
from src.pdf_parser import parse_pdf, BookMetadata, Chapter, ParsedBook
from src.epub_parser import parse_epub
try:
    from src import duration_estimate as _duration_estimate
except Exception:  # pragma: no cover — module might be stubbed in parallel dev
    _duration_estimate = None  # type: ignore
from src.tts_base import EngineStatus, TTSEngine, Voice, get_engine, list_engines
from src.tts_engine import TTSConfig, chapters_to_speech

# Import engine modules for their register_engine() side effects.
from src import tts_edge  # noqa: F401
from src import tts_piper  # noqa: F401

# VoxCPM2 is developer-only — don't show it in the installed exe.
if not getattr(sys, "frozen", False):
    try:
        from src import tts_voxcpm  # noqa: F401
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Repo root (needed for Chatterbox runner script resolution)
# ---------------------------------------------------------------------------

# In dev mode, this is the repo root. In a PyInstaller bundle, this is
# the _MEIPASS temp directory (or the app directory for onedir builds).
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _APP_ROOT = Path(sys._MEIPASS)
else:
    _APP_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _APP_ROOT

# ---------------------------------------------------------------------------
# CustomTkinter appearance
# ---------------------------------------------------------------------------

# Apply the custom "Cold Forge" palette (cool slate + electric blue).
# Dark is the default; light mode still renders cleanly via the (light, dark)
# pairs baked into every token.
gui_style.apply_theme("dark")

# Lines that represent a successful chunk/chapter progress step. These are
# routed to the green "success" log tag so every gain is visible at a glance.
# Examples that should match:
#   [chapter 1/1] chunk 2/3 (2/3 total) - 0m13s elapsed, ...
#   [chapter 1/1] idx=0 title='Text' chunks=3
_PROGRESS_SUCCESS_RE = re.compile(
    r"\[chapter\s+\d+/\d+\]\s+(?:chunk\s+\d+/\d+|idx=\d+)"
)

def _detect_system_language() -> str:
    """Return 'fi' if the system locale is Finnish, 'en' otherwise."""
    import locale
    try:
        lang = locale.getdefaultlocale()[0] or ""
        if lang.lower().startswith("fi"):
            return "fi"
    except Exception:
        pass
    return "en"


# ---------------------------------------------------------------------------
# UI constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = f"AudiobookMaker v{APP_VERSION}"
WINDOW_MIN_W = 780
WINDOW_MIN_H = 860

LANGUAGES = {
    "Suomi": "fi",
    "English": "en",
}

SPEED_OPTIONS = {
    "fi": {
        "Hidas (-25%)": "-25%",
        "Normaali": "+0%",
        "Nopea (+25%)": "+25%",
        "Erittäin nopea (+50%)": "+50%",
    },
    "en": {
        "Slow (-25%)": "-25%",
        "Normal": "+0%",
        "Fast (+25%)": "+25%",
        "Very fast (+50%)": "+50%",
    },
}

OUTPUT_MODES = {
    "fi": {
        "Yksi MP3": "single",
        "Yksi MP3 per luku": "chapters",
    },
    "en": {
        "Single MP3": "single",
        "One MP3 per chapter": "chapters",
    },
}

# Short sample sentences for voice preview.
_SAMPLE_TEXT = {
    "fi": "Tämä on ääninäyte valitulla äänellä.",
    "en": "This is a voice sample with the selected voice.",
}


# Display-name tags per language for the single Chatterbox "Grandmom" voice.
# Same underlying voice, two code paths inside the subprocess:
#   fi -> T3 Finnish finetune
#   en -> multilingual base + voice-clone reference audio
# See scripts/generate_chatterbox_audiobook.py for the routing.
_CHATTERBOX_LANG_TAGS = {
    "fi": "suomi",
    "en": "English",
}


def _chatterbox_voices_for_language(lang: str) -> list[str]:
    """Return the Chatterbox voice entries to show for ``lang``.

    The engine only has one voice — "Grandmom" — but it works in both
    Finnish and English, so we surface it once per language with a
    parenthetical tag matching the format used by Edge/Piper voices.
    An unknown language returns an empty list so the voice combobox
    clears instead of offering a voice that can't speak it.
    """
    tag = _CHATTERBOX_LANG_TAGS.get(lang)
    if not tag:
        return []
    return [f"Grandmom ({tag})"]

# Engine status colours — kept as named module-level aliases for call-site
# readability. Source of truth is ``gui_style.STATUS_DOT`` so the palette
# lives in one place. Each value is a (light, dark) tuple that CTkLabel
# accepts directly for its ``text_color`` parameter.
_CLR_READY = gui_style.STATUS_DOT["ready"]
_CLR_NEEDS_SETUP = gui_style.STATUS_DOT["needs_setup"]
_CLR_UNAVAILABLE = gui_style.STATUS_DOT["unavailable"]

# ---------------------------------------------------------------------------
# Translatable UI strings
# ---------------------------------------------------------------------------

_STRINGS = {
    "fi": {
        "window_title": "AudiobookMaker",
        "tab_pdf": "Kirja",
        "tab_text": "Teksti",
        "text_placeholder": "Kirjoita tai liitä teksti tähän...",
        "settings_frame": "Asetukset",
        "engine_label": "Moottori:",
        "install_engines": "Asenna moottoreita\u2026",
        "status_ready": "Valmis",
        "language_label": "Kieli:",
        "speed_label": "Nopeus:",
        "voice_label": "Ääni:",
        "test_voice": "Testaa ääni",
        "ref_audio_label": "Ref. ääni:",
        "browse": "Selaa\u2026",
        "clear": "Tyhjennä",
        "voice_desc_label": "Äänityyli:",
        "output_mode_label": "Tuloste:",
        "output_single": "Yksi MP3",
        "output_chapters": "Yksi MP3 per luku",
        "save_label": "Tallenna:",
        "status_label": "Tila:",
        "status_ready_msg": "Valitse PDF tai kirjoita teksti aloittaaksesi.",
        "convert": "Muunna",
        "make_sample": "Tee n\u00e4yte",
        "making_sample": "Tehd\u00e4\u00e4n n\u00e4ytett\u00e4\u2026",
        "sample_run_saved": "N\u00e4yte tallennettu: {path}",
        "cancel": "Peruuta",
        "open_folder": "Avaa kansio",
        "show_log": "Näytä loki",
        "hide_log": "Piilota loki",
        "ui_language": "Käyttöliittymä:",
        "converting": "Muunnetaan...",
        "cancelling": "Peruuta\u2026",
        "done": "Valmis!",
        "error": "Virhe",
        "no_pdf": "Valitse ensin kirjatiedosto (PDF, EPUB tai TXT).",
        "no_text": "Kirjoita tai liitä ensin teksti.",
        "select_pdf": "Valitse kirjatiedosto",
        "save_as": "Tallenna nimellä",
        "speed_slow": "Hidas",
        "speed_normal": "Normaali",
        "speed_fast": "Nopea",
        "speed_very_fast": "Erittäin nopea",
        "engine_manager_title": "Moottoreiden hallinta",
        "no_file_selected": "Ei tiedostoa valittu",
        "no_output_selected": "Ei valittu",
        "select_input_prompt": "Valitse syöte ja paina Muunna.",
        "update_available": "Versio {version} saatavilla.",
        "update_now": "Päivitä nyt",
        "update_download_manually": "Lataa selaimella",
        "update_downloading": "Ladataan päivitystä...",
        "update_installing": "Asennetaan päivitys...",
        "update_failed": "Päivitys epäonnistui.",
        "update_error_detail": "Päivitys epäonnistui: {error}",
        "listen": "Esikuuntele",
        "listening": "Toistetaan...",
        "listen_no_text": "Kirjoita ensin teksti Teksti-välilehdelle.",
        "pdf_no_text": "PDF ei sisällä tekstiä (tiedosto voi olla skannattu). Kokeile ensin OCR-muunnosta.",
        "voice_sample_title": "Ääninäyte",
        "chatterbox_no_sample": "Chatterbox ei tue ääninäytettä tästä käyttöliittymästä.",
        "select_engine_voice": "Valitse ensin moottori ja ääni.",
        "synthesizing_sample": "Syntetisoidaan ääninäytettä\u2026",
        "sample_failed": "Näyteen luonti epäonnistui: {error}",
        "sample_saved": "Ääninäyte tallennettu: {path}",
        "playback_failed": "\u2718 Toisto epäonnistui: {error}",
        "select_tts_engine": "Valitse TTS-moottori.",
        "engine_not_found": "Moottoria ei löytynyt.",
        "select_voice": "Valitse ääni.",
        "listen_convert_first": "Muunna teksti ensin \u2014 Esikuuntele toistaa sen jälkeen valmiin MP3-tiedoston.",
        "listen_error": "Listen error: {error}",
        "generic_error": "\u2718 Virhe: {error}",
        "disk_space_log": "\u2718 Levytilaa ei riitä: vapaa {free} MB, tarvitaan ~{need} MB",
        "disk_space_msg": "Levytilaa ei riitä tulostekansiossa.\n\nVapaa: {free} MB\nTarvitaan: ~{need} MB\n\nVapauta tilaa tai valitse toinen tallennuspaikka.",
        "pdf_selected": "PDF valittu.",
        "chatterbox_venv_missing": "Chatterbox-venviä ei löytynyt. Asenna se ensin suorittamalla scripts/setup_chatterbox_windows.bat.",
        "subprocess_failed": "Subprocess ei käynnistynyt: {error}",
        "engine_not_found_id": "Moottoria '{engine_id}' ei löytynyt.",
        "reading_input": "Luetaan syötettä\u2026",
        "no_text_to_synth": "Ei tekstiä syntetisoitavaksi.",
        "engine_no_voice_for_lang": "Moottorilla ei ole ääntä valitulle kielelle.",
        "user_cancelled_synth": "Käyttäjä peruutti synteesin.",
        "chapters_only_edge": "Lukukohtainen tulostus on tällä hetkellä tuettu vain Edge-TTS-moottorilla.",
        "cancelled": "Peruutettu.",
        "total_chunks": "Yhteensä {n} palaa synteesissä.",
        "cache_resume": "Jatketaan välimuistista: {done}/{total} palaa valmiina.",
        "chapter_chunk_status": "Luku {ci}/{ct}, pala {chi}/{cht}",
        "elapsed_eta": "Kulunut {elapsed} min \u2014 jäljellä noin {eta} min",
        "synth_in_progress": "Synteesi käynnissä\u2026",
        "error_exit_code": "\u2718 {error} (exit code {rc})",
        "status_ready_strip": "\U0001F4D6 {name} \u00B7 {chars}k merkki\u00E4 \u00B7 ~{audio_human} audiota \u00B7 synteesi ~{wall_human} ({engine_display})",
        "status_synthesizing_strip": "\U0001F4D6 {name} \u00B7 {pct}% \u00B7 {eta_human} j\u00E4ljell\u00E4 \u00B7 valmis klo {hhmm}{rtf_suffix}",
        "status_done_strip": "\u2714 {name} \u00B7 valmis {wall_human} \u00B7 {size_mb:.0f} MB MP3",
        "voice_count_label": "{n} {lang_name}-\u00e4\u00e4nt\u00e4",
        "lang_name_fi": "suomenkielist\u00e4",
        "lang_name_en": "englanninkielist\u00e4",
    },
    "en": {
        "window_title": "AudiobookMaker",
        "tab_pdf": "Book",
        "tab_text": "Text",
        "text_placeholder": "Type or paste text here...",
        "settings_frame": "Settings",
        "engine_label": "Engine:",
        "install_engines": "Install engines\u2026",
        "status_ready": "Ready",
        "language_label": "Language:",
        "speed_label": "Speed:",
        "voice_label": "Voice:",
        "test_voice": "Test voice",
        "ref_audio_label": "Ref. audio:",
        "browse": "Browse\u2026",
        "clear": "Clear",
        "voice_desc_label": "Voice style:",
        "output_mode_label": "Output:",
        "output_single": "Single MP3",
        "output_chapters": "One MP3 per chapter",
        "save_label": "Save to:",
        "status_label": "Status:",
        "status_ready_msg": "Select a PDF or enter text to begin.",
        "convert": "Convert",
        "make_sample": "Make sample",
        "making_sample": "Generating sample\u2026",
        "sample_run_saved": "Sample saved: {path}",
        "cancel": "Cancel",
        "open_folder": "Open folder",
        "show_log": "Show log",
        "hide_log": "Hide log",
        "ui_language": "Interface:",
        "converting": "Converting...",
        "cancelling": "Cancelling\u2026",
        "done": "Done!",
        "error": "Error",
        "no_pdf": "Please select a book file first (PDF, EPUB, or TXT).",
        "no_text": "Please enter or paste text first.",
        "select_pdf": "Select book file",
        "save_as": "Save as",
        "speed_slow": "Slow",
        "speed_normal": "Normal",
        "speed_fast": "Fast",
        "speed_very_fast": "Very fast",
        "engine_manager_title": "Engine Manager",
        "no_file_selected": "No file selected",
        "no_output_selected": "Not selected",
        "select_input_prompt": "Select input and press Convert.",
        "update_available": "Version {version} available.",
        "update_now": "Update now",
        "update_download_manually": "Open in browser",
        "update_downloading": "Downloading update...",
        "update_installing": "Installing update...",
        "update_failed": "Update failed.",
        "update_error_detail": "Update failed: {error}",
        "listen": "Preview",
        "listening": "Playing...",
        "listen_no_text": "Enter text in the Text tab first.",
        "pdf_no_text": "PDF contains no extractable text (it may be scanned). Try OCR first.",
        "voice_sample_title": "Voice sample",
        "chatterbox_no_sample": "Chatterbox does not support voice sampling from this interface.",
        "select_engine_voice": "Please select an engine and voice first.",
        "synthesizing_sample": "Synthesizing voice sample\u2026",
        "sample_failed": "Sample generation failed: {error}",
        "sample_saved": "Voice sample saved: {path}",
        "playback_failed": "\u2718 Playback failed: {error}",
        "select_tts_engine": "Please select a TTS engine.",
        "engine_not_found": "Engine not found.",
        "select_voice": "Please select a voice.",
        "listen_convert_first": "Convert the text first \u2014 Preview plays the resulting MP3 file afterwards.",
        "listen_error": "Listen error: {error}",
        "generic_error": "\u2718 Error: {error}",
        "disk_space_log": "\u2718 Not enough disk space: free {free} MB, need ~{need} MB",
        "disk_space_msg": "Not enough disk space at the output path.\n\nFree: {free} MB\nNeeded: ~{need} MB\n\nFree some space or pick a different save location.",
        "pdf_selected": "PDF selected.",
        "chatterbox_venv_missing": "Chatterbox venv not found. Install it first by running scripts/setup_chatterbox_windows.bat.",
        "subprocess_failed": "Subprocess failed to start: {error}",
        "engine_not_found_id": "Engine '{engine_id}' not found.",
        "reading_input": "Reading input\u2026",
        "no_text_to_synth": "No text to synthesize.",
        "engine_no_voice_for_lang": "Engine has no voice for the selected language.",
        "user_cancelled_synth": "User cancelled synthesis.",
        "chapters_only_edge": "Per-chapter output is currently only supported with the Edge-TTS engine.",
        "cancelled": "Cancelled.",
        "total_chunks": "Total {n} chunks to synthesize.",
        "cache_resume": "Resuming from cache: {done}/{total} chunks ready.",
        "chapter_chunk_status": "Chapter {ci}/{ct}, chunk {chi}/{cht}",
        "elapsed_eta": "Elapsed {elapsed} min \u2014 about {eta} min remaining",
        "synth_in_progress": "Synthesis in progress\u2026",
        "error_exit_code": "\u2718 {error} (exit code {rc})",
        "status_ready_strip": "\U0001F4D6 {name} \u00B7 {chars}k chars \u00B7 ~{audio_human} audio \u00B7 synthesis ~{wall_human} ({engine_display})",
        "status_synthesizing_strip": "\U0001F4D6 {name} \u00B7 {pct}% \u00B7 {eta_human} remaining \u00B7 done at {hhmm}{rtf_suffix}",
        "status_done_strip": "\u2714 {name} \u00B7 done in {wall_human} \u00B7 {size_mb:.0f} MB MP3",
        "voice_count_label": "{n} {lang_name} voices",
        "lang_name_fi": "Finnish",
        "lang_name_en": "English",
    },
}


from src.gui_engine_dialog import EngineManagerDialog, EngineManagerView  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Book input dispatcher
# ---------------------------------------------------------------------------


def parse_book(file_path: str) -> ParsedBook:
    """Route a book-shaped file to the right parser by extension.

    ``.pdf``  -> :func:`src.pdf_parser.parse_pdf`
    ``.epub`` -> :func:`src.epub_parser.parse_epub`
    ``.txt``  -> read UTF-8, wrap as a single-chapter ParsedBook

    Keeping the dispatcher in one place means every call site (conversion,
    preview, disk-space estimate) stays in sync when new formats are added.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return parse_pdf(file_path)
    if ext == ".epub":
        return parse_epub(file_path)
    if ext == ".txt":
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        meta = BookMetadata(
            title=Path(file_path).stem.replace("_", " ").title(),
            author="",
            subject="",
            num_pages=1,
            file_path=str(file_path),
        )
        chapter = Chapter(
            title=meta.title or "Text",
            content=text,
            page_start=1,
            page_end=1,
            index=0,
        )
        return ParsedBook(metadata=meta, chapters=[chapter])
    # Unknown extension — default to PDF so legacy call sites still raise
    # the familiar error message.
    return parse_pdf(file_path)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class UnifiedApp(SynthMixin, UpdateMixin, ctk.CTk):
    """Single-window AudiobookMaker GUI."""

    POLL_INTERVAL_MS = 100

    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self._center_window()

        # Set goat icon on the window title bar.
        self._set_window_icon()

        # ---- internal state ----
        self._pdf_path: Optional[str] = None
        self._output_path: Optional[str] = None
        self._synth_running = False
        self._cancel_requested = False
        self._cancel_flag = threading.Event()
        self._testing_voice = False
        self._listening = False
        self._listen_proc: Optional[subprocess.Popen] = None
        self._listen_temp_path: Optional[str] = None
        self._chatterbox_runner: Optional[ChatterboxRunner] = None
        self._chatterbox_last_mp3: str = ""
        # Sample-mode flags. Set by _on_sample_click, cleared by
        # _on_convert_click and _on_synth_exit so the success path can
        # show "Sample saved" instead of the generic "Done".
        self._is_sample_run: bool = False
        self._sample_output_path: Optional[str] = None
        # The most recently produced MP3 from this session — sample or full
        # run, whichever finished last. Esikuuntele/Preview prefers this
        # over self._output_path so the user can always re-listen to the
        # newest result without having to remember which button produced it.
        self._last_playable_path: Optional[str] = None
        self._event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self._log_visible = True
        self._pending_update: Optional[UpdateInfo] = None
        # True if the user explicitly chose the output path via Vaihda…
        # Auto-paths get bumped to the next free number before each run
        # so repeated Muunna presses never overwrite the previous MP3.
        self._output_user_chosen: bool = False

        # Load persisted preferences.
        self._user_cfg = app_config.load()
        self._ui_lang: str = self._user_cfg.ui_language or _detect_system_language()

        # Maps populated during engine list init.
        self._engine_display_to_id: dict[str, str] = {}

        # Build all widgets.
        self._build_ui()

        # Restore settings from config.
        self._apply_loaded_config()

        # Wire engine-bar combobox callbacks now that all programmatic
        # .set() calls are done — from here on, only user clicks trigger
        # the Kieli/Moottori cascade.
        self._wire_engine_bar_callbacks()

        # Apply UI language (updates all widget texts).
        self._apply_ui_language()

        # Check for updates in background (only in frozen/installed mode).
        self._update_queue: "queue.Queue[UpdateInfo]" = queue.Queue()
        if getattr(sys, "frozen", False):
            # If we just relaunched after a successful auto-update, pop
            # ourselves to the foreground so the user sees the new version
            # immediately. Must run BEFORE _check_pending_update_marker
            # because that call clears the marker on success.
            if is_post_update_launch(APP_VERSION):
                self.after(400, self._pop_to_foreground)

            # Self-heal: if the last in-app update didn't take effect,
            # offer a visible-installer fallback.
            self.after(800, self._check_pending_update_marker)

            threading.Thread(
                target=self._check_update_worker, daemon=True, name="update-check",
            ).start()
            self.after(500, self._poll_update_check)

            # Check for old installs / orphan shortcuts shortly after launch.
            # Only in frozen mode — dev mode shouldn't trigger cleanup.
            self.after(1500, self._check_for_old_installs)

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------

    def _center_window(self) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - WINDOW_MIN_W) // 2
        y = (sh - WINDOW_MIN_H) // 2
        self.geometry(f"{WINDOW_MIN_W}x{WINDOW_MIN_H}+{x}+{y}")

    def _set_window_icon(self) -> None:
        """Set the goat icon on the window title bar and taskbar."""
        # Set AppUserModelID so Windows shows our icon in the taskbar
        # instead of the default Python icon.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "AudiobookMaker.AudiobookMaker"
            )
        except Exception:
            pass

        try:
            icon_path = _APP_ROOT / "assets" / "icon.ico"
            if icon_path.exists():
                self.iconbitmap(str(icon_path))
                return
            # Fallback: try PNG via PhotoImage (works on some platforms).
            png_path = _APP_ROOT / "assets" / "icon.png"
            if png_path.exists():
                icon_img = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, icon_img)
        except Exception:
            pass  # Non-critical — default icon is fine as fallback.

    # ------------------------------------------------------------------
    # UI language helpers
    # ------------------------------------------------------------------

    def _s(self, key: str) -> str:
        """Return the UI string for *key* in the current UI language."""
        return _STRINGS.get(self._ui_lang, _STRINGS["fi"]).get(key, key)

    def _on_ui_language_changed(self, selection: str = "") -> None:
        """Handle the UI language combobox change."""
        sel = self._ui_lang_cb.get()
        self._ui_lang = "en" if sel == "English" else "fi"
        self._apply_ui_language()
        # Persist immediately.
        self._save_current_config()

    def _apply_ui_language(self) -> None:
        """Update ALL widget texts to match ``self._ui_lang``."""
        s = lambda key: self._s(key)  # noqa: E731 — local shorthand

        # Window title — suffix with the running version so the user can
        # tell at a glance which build they're on (and post-update verify).
        self.title(f"{s('window_title')} v{APP_VERSION}")

        # Input tabview tabs — CTkTabview can't rename tabs, so the internal
        # names are always the Finnish originals ("Kirja", "Teksti").
        # The map must use those original names, not translated ones.

        # PDF browse button.
        self._pdf_browse_btn.configure(text=s("browse"))

        # Text placeholder (only update if placeholder is currently shown).
        self._text_placeholder = s("text_placeholder")
        if self._text_has_placeholder:
            self._text_widget.delete("1.0", tk.END)
            self._text_widget.insert("1.0", self._text_placeholder)

        # PDF var — if no file selected, update the placeholder text.
        if not self._pdf_path:
            self._pdf_entry.configure(state="normal")
            self._pdf_entry.delete(0, tk.END)
            self._pdf_entry.insert(0, s("no_file_selected"))
            self._pdf_entry.configure(state="disabled")

        # Settings header button (collapsible label).
        if hasattr(self, "_settings_header_btn"):
            arrow = "\u25BE" if self._settings_open else "\u25B8"
            self._settings_header_btn.configure(
                text=f"{arrow} {s('settings_frame')}"
            )

        # Engine label + engine manager button (header bar).
        self._engine_label.configure(text=s("engine_label"))
        self._install_engines_btn.configure(text=s("install_engines"))

        # TTS language + speed labels.
        self._tts_lang_label.configure(text=s("language_label"))
        self._speed_label.configure(text=s("speed_label"))

        # Speed combobox: translate values while preserving the selected rate.
        old_speed_opts = SPEED_OPTIONS["fi" if self._ui_lang == "en" else "en"]
        new_speed_opts = SPEED_OPTIONS[self._ui_lang]
        current_rate = old_speed_opts.get(self._speed_cb.get())
        if current_rate is None:
            # Might already be in current language — look up directly.
            current_rate = new_speed_opts.get(self._speed_cb.get(), "+0%")
        self._speed_cb.configure(values=list(new_speed_opts.keys()))
        # Find the label for the current rate in the new language.
        new_label = next(
            (lbl for lbl, val in new_speed_opts.items() if val == current_rate),
            list(new_speed_opts.keys())[1],  # fallback to Normal
        )
        self._speed_cb.set(new_label)

        # Voice label + test button.
        self._voice_label.configure(text=s("voice_label"))
        self._test_btn.configure(text=s("test_voice"))

        # Reference audio widgets.
        self._ref_label.configure(text=s("ref_audio_label"))
        self._ref_browse_btn.configure(text=s("browse").rstrip("\u2026"))
        self._ref_clear_btn.configure(text=s("clear"))

        # Voice description label.
        self._desc_label.configure(text=s("voice_desc_label"))

        # Output mode: translate values while preserving the selected mode.
        old_out_opts = OUTPUT_MODES["fi" if self._ui_lang == "en" else "en"]
        new_out_opts = OUTPUT_MODES[self._ui_lang]
        current_mode = old_out_opts.get(self._output_mode_cb.get())
        if current_mode is None:
            current_mode = new_out_opts.get(self._output_mode_cb.get(), "single")
        self._output_mode_cb.configure(values=list(new_out_opts.keys()))
        new_mode_label = next(
            (lbl for lbl, val in new_out_opts.items() if val == current_mode),
            list(new_out_opts.keys())[0],
        )
        self._output_mode_cb.set(new_mode_label)
        self._output_mode_label.configure(text=s("output_mode_label"))

        # Save label + browse button.
        self._save_label.configure(text=s("save_label"))
        self._out_browse_btn.configure(text=s("browse"))

        # Output var — if no output selected, update placeholder.
        if not self._output_path:
            self._out_entry.configure(state="normal")
            self._out_entry.delete(0, tk.END)
            self._out_entry.insert(0, s("no_output_selected"))
            self._out_entry.configure(state="disabled")

        # Status line (only if not mid-conversion).
        if not self._synth_running:
            self._status_label_val.configure(text=s("select_input_prompt"))

        # Listen / convert / cancel / open folder buttons.
        if not self._listening:
            self._listen_btn.configure(text=s("listen"))
        self._convert_btn.configure(text=s("convert"))
        self._sample_btn.configure(text=s("make_sample"))
        if not self._cancel_requested:
            self._cancel_btn.configure(text=s("cancel"))
        self._open_folder_btn.configure(text=s("open_folder"))

        # Log toggle button.
        if self._log_visible:
            self._log_toggle_btn.configure(text=s("hide_log"))
        else:
            self._log_toggle_btn.configure(text=s("show_log"))

        # Update banner.
        if self._pending_update and self._pending_update.available:
            self._update_label.configure(
                text=s("update_available").format(
                    version=self._pending_update.latest_version
                )
            )
            self._update_btn.configure(text=s("update_now"))
            self._update_browser_btn.configure(text=s("update_download_manually"))

        # UI lang combobox — keep it in sync.
        self._ui_lang_cb.set("Suomi" if self._ui_lang == "fi" else "English")

        # Re-render the sticky status strip in the new language (if visible).
        state = getattr(self, "_status_strip_state", "idle")
        if state != "idle" and hasattr(self, "_status_strip_frame"):
            self._set_status_strip(state, **getattr(self, "_status_strip_fields", {}))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Root container holding two stacked views we can swap with tkraise().
        # Keeps the whole app in a single window — no Toplevel popups for
        # settings / engine manager.
        self._view_container = ctk.CTkFrame(self)
        self._view_container.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._view_container.columnconfigure(0, weight=1)
        self._view_container.rowconfigure(0, weight=1)

        # Main view — the normal "create audiobook" screen.
        main = ctk.CTkFrame(self._view_container, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew")
        self._main_view = main
        main.columnconfigure(0, weight=1)
        # Let log panel row stretch.
        main.rowconfigure(9, weight=1)

        # Track current input mode for tab renaming during language changes.
        self._input_mode_raw = "pdf"
        self._tab_name_map: dict[str, str] = {}

        # Main view layout (top → bottom):
        #   0  Update banner (hidden by default)
        #   1  Header bar (UI language + Moottorit… back-nav)
        #   2  Input tabs (PDF / Text)
        #   3  Engine + voice bar (ALWAYS visible — primary model picker)
        #   4  Action row (big Muunna + small secondaries + progress)
        #   5  Status strip (sticky one-liner; hidden until a file is picked)
        #   6  Asetukset header (collapse / expand)
        #   7  Asetukset body (language, speed, ref audio, voice style, output path)
        #   8  Log toggle
        #   9  Log panel (visible by default)
        self._build_update_banner(main, row=0)
        self._build_header_bar(main, row=1)
        self._build_input_tabs(main, row=2)
        self._build_engine_bar(main, row=3)
        self._build_action_row(main, row=4)
        self._build_status_strip(main, row=5)
        self._build_settings_frame(main, row=6)  # header at row=6, body at row=7
        self._build_log_panel(main, row=8, stretch_row=9)

        # Settings view — in-place alternative to the old Toplevel popup.
        # Stacked in the same grid cell; switched via tkraise().
        self._settings_view = EngineManagerView(
            self._view_container, ui_lang=self._ui_lang,
            on_back=self._show_main_view,
        )
        self._settings_view.grid(row=0, column=0, sticky="nsew")
        main.tkraise()

    # ---- View switching (single-window flow, no popups) --------------

    def _show_settings_view(self) -> None:
        """Swap to the engine manager view."""
        self._settings_view.refresh()
        self._settings_view.tkraise()

    def _show_main_view(self) -> None:
        """Return to the main audiobook creation view."""
        # Engine status may have changed (install/uninstall); refresh the dot.
        self._populate_engine_list()
        self._main_view.tkraise()

    # ---- Engine + voice picker bar (ALWAYS visible) -------------------

    def _build_engine_bar(self, parent: ctk.CTkFrame, row: int) -> None:
        """Compact always-visible row: Kieli + Moottori + Ääni dropdowns.

        Kieli lives up here with Moottori and Ääni so users pick what
        the voice speaks before they pick the voice itself — the Kieli
        → Moottori → Ääni funnel promised by the Phase 2 redesign.

        Layout (two rows, each combobox ~200 px):
            Kieli:  [  Suomi         v ]   Moottori: [ Chatterbox   v ]
            Ääni:   [  Grandmom      v ]   3 suomi-kielistä ääntä  [Testaa]
        """
        bar = ctk.CTkFrame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(1, weight=1)
        bar.columnconfigure(3, weight=1)
        self._engine_bar = bar

        # Row 0: Kieli + Moottori
        self._tts_lang_label = ctk.CTkLabel(bar, text="Kieli:")
        self._tts_lang_label.grid(row=0, column=0, sticky="w", padx=(8, 6), pady=6)
        # Note: command= is intentionally NOT wired here. CTkComboBox.set()
        # triggers the command callback, and both _apply_loaded_config()
        # and the initial .set("Suomi") below would fire the cascade
        # (re-filter engines, refresh voices, save config) before the rest
        # of the widget tree exists. We attach the callbacks at the end of
        # __init__ via _wire_engine_bar_callbacks() so they only ever run
        # in response to real user clicks.
        self._lang_cb = ctk.CTkComboBox(
            bar,
            values=list(LANGUAGES.keys()), state="readonly",
        )
        self._lang_cb.set("Suomi")
        self._lang_cb.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=6)

        self._engine_label = ctk.CTkLabel(bar, text="Moottori:")
        self._engine_label.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=6)
        self._engine_cb = ctk.CTkComboBox(
            bar, state="readonly",
        )
        self._engine_cb.grid(row=0, column=3, sticky="ew", padx=(0, 4), pady=6)
        # Engine combobox stays empty until _apply_loaded_config() resolves
        # the user's saved Language and calls _populate_engine_list() with
        # the right language context. Populating here would build the
        # dropdown for "Suomi" first and rebuild it ms later for the
        # actual saved Language — invisible to the user but flagged by the
        # sequencing audit as the wrong shape.

        # Row 1: Ääni + voice-count side label + Testaa
        self._voice_label = ctk.CTkLabel(bar, text="Ääni:")
        self._voice_label.grid(row=1, column=0, sticky="w", padx=(8, 6), pady=6)
        self._voice_cb = ctk.CTkComboBox(bar, state="readonly")
        self._voice_cb.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=6)

        # Grey side-label — honest about how many voices are available in
        # the selected language without colouring dropdown items (which
        # CTkComboBox doesn't support).
        self._voice_count_lbl = ctk.CTkLabel(
            bar, text="", text_color=("gray40", "gray60"),
        )
        self._voice_count_lbl.grid(
            row=1, column=2, columnspan=2, sticky="w", padx=(0, 6), pady=6
        )

        self._test_btn = ctk.CTkButton(
            bar, text="Testaa ääni", command=self._on_test_voice,
            width=110,
        )
        self._test_btn.grid(row=1, column=4, padx=(0, 8), pady=6)

    # ---- Header bar (language toggle, engine manager, about) ----------

    def _build_header_bar(self, parent: ctk.CTkFrame, row: int) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(0, weight=1)

        # Right-aligned icon buttons.
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")

        # Language toggle — compact combobox (kept as _ui_lang_cb so existing
        # language-change handler keeps working). Replaces the old
        # "Käyttöliittymä" row in Asetukset.
        self._ui_lang_cb = ctk.CTkComboBox(
            right,
            values=["Suomi", "English"], state="readonly", width=100,
            command=self._on_ui_language_changed,
        )
        self._ui_lang_cb.set("Suomi" if self._ui_lang == "fi" else "English")
        self._ui_lang_cb.grid(row=0, column=0, padx=(0, 6))

        # Engine manager — now an in-place view (no Toplevel popup).
        self._install_engines_btn = ctk.CTkButton(
            right, text="Moottorit\u2026",
            command=self._show_settings_view, width=120,
        )
        self._install_engines_btn.grid(row=0, column=1)

    # ---- Primary action row (big Muunna + secondaries + progress) ----

    def _build_action_row(self, parent: ctk.CTkFrame, row: int) -> None:
        ar = ctk.CTkFrame(parent, fg_color="transparent")
        ar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        ar.columnconfigure(0, weight=1)

        # Top: big primary + small secondaries.
        btn_row = ctk.CTkFrame(ar, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew")
        btn_row.columnconfigure(0, weight=1)

        # The star of the show — wide, bold, clearly the primary action.
        self._convert_btn = ctk.CTkButton(
            btn_row, text="Muunna", command=self._on_convert_click,
            height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self._convert_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._sample_btn = ctk.CTkButton(
            btn_row, text="Tee n\u00e4yte", command=self._on_sample_click,
            height=44, width=120,
        )
        self._sample_btn.grid(row=0, column=1, padx=(0, 6))

        self._listen_btn = ctk.CTkButton(
            btn_row, text="Esikuuntele", command=self._on_listen_click,
            height=44, width=120,
        )
        self._listen_btn.grid(row=0, column=2, padx=(0, 6))

        self._cancel_btn = ctk.CTkButton(
            btn_row, text="Peruuta", command=self._request_cancel,
            height=44, width=100,
            fg_color=gui_style.DANGER,
            hover_color=("#8b0000", "#B03A36"),
        )
        self._cancel_btn.grid(row=0, column=3, padx=(0, 6))
        self._cancel_btn.grid_remove()  # Only visible while running.

        self._open_folder_btn = ctk.CTkButton(
            btn_row, text="Avaa kansio", command=self._open_output_folder,
            height=44, width=120, state="disabled",
        )
        self._open_folder_btn.grid(row=0, column=4)

        # Bottom: progress bar + inline status (small, right-aligned).
        progress_row = ctk.CTkFrame(ar, fg_color="transparent")
        progress_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        progress_row.columnconfigure(0, weight=1)

        self._progress_bar = ctk.CTkProgressBar(progress_row)
        self._progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._progress_bar.set(0)

        self._status_label_val = ctk.CTkLabel(
            progress_row, text="", text_color="gray", width=180, anchor="e",
        )
        self._status_label_val.grid(row=0, column=1, sticky="e")

        # ETA label (kept for existing code paths; placed unobtrusively).
        self._eta_label = ctk.CTkLabel(ar, text="", text_color="gray")
        self._eta_label.grid(row=2, column=0, sticky="e", pady=(2, 0))

    # ---- Sticky status strip (between progress and log) ---------------

    def _build_status_strip(self, parent: ctk.CTkFrame, row: int) -> None:
        """Build the sticky one-line status strip.

        Hidden by default (via ``grid_remove``). The three visible states
        (ready / synthesizing / done) set both text and background color
        via :meth:`_set_status_strip`.
        """
        self._status_strip_frame = ctk.CTkFrame(
            parent, fg_color=("#1976d2", "#0d47a1"), corner_radius=6,
        )
        self._status_strip_frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        self._status_strip_frame.columnconfigure(0, weight=1)
        self._status_strip_label = ctk.CTkLabel(
            self._status_strip_frame, text="", text_color="white", anchor="w",
        )
        self._status_strip_label.grid(row=0, column=0, sticky="ew", padx=10, pady=4)
        self._status_strip_frame.grid_remove()

        # Cached fields from the latest _set_status_strip call — used to
        # re-render the strip in the new language when the user toggles UI
        # language mid-session.
        self._status_strip_state: str = "idle"
        self._status_strip_fields: dict[str, Any] = {}

    # Status-strip background colors. Source of truth is
    # ``gui_style.STATUS_STRIP`` so the palette stays in one place; the
    # class attribute is retained so external hooks / tests that inspect
    # ``UnifiedApp._STATUS_STRIP_COLORS`` keep working.
    _STATUS_STRIP_COLORS = gui_style.STATUS_STRIP
    _STATUS_STRIP_KEYS = {
        "ready": "status_ready_strip",
        "synthesizing": "status_synthesizing_strip",
        "done": "status_done_strip",
    }

    def _set_status_strip(self, state: str, **fields: Any) -> None:
        """Update the strip to one of {idle, ready, synthesizing, done}.

        ``idle`` hides the strip. Other states show it, set the background
        color, and interpolate ``fields`` into the language-appropriate
        template.
        """
        # Always remember the last state so UI-language toggle can re-render.
        self._status_strip_state = state
        self._status_strip_fields = dict(fields)

        if state == "idle":
            if hasattr(self, "_status_strip_frame"):
                self._status_strip_frame.grid_remove()
            return

        if not hasattr(self, "_status_strip_frame"):
            return

        color = self._STATUS_STRIP_COLORS.get(state, gui_style.INFO)
        key = self._STATUS_STRIP_KEYS.get(state)
        if key is None:
            return

        template = self._s(key)
        # ``rtf_suffix`` is optional in the synthesizing template; callers
        # that don't have a live RTF yet can omit it.
        if "rtf_suffix" not in fields:
            fields = {**fields, "rtf_suffix": ""}
        try:
            text = template.format(**fields)
        except (KeyError, ValueError):
            # Missing field — fall back to a safe minimal line rather than
            # crashing the UI thread.
            text = f"{fields.get('name', '')}"

        self._status_strip_frame.configure(fg_color=color)
        self._status_strip_label.configure(text=text)
        self._status_strip_frame.grid()

    def _update_synthesizing_strip(self, ev: "ProgressEvent") -> None:
        """Refresh the strip while a chunk event is being processed."""
        if not hasattr(self, "_status_strip_frame"):
            return
        if ev.total_chunks <= 0:
            return
        name = Path(self._pdf_path).name if self._pdf_path else ""
        ratio = ev.total_done / ev.total_chunks
        pct = int(round(ratio * 100))

        # ETA: prefer the event's own eta_s if present; else derive from
        # elapsed-so-far and remaining chunks.
        eta_s = getattr(ev, "eta_s", None)
        started = getattr(self, "_synth_started_at", None)
        if eta_s is None and started is not None and ratio > 0:
            elapsed = (datetime.now() - started).total_seconds()
            eta_s = elapsed * (1 - ratio) / ratio if ratio > 0 else 0

        if _duration_estimate is not None and eta_s is not None:
            eta_human = _duration_estimate.format_duration(eta_s)
        else:
            eta_human = "?"

        hhmm = (datetime.now() + timedelta(seconds=eta_s or 0)).strftime("%H:%M")

        # Optional RTF suffix — we only have a rough estimate from running
        # averages if the event carries audio_s / synth_s. Best-effort.
        rtf_suffix = ""
        audio_s = getattr(ev, "audio_s", None)
        synth_s = getattr(ev, "synth_s", None)
        if audio_s and synth_s and synth_s > 0:
            rtf = audio_s / synth_s
            rtf_suffix = f" \u00B7 RTF {rtf:.2f}x"

        self._set_status_strip(
            "synthesizing",
            name=name,
            pct=pct,
            eta_human=eta_human,
            hhmm=hhmm,
            rtf_suffix=rtf_suffix,
        )

    def _update_done_strip(self, output_path: Optional[str]) -> None:
        """Flip the strip into green ``done`` state after a successful run."""
        if not hasattr(self, "_status_strip_frame"):
            return
        name = Path(self._pdf_path).name if self._pdf_path else (
            Path(output_path).name if output_path else ""
        )
        started = getattr(self, "_synth_started_at", None)
        if started is not None:
            elapsed = (datetime.now() - started).total_seconds()
            if _duration_estimate is not None:
                wall_human = _duration_estimate.format_duration(elapsed)
            else:
                wall_human = f"{int(elapsed)} s"
        else:
            wall_human = "?"

        size_mb = 0.0
        if output_path:
            try:
                size_mb = Path(output_path).stat().st_size / 1024 / 1024
            except OSError:
                pass

        self._set_status_strip(
            "done", name=name, wall_human=wall_human, size_mb=size_mb,
        )

    # ---- 0. Update banner ---------------------------------------------

    def _build_update_banner(self, parent: ctk.CTkFrame, row: int) -> None:
        self._update_banner = ctk.CTkFrame(
            parent, fg_color=gui_style.SUCCESS, corner_radius=8
        )
        self._update_banner.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._update_banner.columnconfigure(0, weight=1)

        self._update_label = ctk.CTkLabel(
            self._update_banner, text="", text_color="white"
        )
        self._update_label.grid(row=0, column=0, sticky="w", padx=(8, 8))

        self._update_btn = ctk.CTkButton(
            self._update_banner, text=self._s("update_now"),
            command=self._on_update_click,
        )
        self._update_btn.grid(row=0, column=1, padx=(0, 4), pady=4)

        # Secondary "open in browser" fallback — always visible when the
        # banner is shown. Works even if the in-app updater is broken
        # (as happened on v3.3.1); gives the user a visible escape hatch
        # without needing to know about the Releases page.
        self._update_browser_btn = ctk.CTkButton(
            self._update_banner,
            text=self._s("update_download_manually"),
            command=self._on_update_browser_click,
            fg_color="transparent", border_width=1, border_color="white",
            text_color="white", hover_color=("#1b5e20", "#0b3d12"),
        )
        self._update_browser_btn.grid(row=0, column=2, padx=(0, 8), pady=4)

        # Hidden by default.
        self._update_banner.grid_remove()

    def _on_update_browser_click(self) -> None:
        """Open the latest release page in the user's browser.

        Escape hatch for any case where the in-app update flow can't
        complete (buggy shadowing method in an older version, file lock,
        AV quarantine, etc.). Always works because it's just a webbrowser.open.
        """
        import webbrowser
        url = (
            f"https://github.com/{GITHUB_REPO}/releases/latest"
            if not self._pending_update or not self._pending_update.latest_version
            else f"https://github.com/{GITHUB_REPO}/releases/tag/"
                 f"v{self._pending_update.latest_version}"
        )
        try:
            webbrowser.open(url, new=2)
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror(self._s("error"), f"{url}\n\n{exc}")

    # ---- 1. Input tabs ------------------------------------------------

    def _build_input_tabs(self, parent: ctk.CTkFrame, row: int) -> None:
        self._input_nb = ctk.CTkTabview(
            parent, height=200, command=self._on_tab_changed
        )
        self._input_nb.grid(row=row, column=0, sticky="ew", pady=(0, 8))

        # Book tab (accepts PDF / EPUB / TXT — the internal tab name is
        # kept short for the CTkTabview header).
        pdf_tab_name = "Kirja"
        pdf_frame = self._input_nb.add(pdf_tab_name)
        pdf_frame.columnconfigure(0, weight=1)

        self._pdf_entry = ctk.CTkEntry(pdf_frame, state="disabled")
        self._pdf_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        # Set initial placeholder text.
        self._pdf_entry.configure(state="normal")
        self._pdf_entry.insert(0, "Ei tiedostoa valittu")
        self._pdf_entry.configure(state="disabled")

        self._pdf_browse_btn = ctk.CTkButton(
            pdf_frame, text="Selaa\u2026", command=self._browse_pdf, width=80
        )
        self._pdf_browse_btn.grid(row=0, column=1)

        # Text tab
        text_tab_name = "Teksti"
        text_frame = self._input_nb.add(text_tab_name)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self._text_widget = ctk.CTkTextbox(
            text_frame, height=160, wrap="word", font=ctk.CTkFont(size=13)
        )
        self._text_widget.grid(row=0, column=0, sticky="nsew")

        # Placeholder handling.
        self._text_placeholder = "Kirjoita tai liitä teksti tähän..."
        self._text_has_placeholder = True
        self._text_widget.insert("1.0", self._text_placeholder)
        self._text_widget.configure(text_color="gray")
        self._text_widget.bind("<FocusIn>", self._on_text_focus_in)
        self._text_widget.bind("<FocusOut>", self._on_text_focus_out)

        # Build the tab name map.
        self._tab_name_map = {
            pdf_tab_name: "pdf",
            text_tab_name: "text",
        }

    def _on_text_focus_in(self, _event: object = None) -> None:
        if self._text_has_placeholder:
            self._text_widget.delete("1.0", tk.END)
            self._text_widget.configure(text_color=("black", "white"))
            self._text_has_placeholder = False

    def _on_text_focus_out(self, _event: object = None) -> None:
        content = self._text_widget.get("1.0", tk.END).strip()
        if not content:
            self._text_widget.insert("1.0", self._text_placeholder)
            self._text_widget.configure(text_color="gray")
            self._text_has_placeholder = True

    # ---- 2. Settings frame --------------------------------------------

    def _build_settings_frame(self, parent: ctk.CTkFrame, row: int) -> None:
        # Collapsible header bar + hidden body.
        self._settings_open = False

        header_frame = ctk.CTkFrame(parent, fg_color="transparent")
        header_frame.grid(row=row, column=0, sticky="ew", pady=(4, 2))
        header_frame.columnconfigure(0, weight=1)

        self._settings_header_btn = ctk.CTkButton(
            header_frame,
            text="\u25B8 Asetukset",
            command=self._toggle_settings,
            anchor="w",
            height=32,
            fg_color="transparent",
            text_color=("gray20", "gray80"),
            hover_color=("gray90", "gray25"),
        )
        self._settings_header_btn.grid(row=0, column=0, sticky="ew")

        settings_outer = ctk.CTkFrame(parent)
        settings_outer.grid(row=row + 1, column=0, sticky="ew", pady=(0, 8))
        settings_outer.columnconfigure(0, weight=1)
        self._settings_outer = settings_outer
        settings_outer.grid_remove()  # Collapsed by default

        self._settings_frame = ctk.CTkFrame(settings_outer, fg_color="transparent")
        self._settings_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        self._settings_frame.columnconfigure(1, weight=1)
        self._settings_frame.columnconfigure(3, weight=1)
        settings = self._settings_frame

        srow = 0

        # Hidden compatibility widget for legacy engine-status hook points.
        self._engine_status_lbl = ctk.CTkLabel(
            settings, text="", text_color=_CLR_READY, wraplength=560,
        )
        # Not gridded — other code can still call .configure(text=...).

        # Row 0: Speed. Kieli lives in the engine bar now, not here, so
        # the Nopeus widget gets promoted to column 0 and stays on its
        # own row instead of sharing with a (removed) language picker.
        self._speed_label = ctk.CTkLabel(settings, text="Nopeus:")
        self._speed_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6)
        )
        self._speed_cb = ctk.CTkComboBox(
            settings,
            values=list(SPEED_OPTIONS["fi"].keys()), state="readonly", width=200,
        )
        self._speed_cb.set("Normaali")
        self._speed_cb.grid(row=srow, column=1, sticky="w")
        srow += 1

        # Row 3: Reference audio (voice cloning) — hidden when unsupported
        self._ref_label = ctk.CTkLabel(settings, text="Ref. ääni:")
        self._ref_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._ref_frame = ctk.CTkFrame(settings, fg_color="transparent")
        self._ref_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        self._ref_frame.columnconfigure(0, weight=1)
        self._ref_audio_var = tk.StringVar(value="")
        self._ref_entry = ctk.CTkEntry(
            self._ref_frame, textvariable=self._ref_audio_var, state="disabled"
        )
        self._ref_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._ref_browse_btn = ctk.CTkButton(
            self._ref_frame, text="Selaa", command=self._browse_reference_audio,
            width=60,
        )
        self._ref_browse_btn.grid(row=0, column=1)
        self._ref_clear_btn = ctk.CTkButton(
            self._ref_frame, text="Tyhjennä", command=self._clear_reference_audio,
            width=80,
        )
        self._ref_clear_btn.grid(row=0, column=2, padx=(4, 0))
        srow += 1

        # Row 4: Voice description — hidden when unsupported
        self._desc_label = ctk.CTkLabel(settings, text="Äänityyli:")
        self._desc_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        self._voice_desc_var = tk.StringVar(value="")
        self._voice_desc_entry = ctk.CTkEntry(
            settings, textvariable=self._voice_desc_var
        )
        self._voice_desc_entry.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        srow += 1

        # Row 5: Tallenna + Tuloste on the SAME row (merged output controls).
        self._save_label = ctk.CTkLabel(settings, text="Tallenna:")
        self._save_label.grid(
            row=srow, column=0, sticky="w", padx=(0, 6), pady=(6, 0)
        )
        out_frame = ctk.CTkFrame(settings, fg_color="transparent")
        out_frame.grid(
            row=srow, column=1, columnspan=3, sticky="ew", pady=(6, 0)
        )
        out_frame.columnconfigure(0, weight=1)

        self._out_entry = ctk.CTkEntry(out_frame, state="disabled")
        self._out_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._out_browse_btn = ctk.CTkButton(
            out_frame, text="Vaihda\u2026", command=self._browse_output,
            width=80,
        )
        self._out_browse_btn.grid(row=0, column=1, padx=(0, 8))

        self._output_mode_label = ctk.CTkLabel(out_frame, text="Tuloste:")
        self._output_mode_label.grid(row=0, column=2, sticky="w", padx=(0, 4))

        self._output_mode_cb = ctk.CTkComboBox(
            out_frame,
            values=list(OUTPUT_MODES["fi"].keys()), state="readonly",
            width=140,
        )
        self._output_mode_cb.set("Yksi MP3")
        self._output_mode_cb.grid(row=0, column=3, sticky="w")
        # Set initial auto-generated path.
        self._auto_output_path()

        # Initially hide capability-specific widgets.
        self._ref_label.grid_remove()
        self._ref_frame.grid_remove()
        self._desc_label.grid_remove()
        self._voice_desc_entry.grid_remove()

    def _toggle_settings(self) -> None:
        """Show/hide the Asetukset body."""
        self._settings_open = not self._settings_open
        if self._settings_open:
            self._settings_outer.grid()
            self._settings_header_btn.configure(text="\u25BE Asetukset")
        else:
            self._settings_outer.grid_remove()
            self._settings_header_btn.configure(text="\u25B8 Asetukset")

    # ---- 5. Log panel (collapsible) -----------------------------------

    def _build_log_panel(
        self, parent: ctk.CTkFrame, row: int, stretch_row: int
    ) -> None:
        toggle_frame = ctk.CTkFrame(parent, fg_color="transparent")
        toggle_frame.grid(row=row, column=0, sticky="ew", pady=(4, 0))

        self._log_toggle_btn = ctk.CTkButton(
            toggle_frame, text="Piilota loki", command=self._toggle_log,
            width=120,
        )
        self._log_toggle_btn.grid(row=0, column=0, sticky="w")

        self._log_frame = ctk.CTkFrame(parent)
        # Placed in stretch_row so it can grow.
        self._log_frame.grid(
            row=stretch_row, column=0, sticky="nsew", pady=(4, 0)
        )
        self._log_frame.columnconfigure(0, weight=1)
        self._log_frame.rowconfigure(0, weight=1)

        self._log_text = ctk.CTkTextbox(
            self._log_frame, height=200, wrap="word",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._log_text.grid(row=0, column=0, sticky="nsew")
        self._log_text.configure(state="disabled")

        # Visible by default (log panel shown on launch).

    # ------------------------------------------------------------------
    # Engine list population
    # ------------------------------------------------------------------

    def _populate_engine_list(self) -> None:
        """Fill the engine combobox from the registry + Chatterbox check.

        Runs check_status() on every engine so the dropdown label
        reflects current availability (e.g. "Piper (ladattava)" when
        voices are missing). Also filters by the currently selected
        Kieli — engines whose supported_languages() does not include
        the chosen language are hidden from the dropdown, enforcing
        the Kieli → Moottori → Ääni funnel.
        """
        self._engine_display_to_id.clear()

        # Resolve current language; fall back to 'fi' during the first
        # build pass where _lang_cb may not exist yet.
        try:
            current_lang = self._current_language()
        except AttributeError:
            current_lang = "fi"

        # Status dots give the user a glanceable read of each engine:
        #   🟢 ready    🟡 needs download (voice or model)    🔴 not available
        for engine in list_engines():
            # Skip engines that don't speak the selected language at all.
            try:
                if current_lang not in engine.supported_languages():
                    continue
            except Exception:
                # Be defensive: a buggy third-party engine must not
                # break the whole dropdown.
                pass
            dot = "\U0001F7E2"  # green circle — default: ready
            try:
                status = engine.check_status()
                if not status.available:
                    dot = "\U0001F534"  # red circle
                elif status.needs_download:
                    dot = "\U0001F7E1"  # yellow circle
            except Exception:
                pass
            label = f"{dot}  {engine.display_name}"
            self._engine_display_to_id[label] = engine.id

        # Chatterbox via subprocess bridge. It supports both Finnish (T3
        # finetune) and English (voice-clone reference) so it's shown
        # for every language we expose.
        chatterbox_py = resolve_chatterbox_python()
        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        if chatterbox_py is not None and runner_script.exists():
            if current_lang in _CHATTERBOX_LANG_TAGS:
                label = "\U0001F7E2  Chatterbox Finnish (paras laatu, NVIDIA)"
                self._engine_display_to_id[label] = "chatterbox_fi"

        labels = list(self._engine_display_to_id.keys())
        self._engine_cb.configure(values=labels)
        # Preserve selection if it is still valid; otherwise pick the first.
        current = self._engine_cb.get()
        if current not in labels:
            self._engine_cb.set(labels[0] if labels else "")

    def _current_engine_id(self) -> str:
        display = self._engine_cb.get()
        return self._engine_display_to_id.get(display, "")

    def _current_engine(self) -> Optional[TTSEngine]:
        eid = self._current_engine_id()
        if not eid or eid == "chatterbox_fi":
            return None
        return get_engine(eid)

    def _current_language(self) -> str:
        return LANGUAGES.get(self._lang_cb.get(), "fi")

    def _current_voice(self) -> Optional[Voice]:
        engine = self._current_engine()
        if not engine:
            return None
        display = self._voice_cb.get()
        for voice in engine.list_voices(self._current_language()):
            if voice.display_name == display:
                return voice
        return None

    # ------------------------------------------------------------------
    # Input mode helpers
    # ------------------------------------------------------------------

    @property
    def _input_mode(self) -> str:
        """Return 'pdf' or 'text' based on the active tabview tab."""
        current_tab = self._input_nb.get()
        return self._tab_name_map.get(current_tab, "pdf")

    def _on_tab_changed(self) -> None:
        """Refresh the auto-generated output path when switching tabs."""
        # Update the raw input mode tracker.
        self._input_mode_raw = self._input_mode
        self._auto_output_path()

    def _get_input_text(self) -> str:
        """Return the text to synthesize based on the active input tab."""
        if self._input_mode == "pdf":
            if not self._pdf_path:
                raise ValueError(self._s("no_pdf"))
            book = parse_book(self._pdf_path)
            if not book.full_text.strip():
                raise ValueError(self._s("pdf_no_text"))
            return book.full_text
        else:
            if self._text_has_placeholder:
                return ""
            return self._text_widget.get("1.0", tk.END).strip()

    # ------------------------------------------------------------------
    # Engine change
    # ------------------------------------------------------------------

    def _on_engine_changed(self, selection: str = "") -> None:
        self._refresh_voice_list()
        # Engine change affects the wall-time estimate — refresh the strip.
        self._refresh_ready_status_strip()

    def _on_language_changed(self, selection: str = "") -> None:
        # Kieli drives the whole cascade: first re-filter engines so the
        # user can't stay on one that can't speak the new language, then
        # re-filter voices inside the surviving engine, then persist.
        self._populate_engine_list()
        self._refresh_voice_list()
        # Language change affects both audio rate and wall time — refresh.
        self._refresh_ready_status_strip()
        # Persist the new Kieli so the choice survives across sessions.
        # Safe here because the combobox command is only wired after
        # __init__ finishes — see _wire_engine_bar_callbacks().
        self._save_current_config()

    def _wire_engine_bar_callbacks(self) -> None:
        """Attach command= callbacks to the engine-bar comboboxes.

        Called at the end of __init__, AFTER _build_ui() and
        _apply_loaded_config() have finished programmatically setting
        initial values. This guarantees the Kieli/Moottori cascade only
        runs on real user picks, never on construction-time .set() calls.
        """
        self._lang_cb.configure(command=self._on_language_changed)
        self._engine_cb.configure(command=self._on_engine_changed)

    def _update_voice_count_label(self, n: int) -> None:
        """Render the grey side-label e.g. '3 suomenkielist\u00e4 \u00e4\u00e4nt\u00e4'."""
        if not hasattr(self, "_voice_count_lbl"):
            return
        lang = self._current_language()
        lang_key = f"lang_name_{lang}"
        lang_name = self._s(lang_key) if lang_key in _STRINGS.get(self._ui_lang, {}) else lang
        text = self._s("voice_count_label").format(n=n, lang_name=lang_name)
        self._voice_count_lbl.configure(text=text)

    def _refresh_voice_list(self) -> None:
        """Refresh voices, status label, and capability widgets."""
        eid = self._current_engine_id()

        # Chatterbox is subprocess-only — no voice list from registry.
        if eid == "chatterbox_fi":
            # "Grandmom" is the single Chatterbox voice but it works in
            # both languages: T3 finetune for Finnish, multilingual base
            # + voice-clone reference for English (see
            # scripts/generate_chatterbox_audiobook.py). We surface it as
            # a language-tagged entry so the voice list is honest about
            # what speaks what, matching the Edge/Piper display format.
            names = _chatterbox_voices_for_language(self._current_language())
            self._voice_cb.configure(values=names)
            self._voice_cb.set(names[0] if names else "")
            self._update_voice_count_label(len(names))
            self._engine_status_lbl.configure(text_color=_CLR_READY)
            self._engine_status_lbl.configure(
                text="Offline, paras laatu. Kesto ~1\u20132 h NVIDIA-koneella."
            )
            self._update_capability_widgets(
                supports_cloning=True, supports_description=False
            )
            return

        engine = self._current_engine()
        if engine is None:
            self._voice_cb.configure(values=[])
            self._voice_cb.set("")
            self._update_voice_count_label(0)
            self._engine_status_lbl.configure(text="")
            self._update_capability_widgets(False, False)
            return

        status = engine.check_status()
        if not status.available:
            self._engine_status_lbl.configure(text_color=_CLR_UNAVAILABLE)
            self._engine_status_lbl.configure(text=status.reason)
            self._voice_cb.configure(values=[])
            self._voice_cb.set("")
            self._update_voice_count_label(0)
        elif status.needs_download:
            self._engine_status_lbl.configure(text_color=_CLR_NEEDS_SETUP)
            self._engine_status_lbl.configure(
                text=status.reason + "  Lataus käynnistyy automaattisesti."
            )
            self._populate_voice_combobox(engine)
        else:
            self._engine_status_lbl.configure(text_color=_CLR_READY)
            self._engine_status_lbl.configure(text=engine.description)
            self._populate_voice_combobox(engine)

        self._update_capability_widgets(
            supports_cloning=bool(engine.supports_voice_cloning),
            supports_description=bool(engine.supports_voice_description),
        )

    def _populate_voice_combobox(self, engine: TTSEngine) -> None:
        lang = self._current_language()
        voices = engine.list_voices(lang)
        names = [v.display_name for v in voices]
        self._voice_cb.configure(values=names)
        if names:
            default_id = engine.default_voice(lang)
            default_name = next(
                (v.display_name for v in voices if v.id == default_id),
                names[0],
            )
            self._voice_cb.set(default_name)
        else:
            self._voice_cb.set("")
        self._update_voice_count_label(len(names))

    def _update_capability_widgets(
        self, supports_cloning: bool, supports_description: bool
    ) -> None:
        for w in (self._ref_label, self._ref_frame):
            if supports_cloning:
                w.grid()
            else:
                w.grid_remove()

        for w in (self._desc_label, self._voice_desc_entry):
            if supports_description:
                w.grid()
            else:
                w.grid_remove()

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _bump_output_path_if_exists(self) -> None:
        """If the current output path already points at an existing file,
        bump it to the next free numbered variant so Muunna never
        overwrites the previous recording.

        Examples:
            texttospeech_3.mp3 (exists) → texttospeech_4.mp3
            book.mp3 (exists)           → book_2.mp3
            book_5.mp3 (exists)         → book_6.mp3
        """
        if not self._output_path:
            return
        target = Path(self._output_path)
        if not target.exists():
            return  # Fresh name, nothing to do.

        stem = target.stem
        suffix = target.suffix or ".mp3"
        parent = target.parent

        # Split trailing _N off the stem, defaulting to 1 if not numbered.
        import re
        match = re.match(r"^(.*?)_(\d+)$", stem)
        if match:
            base, n = match.group(1), int(match.group(2))
        else:
            base, n = stem, 1

        while True:
            n += 1
            candidate = parent / f"{base}_{n}{suffix}"
            if not candidate.exists():
                break

        new_path = str(candidate)
        self._output_path = new_path
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, new_path)
        self._out_entry.configure(state="disabled")

    def _default_output_dir(self) -> Path:
        """Return the default folder where generated MP3s go.

        Installed (frozen) mode: next to the running .exe (install root).
        Dev mode: Documents\\AudiobookMaker (no sensible install root
        when running from source).
        """
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path.home() / "Documents" / "AudiobookMaker"

    def _auto_output_path(self) -> None:
        """Generate an automatic output path based on current input mode."""
        if self._input_mode == "pdf" and self._pdf_path:
            # Output goes next to the PDF: book.pdf -> book.mp3
            suggested = str(Path(self._pdf_path).with_suffix(".mp3"))
        else:
            # Auto-increment: texttospeech_1.mp3, texttospeech_2.mp3, ...
            out_dir = self._default_output_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            n = 1
            while True:
                candidate = out_dir / f"texttospeech_{n}.mp3"
                if not candidate.exists():
                    break
                n += 1
            suggested = str(candidate)
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, suggested)
        self._out_entry.configure(state="disabled")
        self._output_path = suggested

    def _browse_pdf(self) -> None:
        # Accept PDF / EPUB / TXT so the same "Kirja" tab works for any
        # book-shaped input the parsers support. "All files" stays first
        # so the user can see and pick anything by default — the
        # book-only filters trimmed legitimate input on some Windows
        # locales where the extension matching was case-sensitive.
        if self._ui_lang == "fi":
            types = [
                ("Kaikki tiedostot", "*.*"),
                ("Kirjatiedostot", "*.pdf *.epub *.txt"),
                ("PDF-tiedostot", "*.pdf"),
                ("EPUB-tiedostot", "*.epub"),
                ("Tekstitiedostot", "*.txt"),
            ]
        else:
            types = [
                ("All files", "*.*"),
                ("Book files", "*.pdf *.epub *.txt"),
                ("PDF files", "*.pdf"),
                ("EPUB files", "*.epub"),
                ("Text files", "*.txt"),
            ]
        path = filedialog.askopenfilename(
            title=self._s("select_pdf"),
            filetypes=types,
        )
        if path:
            self._pdf_path = path
            self._pdf_entry.configure(state="normal")
            self._pdf_entry.delete(0, tk.END)
            self._pdf_entry.insert(0, path)
            self._pdf_entry.configure(state="disabled")
            self._status_label_val.configure(text=self._s("pdf_selected"))
            self._auto_output_path()
            # Kick off a background parse+estimate so the sticky status
            # strip shows book metadata without blocking the UI thread.
            self._start_ready_estimate(path)

    # ---- Sticky status strip: ready-state estimate --------------------

    def _start_ready_estimate(self, path: str) -> None:
        """Parse the book on a worker thread and update the status strip.

        Parsing even a full EPUB/PDF is typically well under a second, but
        we still don't want to freeze the UI thread if the user picks a
        big file. The final text is posted via ``self.after(0, ...)``.
        """
        def _worker() -> None:
            try:
                book = parse_book(path)
                chars = sum(len(ch.content) for ch in book.chapters)
            except Exception:
                return  # Silent: strip stays in its previous state.
            self.after(0, lambda: self._apply_ready_estimate(path, chars))

        self._pending_ready_chars = None  # type: ignore[attr-defined]
        threading.Thread(target=_worker, daemon=True, name="ready-estimate").start()

    def _apply_ready_estimate(self, path: str, chars: int) -> None:
        """Store char count + refresh the strip on the main thread."""
        self._pending_ready_chars = chars  # type: ignore[attr-defined]
        self._refresh_ready_status_strip()

    def _refresh_ready_status_strip(self) -> None:
        """Recompute and show the ``ready`` strip from cached char count.

        Called on file pick, engine change, and language change. Safe to
        call when no file is selected (it becomes a no-op).
        """
        chars = getattr(self, "_pending_ready_chars", None)
        if not chars or not self._pdf_path:
            return
        if _duration_estimate is None:
            return  # Sibling agent's module not ready — skip gracefully.

        engine_id = self._current_engine_id() or "edge"
        lang = self._current_language() or "fi"
        try:
            est = _duration_estimate.estimate_job(
                chars, engine_id, lang, device="cuda",
            )
        except Exception:
            return

        engine_display = self._engine_cb.get() or engine_id
        name = Path(self._pdf_path).name
        self._set_status_strip(
            "ready",
            name=name,
            chars=max(1, round(chars / 1000)),
            audio_human=est.get("audio_human", "?"),
            wall_human=est.get("wall_human", "?"),
            engine_display=engine_display,
        )

    def _browse_reference_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="Valitse referenssiäänitiedosto",
            filetypes=[
                ("Äänitiedostot", "*.wav *.mp3 *.flac *.ogg *.m4a"),
                ("Kaikki tiedostot", "*.*"),
            ],
        )
        if path:
            self._ref_audio_var.set(path)

    def _clear_reference_audio(self) -> None:
        self._ref_audio_var.set("")

    def _browse_output(self) -> None:
        """Let the user pick an output folder. Filename is auto-generated."""
        current = self._out_entry.get()
        initial_dir = str(Path(current).parent) if current else str(Path.home())
        chosen = filedialog.askdirectory(
            title=self._s("save_as"),
            initialdir=initial_dir,
        )
        if not chosen:
            return
        out_dir = Path(chosen)
        out_dir.mkdir(parents=True, exist_ok=True)
        mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_cb.get(), "single")
        if mode == "single":
            # Auto-increment: texttospeech_1.mp3, texttospeech_2.mp3, ...
            n = 1
            while True:
                candidate = out_dir / f"texttospeech_{n}.mp3"
                if not candidate.exists():
                    break
                n += 1
            path = str(candidate)
        else:
            path = str(out_dir)
        self._output_path = path
        # User explicitly chose this folder — pin the name in the entry.
        # (Auto-increment on next run is still applied via _bump_output_path.)
        self._output_user_chosen = True
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, path)
        self._out_entry.configure(state="disabled")

    # ------------------------------------------------------------------
    # Engine installer (placeholder)
    # ------------------------------------------------------------------

    def _open_engine_manager(self) -> None:
        """Legacy entry-point. Routes to the in-place settings view."""
        self._show_settings_view()

    # ------------------------------------------------------------------
    # Update self-heal: fallback when silent install didn't take effect
    # ------------------------------------------------------------------

    def _pop_to_foreground(self) -> None:
        """Force the main window to the active foreground.

        Called when the app launches as the result of a successful
        auto-update — the user clicked "Päivitä nyt" minutes ago and
        has likely moved on to other windows during the install. Bring
        the new version to the front so they see the result.

        Combines three mechanisms because Windows is picky about
        cross-process foreground stealing:
          1. ``deiconify()`` in case the window came up minimised.
          2. Brief ``-topmost True`` flicker — the canonical Tk way to
             grab focus across processes; the topmost flag is cleared
             on the next tick so the window doesn't stay always-on-top.
          3. Tk ``focus_force()`` for keyboard focus.

        The old process (in ``apply_update``) called
        ``AllowSetForegroundWindow(-1)`` before exiting, granting us
        the right to do this.
        """
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass  # never break the app over a foreground hint

    def _check_pending_update_marker(self) -> None:
        """If the last update failed, offer a visible-installer fallback.

        apply_update() writes a marker before exiting. On next launch we
        compare our version to the expected one — if we're still old,
        the silent install didn't work. Offer to run the downloaded
        installer visibly via os.startfile.
        """
        from src.auto_updater import (
            verify_pending_update, clear_pending_marker, run_installer_visibly,
            APP_VERSION,
        )
        marker = verify_pending_update(APP_VERSION)
        if marker is None:
            return  # No marker, or update succeeded — nothing to do.

        expected = marker.get("expected_version", "")
        installer_path = Path(marker.get("installer_path", ""))

        if not installer_path.exists():
            # The installer file is gone — can't recover automatically.
            clear_pending_marker()
            self._append_log(
                f"Aiempi päivitys v{expected} epäonnistui; "
                "asennustiedosto ei ole enää saatavilla."
            )
            return

        # Tell the user and offer the visible fallback.
        if self._ui_lang == "fi":
            msg = (
                f"Automaattinen päivitys versioon {expected} epäonnistui. "
                f"Haluatko käynnistää asentajan nyt?\n\n"
                f"Asennustiedosto: {installer_path.name}"
            )
            title = "Päivityksen korjaus"
        else:
            msg = (
                f"The auto-update to version {expected} did not take effect. "
                f"Run the installer now?\n\n"
                f"Installer: {installer_path.name}"
            )
            title = "Update recovery"

        if messagebox.askyesno(title, msg):
            self._append_log(
                f"Käynnistetään näkyvä asennus: {installer_path}"
            )
            try:
                run_installer_visibly(installer_path)
                # Must exit so the installer can replace our files.
                self.after(100, lambda: os._exit(0))
            except Exception as exc:
                messagebox.showerror(title, str(exc))
                clear_pending_marker()
        else:
            # User declined — clear the marker so we don't nag them again.
            clear_pending_marker()

    # ------------------------------------------------------------------
    # Old install / orphan shortcut cleanup
    # ------------------------------------------------------------------

    def _check_for_old_installs(self) -> None:
        """Silent background cleanup of old installs and orphan shortcuts.

        Runs once on startup. Removes stale items automatically and logs
        what was done to the loki panel. Never shows a popup — the user
        should not have to manage leftovers from previous versions.
        """
        def worker() -> None:
            try:
                from src.cleanup import (
                    find_old_installs, find_orphan_shortcuts,
                    remove_old_install, remove_orphan_shortcut,
                )
                old = find_old_installs()
                orphans = find_orphan_shortcuts()
                if not old and not orphans:
                    return

                # Rescue user MP3s from any old install into the currently
                # running install's output folder (the new v3.3+ default:
                # {app} root). Users never lose audiobooks to cleanup.
                rescue_dir = self._default_output_dir()
                removed = 0
                for inst in old:
                    ok, msg = remove_old_install(inst, rescue_to=rescue_dir)
                    if ok:
                        removed += 1
                        self.after(0, lambda p=inst.path, m=msg:
                                   self._append_log(
                                       f"Poistettu vanha asennus: {p} ({m})"
                                   ))
                for short in orphans:
                    ok, _ = remove_orphan_shortcut(short)
                    if ok:
                        removed += 1
                        self.after(0, lambda p=short.shortcut_path: self._append_log(
                            f"Poistettu rikkinäinen pikakuvake: {p.name}"
                        ))
            except Exception as exc:
                self.after(0, lambda: self._append_log_warning(
                    f"Siivous epäonnistui: {exc}"
                ))

        threading.Thread(
            target=worker, daemon=True, name="cleanup-scan",
        ).start()

    # ------------------------------------------------------------------
    # Auto-update (periodic check + download + install)
    # ------------------------------------------------------------------

    UPDATE_CHECK_INTERVAL_MS = 5 * 60 * 1000  # 5 minutes

    def _check_update_worker(self) -> None:
        """Background thread: check GitHub for a newer release."""
        try:
            info = check_for_update(APP_VERSION)
            self._update_queue.put(info)
        except Exception:
            pass  # Never crash on update check failure.

    def _poll_update_check(self) -> None:
        """Main thread: pick up the result from the update check thread."""
        try:
            info = self._update_queue.get_nowait()
            if info.available:
                self._pending_update = info
                self._show_update_banner(info)
        except queue.Empty:
            pass

        # Schedule the next periodic check.
        if getattr(sys, "frozen", False):
            self.after(
                self.UPDATE_CHECK_INTERVAL_MS,
                self._schedule_update_check,
            )

    def _schedule_update_check(self) -> None:
        """Launch a new background update check and poll for results."""
        threading.Thread(
            target=self._check_update_worker, daemon=True,
            name="update-check-periodic",
        ).start()
        self.after(500, self._poll_update_check)

    def _show_update_banner(self, info: UpdateInfo) -> None:
        """Show the update banner with version info."""
        msg = self._s("update_available").format(version=info.latest_version)
        self._update_label.configure(text=msg)
        self._update_banner.grid()

    # Note: _on_update_click and the download worker live in
    # src/gui_update_mixin.py. Earlier copies here shadowed the mixin
    # and silently swallowed errors (no messagebox, no expected_version
    # passed through to apply_update). Removed so the mixin's more
    # robust event-queue version is the single source of truth.

    # ------------------------------------------------------------------
    # Voice preview
    # ------------------------------------------------------------------

    def _on_test_voice(self) -> None:
        if self._testing_voice:
            return
        engine = self._current_engine()
        voice = self._current_voice()
        eid = self._current_engine_id()

        if eid == "chatterbox_fi":
            # Chatterbox is too slow to synthesize on demand from the
            # voice-test button, but we ship pre-baked Grandmom samples
            # for both supported languages so the button still produces
            # something the user can hear immediately.
            lang = self._current_language()
            bundled = self._bundled_voice_sample(lang)
            if bundled is not None:
                self._safe_play_sample(bundled)
                return
            messagebox.showinfo(
                self._s("voice_sample_title"),
                self._s("chatterbox_no_sample"),
            )
            return

        if engine is None or voice is None:
            messagebox.showerror(self._s("error"), self._s("select_engine_voice"))
            return

        self._testing_voice = True
        self._test_btn.configure(state="disabled")
        self._status_label_val.configure(text=self._s("synthesizing_sample"))

        threading.Thread(
            target=self._test_voice_worker, daemon=True, name="voice-test"
        ).start()

    def _test_voice_worker(self) -> None:
        try:
            engine = self._current_engine()
            voice = self._current_voice()
            if engine is None or voice is None:
                return
            lang = self._current_language()
            text = _SAMPLE_TEXT.get(lang, _SAMPLE_TEXT["fi"])

            tmp = tempfile.NamedTemporaryFile(
                prefix="sample_", suffix=".mp3", delete=False
            )
            tmp.close()

            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None

            engine.synthesize(
                text, tmp.name, voice.id, lang,
                lambda c, t, m: None,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )
            self._safe_play_sample(tmp.name)
        except Exception as exc:
            self.after(0, lambda: self._status_label_val.configure(
                text=self._s("sample_failed").format(error=exc)
            ))
        finally:
            self.after(0, lambda: self._test_btn.configure(state="normal"))
            self.after(0, lambda: setattr(self, "_testing_voice", False))

    def _bundled_voice_sample(self, lang: str) -> Optional[str]:
        """Return the path to the bundled Grandmom voice sample for ``lang``.

        Used by the Test-voice button on the Chatterbox engine, where
        on-demand synthesis is too slow to give the user instant feedback.
        Falls back to None if the bundled file isn't present (dev box
        without assets, or a fresh checkout).

        Files (relative to install root):
          assets/voices/grandmom_en_sample.mp3   — English Grandmom
          assets/voices/grandmom_reference.wav   — Finnish Grandmom (reused
                                                    from the voice-clone
                                                    reference; it's a
                                                    clean ~11 s clip)

        Layout in frozen install: same paths under ``_internal/``.
        """
        if lang == "en":
            filename = "grandmom_en_sample.mp3"
        else:
            filename = "grandmom_reference.wav"

        # Same search pattern as _bundled_grandmom_ref in the Chatterbox
        # subprocess: dev layout, then frozen-install _internal layout.
        repo_root = Path(__file__).resolve().parent.parent
        candidates = [
            repo_root / "assets" / "voices" / filename,
            repo_root / "_internal" / "assets" / "voices" / filename,
        ]
        # In a frozen build src/ lives at {app}/_internal/src/; assets sit
        # at {app}/_internal/assets/, so repo_root above already points at
        # _internal/. Cover the legacy {app}/assets/ layout too just in
        # case an older installer left things there.
        if getattr(sys, "frozen", False):
            install_root = Path(sys.executable).parent
            candidates.append(install_root / "assets" / "voices" / filename)
            candidates.append(install_root / "_internal" / "assets" / "voices" / filename)

        for cand in candidates:
            if cand.is_file():
                return str(cand)
        return None

    def _safe_play_sample(self, path: str) -> None:
        def _play() -> None:
            self._status_label_val.configure(text=self._s("sample_saved").format(path=path))
            try:
                if sys.platform == "win32":
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception:
                pass
        self.after(0, _play)

    # ------------------------------------------------------------------
    # Listen (synthesize + play without saving)
    # ------------------------------------------------------------------

    def _find_ffplay(self) -> Optional[str]:
        """Locate ffplay, checking the bundled ffmpeg dir first."""
        path = shutil.which("ffplay")
        if path:
            return path
        # On Windows, also check for ffplay.exe next to ffmpeg.
        ffmpeg_dir = get_ffmpeg_dir()
        if ffmpeg_dir:
            candidate = os.path.join(ffmpeg_dir, "ffplay.exe")
            if os.path.isfile(candidate):
                return candidate
        return None

    def _on_listen_click(self) -> None:
        if self._listening or self._synth_running:
            return

        # Priority 1: play the most recent finished MP3 from this session,
        # whichever button produced it (Tee näyte / Make sample, or the
        # full Muunna / Convert run). _last_playable_path is set by both
        # success paths in _drain_event_queue. Falls back to _output_path
        # so re-launching the app still finds the user's last planned
        # output if it still exists on disk.
        candidates = [
            p for p in (self._last_playable_path, self._output_path) if p
        ]
        for candidate in candidates:
            cand_file = Path(candidate)
            if cand_file.is_file() and cand_file.suffix.lower() == ".mp3":
                self._append_log(f"Toistetaan: {cand_file}")
                try:
                    if sys.platform == "win32":
                        os.startfile(str(cand_file))  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(cand_file)])
                    else:
                        subprocess.Popen(["xdg-open", str(cand_file)])
                except Exception as exc:
                    self._append_log_error(self._s("playback_failed").format(error=exc))
                return

        # Priority 2: synthesize a short preview from the input text.
        if self._input_mode == "text":
            if self._text_has_placeholder:
                text = ""
            else:
                text = self._text_widget.get("1.0", tk.END).strip()
            if not text:
                messagebox.showerror(self._s("error"), self._s("listen_no_text"))
                return
        else:
            # PDF mode: synthesize first paragraph as preview.
            if not self._pdf_path:
                messagebox.showerror(self._s("error"), self._s("no_pdf"))
                return
            try:
                book = parse_pdf(self._pdf_path)
                text = book.full_text
            except Exception as exc:
                messagebox.showerror(self._s("error"), str(exc))
                return
            if not text:
                messagebox.showerror(self._s("error"), self._s("listen_no_text"))
                return

        # Truncate long text to 1000 chars for preview.
        if len(text) > 1000:
            text = text[:1000]

        # Validate engine/voice.
        engine_id = self._current_engine_id()
        if not engine_id:
            messagebox.showerror(self._s("error"), self._s("select_tts_engine"))
            return

        if engine_id == "chatterbox_fi":
            # Chatterbox is too slow for an on-demand preview, and there's
            # no existing MP3 to play. Tell the user to run Muunna first.
            messagebox.showinfo(
                self._s("listen"),
                self._s("listen_convert_first"),
            )
            return

        engine = self._current_engine()
        if engine is None:
            messagebox.showerror(self._s("error"), self._s("engine_not_found"))
            return
        status = engine.check_status()
        if not status.available:
            messagebox.showerror(
                self._s("error"), f"{engine.display_name}: {status.reason}"
            )
            return
        voice = self._current_voice()
        if voice is None:
            messagebox.showerror(self._s("error"), self._s("select_voice"))
            return

        # Enter listening state.
        self._listening = True
        self._listen_btn.configure(state="disabled", text=self._s("listening"))
        self._convert_btn.configure(state="disabled")
        self._status_label_val.configure(text=self._s("listening"))

        threading.Thread(
            target=self._listen_worker,
            args=(text, engine, voice),
            daemon=True,
            name="listen",
        ).start()

    def _listen_worker(
        self, text: str, engine: TTSEngine, voice: Voice,
    ) -> None:
        import time
        tmp_path: Optional[str] = None
        try:
            lang = self._current_language()
            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None

            preview_len = len(text)
            self.after(0, lambda: self._append_log(
                f"Kuuntele: {engine.display_name}, {voice.display_name}"
            ))
            self.after(0, lambda: self._append_log(
                f"Teksti: {preview_len} merkkiä"
            ))

            tmp = tempfile.NamedTemporaryFile(
                prefix="listen_", suffix=".mp3", delete=False
            )
            tmp.close()
            tmp_path = tmp.name
            self._listen_temp_path = tmp_path

            t0 = time.perf_counter()
            self.after(0, lambda: self._append_log("Syntetisoidaan..."))

            engine.synthesize(
                text, tmp_path, voice.id, lang,
                lambda c, t, m: None,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )

            elapsed = time.perf_counter() - t0
            size_kb = os.path.getsize(tmp_path) / 1024
            self.after(0, lambda: self._append_log_success(
                f"\u2714 Synteesi valmis: {elapsed:.1f}s, {size_kb:.0f} KB"
            ))

            self.after(0, lambda: self._append_log("Toistetaan ääntä..."))

            # Play via the system's default audio player.
            if sys.platform == "win32":
                os.startfile(tmp_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", tmp_path])
            else:
                subprocess.Popen(["xdg-open", tmp_path])
            # Give the player time to open the file before cleanup.
            time.sleep(2)

        except Exception as exc:
            self.after(0, lambda: self._append_log_error(self._s("generic_error").format(error=exc)))
            self.after(0, lambda: self._status_label_val.configure(
                text=self._s("listen_error").format(error=exc)
            ))
        finally:
            # Don't delete temp file — the external player still needs it.
            # OS temp cleanup will handle it eventually.
            self._listen_temp_path = None
            self._listen_proc = None
            self.after(0, self._listen_finished)

    def _listen_finished(self) -> None:
        self._listening = False
        self._listen_btn.configure(state="normal", text=self._s("listen"))
        self._convert_btn.configure(state="normal")
        if not self._synth_running:
            self._status_label_val.configure(text=self._s("select_input_prompt"))

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _apply_loaded_config(self) -> None:
        """Restore persisted preferences to widgets."""
        cfg = self._user_cfg

        # Language. Empty config.language = first run — pick a default
        # based on the system locale so Finnish users get Finnish out
        # of the box and everyone else defaults to English.
        lang_code = cfg.language or app_config._default_language_from_locale()
        for label, code in LANGUAGES.items():
            if code == lang_code:
                self._lang_cb.set(label)
                break
        # Now that the language widget reflects the resolved default,
        # re-filter the engine list so the Moottori dropdown matches
        # (it was first populated inside _build_engine_bar before the
        # config had been applied, and may contain engines for 'fi'
        # when the user's actual default is 'en').
        self._populate_engine_list()

        # Engine.
        engine = get_engine(cfg.engine_id)
        if engine and engine.check_status().available:
            self._engine_cb.set(engine.display_name)
        elif cfg.engine_id == "chatterbox_fi":
            for lbl, eid in self._engine_display_to_id.items():
                if eid == "chatterbox_fi":
                    self._engine_cb.set(lbl)
                    break

        self._refresh_voice_list()

        # Voice.
        eng = self._current_engine()
        if eng and cfg.voice_id:
            for voice in eng.list_voices(self._current_language()):
                if voice.id == cfg.voice_id:
                    self._voice_cb.set(voice.display_name)
                    break

        # Speed.
        for label, val in SPEED_OPTIONS[self._ui_lang].items():
            if val == cfg.speed:
                self._speed_cb.set(label)
                break

        # Reference audio + voice description.
        if cfg.reference_audio:
            self._ref_audio_var.set(cfg.reference_audio)
        if cfg.voice_description:
            self._voice_desc_var.set(cfg.voice_description)

        # Input mode tab.
        if cfg.input_mode == "text":
            # Find the text tab name from the map.
            for tab_name, mode in self._tab_name_map.items():
                if mode == "text":
                    self._input_nb.set(tab_name)
                    self._input_mode_raw = "text"
                    break

        # Output mode.
        for label, val in OUTPUT_MODES[self._ui_lang].items():
            if val == cfg.output_mode:
                self._output_mode_cb.set(label)
                break

        # Log panel visibility (visible by default; hide if user previously chose to).
        if not cfg.log_panel_visible:
            self._toggle_log()

    def _save_current_config(self) -> None:
        """Snapshot current UI state into on-disk config."""
        cfg = self._user_cfg
        cfg.engine_id = self._current_engine_id() or "edge"
        voice = self._current_voice()
        cfg.voice_id = voice.id if voice else ""
        cfg.language = self._current_language()
        cfg.speed = SPEED_OPTIONS[self._ui_lang].get(self._speed_cb.get(), "+0%")
        cfg.reference_audio = self._ref_audio_var.get()
        cfg.voice_description = self._voice_desc_var.get()
        cfg.input_mode = self._input_mode
        cfg.output_mode = OUTPUT_MODES[self._ui_lang].get(
            self._output_mode_cb.get(), "single"
        )
        cfg.log_panel_visible = self._log_visible
        cfg.ui_language = self._ui_lang
        app_config.save(cfg)

    # ------------------------------------------------------------------
    # Conversion dispatch
    # ------------------------------------------------------------------

    def _on_sample_click(self) -> None:
        """Generate a short ~30 s sample from the start of the input.

        Lets the user audition the chosen voice/engine before committing
        to a full-book synthesis run that may take an hour or more. The
        sample lands next to the planned full-run MP3 with a ``_sample``
        suffix, so the user can A/B compare across engines without
        polluting their Documents folder.
        """
        from src.sample_helpers import (
            compute_sample_output_path,
            extract_sample_text,
        )

        if self._synth_running:
            return

        # Validate input — same rules as Muunna.
        if self._input_mode == "pdf" and not self._pdf_path:
            messagebox.showerror(self._s("error"), self._s("no_pdf"))
            return
        if self._input_mode == "text":
            content = "" if self._text_has_placeholder else self._text_widget.get("1.0", tk.END).strip()
            if not content:
                messagebox.showerror(self._s("error"), self._s("no_text"))
                return

        # Resolve full-run output path so we can derive the _sample sibling.
        if not self._output_path:
            self._auto_output_path()
        if not self._output_path:
            messagebox.showerror(self._s("error"), self._s("no_pdf"))
            return

        # Extract the sample text on the main thread (PDF parsing is
        # fast enough that blocking briefly is fine).
        if self._input_mode == "pdf":
            try:
                source_text = parse_book(self._pdf_path).full_text
            except Exception as exc:
                messagebox.showerror(self._s("error"), str(exc))
                return
        else:
            source_text = self._text_widget.get("1.0", tk.END).strip()
        sample_text = extract_sample_text(source_text)
        if not sample_text:
            messagebox.showerror(self._s("error"), self._s("no_text"))
            return

        sample_output_path = compute_sample_output_path(self._output_path)

        engine_id = self._current_engine_id()
        if not engine_id:
            messagebox.showerror(self._s("error"), self._s("select_tts_engine"))
            return

        # For registry engines, verify availability + voice.
        if engine_id != "chatterbox_fi":
            engine = self._current_engine()
            if engine is None:
                messagebox.showerror(self._s("error"), self._s("engine_not_found"))
                return
            status = engine.check_status()
            if not status.available:
                messagebox.showerror(
                    self._s("error"), f"{engine.display_name}: {status.reason}"
                )
                return
            voice = self._current_voice()
            if voice is None:
                messagebox.showerror(self._s("error"), self._s("select_voice"))
                return

        # Persist current selections (engine, voice, etc.) before kicking off.
        self._save_current_config()

        # Mark this run as a sample so _set_running_state shows the
        # right status text and _on_synth_done announces it correctly.
        self._is_sample_run = True
        self._sample_output_path = sample_output_path
        self._set_running_state()
        self._append_log(f"Sample: {len(sample_text)} chars → {sample_output_path}")

        if engine_id == "chatterbox_fi":
            self._start_chatterbox_subprocess(
                text_override=sample_text,
                output_basename_override=Path(sample_output_path).stem,
            )
        else:
            self._start_inprocess_engine(
                engine_id,
                text_override=sample_text,
                output_path_override=sample_output_path,
            )

        self.after(self.POLL_INTERVAL_MS, self._pump_events)

    def _on_convert_click(self) -> None:
        if self._synth_running:
            return

        # Reset the sample-run flag — a full Muunna press is never a sample.
        self._is_sample_run = False
        self._sample_output_path = None

        # Validate input.
        if self._input_mode == "pdf" and not self._pdf_path:
            messagebox.showerror(self._s("error"), self._s("no_pdf"))
            return
        if self._input_mode == "text":
            content = "" if self._text_has_placeholder else self._text_widget.get("1.0", tk.END).strip()
            if not content:
                messagebox.showerror(self._s("error"), self._s("no_text"))
                return

        if not self._output_path:
            self._auto_output_path()
        if not self._output_path:
            messagebox.showerror(self._s("error"), self._s("no_pdf"))
            return

        # Never overwrite an existing file — if the target already exists
        # (from a previous Muunna press this session, or from an earlier
        # run that auto-picked the same number), bump the filename to the
        # next free variant.
        self._bump_output_path_if_exists()

        engine_id = self._current_engine_id()
        if not engine_id:
            messagebox.showerror(self._s("error"), self._s("select_tts_engine"))
            return

        # For registry engines, verify availability.
        if engine_id != "chatterbox_fi":
            engine = self._current_engine()
            if engine is None:
                messagebox.showerror(self._s("error"), self._s("engine_not_found"))
                return
            status = engine.check_status()
            if not status.available:
                messagebox.showerror(
                    self._s("error"), f"{engine.display_name}: {status.reason}"
                )
                return
            voice = self._current_voice()
            if voice is None:
                messagebox.showerror(self._s("error"), self._s("select_voice"))
                return

        # Disk-space sanity check for the output drive.
        try:
            from src.system_checks import check_output_disk_space
            if self._input_mode == "pdf":
                try:
                    text_len = len(parse_book(self._pdf_path).full_text)
                except Exception:
                    text_len = 0
            else:
                text_len = len(
                    "" if self._text_has_placeholder
                    else self._text_widget.get("1.0", tk.END).strip()
                )
            ok, free_mb, need_mb = check_output_disk_space(
                self._output_path, text_len, engine_id,
            )
            if not ok:
                self._append_log_error(
                    self._s("disk_space_log").format(
                        free=f"{free_mb:.0f}", need=f"{need_mb:.0f}"
                    )
                )
                messagebox.showerror(
                    self._s("error"),
                    self._s("disk_space_msg").format(
                        free=f"{free_mb:.0f}", need=f"{need_mb:.0f}"
                    ),
                )
                return
            self._append_log(
                f"Levy: vapaa {free_mb:.0f} MB, arvioitu tarve {need_mb:.0f} MB"
            )
        except Exception as exc:
            self._append_log_warning(f"Disk check skipped: {exc}")

        # Persist settings before synthesis.
        self._save_current_config()

        # Enter running state.
        self._set_running_state()

        if engine_id == "chatterbox_fi":
            self._start_chatterbox_subprocess()
        else:
            self._start_inprocess_engine(engine_id)

        self.after(self.POLL_INTERVAL_MS, self._pump_events)

    # ------------------------------------------------------------------
    # In-process engine synthesis (Edge-TTS, Piper, VoxCPM2)
    # ------------------------------------------------------------------

    def _start_inprocess_engine(
        self,
        engine_id: str,
        text_override: Optional[str] = None,
        output_path_override: Optional[str] = None,
    ) -> None:
        """Start synthesis in a background thread for registry engines.

        ``text_override`` and ``output_path_override`` let the sample
        flow inject a truncated text snippet and a sibling ``_sample``
        output path without mutating the host's state.
        """
        output_path = output_path_override or self._output_path
        self._append_log(f"Engine: {engine_id}")
        self._append_log(f"Output: {output_path}")
        # Capture input on the main thread (thread-safe).
        if text_override is not None:
            input_mode = "text"
            pdf_path = None
            input_text = text_override
        else:
            input_mode = self._input_mode
            pdf_path = self._pdf_path
            input_text = None
            if input_mode == "text" and not self._text_has_placeholder:
                input_text = self._text_widget.get("1.0", tk.END).strip()
        voice = self._current_voice()
        voice_id = voice.id if voice else None
        language = self._current_language()
        speed = SPEED_OPTIONS[self._ui_lang].get(self._speed_cb.get(), "+0%")
        ref_audio = self._ref_audio_var.get() or None
        voice_desc = self._voice_desc_var.get() or None

        threading.Thread(
            target=self._run_inprocess,
            args=(engine_id, input_mode, pdf_path, input_text,
                  output_path, voice_id, language, speed,
                  ref_audio, voice_desc),
            daemon=True, name=f"tts-{engine_id}",
        ).start()

    def _run_inprocess(
        self, engine_id: str, input_mode: str,
        pdf_path: Optional[str], input_text: Optional[str],
        output_path: Optional[str], voice_id: Optional[str],
        language: str, speed: str,
        ref_audio: Optional[str], voice_desc: Optional[str],
    ) -> None:
        """Background thread for in-process TTS synthesis."""
        try:
            engine = get_engine(engine_id)
            if engine is None:
                raise RuntimeError(f"Engine '{engine_id}' not found.")

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line="Reading input...")
            )

            if input_mode == "pdf":
                assert pdf_path is not None
                book = parse_book(pdf_path)
                text = book.full_text
            else:
                text = input_text or ""

            if not text:
                raise ValueError("No text to synthesize.")

            if voice_id is None:
                voice_id = engine.default_voice(language)
                if voice_id is None:
                    raise RuntimeError("No voice available for the selected language.")

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line=f"Synthesizing ({len(text)} chars)...")
            )

            # Create output directory.
            out = Path(output_path) if output_path else (
                self._default_output_dir() / "output.mp3"
            )
            out.parent.mkdir(parents=True, exist_ok=True)

            def progress_cb(current: int, total: int, msg: str = "") -> None:
                pct = (current / total) if total > 0 else 0
                self._event_queue.put(ProgressEvent(
                    kind="chunk",
                    total_done=current,
                    total_chunks=total,
                    raw_line=msg or f"Chunk {current}/{total}",
                ))

            engine.synthesize(
                text=text,
                output_path=str(out),
                voice_id=voice_id,
                language=language,
                progress_cb=progress_cb,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )

            self._event_queue.put(ProgressEvent(
                kind="done",
                output_path=str(out),
                raw_line=f"Saved: {out}",
            ))

        except Exception as exc:
            self._event_queue.put(ProgressEvent(
                kind="error",
                raw_line=str(exc),
            ))

    # ------------------------------------------------------------------
    # Chatterbox subprocess relay
    # ------------------------------------------------------------------

    def _relay_chatterbox_events(self) -> None:
        """Drain ChatterboxRunner events into the GUI queue."""
        runner = self._chatterbox_runner
        if runner is None:
            return
        while not runner.finished:
            ev = runner.poll_event(timeout=0.2)
            if ev is not None:
                self._event_queue.put(ev)
        # Final drain.
        while True:
            ev = runner.poll_event(timeout=0.05)
            if ev is None:
                break
            self._event_queue.put(ev)

    # ------------------------------------------------------------------
    # Event pump (main thread — processes events from background threads)
    # ------------------------------------------------------------------

    def _pump_events(self) -> None:
        """Poll the event queue and update UI. Reschedules itself while running."""
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break

            # Route the line to the right severity color (yellow for
            # WARNING/FutureWarning/DeprecationWarning, red for ERROR /
            # Traceback, green for success markers). _log_line_by_severity
            # wraps _append_log_* with keyword heuristics so subprocess
            # stdout from Chatterbox gets the same treatment as in-process
            # events.
            if ev.raw_line:
                self._log_line_by_severity(ev.raw_line, kind=ev.kind)

            if ev.kind == "chunk":
                if ev.total_chunks > 0:
                    pct = ev.total_done / ev.total_chunks
                    self._progress_bar.set(pct)
                    self._status_label_val.configure(
                        text=f"{ev.total_done}/{ev.total_chunks}"
                    )
                    self._update_synthesizing_strip(ev)

            elif ev.kind in ("chapter_done", "full_done"):
                # A file was written — treat this as "done enough" for
                # progress-bar purposes. Chatterbox's final "assembling
                # MP3" phase doesn't produce chunk events, so the bar
                # would otherwise stay stuck at the last chunk count.
                self._progress_bar.set(1.0)
                self._chatterbox_last_mp3 = ev.output_path

            elif ev.kind == "done":
                # Force the bar to full and flush pending paint events
                # so the user sees 100% when Valmis! appears — not the
                # last partial chunk value left over from progress pulses.
                self._progress_bar.set(1.0)
                self.update_idletasks()
                # If this was Chatterbox, move its output to where the user
                # asked for it. Chatterbox doesn't honor a --output-file path.
                self._finalize_chatterbox_output_if_needed()
                if self._is_sample_run:
                    # Sample run: announce the sample path but do NOT
                    # mutate self._output_path — the user's planned full
                    # run target stays as it was so Muunna won't bump.
                    # Prefer the flat sample path set by the finalizer
                    # (Chatterbox reports its nested `00_full.mp3` in
                    # ev.output_path, which we've already relocated).
                    sample_path = (
                        self._sample_output_path or ev.output_path or ""
                    )
                    self._status_label_val.configure(
                        text=self._s("sample_run_saved").format(path=sample_path)
                    )
                    if sample_path:
                        self._log_success_summary(sample_path)
                        self._last_playable_path = sample_path
                    self._is_sample_run = False
                    self._sample_output_path = None
                else:
                    self._status_label_val.configure(text=self._s("done"))
                    if ev.output_path:
                        self._output_path = ev.output_path
                    if self._output_path:
                        self._log_success_summary(self._output_path)
                        self._last_playable_path = self._output_path
                    self._update_done_strip(self._output_path)
                self._set_idle_state()
                # One final set(1.0) after idle-state toggles anything
                # that might have reset the widget.
                self._progress_bar.set(1.0)
                self.update_idletasks()
                return  # Stop pumping.

            elif ev.kind == "error":
                self._fail(ev.raw_line or "Unknown error")
                return  # Stop pumping.

        # Reschedule if still running.
        if self._synth_running:
            self.after(self.POLL_INTERVAL_MS, self._pump_events)

    def _log_line_by_severity(self, line: str, kind: str = "log") -> None:
        """Append a log line and color-code it based on kind + keywords.

        Called from both the main pump loop and the _handle_event mixin
        method so Chatterbox subprocess stdout gets the same treatment.
        """
        upper = line.upper()
        if (kind == "error"
            or "ERROR:" in upper
            or "TRACEBACK" in upper
            or "\u2718" in line):
            self._append_log_error(line)
        elif ("WARNING" in upper
              or "WARN:" in upper
              or "FUTUREWARNING" in upper
              or "DEPRECATIONWARNING" in upper):
            self._append_log_warning(line)
        elif (
            "\u2714" in line
            or "VALMIS" in upper
            or line.startswith("[done]")
            or _PROGRESS_SUCCESS_RE.search(line) is not None
        ):
            self._append_log_success(line)
        else:
            self._append_log(line)

    def _finalize_chatterbox_output_if_needed(self) -> None:
        """Copy Chatterbox's per-chapter MP3 to the user's target path.

        Chatterbox writes `{out_dir}/{book_stem}/01_Title.mp3` (and
        `00_full.mp3` for multi-chapter books). The user requested a
        single file at `self._output_path` — copy the result there and
        log the final location.
        """
        src = getattr(self, "_chatterbox_last_mp3", "")
        # In sample mode, target the flat sibling path instead of the
        # planned full-run output so the sample lands at
        # `<out_dir>/<base>_sample.mp3` rather than nested under
        # `<out_dir>/<base>_sample_xyz/00_full.mp3`.
        target = (
            self._sample_output_path if self._is_sample_run
            else self._output_path
        )
        if not src or not target:
            return

        src_path = Path(src)
        if not src_path.is_absolute():
            # Chatterbox reports relative paths for chapters; resolve
            # against the out_dir passed to the runner.
            runner = self._chatterbox_runner
            if runner is None:
                return
            src_path = Path(runner.out_dir) / src_path
            if not src_path.exists():
                # Chatterbox nests: {out_dir}/{book_stem}/NN_*.mp3
                # Try finding the newest .mp3 under out_dir.
                candidates = sorted(
                    Path(runner.out_dir).rglob("*.mp3"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    src_path = candidates[0]

        dst_path = Path(target)
        if src_path.resolve() == dst_path.resolve():
            return  # Already at target

        if not src_path.exists():
            self._append_log(f"Chatterbox output not found: {src_path}")
            return

        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            self._append_log(f"Saved: {dst_path}")
            self._chatterbox_last_mp3 = ""
        except OSError as exc:
            self._append_log(f"Could not move output to {dst_path}: {exc}")
            return

        # Do not delete the nested Chatterbox folder after a sample run.
        # It lives under dist/audiobook/<stem>/ and may hold a full-book
        # .chunks/ WAV cache; rmtree here wiped user data when sample and
        # long jobs shared the same output tree.

    # ------------------------------------------------------------------
    # Running/idle state management
    # ------------------------------------------------------------------

    def _set_running_state(self) -> None:
        self._synth_running = True
        self._synth_started_at = datetime.now()
        # Reset cancel state from any prior run — without this, a cancel
        # signal from the previous run can leak into the new one.
        self._cancel_requested = False
        self._cancel_flag.clear()
        self._convert_btn.configure(state="disabled")
        self._listen_btn.configure(state="disabled")
        self._sample_btn.configure(state="disabled")
        # Disable Open-folder mid-run: it would point at the previous file
        # and confuse the user about which output is current.
        self._open_folder_btn.configure(state="disabled")
        self._cancel_btn.grid()
        self._progress_bar.set(0)
        self._status_label_val.configure(
            text=self._s("making_sample") if self._is_sample_run
            else self._s("converting")
        )
        # Clear stale ETA from the previous run before the new ETA arrives.
        self._eta_label.configure(text="")
        self._clear_log()

    def _set_idle_state(self) -> None:
        self._synth_running = False
        self._convert_btn.configure(state="normal")
        self._listen_btn.configure(state="normal")
        self._sample_btn.configure(state="normal")
        self._cancel_btn.grid_remove()
        self._open_folder_btn.configure(state="normal")
        self._chatterbox_runner = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _on_cancel_click(self) -> None:
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()
        self._cancel_requested = True
        self._cancel_btn.configure(text=self._s("cancelling"), state="disabled")

    # ------------------------------------------------------------------
    # Output folder
    # ------------------------------------------------------------------

    def _open_output_folder(self) -> None:
        if not self._output_path:
            return
        folder = Path(self._output_path)
        if folder.is_file():
            folder = folder.parent
        if not folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _toggle_log(self) -> None:
        if self._log_visible:
            self._log_frame.grid_remove()
            self._log_toggle_btn.configure(text=self._s("show_log"))
        else:
            self._log_frame.grid()
            self._log_toggle_btn.configure(text=self._s("hide_log"))
        self._log_visible = not self._log_visible

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert(tk.END, line + "\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state="disabled")

    def _append_log_styled(self, line: str, severity: str = "info") -> None:
        """Append a log line with a colored/bold tag based on severity.

        severity values:
          success → bold green
          warning → bold amber/yellow (for issues, not failures)
          error   → bold red
          info    → default (no styling)
        """
        self._log_text.configure(state="normal")
        start_index = self._log_text.index(tk.END + "-1c")
        self._log_text.insert(tk.END, line + "\n")
        end_index = self._log_text.index(tk.END + "-1c")

        palette = {
            "success": ("#2e7d32", ("Consolas", 12, "bold")),   # dark green
            "warning": ("#b8860b", ("Consolas", 12, "bold")),   # dark goldenrod
            "error":   ("#c62828", ("Consolas", 12, "bold")),   # dark red
        }
        if severity in palette:
            color, font_tuple = palette[severity]
            try:
                inner = self._log_text._textbox  # type: ignore[attr-defined]
                inner.tag_configure(
                    f"log_{severity}",
                    foreground=color, font=font_tuple,
                )
                inner.tag_add(f"log_{severity}", start_index, end_index)
            except Exception:
                pass  # Fall back to plain text if tag setup fails

        self._log_text.see(tk.END)
        self._log_text.configure(state="disabled")

    def _append_log_success(self, line: str) -> None:
        self._append_log_styled(line, severity="success")

    def _append_log_warning(self, line: str) -> None:
        self._append_log_styled(line, severity="warning")

    def _append_log_error(self, line: str) -> None:
        self._append_log_styled(line, severity="error")

    def _log_success_summary(self, saved_path: str, elapsed_s: Optional[float] = None) -> None:
        """Write a friendly final summary line to the log after a job finishes."""
        size_note = ""
        try:
            size_kb = Path(saved_path).stat().st_size / 1024
            if size_kb > 1024:
                size_note = f" ({size_kb / 1024:.1f} MB)"
            else:
                size_note = f" ({size_kb:.0f} KB)"
        except OSError:
            pass

        time_note = ""
        if elapsed_s is not None and elapsed_s > 0:
            if elapsed_s >= 60:
                time_note = f" \u2014 {int(elapsed_s // 60)}m {int(elapsed_s % 60)}s"
            else:
                time_note = f" \u2014 {elapsed_s:.1f}s"

        if self._ui_lang == "fi":
            msg = f"\u2714 Valmis! Tallennettu: {saved_path}{size_note}{time_note}"
        else:
            msg = f"\u2714 Done! Saved: {saved_path}{size_note}{time_note}"
        self._append_log_success(msg)

    # ------------------------------------------------------------------
    # Error helper
    # ------------------------------------------------------------------

    def _fail(self, message: str) -> None:
        self._set_idle_state()
        self._status_label_val.configure(text=message)
        # Revert the sticky strip: no green "done", no lingering progress.
        if hasattr(self, "_status_strip_frame"):
            self._set_status_strip("idle")
        messagebox.showerror(self._s("error"), message)

    def _request_cancel(self) -> None:  # type: ignore[override]
        """Override mixin's cancel to also hide the status strip."""
        super()._request_cancel()
        if hasattr(self, "_status_strip_frame"):
            self._set_status_strip("idle")


# ---------------------------------------------------------------------------
# Self-test (for CI / frozen-exe verification)
# ---------------------------------------------------------------------------


def assert_true(condition: bool, msg: str = "") -> None:
    """Raise AssertionError if *condition* is falsy."""
    if not condition:
        raise AssertionError(msg or "assertion failed")


def self_test() -> int:
    """Headless sanity check: construct + destroy the window, verify core functionality."""
    errors: list[str] = []

    def check(label: str, fn) -> None:  # noqa: ANN001
        try:
            fn()
            print(f"[self-test] \u2713 {label}", flush=True)
        except Exception as exc:
            errors.append(f"{label}: {exc!r}")
            print(f"[self-test] \u2717 {label}: {exc!r}", flush=True)

    try:
        # -- Engine registry --------------------------------------------------
        engines = list_engines()
        print(
            f"[self-test] engines registered: {[e.id for e in engines]}",
            flush=True,
        )

        for engine in engines:
            check(
                f"engine '{engine.id}' status",
                lambda e=engine: e.check_status(),
            )

        # -- Module imports ----------------------------------------------------
        check("pdf_parser import", lambda: __import__("src.pdf_parser"))
        check("tts_engine import", lambda: __import__("src.tts_engine"))

        # -- App creation ------------------------------------------------------
        app = UnifiedApp()
        app.update_idletasks()
        print(f"[self-test] window title={app.title()!r}", flush=True)

        # -- Engine dropdown ---------------------------------------------------
        values = list(app._engine_cb.cget("values"))
        check(
            "engine dropdown populated",
            lambda: assert_true(len(values) >= 1, f"got {len(values)} engines"),
        )
        print(f"[self-test] engine dropdown: {values}", flush=True)

        # -- Settings widgets --------------------------------------------------
        check("language combobox", lambda: app._lang_cb.cget("values"))
        check("speed combobox", lambda: app._speed_cb.cget("values"))
        check("voice combobox", lambda: app._voice_cb.cget("values"))

        # -- Input tabs --------------------------------------------------------
        check(
            "input tabs exist",
            lambda: assert_true(
                hasattr(app, "_input_nb"), "missing _input_nb tabview"
            ),
        )
        tab_names = list(app._tab_name_map.keys())
        check(
            "input tabs switchable",
            lambda: [app._input_nb.set(t) for t in tab_names],
        )
        print(f"[self-test] input tabs: {tab_names}", flush=True)

        # -- Text widget -------------------------------------------------------
        check(
            "text widget input",
            lambda: (
                app._text_widget.delete("1.0", "end"),
                app._text_widget.insert("1.0", "Self-test input"),
            ),
        )

        # -- Config load/save --------------------------------------------------
        from src import app_config

        check("config load", lambda: app_config.load())
        cfg = app_config.load()
        check("config save", lambda: app_config.save(cfg))

        # -- Cleanup -----------------------------------------------------------
        app.destroy()

        if errors:
            print(f"\n[self-test] FAILED \u2014 {len(errors)} error(s):", flush=True)
            for e in errors:
                print(f"  \u2022 {e}", flush=True)
            return 1

        print("[self-test] ALL CHECKS PASSED", flush=True)
        return 0

    except Exception as exc:
        print(f"[self-test] FATAL: {exc!r}", flush=True, file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Launch the unified GUI application."""
    app = UnifiedApp()
    app.mainloop()


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--self-test" in argv:
        return self_test()
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
