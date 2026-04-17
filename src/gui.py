"""Legacy GUI entry point retained for backwards compatibility. New work
should use src/gui_unified.py — do not extend this file.

Tkinter GUI for AudiobookMaker.

Provides a simple window for selecting a PDF, configuring TTS settings,
and converting to MP3. Runs TTS in a background thread to keep the UI
responsive.

The GUI talks to engines through the `TTSEngine` interface and never
imports any engine module directly — all engines register themselves on
import below.
"""

from __future__ import annotations

import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from src import app_config
from src.ffmpeg_path import setup_ffmpeg_path
from src.pdf_parser import parse_pdf
from src.tts_base import TTSEngine, Voice, get_engine, list_engines

# Import engine adapters for their side effect of registering with tts_base.
# Order matters: Edge-TTS first so it's the default. Piper next (offline,
# no GPU). VoxCPM2 last (developer-install only, requires NVIDIA GPU).
from src import tts_edge  # noqa: F401  (registers EdgeTTSEngine)
from src import tts_piper  # noqa: F401  (registers PiperTTSEngine)
from src import tts_voxcpm  # noqa: F401  (registers VoxCPM2Engine)

# Also import the chapter helper for the "one MP3 per chapter" output mode.
from src.tts_engine import TTSConfig, chapters_to_speech

setup_ffmpeg_path()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = "AudiobookMaker"
WINDOW_MIN_W = 680
WINDOW_MIN_H = 540

LANGUAGES = {
    "Suomi": "fi",
    "English": "en",
    "Deutsch": "de",
    "Svenska": "sv",
    "Français": "fr",
    "Español": "es",
}

SPEED_OPTIONS = {
    "Hidas (-25%)": "-25%",
    "Normaali": "+0%",
    "Nopea (+25%)": "+25%",
    "Erittäin nopea (+50%)": "+50%",
}

OUTPUT_MODES = {
    "Yksi MP3-tiedosto": "single",
    "Yksi tiedosto per luku": "chapters",
}

# Short sample sentence used by the "Test voice" button.
_SAMPLE_TEXT_FI = "Tämä on ääninäyte valitulla äänellä."
_SAMPLE_TEXT_EN = "This is a voice sample with the selected voice."


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class App(tk.Tk):
    """Root application window."""

    def __init__(self) -> None:
        """Initialise the window, load persisted preferences, and build the UI."""
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self.resizable(True, True)
        self._center_window()

        # State
        self._pdf_path: Optional[str] = None
        self._output_path: Optional[str] = None
        self._converting = False
        self._testing_voice = False

        # Load user preferences; defaults kick in on first launch.
        self._user_cfg = app_config.load()

        self._build_ui()
        self._apply_loaded_config()

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------

    def _center_window(self) -> None:
        """Position the window in the middle of the primary screen."""
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - WINDOW_MIN_W) // 2
        y = (sh - WINDOW_MIN_H) // 2
        self.geometry(f"{WINDOW_MIN_W}x{WINDOW_MIN_H}+{x}+{y}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct all widgets."""
        main = ttk.Frame(self, padding=16)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)

        # ---- PDF file selection ----
        ttk.Label(main, text="PDF-tiedosto", font=("", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 2)
        )
        pdf_row = ttk.Frame(main)
        pdf_row.grid(row=1, column=0, sticky=tk.EW, pady=(0, 12))
        pdf_row.columnconfigure(0, weight=1)

        self._pdf_var = tk.StringVar(value="Ei tiedostoa valittu")
        ttk.Entry(pdf_row, textvariable=self._pdf_var, state="readonly").grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 8)
        )
        ttk.Button(pdf_row, text="Selaa…", command=self._browse_pdf).grid(
            row=0, column=1
        )

        # ---- Settings frame ----
        settings = ttk.LabelFrame(main, text="Asetukset", padding=8)
        settings.grid(row=2, column=0, sticky=tk.EW, pady=(0, 12))
        settings.columnconfigure(1, weight=1)

        row = 0

        # TTS engine selector
        ttk.Label(settings, text="TTS-moottori:").grid(
            row=row, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 0)
        )
        self._engine_var = tk.StringVar()
        self._engine_cb = ttk.Combobox(
            settings,
            textvariable=self._engine_var,
            state="readonly",
            width=46,
        )
        self._engine_cb.grid(row=row, column=1, columnspan=3, sticky=tk.EW, pady=(0, 0))
        self._engine_cb.bind("<<ComboboxSelected>>", self._on_engine_changed)
        self._populate_engine_list()
        row += 1

        # Engine status / notice line
        self._engine_status_var = tk.StringVar(value="")
        self._engine_status_lbl = ttk.Label(
            settings,
            textvariable=self._engine_status_var,
            foreground="#0a7",
            wraplength=560,
        )
        self._engine_status_lbl.grid(
            row=row, column=1, columnspan=3, sticky=tk.W, pady=(2, 6)
        )
        row += 1

        # Language
        ttk.Label(settings, text="Kieli:").grid(row=row, column=0, sticky=tk.W, padx=(0, 8))
        self._lang_var = tk.StringVar(value="Suomi")
        lang_cb = ttk.Combobox(
            settings,
            textvariable=self._lang_var,
            values=list(LANGUAGES.keys()),
            state="readonly",
            width=14,
        )
        lang_cb.grid(row=row, column=1, sticky=tk.W)
        lang_cb.bind("<<ComboboxSelected>>", self._on_language_changed)

        # Speed
        ttk.Label(settings, text="Nopeus:").grid(row=row, column=2, sticky=tk.W, padx=(16, 8))
        self._speed_var = tk.StringVar(value="Normaali")
        ttk.Combobox(
            settings,
            textvariable=self._speed_var,
            values=list(SPEED_OPTIONS.keys()),
            state="readonly",
            width=20,
        ).grid(row=row, column=3, sticky=tk.W)
        row += 1

        # Voice
        ttk.Label(settings, text="Ääni:").grid(row=row, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0))
        voice_row = ttk.Frame(settings)
        voice_row.grid(row=row, column=1, columnspan=3, sticky=tk.EW, pady=(8, 0))
        voice_row.columnconfigure(0, weight=1)
        self._voice_var = tk.StringVar()
        self._voice_cb = ttk.Combobox(
            voice_row,
            textvariable=self._voice_var,
            state="readonly",
        )
        self._voice_cb.grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        self._test_btn = ttk.Button(
            voice_row, text="Kuuntele näyte", command=self._on_test_voice
        )
        self._test_btn.grid(row=0, column=1)
        row += 1

        # Reference audio (voice cloning) — only shown for engines that
        # support cloning.  Widgets are created up front and grid_remove()d
        # until the current engine needs them.
        self._ref_audio_label = ttk.Label(settings, text="Referenssiääni:")
        self._ref_audio_label.grid(
            row=row, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0)
        )
        ref_row = ttk.Frame(settings)
        ref_row.grid(row=row, column=1, columnspan=3, sticky=tk.EW, pady=(8, 0))
        ref_row.columnconfigure(0, weight=1)
        self._ref_audio_row = ref_row
        self._ref_audio_var = tk.StringVar(value="")
        self._ref_audio_entry = ttk.Entry(
            ref_row, textvariable=self._ref_audio_var, state="readonly"
        )
        self._ref_audio_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        ttk.Button(ref_row, text="Selaa…", command=self._browse_reference_audio).grid(
            row=0, column=1
        )
        ttk.Button(ref_row, text="Tyhjennä", command=self._clear_reference_audio).grid(
            row=0, column=2, padx=(4, 0)
        )
        row += 1

        # Voice description (natural-language voice design) — only for
        # engines that support it.
        self._voice_desc_label = ttk.Label(settings, text="Äänen kuvaus:")
        self._voice_desc_label.grid(
            row=row, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0)
        )
        self._voice_desc_var = tk.StringVar(value="")
        self._voice_desc_entry = ttk.Entry(
            settings, textvariable=self._voice_desc_var
        )
        self._voice_desc_entry.grid(
            row=row, column=1, columnspan=3, sticky=tk.EW, pady=(8, 0)
        )
        row += 1
        self._voice_desc_hint = ttk.Label(
            settings,
            text='Esim. "warm baritone elderly male" — käytetään vain jos moottori tukee.',
            foreground="#888",
            font=("", 9),
        )
        self._voice_desc_hint.grid(
            row=row, column=1, columnspan=3, sticky=tk.W, pady=(0, 4)
        )
        row += 1

        # Output mode
        ttk.Label(settings, text="Tulostus:").grid(row=row, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0))
        self._output_mode_var = tk.StringVar(value="Yksi MP3-tiedosto")
        ttk.Combobox(
            settings,
            textvariable=self._output_mode_var,
            values=list(OUTPUT_MODES.keys()),
            state="readonly",
        ).grid(row=row, column=1, columnspan=3, sticky=tk.EW, pady=(8, 0))
        row += 1

        # ---- Output file selection ----
        ttk.Label(main, text="Tallennuspaikka", font=("", 10, "bold")).grid(
            row=3, column=0, sticky=tk.W, pady=(0, 2)
        )
        out_row = ttk.Frame(main)
        out_row.grid(row=4, column=0, sticky=tk.EW, pady=(0, 12))
        out_row.columnconfigure(0, weight=1)

        self._out_var = tk.StringVar(value="Ei valittu")
        ttk.Entry(out_row, textvariable=self._out_var, state="readonly").grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 8)
        )
        ttk.Button(out_row, text="Valitse…", command=self._browse_output).grid(
            row=0, column=1
        )

        # ---- Progress area ----
        self._status_var = tk.StringVar(value="Valitse PDF-tiedosto aloittaaksesi.")
        ttk.Label(main, textvariable=self._status_var, wraplength=560).grid(
            row=5, column=0, sticky=tk.W, pady=(0, 4)
        )

        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress_bar = ttk.Progressbar(
            main, variable=self._progress_var, maximum=100
        )
        self._progress_bar.grid(row=6, column=0, sticky=tk.EW, pady=(0, 12))

        # ---- Convert button ----
        self._convert_btn = ttk.Button(
            main, text="Muunna äänikirjaksi", command=self._start_conversion
        )
        self._convert_btn.grid(row=7, column=0, pady=(0, 4))

    # ------------------------------------------------------------------
    # Config application
    # ------------------------------------------------------------------

    def _apply_loaded_config(self) -> None:
        """Apply persisted preferences to the widgets after they exist."""
        # Language
        for label, code in LANGUAGES.items():
            if code == self._user_cfg.language:
                self._lang_var.set(label)
                break

        # Engine — only if the saved engine is still registered & available.
        engine = get_engine(self._user_cfg.engine_id)
        if engine and engine.check_status().available:
            self._engine_var.set(engine.display_name)
        else:
            # Fall back to the first registered engine.
            engines = list_engines()
            if engines:
                self._engine_var.set(engines[0].display_name)

        self._refresh_voice_list()

        # Try to restore the saved voice if the current engine offers it.
        engine = self._current_engine()
        if engine and self._user_cfg.voice_id:
            for voice in engine.list_voices(self._current_language()):
                if voice.id == self._user_cfg.voice_id:
                    self._voice_var.set(voice.display_name)
                    break

        # Speed: mapped back from the stored "+0%" etc. value.
        for label, val in SPEED_OPTIONS.items():
            if val == self._user_cfg.speed:
                self._speed_var.set(label)
                break

        # Restore reference audio + voice description if present and the
        # current engine actually supports them.
        if self._user_cfg.reference_audio:
            self._ref_audio_var.set(self._user_cfg.reference_audio)
        if self._user_cfg.voice_description:
            self._voice_desc_var.set(self._user_cfg.voice_description)

    def _save_current_config(self) -> None:
        """Snapshot the current UI selection into the on-disk config."""
        engine = self._current_engine()
        voice = self._current_voice()
        self._user_cfg.engine_id = engine.id if engine else "edge"
        self._user_cfg.voice_id = voice.id if voice else ""
        self._user_cfg.language = self._current_language()
        self._user_cfg.speed = SPEED_OPTIONS.get(self._speed_var.get(), "+0%")
        self._user_cfg.reference_audio = self._ref_audio_var.get()
        self._user_cfg.voice_description = self._voice_desc_var.get()
        app_config.save(self._user_cfg)

    # ------------------------------------------------------------------
    # Engine / voice helpers
    # ------------------------------------------------------------------

    def _populate_engine_list(self) -> None:
        """Fill the engine combobox from the currently registered TTS engines."""
        engines = list_engines()
        self._engine_display_to_id = {e.display_name: e.id for e in engines}
        self._engine_cb["values"] = list(self._engine_display_to_id.keys())
        if engines and not self._engine_var.get():
            self._engine_var.set(engines[0].display_name)

    def _current_engine(self) -> Optional[TTSEngine]:
        """Return the TTSEngine for the selected combobox entry, or None."""
        display = self._engine_var.get()
        engine_id = self._engine_display_to_id.get(display)
        return get_engine(engine_id) if engine_id else None

    def _current_language(self) -> str:
        """Return the ISO 639-1 code of the language selected in the UI."""
        return LANGUAGES.get(self._lang_var.get(), "fi")

    def _current_voice(self) -> Optional[Voice]:
        """Return the Voice matching the current selection, or None if unresolved."""
        engine = self._current_engine()
        if not engine:
            return None
        lang = self._current_language()
        display = self._voice_var.get()
        for voice in engine.list_voices(lang):
            if voice.display_name == display:
                return voice
        return None

    def _refresh_voice_list(self) -> None:
        """Refresh the voice dropdown and engine status based on current selection."""
        engine = self._current_engine()
        if engine is None:
            self._voice_cb["values"] = []
            self._voice_var.set("")
            self._engine_status_var.set("")
            return

        status = engine.check_status()
        # Update status line
        if not status.available:
            self._engine_status_lbl.configure(foreground="#c33")
            self._engine_status_var.set(status.reason)
            self._voice_cb["values"] = []
            self._voice_var.set("")
            # Still show/hide capability widgets so the user can see what
            # the engine *would* offer once installed.
            self._update_capability_widgets(engine)
            return

        if status.needs_download:
            self._engine_status_lbl.configure(foreground="#b60")
            self._engine_status_var.set(
                status.reason + "  Lataus käynnistyy automaattisesti."
            )
        else:
            self._engine_status_lbl.configure(foreground="#0a7")
            self._engine_status_var.set(engine.description)

        lang = self._current_language()
        voices = engine.list_voices(lang)
        names = [v.display_name for v in voices]
        self._voice_cb["values"] = names
        if names:
            default_id = engine.default_voice(lang)
            default_name = next(
                (v.display_name for v in voices if v.id == default_id), names[0]
            )
            self._voice_var.set(default_name)
        else:
            self._voice_var.set("")

        # Show/hide reference-audio and voice-description widgets based
        # on what the current engine advertises.
        self._update_capability_widgets(engine)

    def _update_capability_widgets(self, engine: TTSEngine) -> None:
        """Toggle the reference audio + voice description rows on/off
        depending on the current engine's capability flags."""
        show_ref = bool(engine.supports_voice_cloning)
        show_desc = bool(engine.supports_voice_description)

        for w in (self._ref_audio_label, self._ref_audio_row):
            if show_ref:
                w.grid()
            else:
                w.grid_remove()

        for w in (self._voice_desc_label, self._voice_desc_entry, self._voice_desc_hint):
            if show_desc:
                w.grid()
            else:
                w.grid_remove()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _browse_pdf(self) -> None:
        """Open a file picker for the input PDF and remember the chosen path."""
        path = filedialog.askopenfilename(
            title="Valitse PDF-tiedosto",
            filetypes=[("PDF-tiedostot", "*.pdf"), ("Kaikki tiedostot", "*.*")],
        )
        if path:
            self._pdf_path = path
            self._pdf_var.set(path)
            self._status_var.set("PDF valittu. Voit aloittaa muunnoksen.")
            # Auto-suggest output path
            if not self._output_path:
                suggested = str(Path(path).with_suffix(".mp3"))
                self._out_var.set(suggested)
                self._output_path = suggested

    def _browse_reference_audio(self) -> None:
        """Open a file picker for a voice-cloning reference audio clip."""
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
        """Clear any previously selected reference audio path."""
        self._ref_audio_var.set("")

    def _browse_output(self) -> None:
        """Pick an output MP3 file or directory depending on the output mode."""
        mode = OUTPUT_MODES[self._output_mode_var.get()]
        if mode == "single":
            path = filedialog.asksaveasfilename(
                title="Tallenna MP3-tiedostona",
                defaultextension=".mp3",
                filetypes=[("MP3-tiedostot", "*.mp3")],
            )
            if path:
                self._output_path = path
                self._out_var.set(path)
        else:
            path = filedialog.askdirectory(title="Valitse kohdekansio")
            if path:
                self._output_path = path
                self._out_var.set(path)

    def _on_language_changed(self, _event: object = None) -> None:
        """Refresh the voice list when the user picks a different language."""
        self._refresh_voice_list()

    def _on_engine_changed(self, _event: object = None) -> None:
        """Refresh the voice list and capability widgets when the engine changes."""
        self._refresh_voice_list()

    # ------------------------------------------------------------------
    # Test voice button
    # ------------------------------------------------------------------

    def _on_test_voice(self) -> None:
        """Synthesise a short sample with the current settings and play it."""
        if self._testing_voice:
            return
        engine = self._current_engine()
        voice = self._current_voice()
        if engine is None or voice is None:
            messagebox.showerror("Virhe", "Valitse ensin TTS-moottori ja ääni.")
            return

        self._testing_voice = True
        self._test_btn.config(state=tk.DISABLED)
        self._status_var.set("Syntetisoidaan ääninäytettä…")

        thread = threading.Thread(target=self._test_voice_worker, daemon=True)
        thread.start()

    def _test_voice_worker(self) -> None:
        """Background worker for the Test voice button; writes to a temp MP3."""
        try:
            engine = self._current_engine()
            voice = self._current_voice()
            if engine is None or voice is None:
                return
            lang = self._current_language()
            text = _SAMPLE_TEXT_FI if lang == "fi" else _SAMPLE_TEXT_EN

            tmp = tempfile.NamedTemporaryFile(
                prefix="sample_", suffix=".mp3", delete=False
            )
            tmp.close()

            def progress(current: int, total: int, msg: str) -> None:
                self._safe_update_status(msg)

            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None
            engine.synthesize(
                text,
                tmp.name,
                voice.id,
                lang,
                progress,
                reference_audio=ref_audio,
                voice_description=voice_desc,
            )
            self._safe_play_sample(tmp.name)
        except Exception as exc:
            self._safe_update_status(f"Näyteen luonti epäonnistui: {exc}")
            self.after(0, lambda: self._test_btn.config(state=tk.NORMAL))
            self.after(0, lambda: setattr(self, "_testing_voice", False))

    def _safe_play_sample(self, path: str) -> None:
        """Hand playback back to the Tk main thread and open the MP3 externally."""
        def _play() -> None:
            self._test_btn.config(state=tk.NORMAL)
            self._testing_voice = False
            self._status_var.set(f"Ääninäyte tallennettu: {path}")
            # Open in the default audio player.
            import platform
            import subprocess

            try:
                if platform.system() == "Darwin":
                    subprocess.Popen(["open", path])
                elif platform.system() == "Windows":
                    import os as _os

                    _os.startfile(path)  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception:
                pass  # Best-effort; user can still find the file via the status line.

        self.after(0, _play)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _start_conversion(self) -> None:
        """Validate inputs and spawn the background PDF-to-audio conversion."""
        if self._converting:
            return
        if not self._pdf_path:
            messagebox.showerror("Virhe", "Valitse ensin PDF-tiedosto.")
            return
        if not self._output_path:
            messagebox.showerror("Virhe", "Valitse tallennuspaikka.")
            return

        engine = self._current_engine()
        voice = self._current_voice()
        if engine is None:
            messagebox.showerror("Virhe", "Valitse TTS-moottori.")
            return
        if voice is None:
            messagebox.showerror("Virhe", "Valitse ääni.")
            return

        status = engine.check_status()
        if not status.available:
            messagebox.showerror("Virhe", f"{engine.display_name}: {status.reason}")
            return

        # Remember the user's selections.
        self._save_current_config()

        self._converting = True
        self._convert_btn.config(state=tk.DISABLED)
        self._progress_var.set(0)
        self._status_var.set("Parsitaan PDF…")

        thread = threading.Thread(target=self._conversion_worker, daemon=True)
        thread.start()

    def _conversion_worker(self) -> None:
        """Run in background thread — must not call Tkinter directly."""
        try:
            engine = self._current_engine()
            voice = self._current_voice()
            if engine is None or voice is None:
                raise RuntimeError("Engine or voice not selected")

            self._safe_update_status("Luetaan PDF-tiedostoa…")
            book = parse_pdf(self._pdf_path)

            lang_code = self._current_language()
            mode = OUTPUT_MODES[self._output_mode_var.get()]

            def progress_cb(current: int, total: int, msg: str) -> None:
                if total > 0:
                    pct = (current / total) * 100
                else:
                    pct = 0
                self._safe_update_progress(pct, msg)

            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None

            if mode == "single":
                self._safe_update_status(
                    f"Muunnetaan tekstiä puheeksi ({engine.display_name})…"
                )
                engine.synthesize(
                    book.full_text,
                    self._output_path,
                    voice.id,
                    lang_code,
                    progress_cb,
                    reference_audio=ref_audio,
                    voice_description=voice_desc,
                )
            else:
                # Chapter mode currently only supports edge-tts via the
                # legacy chapters_to_speech helper. Keep the old behaviour.
                if engine.id != "edge":
                    raise RuntimeError(
                        "Lukukohtainen tulostus on tällä hetkellä tuettu vain "
                        "Edge-TTS-moottorilla."
                    )
                chapters = [(ch.title, ch.content) for ch in book.chapters]
                rate = SPEED_OPTIONS.get(self._speed_var.get(), "+0%")
                config = TTSConfig(language=lang_code, voice=voice.id, rate=rate)
                chapters_to_speech(chapters, self._output_path, config, progress_cb)

            self._safe_conversion_done(success=True, message="Valmis! Äänikirja tallennettu.")

        except Exception as exc:
            self._safe_conversion_done(success=False, message=str(exc))

    # ------------------------------------------------------------------
    # Thread-safe UI updates via after()
    # ------------------------------------------------------------------

    def _safe_update_status(self, msg: str) -> None:
        """Thread-safe status line update; schedules the change on the Tk loop."""
        self.after(0, lambda: self._status_var.set(msg))

    def _safe_update_progress(self, pct: float, msg: str) -> None:
        """Thread-safe update of the progress bar and status line."""
        def _update() -> None:
            self._progress_var.set(pct)
            self._status_var.set(msg)
        self.after(0, _update)

    def _safe_conversion_done(self, success: bool, message: str) -> None:
        """Reset UI state and show the final success/error dialog from the Tk loop."""
        def _done() -> None:
            self._converting = False
            self._convert_btn.config(state=tk.NORMAL)
            self._progress_var.set(100 if success else 0)
            self._status_var.set(message)
            if success:
                messagebox.showinfo("Valmis", message)
            else:
                messagebox.showerror(
                    "Virhe",
                    f"Muunnos epäonnistui:\n\n{message}\n\n"
                    "Tarkista asetukset ja yritä uudelleen.",
                )
        self.after(0, _done)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Launch the GUI application."""
    app = App()
    app.mainloop()
