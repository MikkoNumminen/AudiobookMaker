"""Unified AudiobookMaker GUI — single window that replaced gui.py and the retired launcher.py.

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

import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

import customtkinter as ctk

from src import _audio_player, app_config, error_log
from src.auto_updater import (
    check_for_update, download_update, apply_update,
    APP_VERSION, GITHUB_REPO, UpdateInfo,
    is_post_update_launch, _no_update, extract_whats_new,
)
from src import gui_style
from src.gui_builders import (
    build_action_row,
    build_engine_bar,
    build_header_bar,
    build_settings_frame,
)
from src.gui_synth_mixin import SynthMixin
from src.gui_update_mixin import UpdateMixin
from src.ffmpeg_path import get_ffmpeg_dir, setup_ffmpeg_path
from src.launcher_bridge import ChatterboxRunner, ProgressEvent
from src.pdf_parser import parse_pdf, BookMetadata, Chapter, ParsedBook
from src.epub_parser import parse_epub
from src.synthesis_orchestrator import (
    InprocessRequest,
    default_output_dir,
    next_available_numbered_path,
    parse_book,
    run_inprocess_synthesis,
    suggest_output_path,
)
try:
    from src import duration_estimate as _duration_estimate
except Exception:  # pragma: no cover — module might be stubbed in parallel dev
    _duration_estimate = None  # type: ignore
from src.tts_base import EngineStatus, TTSEngine, Voice, get_engine, list_engines
from src.version_manifest import is_engine_load_failure
from src.tts_engine import TTSConfig, chapters_to_speech
from src.voice_pack import (
    PACK_ARCHIVE_SUFFIX,
    VoicePack,
    VoicePackError,
    base_model_requirements,
    default_voice_packs_root,
    export_pack,
    install_pack_source,
    list_packs,
)

# Single import point for every TTS engine. See src/engine_registry.py
# for the list (developer-only engines like VoxCPM2 are guarded there).
from src import engine_registry  # noqa: F401


logger = logging.getLogger(__name__)


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
# Chatterbox language routing + voice list moved to src.tts_chatterbox_bridge
# so every engine exposes its metadata through the same TTSEngine contract.

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
        "header_tagline": "Kirjasi, luettuna.",
        "section_voice": "Ääni",
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
        "save_error_log": "Tallenna virheloki…",
        "save_error_log_done": "Virheloki tallennettu: {path} — lähetä tämä tiedosto kehittäjälle.",
        "save_error_log_empty": "Lokia ei vielä ole — aja jotain ensin.",
        "log_file_filter": "Lokitiedosto",
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
        "whats_new": "Mikä on uutta",
        "update_downloading": "Ladataan päivitystä...",
        "update_installing": "Asennetaan päivitys...",
        "update_failed": "Päivitys epäonnistui.",
        "update_error_detail": "Päivitys epäonnistui: {error}",
        "listen": "Esikuuntele",
        "play": "Esikuuntele",
        "stop": "Pys\u00e4yt\u00e4",
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
        "chatterbox_venv_missing": "Chatterbox-moottoria ei ole asennettu. Asenna se ikkunan oikeasta yläkulmasta löytyvällä \"Asenna moottoreita…\" -painikkeella.",
        "engine_repair_title": "Moottori vaatii korjauksen",
        "engine_repair_prompt": "Chatterbox-moottoria ei voitu ladata — sen ympäristön versiot ovat todennäköisesti yhteensopimattomat. Avataanko \"Asenna moottoreita…\" ja korjataan se nyt?",
        "engine_installing_wait": "Moottoria asennetaan parhaillaan. Odota, että asennus valmistuu, ennen kuin aloitat muunnoksen.",
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
        "chunk_chars_label": "Chatterbox-palan pituus (merkki\u00e4):",
        "import_pack_btn": "Tuo \u00e4\u00e4nipaketti\u2026",
        "import_pack_title": "Valitse \u00e4\u00e4nipakettitiedosto",
        "import_pack_success": "\u00c4\u00e4nipaketti tuotu: {name}",
        "import_pack_error": "\u00c4\u00e4nipaketin tuonti ep\u00e4onnistui: {error}",
        "import_pack_base_models": "Huom: t\u00e4m\u00e4 \u00e4\u00e4ni tarvitsee koneella: {models} (ladataan ensimm\u00e4isell\u00e4 k\u00e4yt\u00f6ll\u00e4)",
        "export_pack_btn": "Vie \u00e4\u00e4nipaketti\u2026",
        "export_pack_title": "Tallenna \u00e4\u00e4nipaketti",
        "export_pack_success": "\u00c4\u00e4nipaketti viety: {path}",
        "export_pack_error": "\u00c4\u00e4nipaketin vienti ep\u00e4onnistui: {error}",
        "export_pack_no_selection": "Valitse ensin tuotu \u00e4\u00e4nipaketti \u00c4\u00e4ni-valikosta.",
        "pack_archive_filter": "\u00c4\u00e4nipaketti (.zip)",
        "all_files_filter": "Kaikki tiedostot",
        "voice_pack_tag": "\u00e4\u00e4nipaketti",
        "voice_pack_unresolved": "Valitun \u00e4\u00e4nipaketin lataus ep\u00e4onnistui. Tuo \u00e4\u00e4nipaketti uudelleen ja valitse se \u00c4\u00e4ni-valikosta.",
        "report_bug_btn": "Ilmoita bugista\u2026",
    },
    "en": {
        "window_title": "AudiobookMaker",
        "header_tagline": "Your books, spoken.",
        "section_voice": "Voice",
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
        "save_error_log": "Save error log…",
        "save_error_log_done": "Error log saved: {path} — send this file to the developer.",
        "save_error_log_empty": "No log yet — run something first.",
        "log_file_filter": "Log file",
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
        "whats_new": "What's new",
        "update_downloading": "Downloading update...",
        "update_installing": "Installing update...",
        "update_failed": "Update failed.",
        "update_error_detail": "Update failed: {error}",
        "listen": "Preview",
        "play": "Preview",
        "stop": "Stop",
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
        "chatterbox_venv_missing": "Chatterbox engine is not installed. Install it via the \"Install engines…\" button in the top-right corner of the main window.",
        "engine_repair_title": "Engine needs repair",
        "engine_repair_prompt": "The Chatterbox engine could not load — its environment likely has incompatible package versions. Open \"Install engines…\" and repair it now?",
        "engine_installing_wait": "An engine is installing right now. Wait for the install to finish before starting a conversion.",
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
        "chunk_chars_label": "Chatterbox chunk size (chars):",
        "import_pack_btn": "Import voice pack\u2026",
        "import_pack_title": "Select voice pack file",
        "import_pack_success": "Voice pack imported: {name}",
        "import_pack_error": "Voice pack import failed: {error}",
        "import_pack_base_models": "Note: this voice needs {models} on this computer (downloads on first use)",
        "export_pack_btn": "Export voice pack\u2026",
        "export_pack_title": "Save voice pack",
        "export_pack_success": "Voice pack exported: {path}",
        "export_pack_error": "Voice pack export failed: {error}",
        "export_pack_no_selection": "Select an imported voice pack from the Voice menu first.",
        "pack_archive_filter": "Voice pack (.zip)",
        "all_files_filter": "All files",
        "voice_pack_tag": "voice pack",
        "voice_pack_unresolved": "Couldn't load the selected voice pack. Re-import it and pick it again from the Voice menu.",
        "report_bug_btn": "Report a bug\u2026",
    },
}


from src.gui_engine_dialog import EngineManagerDialog, EngineManagerView  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class UnifiedApp(SynthMixin, UpdateMixin, ctk.CTk):
    """Single-window AudiobookMaker GUI."""

    POLL_INTERVAL_MS = 100

    def after(self, ms, func=None, *args):  # type: ignore[override]
        """Override CTk.after so destroy() can cancel exactly our own callbacks.

        Wraps the user's callback to discard the after-ID from the tracking
        set when it fires naturally, preventing unbounded growth across a
        long-running app session.
        """
        if func is None:
            # ``after(ms)`` with no callback is a Tk poll; nothing to wrap.
            return super().after(ms)

        scheduled = self.__dict__.setdefault("_scheduled_afters", set())
        after_id_box: list[str | None] = [None]

        def _wrapped():
            if after_id_box[0] is not None:
                scheduled.discard(after_id_box[0])
            return func(*args)

        after_id = super().after(ms, _wrapped)
        if after_id:
            after_id_box[0] = str(after_id)
            scheduled.add(after_id_box[0])
        return after_id

    def after_idle(self, func, *args):  # type: ignore[override]
        """Override CTk.after_idle so destroy() can cancel exactly our own callbacks.

        Wraps the callback to discard the after-ID from the tracking set
        when it fires naturally.
        """
        scheduled = self.__dict__.setdefault("_scheduled_afters", set())
        after_id_box: list[str | None] = [None]

        def _wrapped():
            if after_id_box[0] is not None:
                scheduled.discard(after_id_box[0])
            return func(*args)

        after_id = super().after_idle(_wrapped)
        if after_id:
            after_id_box[0] = str(after_id)
            scheduled.add(after_id_box[0])
        return after_id

    def after_cancel(self, id_):  # type: ignore[override]
        """Override CTk.after_cancel to keep ``_scheduled_afters`` consistent
        when callbacks are cancelled outside of ``destroy()``."""
        super().after_cancel(id_)
        scheduled = self.__dict__.get("_scheduled_afters")
        if scheduled is not None and id_ is not None:
            scheduled.discard(str(id_))

    def __init__(self) -> None:
        super().__init__()
        # Tracking set is populated by overridden after() / after_idle().
        # An explicit setdefault() in those overrides covers the bootstrap
        # case where CTk's own __init__ calls self.after() before this body
        # runs, so this assignment is the conventional convergence point
        # rather than a hard requirement.
        # Note: this wipes any IDs CTk scheduled during super().__init__() that
        # are still pending.  Those are CTk-internal callbacks (e.g.
        # _windows_set_titlebar_icon) that we must NOT cancel in destroy() —
        # doing so would TclError on CTk's deletecommand path.  The wipe is
        # therefore correct: after this point _scheduled_afters tracks only
        # our own callbacks.
        self._scheduled_afters: set[str] = set()
        # Set in destroy(); read by report_callback_exception to suppress the
        # error dialog for the transient TclErrors a closing window produces.
        self._closing = False

        # Diagnostic logging. Idempotent with src/main.py's install(), so the
        # GUI still captures failures when launched via gui_unified.run()
        # directly (dev / tests / --self-test) rather than through main().
        error_log.install()
        logger.info("AudiobookMaker %s — GUI window starting", APP_VERSION)

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
        # _on_convert_click and the "done" branch of _pump_events so the
        # success path can show "Sample saved" instead of the generic "Done".
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
        except Exception as exc:
            logger.debug("SetCurrentProcessExplicitAppUserModelID failed", exc_info=exc)

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

        # Hero tagline — rebuild with the fresh localized subtitle so
        # the header flips languages when the user toggles Suomi/English.
        if hasattr(self, "_hero_tagline"):
            self._hero_tagline.configure(text=self._hero_tagline_text())

        # Voice-section mini-title on the engine bar card.
        if hasattr(self, "_engine_section_lbl"):
            self._engine_section_lbl.configure(text=s("section_voice"))

        # Chatterbox chunk size label — relabel on language toggle.
        if hasattr(self, "_chunk_chars_label"):
            self._chunk_chars_label.configure(text=s("chunk_chars_label"))

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

        # Voice pack import/export buttons (visible in Settings regardless of engine).
        self._import_pack_btn.configure(text=s("import_pack_btn"))
        self._export_pack_btn.configure(text=s("export_pack_btn"))

        # Report-a-bug button (always visible in Settings).
        if hasattr(self, "_report_bug_btn"):
            self._report_bug_btn.configure(text=s("report_bug_btn"))

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
            self._refresh_listen_btn_label()
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

        # Save-error-log button (same toggle row).
        if hasattr(self, "_save_log_btn"):
            self._save_log_btn.configure(text=s("save_error_log"))

        # Update banner.
        if self._pending_update and self._pending_update.available:
            self._update_label.configure(
                text=s("update_available").format(
                    version=self._pending_update.latest_version
                )
            )
            self._update_btn.configure(text=s("update_now"))
            self._update_browser_btn.configure(text=s("update_download_manually"))
            if getattr(self, "_update_whatsnew_toggle", None) is not None:
                self._update_whatsnew_toggle.configure(text=self._whatsnew_toggle_text())

        # UI lang combobox — keep it in sync.
        self._ui_lang_cb.set("Suomi" if self._ui_lang == "fi" else "English")

        # Engine manager view is built once and lives in a stacked grid,
        # so its strings don't auto-refresh. Push the new language through.
        if hasattr(self, "_settings_view"):
            self._settings_view.set_language(self._ui_lang)

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
        build_header_bar(self, main, row=1)
        self._build_input_tabs(main, row=2)
        build_engine_bar(self, main, row=3)
        build_action_row(self, main, row=4)
        self._build_status_strip(main, row=5)
        build_settings_frame(self, main, row=6)  # header at row=6, body at row=7
        self._build_log_panel(main, row=8, stretch_row=9)

        # Settings view — in-place alternative to the old Toplevel popup.
        # Stacked in the same grid cell; switched via tkraise().
        self._settings_view = EngineManagerView(
            self._view_container, ui_lang=self._ui_lang,
            on_back=self._show_main_view, host=self,
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

    def _hero_tagline_text(self) -> str:
        """Build the hero tagline string: localized subtitle + app version.

        Kept as a helper so the language-toggle handler can refresh the
        label without duplicating the format.
        """
        return f"{self._s('header_tagline')} \u00b7 v{APP_VERSION}"

    # ---- Primary action row (big Muunna + secondaries + progress) ----

    # ---- Sticky status strip (between progress and log) ---------------

    def _build_status_strip(self, parent: ctk.CTkFrame, row: int) -> None:
        """Build the sticky one-line status strip.

        Hidden by default (via ``grid_remove``). The three visible states
        (ready / synthesizing / done) set both text and background color
        via :meth:`_set_status_strip`.
        """
        self._status_strip_frame = ctk.CTkFrame(
            parent, fg_color=gui_style.INFO, corner_radius=gui_style.RADIUS_MD,
        )
        self._status_strip_frame.grid(
            row=row, column=0, sticky="ew", pady=(0, gui_style.PAD_SM)
        )
        self._status_strip_frame.columnconfigure(0, weight=1)
        self._status_strip_label = ctk.CTkLabel(
            self._status_strip_frame, text="", text_color="white", anchor="w",
            font=gui_style.font_body(),
        )
        self._status_strip_label.grid(
            row=0, column=0, sticky="ew",
            padx=gui_style.PAD_MD, pady=gui_style.PAD_XS + 2,
        )
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
            parent,
            fg_color=gui_style.SUCCESS,
            corner_radius=gui_style.RADIUS_MD,
        )
        self._update_banner.grid(
            row=row, column=0, sticky="ew", pady=(0, gui_style.PAD_SM)
        )
        self._update_banner.columnconfigure(0, weight=1)

        self._update_label = ctk.CTkLabel(
            self._update_banner, text="", text_color="white",
            font=gui_style.font_body(),
        )
        self._update_label.grid(
            row=0, column=0, sticky="w",
            padx=(gui_style.PAD_MD, gui_style.PAD_SM),
            pady=gui_style.PAD_SM,
        )

        self._update_btn = ctk.CTkButton(
            self._update_banner, text=self._s("update_now"),
            command=self._on_update_click,
            font=gui_style.font_button(),
            corner_radius=gui_style.RADIUS_SM,
            image=gui_style.icon("download", size=16),
            compound="left",
        )
        self._update_btn.grid(
            row=0, column=1,
            padx=(0, gui_style.PAD_XS), pady=gui_style.PAD_XS,
        )

        # Secondary "open in browser" fallback — always visible when the
        # banner is shown. Works even if the in-app updater is broken
        # (as happened on v3.3.1); gives the user a visible escape hatch
        # without needing to know about the Releases page.
        # Darker green hovers keep the button readable on the SUCCESS bg
        # when the user mouses over. Tied to gui_style.SUCCESS via a
        # small deepening; kept as literals because CTk doesn't expose a
        # darken() helper.
        self._update_browser_btn = ctk.CTkButton(
            self._update_banner,
            text=self._s("update_download_manually"),
            command=self._on_update_browser_click,
            font=gui_style.font_button(),
            corner_radius=gui_style.RADIUS_SM,
            fg_color="transparent", border_width=1, border_color="white",
            text_color="white", hover_color=("#156326", "#0f7a2a"),
        )
        self._update_browser_btn.grid(
            row=0, column=2,
            padx=(0, gui_style.PAD_SM), pady=gui_style.PAD_XS,
        )

        # Expandable "What's new" — the release notes the updater already
        # fetched (UpdateInfo.release_notes) but never showed. Collapsed by
        # default so the banner stays one line; the toggle (row 1) and the
        # notes (row 2) are only gridded when a release actually carries
        # notes — see _show_update_banner. Both span the three columns above.
        self._whatsnew_expanded = False
        self._update_whatsnew_toggle = ctk.CTkButton(
            self._update_banner,
            text="",
            command=self._toggle_whatsnew,
            font=gui_style.font_button(),
            fg_color="transparent",
            hover_color=("#156326", "#0f7a2a"),
            text_color="white",
            anchor="w",
            corner_radius=gui_style.RADIUS_SM,
        )
        self._update_whatsnew_toggle.grid(
            row=1, column=0, columnspan=3, sticky="w",
            padx=(gui_style.PAD_MD, gui_style.PAD_SM), pady=(0, gui_style.PAD_XS),
        )
        self._update_whatsnew_toggle.grid_remove()

        self._update_whatsnew_text = ctk.CTkLabel(
            self._update_banner,
            text="",
            text_color="white",
            font=gui_style.font_body(),
            justify="left",
            anchor="w",
            wraplength=520,
        )
        self._update_whatsnew_text.grid(
            row=2, column=0, columnspan=3, sticky="w",
            padx=(gui_style.PAD_MD, gui_style.PAD_MD), pady=(0, gui_style.PAD_SM),
        )
        self._update_whatsnew_text.grid_remove()

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

    def _whatsnew_toggle_text(self) -> str:
        """Label for the What's-new expander, with a ▾/▴ affordance."""
        arrow = "▴" if self._whatsnew_expanded else "▾"
        return f"{self._s('whats_new')} {arrow}"

    def _toggle_whatsnew(self) -> None:
        """Expand/collapse the release notes shown under the update banner."""
        self._whatsnew_expanded = not self._whatsnew_expanded
        if self._whatsnew_expanded:
            self._update_whatsnew_text.grid()
        else:
            self._update_whatsnew_text.grid_remove()
        self._update_whatsnew_toggle.configure(text=self._whatsnew_toggle_text())

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
            pdf_frame, text="Selaa\u2026", command=self._browse_pdf, width=100,
            image=gui_style.icon("folder", size=16),
            compound="left",
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
        self._text_widget.bind("<KeyRelease>", self._on_text_keyrelease)

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
            self._update_action_buttons_state()

    def _on_text_focus_out(self, _event: object = None) -> None:
        content = self._text_widget.get("1.0", tk.END).strip()
        if not content:
            self._text_widget.insert("1.0", self._text_placeholder)
            self._text_widget.configure(text_color="gray")
            self._text_has_placeholder = True
        self._update_action_buttons_state()

    def _on_text_keyrelease(self, _event: object = None) -> None:
        """Keep the action-row gating honest while the user types."""
        self._update_action_buttons_state()

    # ---- 2. Settings frame --------------------------------------------

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
        # Secondary-style toggle button above the log surface card —
        # matches the Sample/Listen/Open folder visual vocabulary so the
        # toolbar reads as a consistent row of utility controls.
        toggle_frame = ctk.CTkFrame(parent, fg_color="transparent")
        toggle_frame.grid(
            row=row, column=0, sticky="ew", pady=(gui_style.PAD_XS, 0)
        )

        self._log_toggle_btn = ctk.CTkButton(
            toggle_frame, text="Piilota loki", command=self._toggle_log,
            width=160,
            font=gui_style.font_button(),
            fg_color=gui_style.BTN_SECONDARY_BG,
            hover_color=gui_style.BTN_SECONDARY_HOVER,
            text_color=gui_style.TEXT_PRIMARY,
            border_width=1,
            border_color=gui_style.BORDER_SUBTLE,
            corner_radius=gui_style.RADIUS_SM,
            image=gui_style.icon("list", size=16),
            compound="left",
        )
        self._log_toggle_btn.grid(row=0, column=0, sticky="w")

        # Save-error-log button: exports the diagnostic log so a user can hand
        # it to the developer after a failure. Sits right next to the log
        # toggle — where users look when something goes wrong.
        self._save_log_btn = ctk.CTkButton(
            toggle_frame, text=self._s("save_error_log"),
            command=self._save_error_log,
            width=160,
            font=gui_style.font_button(),
            fg_color=gui_style.BTN_SECONDARY_BG,
            hover_color=gui_style.BTN_SECONDARY_HOVER,
            text_color=gui_style.TEXT_PRIMARY,
            border_width=1,
            border_color=gui_style.BORDER_SUBTLE,
            corner_radius=gui_style.RADIUS_SM,
            image=gui_style.icon("download", size=16),
            compound="left",
        )
        self._save_log_btn.grid(
            row=0, column=1, sticky="w", padx=(gui_style.PAD_SM, 0)
        )

        # Surface card around the log textbox — same 1px-border + elevated
        # fill treatment as the settings panel and engine bar so every
        # major section reads at the same elevation level.
        self._log_frame = ctk.CTkFrame(
            parent,
            fg_color=gui_style.BG_SURFACE_1,
            border_width=1,
            border_color=gui_style.BORDER_SUBTLE,
            corner_radius=gui_style.RADIUS_MD,
        )
        # Placed in stretch_row so it can grow.
        self._log_frame.grid(
            row=stretch_row, column=0, sticky="nsew",
            pady=(gui_style.PAD_XS, 0),
        )
        self._log_frame.columnconfigure(0, weight=1)
        self._log_frame.rowconfigure(0, weight=1)

        # font_log() resolves the mono chain Cascadia Mono → Cascadia
        # Code → Consolas → TkFixedFont — fail-safe on dev boxes that
        # don't have Cascadia installed.
        self._log_text = ctk.CTkTextbox(
            self._log_frame, height=200, wrap="word",
            font=gui_style.font_log(),
            fg_color=gui_style.BG_SURFACE_1,
            border_width=0,
        )
        self._log_text.grid(
            row=0, column=0, sticky="nsew",
            padx=gui_style.PAD_SM, pady=gui_style.PAD_SM,
        )
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
            except Exception as exc:
                logger.debug("engine.check_status() failed for %s", engine.id, exc_info=exc)
            label = f"{dot}  {engine.display_name}"
            self._engine_display_to_id[label] = engine.id

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
        if not eid:
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
        # Fall through to imported voice packs (Chatterbox only — packs
        # target Chatterbox's reference-audio clone path).
        for voice in self._voice_pack_voices(engine, self._current_language()):
            if voice.display_name == display:
                return voice
        return None

    def _voice_pack_voices(self, engine: TTSEngine, language: str) -> list[Voice]:
        """Return Voice entries for installed voice packs that match
        ``engine`` + ``language``.

        Packs are surfaced only for Chatterbox: other engines don't use
        the clone-by-reference path the pack artefact is built around.
        The id uses the ``voicepack:<slug>`` prefix so
        :meth:`_resolve_voice_pack` can map the pick back to a concrete
        ``VoicePack`` and hand the reference audio to the synthesiser.
        """
        if getattr(engine, "id", "") != "chatterbox_grandmom":
            return []
        tag = self._s("voice_pack_tag")
        out: list[Voice] = []
        for pack in self._list_installed_voice_packs():
            if pack.meta.language and pack.meta.language != language:
                continue
            out.append(
                Voice(
                    id=f"voicepack:{pack.root.name}",
                    display_name=f"{pack.display_name} ({tag})",
                    language=language,
                )
            )
        return out

    def _list_installed_voice_packs(self) -> list[VoicePack]:
        """Return installed voice packs, or an empty list on any error.

        The GUI tolerates a broken packs root (missing dir, stray files,
        invalid meta.yaml) — the pack loader skips unreadable entries,
        but we also shield against any unexpected raise so the voice
        dropdown never empties on account of a voice-pack glitch.
        """
        try:
            return list_packs()
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("list_packs() failed", exc_info=exc)
            return []

    def _resolve_voice_pack(self, voice_id: Optional[str]) -> Optional[VoicePack]:
        """Map a ``voicepack:<slug>`` voice id to the installed pack."""
        if not voice_id or not voice_id.startswith("voicepack:"):
            return None
        slug = voice_id.split(":", 1)[1]
        for pack in self._list_installed_voice_packs():
            if pack.root.name == slug:
                return pack
        return None

    def _voice_pack_tag_suffixes(self) -> tuple[str, ...]:
        """Every `` (<tag>)`` suffix the Voice dropdown could carry — one per
        UI language.

        The dropdown builds a pack entry with whatever UI language was active
        when it was last populated, and a later UI-language switch does NOT
        repopulate it (``_apply_ui_language`` leaves the Voice list alone). So
        the live suffix can be in a different language than ``_s()`` currently
        returns. Matching against ALL languages' tags keeps pack detection and
        resolution working across a UI-language toggle instead of silently
        falling back to the default voice — the exact silent-fallback class this
        whole resolver exists to kill.
        """
        return tuple(
            f" ({strings['voice_pack_tag']})" for strings in _STRINGS.values()
        )

    def _selection_is_voice_pack(self) -> bool:
        """True when the Voice dropdown currently shows a voice-pack entry.

        A pack entry reads ``"<name> (<tag>)"`` in some UI language; a built-in
        voice ends with no tag suffix. Used to refuse a silent fall-back to the
        default voice when a pack is selected but can't be resolved.
        """
        display = self._voice_cb.get() if hasattr(self, "_voice_cb") else ""
        return bool(display) and display.endswith(self._voice_pack_tag_suffixes())

    def _selected_voice_pack(self) -> Optional[VoicePack]:
        """Resolve the voice pack selected in the Voice dropdown, robustly.

        :meth:`_current_voice` re-derives the pick by regenerating the
        language-filtered pack list and matching the combobox display exactly.
        That can miss — a language toggle, a transient pack-list read, an
        engine-resolution hiccup — and then synthesis silently falls back to
        the default voice (the field bug where an imported pack came out
        sounding like Grandmom). This resolves the selection directly: match the
        shown display against each installed pack's full ``"<name> (<tag>)"``
        form, in every UI language, with no language filter and no
        ``_current_voice`` dependency. Exact full-string matching (rather than
        stripping a fixed-length suffix) survives a UI-language switch that left
        a stale tag, and can't be fooled by a built-in voice whose own name
        happens to end with the tag text. Returns ``None`` only when the
        selection is a built-in voice or no installed pack matches the shown
        name.
        """
        if not self._selection_is_voice_pack():
            return None
        display = self._voice_cb.get()
        for pack in self._list_installed_voice_packs():
            for suffix in self._voice_pack_tag_suffixes():
                if display == f"{pack.display_name}{suffix}":
                    return pack
        return None

    def _voice_pack_reference_path(self, pack: VoicePack) -> Optional[str]:
        """Pick the audio file Chatterbox should clone from.

        Prefer ``reference.wav`` (curated few-shot reference); fall back
        to ``sample.wav`` so even LoRA packs (where the adapter would
        normally drive voicing, but we don't have the LoRA runtime path
        yet) at least produce *something* in the right voice ballpark.
        """
        if pack.reference_path is not None and pack.reference_path.exists():
            return str(pack.reference_path)
        if pack.sample_path.exists():
            return str(pack.sample_path)
        return None

    def _effective_reference_audio(
        self, voice: Optional[Voice], manual_ref: Optional[str]
    ) -> Optional[str]:
        """Return the reference audio to feed synthesis for ``voice``.

        A voice pack selection auto-populates the reference path from
        the pack artefact, so the Chatterbox clone path runs out of the
        pack's ``reference.wav`` (or ``sample.wav`` fallback) without
        the user having to browse to it manually. If the user has an
        explicit entry in the Ref. ääni field, that wins — lets power
        users override pack choice on a per-run basis.

        Pack resolution prefers the ``voice``-id route (correct for callers
        that pass an explicit voice), but falls back to the robust
        dropdown-based :meth:`_selected_voice_pack` when that route misses —
        e.g. after a UI-language switch, where ``_current_voice`` rebuilds the
        pack list with the current-language tag and no longer matches the stale
        display, so ``voice`` arrives as ``None``. Without the fallback the
        preview/clone paths would silently lose the pack reference and run in
        the default voice (the same silent-fallback class this resolver exists
        to kill).
        """
        if manual_ref:
            return manual_ref
        pack = self._resolve_voice_pack(voice.id) if voice is not None else None
        if pack is None:
            pack = self._selected_voice_pack()
        if pack is None:
            return None
        return self._voice_pack_reference_path(pack)

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
        self._update_action_buttons_state()

    def _get_input_text(self) -> str:
        """Return the text to synthesize based on the active input tab."""
        if self._input_mode == "pdf":
            if not self._pdf_path:
                raise ValueError(self._s("no_pdf"))
            from src.ocr_path import tesseract_lang_for
            book = parse_book(
                self._pdf_path,
                ocr_language=tesseract_lang_for(self._current_language()),
            )
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
            # Still populate the dropdown so imported voice packs stay
            # visible even when the engine binary/venv is missing — the
            # user can't synthesise yet, but they can see what's imported
            # and keep the pick sticky across install flows.
            self._populate_voice_combobox(engine)
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
        self._update_action_buttons_state()

    def _populate_voice_combobox(self, engine: TTSEngine) -> None:
        lang = self._current_language()
        voices = engine.list_voices(lang)
        pack_voices = self._voice_pack_voices(engine, lang)
        combined = list(voices) + pack_voices
        names = [v.display_name for v in combined]
        self._voice_cb.configure(values=names)
        if names:
            default_id = engine.default_voice(lang)
            default_name = next(
                (v.display_name for v in combined if v.id == default_id),
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

        # Chatterbox is the only engine that reads the chunk-chars knob; for
        # every other engine the spinbox is inert clutter, so hide it unless
        # Chatterbox is the active pick. Keeps the engine-bar honest: if a
        # widget is visible, the engine actually uses it.
        engine_id = self._current_engine_id()
        chunk_visible = engine_id == "chatterbox_grandmom"
        for attr in ("_chunk_chars_label", "_chunk_chars_spin"):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            if chunk_visible:
                widget.grid()
            else:
                widget.grid_remove()

    # ------------------------------------------------------------------
    # Progressive disclosure — action buttons follow configuration state
    # ------------------------------------------------------------------

    def _has_usable_input(self) -> bool:
        """True iff the active input tab actually has something to synthesize."""
        if self._input_mode == "pdf":
            return bool(self._pdf_path)
        if self._text_has_placeholder:
            return False
        return bool(self._text_widget.get("1.0", tk.END).strip())

    def _has_playable_output(self) -> bool:
        """True iff a previous run left a real MP3 for Preview / Open folder."""
        for path in (
            getattr(self, "_last_playable_path", None),
            getattr(self, "_output_path", None),
        ):
            if path and Path(path).is_file():
                return True
        return False

    def _update_action_buttons_state(self) -> None:
        """Central switchboard for Convert / Make sample / Preview / Open folder.

        Called on every hook that changes input- or voice-configuration
        state (tab change, text edit, PDF load, engine/voice refresh,
        apply_loaded_config, run-complete). Skipped while a run is in
        flight — _set_running_state owns the buttons during synthesis.

        Convert + Make sample: need input + voice.
        Preview + Open folder: need a playable output from a prior run.
        Test voice is on the engine bar and owns its own state.
        """
        if getattr(self, "_synth_running", False):
            return

        has_input = self._has_usable_input()
        has_voice = self._current_voice() is not None
        primary_state = "normal" if has_input and has_voice else "disabled"

        for attr in ("_convert_btn", "_sample_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.configure(state=primary_state)

        output_state = "normal" if self._has_playable_output() else "disabled"
        for attr in ("_listen_btn", "_open_folder_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.configure(state=output_state)

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _bump_output_path_if_exists(self) -> None:
        """If the current output path already points at an existing file,
        bump it to the next free numbered variant so Muunna never
        overwrites the previous recording.

        Pure numbering logic lives in
        :func:`synthesis_orchestrator.next_available_numbered_path`;
        this method only updates the widget state.
        """
        if not self._output_path:
            return
        new_path = next_available_numbered_path(self._output_path)
        if new_path == self._output_path:
            return  # Fresh name, nothing to do.
        self._output_path = new_path
        self._out_entry.configure(state="normal")
        self._out_entry.delete(0, tk.END)
        self._out_entry.insert(0, new_path)
        self._out_entry.configure(state="disabled")

    def _default_output_dir(self) -> Path:
        """Thin wrapper around :func:`synthesis_orchestrator.default_output_dir`."""
        return default_output_dir()

    def _auto_output_path(self) -> None:
        """Generate an automatic output path based on current input mode.

        Path computation lives in
        :func:`synthesis_orchestrator.suggest_output_path`; this method
        only syncs the widget + state.
        """
        suggested = suggest_output_path(self._input_mode, self._pdf_path)
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
            self._update_action_buttons_state()

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

    def _report_a_bug(self) -> None:
        """Open a pre-filled GitHub issue in the user's browser.

        The URL carries the app version, OS, and selected engine id so
        the user doesn't have to remember which build they were running.
        Failures are swallowed — if the browser can't be opened, we do
        not want to crash the GUI.
        """
        import webbrowser

        from src.bug_report import build_bug_report_url

        url = build_bug_report_url(
            app_version=APP_VERSION,
            engine_id=self._current_engine_id() or None,
        )
        try:
            webbrowser.open(url, new=2)
        except Exception:  # pragma: no cover - browser launcher is OS-level
            pass

    def _save_error_log(self) -> None:
        """Export the diagnostic log so the user can send it for support.

        Combines the persistent log file (full transcript plus any tracebacks
        and crashes captured by the file logger and the excepthooks) with the
        current on-screen log, then writes the result to a user-chosen path via
        a Save dialog. Always available — the whole point is to make handing a
        developer one complete file trivial.
        """
        parts: list[str] = []
        file_text = error_log.read_log_text().strip()
        if file_text:
            parts.append(file_text)
        else:
            # Fall back to the on-screen log ONLY when the persistent file is
            # empty/unavailable (e.g. logging setup failed). The file already
            # mirrors every on-screen line via tee_line, so adding the widget
            # text whenever the file exists would duplicate the current run in
            # the export.
            widget_text = self._log_text.get("1.0", tk.END).strip()
            if widget_text:
                parts.append(
                    "=== Current session (on-screen log) ===\n" + widget_text
                )
        content = "\n\n".join(parts)
        title = self._s("save_error_log").rstrip("…")
        if not content:
            messagebox.showinfo(title, self._s("save_error_log_empty"))
            return
        dest = filedialog.asksaveasfilename(
            title=title,
            defaultextension=".txt",
            initialfile="audiobookmaker-error-log.txt",
            filetypes=[
                (self._s("log_file_filter"), "*.txt"),
                (self._s("all_files_filter"), "*.*"),
            ],
        )
        if not dest:
            return
        try:
            Path(dest).write_text(content, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(self._s("error"), str(exc))
            return
        self._append_log_success(
            self._s("save_error_log_done").format(path=dest)
        )

    def _import_voice_pack(self) -> None:
        """File picker → ``install_pack_source`` → refresh Voice dropdown.

        Accepts a portable ``.abvpack.zip`` archive — the form produced by
        Export voice pack… and the easy thing to send between machines.
        The pack is copied into the user-data voice-packs root
        (``~/.audiobookmaker/voice_packs/``) so the app owns the canonical
        copy; the source file can be moved or deleted afterwards without
        breaking the imported voice.
        """
        source = filedialog.askopenfilename(
            title=self._s("import_pack_title"),
            filetypes=[
                (self._s("pack_archive_filter"), "*.zip"),
                (self._s("all_files_filter"), "*.*"),
            ],
        )
        if not source:
            return
        try:
            pack = install_pack_source(source)
        except (VoicePackError, FileExistsError, OSError) as exc:
            messagebox.showerror(
                self._s("error"),
                self._s("import_pack_error").format(error=str(exc)),
            )
            return
        self._append_log_success(
            self._s("import_pack_success").format(name=pack.display_name)
        )
        self._warn_missing_base_models(pack)
        self._refresh_voice_list()
        # If the active engine is Chatterbox, jump straight to the newly
        # imported pack so the user doesn't have to re-open the dropdown.
        engine = self._current_engine()
        if engine is not None and getattr(engine, "id", "") == "chatterbox_grandmom":
            target_name = f"{pack.display_name} ({self._s('voice_pack_tag')})"
            values = list(self._voice_cb.cget("values"))
            if target_name in values:
                self._voice_cb.set(target_name)

    def _warn_missing_base_models(self, pack: VoicePack) -> None:
        """Log which base models the imported pack needs to actually speak.

        A pack ships only the voice-specific layer (a reference clip or a
        LoRA adapter); the multi-gigabyte base TTS weights download on
        first synthesis. Naming the requirement at import time means a
        missing-model failure later isn't a surprise on a fresh machine.
        """
        requirements = base_model_requirements(pack.meta)
        if not requirements:
            return
        self._append_log_warning(
            self._s("import_pack_base_models").format(models=", ".join(requirements))
        )

    def _export_voice_pack(self) -> None:
        """Bundle the selected voice pack into a portable ``.abvpack.zip``.

        Export targets the currently-selected voice. If the selection is
        not a voice pack (e.g. a built-in voice), there's nothing to
        export, so we tell the user to pick a pack first. Otherwise a Save
        dialog chooses the destination and ``export_pack`` writes the zip.
        """
        voice = self._current_voice()
        pack = self._resolve_voice_pack(voice.id) if voice is not None else None
        if pack is None:
            messagebox.showinfo(
                self._s("export_pack_btn").rstrip("…"),
                self._s("export_pack_no_selection"),
            )
            return
        dest = filedialog.asksaveasfilename(
            title=self._s("export_pack_title"),
            defaultextension=".zip",
            initialfile=f"{pack.root.name}{PACK_ARCHIVE_SUFFIX}",
            filetypes=[
                (self._s("pack_archive_filter"), "*.zip"),
                (self._s("all_files_filter"), "*.*"),
            ],
        )
        if not dest:
            return
        try:
            out = export_pack(pack.root, dest)
        except (VoicePackError, OSError) as exc:
            messagebox.showerror(
                self._s("error"),
                self._s("export_pack_error").format(error=str(exc)),
            )
            return
        self._append_log_success(
            self._s("export_pack_success").format(path=str(out))
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

    def _engine_install_in_progress(self) -> bool:
        """True while the engine manager is running an install/repair thread.

        The synth-launch handlers use this to refuse a Convert/Sample against a
        half-built venv, which fails and can corrupt the in-progress install.
        """
        view = getattr(self, "_settings_view", None)
        return view is not None and view.is_installing()

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
            # Clean launch (or the update succeeded): no failed-update recovery
            # is pending, so any leftover downloaded installers are dead weight.
            from src.auto_updater import prune_old_installers
            prune_old_installers()
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
                logger.exception("Visible-installer fallback failed")
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
        """Background thread: check GitHub for a newer release.

        ALWAYS enqueues exactly one result so the main-thread poller
        (_poll_update_check) can stop re-arming. check_for_update is
        contractually no-raise, but belt-and-suspenders it here: on any
        unexpected error, log it and enqueue a "no update" result, so a
        contract violation can never leave the poll spinning every 500 ms
        with nothing to consume.
        """
        try:
            info = check_for_update(APP_VERSION)
        except Exception:
            logger.debug("Update check raised unexpectedly", exc_info=True)
            info = _no_update(APP_VERSION)
        self._update_queue.put(info)

    def _poll_update_check(self) -> None:
        """Main thread: pick up the result from the update check thread."""
        try:
            info = self._update_queue.get_nowait()
        except queue.Empty:
            # The check thread hasn't returned yet — a GitHub round-trip
            # routinely outlasts the initial 500 ms poll. Re-arm until the
            # result lands; otherwise it sits unread in the queue and the
            # banner never appears until the next periodic check (frozen) or
            # never (dev). Safe from spinning forever: check_for_update never
            # raises and always enqueues exactly one UpdateInfo, so the worker
            # always lands a result within API_TIMEOUT.
            self.after(500, self._poll_update_check)
            return

        if info.available:
            self._pending_update = info
            self._show_update_banner(info)

        # Result consumed — schedule the next periodic check (frozen only).
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
        """Show the update banner with the version and, when the release
        carries notes, an expandable "What's new" section the user can read
        before pressing the button."""
        msg = self._s("update_available").format(version=info.latest_version)
        self._update_label.configure(text=msg)

        notes = extract_whats_new(info.release_notes)
        if notes:
            # Re-collapse on each new release so a long changelog never springs
            # open unbidden; the user opts in via the toggle.
            self._whatsnew_expanded = False
            self._update_whatsnew_text.configure(text=notes)
            self._update_whatsnew_text.grid_remove()
            self._update_whatsnew_toggle.configure(text=self._whatsnew_toggle_text())
            self._update_whatsnew_toggle.grid()
        else:
            # Clear the text too, not just unmap it — otherwise a previous
            # release's notes linger in the hidden widget and could resurface
            # if anything re-grids it later.
            self._update_whatsnew_toggle.grid_remove()
            self._update_whatsnew_text.configure(text="")
            self._update_whatsnew_text.grid_remove()

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

        if engine is not None and engine.uses_subprocess:
            # Subprocess engines (Chatterbox) are too slow to synthesize
            # on demand from the voice-test button, but we ship pre-baked
            # Grandmom samples for both supported languages so the button
            # still produces something the user can hear immediately.
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

            ref_audio = self._effective_reference_audio(
                voice, self._ref_audio_var.get() or None
            )
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
                _audio_player.get_player().play(path)
                self._refresh_listen_btn_label()
            except Exception as exc:
                logger.debug("failed to play sample %s", path, exc_info=exc)
                self._append_log_error(self._s("playback_failed").format(error=exc))
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
        # If a clip is already playing, the button acts as Stop.
        player = _audio_player.get_player()
        if player.is_playing():
            player.stop()
            self._refresh_listen_btn_label()
            return

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
                    player.play(str(cand_file))
                    self._refresh_listen_btn_label()
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
                # A thrown parse error (corrupt/encrypted/empty PDF) is a real
                # failure — log it so it lands in the exported error log, not
                # only the transient dialog.
                logger.exception("Listen preview: PDF parse failed")
                self._append_log_error(str(exc))
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

        engine = self._current_engine()
        if engine is None:
            messagebox.showerror(self._s("error"), self._s("engine_not_found"))
            return

        if engine.uses_subprocess:
            # Subprocess engines (Chatterbox) are too slow for an on-demand
            # preview, and there's no existing MP3 to play. Tell the user
            # to run Muunna first.
            messagebox.showinfo(
                self._s("listen"),
                self._s("listen_convert_first"),
            )
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
            ref_audio = self._effective_reference_audio(
                voice, self._ref_audio_var.get() or None
            )
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

            # Play in-process via the shared AudioPlayer singleton.
            try:
                _audio_player.get_player().play(tmp_path)
                self.after(0, self._refresh_listen_btn_label)
            except Exception as exc:
                self.after(0, lambda e=exc: self._append_log_error(
                    self._s("playback_failed").format(error=e)
                ))

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
        self._convert_btn.configure(state="normal")
        self._listen_btn.configure(state="normal")
        self._refresh_listen_btn_label()
        if not self._synth_running:
            self._status_label_val.configure(text=self._s("select_input_prompt"))

    def _refresh_listen_btn_label(self) -> None:
        """Flip the Preview button between Esikuuntele/Preview and Pys\u00e4yt\u00e4/Stop.

        Polls the AudioPlayer once per ~250ms while playback is active so the
        label reverts to Preview when the clip ends naturally without the user
        clicking Stop.
        """
        try:
            playing = _audio_player.get_player().is_playing()
        except Exception as exc:  # noqa: BLE001 — best-effort UI sync
            logger.debug("is_playing query failed: %s", exc)
            playing = False
        if self._listening:
            # Mid-synthesis: keep the "Toistetaan..." label that
            # _on_listen_click set so the user knows we're working.
            return
        target = self._s("stop") if playing else self._s("listen")
        try:
            current = self._listen_btn.cget("text")
        except Exception:  # noqa: BLE001 — widget may be torn down
            return
        if current != target:
            self._listen_btn.configure(text=target)
        if playing:
            # Re-poll until playback ends so the label can flip back.
            self.after(250, self._refresh_listen_btn_label)

    def destroy(self) -> None:  # type: ignore[override]
        """Tear down the window cleanly to prevent the Tcl notifier thread hang.

        Background: when any tkinter.Tk / CTk window is created, the C extension
        _tkinter.pyd spawns a Win32 Tcl notifier thread.  That thread is invisible
        to Python's threading.enumerate() and is NOT stopped by Tk.destroy().
        On headless Windows (GitHub Actions), Python's shutdown waits for it before
        reaching Py_Finalize() — and the thread never exits because pending Tcl
        ``after`` callbacks (from customtkinter's AppearanceModeTracker.update)
        keep rescheduling themselves on the destroyed window.

        Fix:
        1. Cancel every pending ``after`` callback so the rescheduling loop stops.
        2. Clear AppearanceModeTracker.app_list so update() finds nothing to
           reschedule against and sets update_loop_running = False.
        3. Stop the audio player (existing behaviour).
        4. Call super().destroy() as normal.

        Each step is wrapped individually so a single failure cannot abort the
        rest of teardown.
        """
        # Mark teardown so report_callback_exception stays silent for the
        # transient TclErrors that a closing window naturally produces.
        self._closing = True

        # Step 1 — cancel every after-callback WE scheduled.
        # We use our own tracking set (_scheduled_afters, populated by the
        # overridden after() / after_idle() methods) instead of the previous
        # approach of asking Tcl for all pending IDs and filtering by script
        # name substring.  That substring heuristic was fragile: it relied on
        # "update" appearing in the Tcl script name, which is an implementation
        # detail of AppearanceModeTracker that could change without notice.
        #
        # We deliberately do NOT touch CTk-internal after-IDs (e.g.
        # _windows_set_titlebar_icon).  Those are scheduled directly by CTk
        # internals, not via our overridden after(), so they are absent from
        # _scheduled_afters.  Cancelling them would cause a
        # "_tkinter.TclError: can't delete Tcl command" in super().destroy().
        try:
            # pop() one at a time: the set shrinks with each iteration so no
            # separate .clear() is needed.  after_cancel() (overridden above)
            # also discards from the set, but set.discard is idempotent so
            # the double-discard is harmless.
            while self._scheduled_afters:
                after_id = self._scheduled_afters.pop()
                try:
                    self.after_cancel(after_id)
                except Exception:  # noqa: BLE001 — already fired or invalid
                    pass
        except Exception as exc:  # noqa: BLE001 — Tcl may already be torn down
            logger.debug("after-cancel sweep failed: %s", exc)

        # Step 2 — clear AppearanceModeTracker.app_list so the 30 ms rescheduling
        # loop in customtkinter stops immediately rather than spinning forever on
        # a destroyed window (the root cause of the Tcl notifier thread hang).
        try:
            from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import (
                AppearanceModeTracker,
            )
            AppearanceModeTracker.app_list.clear()
            AppearanceModeTracker.update_loop_running = False
        except Exception as exc:  # noqa: BLE001 — future customtkinter version change
            logger.warning(
                "AppearanceModeTracker cleanup failed; check for customtkinter "
                "version change: %s", exc,
            )

        # Step 3 — stop in-process audio so the mixer thread releases resources.
        try:
            _audio_player.get_player().stop()
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.debug("audio player stop on destroy failed: %s", exc)

        # Step 4 — hand off to CTk / Tk for the rest of widget teardown.
        super().destroy()

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

        # Engine: match the persisted id against the dropdown labels so
        # the dot-prefixed label (e.g. "🟢  Edge-TTS") round-trips cleanly.
        for lbl, eid in self._engine_display_to_id.items():
            if eid == cfg.engine_id:
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

        # Paint the action-row gating now that every input signal
        # (input mode, voice, pdf path) is back to the persisted value.
        self._update_action_buttons_state()

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

        # Refuse before any PDF parsing if an engine install/repair is running
        # (same corruption risk as Convert; fail fast before the parse).
        if self._engine_install_in_progress():
            messagebox.showerror(
                self._s("error"), self._s("engine_installing_wait")
            )
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
                # Same as the Listen path: a thrown parse error is a real
                # failure and must reach the exported error log.
                logger.exception("Make sample: PDF parse failed")
                self._append_log_error(str(exc))
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
        engine = self._current_engine()
        if engine is None:
            messagebox.showerror(self._s("error"), self._s("engine_not_found"))
            return

        # Availability check runs for EVERY engine (cheap, path-only), so a
        # subprocess engine like Chatterbox is blocked when its venv is missing
        # or still incomplete rather than launching against a half-built env.
        status = engine.check_status()
        if not status.available:
            messagebox.showerror(
                self._s("error"), f"{engine.display_name}: {status.reason}"
            )
            return
        # Voice selection only applies to in-process engines.
        if not engine.uses_subprocess:
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

        if engine.uses_subprocess:
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
        engine = self._current_engine()
        if engine is None:
            messagebox.showerror(self._s("error"), self._s("engine_not_found"))
            return

        # Refuse to launch while an engine install/repair is running — a
        # Convert against a half-built venv fails and can corrupt the install
        # (pip cannot overwrite files the synth subprocess has locked).
        if self._engine_install_in_progress():
            messagebox.showerror(
                self._s("error"), self._s("engine_installing_wait")
            )
            return

        # Availability check runs for EVERY engine now (it is cheap, path-only),
        # so a subprocess engine like Chatterbox is blocked when its venv is
        # missing or still incomplete, instead of launching the runner against a
        # half-built environment.
        status = engine.check_status()
        if not status.available:
            messagebox.showerror(
                self._s("error"), f"{engine.display_name}: {status.reason}"
            )
            return
        # Voice selection only applies to in-process engines.
        if not engine.uses_subprocess:
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

        if engine.uses_subprocess:
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

        Widget state is captured on the main thread and frozen into an
        :class:`InprocessRequest`; the background thread then hands it
        to :func:`run_inprocess_synthesis`, which emits
        ``ProgressEvent``s back through our queue. Tkinter widgets are
        never read off-thread.

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
        ref_audio = self._effective_reference_audio(
            voice, self._ref_audio_var.get() or None
        )
        voice_desc = self._voice_desc_var.get() or None

        request = InprocessRequest(
            engine_id=engine_id,
            language=language,
            input_mode=input_mode,
            output_path=output_path,
            voice_id=voice_id,
            pdf_path=pdf_path,
            input_text=input_text,
            reference_audio=ref_audio,
            voice_description=voice_desc,
        )

        threading.Thread(
            target=run_inprocess_synthesis,
            args=(request, self._event_queue.put),
            daemon=True, name=f"tts-{engine_id}",
        ).start()

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
        # It lives under .local/audiobooks/<stem>/ and may hold a full-book
        # .chunks/ WAV cache; rmtree here wiped user data when sample and
        # long jobs shared the same output tree.

    # ------------------------------------------------------------------
    # Running/idle state management — inherited from SynthMixin.
    # _set_running_state / _set_idle_state live in gui_synth_mixin.py as
    # the single canonical impl. The mixin version sets _synth_started_at,
    # toggles all the buttons including Open-folder, and clears the
    # chatterbox_runner on idle, so no override is needed here anymore.
    # ------------------------------------------------------------------

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
            # start_new_session detaches the child from our process group
            # so it (a) survives GUI shutdown and (b) is reaped by init,
            # not left as a zombie hanging off this Python interpreter.
            subprocess.Popen(["open", str(folder)], start_new_session=True)
        else:
            subprocess.Popen(
                ["xdg-open", str(folder)], start_new_session=True
            )

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
        error_log.tee_line(line, "info")
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

        Colors come from the Cold Forge palette so the log panel stays
        legible in both light and dark appearance modes. Tk tags don't
        accept (light, dark) tuples, so we pick the index that matches
        the current ``ctk.get_appearance_mode()`` at render time.
        """
        error_log.tee_line(line, severity)
        self._log_text.configure(state="normal")
        start_index = self._log_text.index(tk.END + "-1c")
        self._log_text.insert(tk.END, line + "\n")
        end_index = self._log_text.index(tk.END + "-1c")

        # Pick the palette index (0=light, 1=dark) that matches the
        # current CTk appearance mode. Unknown / "system" → dark, since
        # the app defaults to dark.
        try:
            mode = ctk.get_appearance_mode().lower()
        except Exception:
            mode = "dark"
        idx = 0 if mode == "light" else 1

        mono_family = gui_style.font_log().cget("family")
        bold_font = (mono_family, 12, "bold")
        palette = {
            "success": gui_style.SUCCESS,
            "warning": gui_style.WARNING,
            "error":   gui_style.DANGER,
        }
        if severity in palette:
            color = palette[severity][idx]
            try:
                inner = self._log_text._textbox  # type: ignore[attr-defined]
                inner.tag_configure(
                    f"log_{severity}",
                    foreground=color, font=bold_font,
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

    def report_callback_exception(self, exc, val, tb) -> None:  # noqa: N802
        """Capture exceptions raised inside Tk widget callbacks.

        Tk's default handler prints the traceback to stderr — invisible in a
        frozen windowed .exe, so these crashes used to vanish entirely. Always
        log the full traceback to the diagnostic file. Surface a dialog for a
        genuine unexpected bug (so the user knows to hit Save error log), but
        stay SILENT for the teardown-class ``TclError``s ("application has been
        destroyed" / "invalid command name") and while the window is closing —
        Tk used to print those to stderr and a modal dialog there is pure noise
        (it could pop on a transient close-time fault the user gains nothing
        from seeing).
        """
        import traceback
        logger.error(
            "Unhandled exception in a GUI callback:\n%s",
            "".join(traceback.format_exception(exc, val, tb)),
        )
        if getattr(self, "_closing", False) or isinstance(val, tk.TclError):
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:  # noqa: BLE001 — root may already be torn down
            return
        try:
            messagebox.showerror(self._s("error"), str(val) or exc.__name__)
        except Exception:  # noqa: BLE001 — the error dialog must never re-raise
            pass

    def _fail(self, message: str) -> None:
        logger.error("Run failed: %s", message)
        self._set_idle_state()
        self._status_label_val.configure(text=message)
        # Revert the sticky strip: no green "done", no lingering progress.
        if hasattr(self, "_status_strip_frame"):
            self._set_status_strip("idle")
        # A broken/drifted engine venv (the "Could not import module
        # 'LlamaModel'" failure) is recoverable in one click: offer to open
        # Install engines, which re-pins the venv. Defensive — if anything in
        # the offer path misbehaves, fall through to the plain error dialog so
        # the failure is never swallowed.
        if is_engine_load_failure(message):
            try:
                if messagebox.askyesno(
                    self._s("engine_repair_title"),
                    f"{message}\n\n{self._s('engine_repair_prompt')}",
                ):
                    self._open_engine_manager()
                    # Don't dead-end on the engine panel (which used to show
                    # only Uninstall): kick off the force-reinstall repair
                    # right away, targeting the engine that actually failed.
                    # is_engine_load_failure() matches on the message, not the
                    # engine, so fall back to Chatterbox — the only engine that
                    # drifts this way today — when the selection is unknown.
                    self._settings_view.start_repair(
                        self._current_engine_id() or "chatterbox_grandmom"
                    )
                return
            except Exception:
                logger.debug("engine-repair offer failed; showing plain error",
                             exc_info=True)
        messagebox.showerror(self._s("error"), message)

    def _request_cancel(self) -> None:  # type: ignore[override]
        """Override mixin's cancel to also hide the status strip."""
        super()._request_cancel()
        if hasattr(self, "_status_strip_frame"):
            self._set_status_strip("idle")

    def _set_idle_state(self) -> None:  # type: ignore[override]
        """Override mixin's idle transition to re-paint progressive-disclosure
        state. The mixin force-enables Open folder unconditionally; we need
        to reassert that Open folder / Preview are only live when an output
        actually exists, and Convert / Make sample only when input + voice
        are configured. Runs once per run-end."""
        super()._set_idle_state()
        self._update_action_buttons_state()


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
