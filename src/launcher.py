"""Legacy launcher entry point retained for backwards compatibility. New
work should use src/gui_unified.py — do not extend this file. Still
frozen by audiobookmaker_launcher.spec and shipped by
installer/launcher.iss, so it cannot simply be deleted.

Minimal AudiobookMaker launcher — "pick PDF, click button, get MP3".

This is the simple entry point aimed at non-technical users.
The existing ``src/gui.py`` is the advanced-mode window with the full
engine/voice/rate/reference/description settings matrix. This launcher:

- shows one engine dropdown populated from engines whose ``check_status()``
  reports available
- one primary button that opens a file picker for the PDF
- a progress bar plus elapsed/remaining time while synthesis runs
- an "Open output folder" button once done
- a collapsible log panel that shows the raw underlying stdout so if
  something goes wrong the user can paste it to Mikko
- all UI strings in Finnish (user-facing exception to the all-English rule)

Chatterbox-Finnish runs as a subprocess via ``launcher_bridge``. Edge-TTS
and Piper run in-process on a background thread, reusing the existing
``text_to_speech()`` path. Both paths feed the same Tkinter progress
update queue.

Entry point::

    python -m src.launcher

The Windows installer's Start Menu shortcut points at this module.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

# Ensure repo-root imports work regardless of how the launcher is invoked.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.ffmpeg_path import setup_ffmpeg_path  # noqa: E402
from src.launcher_bridge import (  # noqa: E402
    ChatterboxRunner,
    ProgressEvent,
    resolve_chatterbox_python,
)
from src.pdf_parser import parse_pdf  # noqa: E402
from src.tts_base import TTSEngine, get_engine, list_engines  # noqa: E402

# Single import point for every TTS engine — see src/engine_registry.py.
from src import engine_registry  # noqa: F401,E402


# ---------------------------------------------------------------------------
# Finnish UI strings
# ---------------------------------------------------------------------------

WINDOW_TITLE = "AudiobookMaker"

LBL_ENGINE = "Moottori:"
BTN_PRIMARY_IDLE = "Valitse PDF ja tee äänikirja"
BTN_PRIMARY_CANCEL = "Peruuta"
BTN_CANCELLING = "Peruutetaan…"
BTN_OPEN_FOLDER = "Avaa kansio"
BTN_SHOW_LOG = "▾ Näytä loki"
BTN_HIDE_LOG = "▴ Piilota loki"
LNK_HELP = "Ohje"

MSG_IDLE = "Valitse PDF-tiedosto aloittaaksesi."
MSG_PARSING = "Luetaan PDF-tiedostoa…"
MSG_STARTING = "Aloitetaan synteesiä…"
MSG_DONE_FMT = "Valmis! Äänikirja tallennettu: {path}"
MSG_CANCELLED = (
    "Peruutettu. Voit jatkaa myöhemmin samalla PDF:llä — synteesi jatkuu "
    "siitä mihin jäi."
)
MSG_ERROR_FMT = "Jotain meni vikaan:\n{err}"

DLG_PICK_PDF_TITLE = "Valitse PDF-tiedosto"
DLG_PDF_FILTER_NAME = "PDF-tiedostot"
DLG_ERROR_TITLE = "Virhe"
DLG_DONE_TITLE = "Valmis"

ENGINE_INFO = {
    "chatterbox_fi": (
        "Offline, paras laatu. Kesto ~1–2 h NVIDIA-koneella. "
        "Katso pika-aloitus ohjeesta."
    ),
    "edge": (
        "Online-palvelu — tarvitsee internet-yhteyden. Nopea ja ilmainen."
    ),
    "piper": (
        "Offline kun ääni on kerran ladattu. Kevyt, ei tarvitse "
        "näytönohjainta."
    ),
    "voxcpm2": (
        "Voice cloning (kehittäjäkäyttö). Vaatii NVIDIA-näytönohjaimen."
    ),
}

WARN_NO_ENGINES_AVAILABLE = (
    "Yhtään TTS-moottoria ei ole saatavilla tässä asennuksessa. "
    "Asenna Edge-TTS tai Piper ja yritä uudelleen."
)


# ---------------------------------------------------------------------------
# App window
# ---------------------------------------------------------------------------


class LauncherApp(tk.Tk):
    """Minimal launcher window. See module docstring for scope."""

    POLL_INTERVAL_MS = 100

    def __init__(self) -> None:
        """Build the window, wire widgets, and populate the engine dropdown."""
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(640, 480)
        self.geometry("640x480")

        setup_ffmpeg_path()

        # --- state -----------------------------------------------------
        self._pdf_path: Optional[Path] = None
        self._output_path: Optional[Path] = None
        self._synth_running = False
        self._cancel_requested = False
        self._chatterbox_runner: Optional[ChatterboxRunner] = None
        self._event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self._details_visible = False

        # --- widgets ---------------------------------------------------
        self._main = ttk.Frame(self, padding=16)
        self._main.pack(fill=tk.BOTH, expand=True)
        self._main.columnconfigure(0, weight=1)
        self._main.rowconfigure(8, weight=1)

        # Engine row
        engine_row = ttk.Frame(self._main)
        engine_row.grid(row=0, column=0, sticky="ew")
        engine_row.columnconfigure(1, weight=1)
        ttk.Label(engine_row, text=LBL_ENGINE).grid(row=0, column=0, padx=(0, 8))
        self._engine_var = tk.StringVar()
        self._engine_cb = ttk.Combobox(
            engine_row, textvariable=self._engine_var, state="readonly"
        )
        self._engine_cb.grid(row=0, column=1, sticky="ew")
        self._engine_cb.bind("<<ComboboxSelected>>", self._on_engine_changed)

        # Engine info
        self._engine_info_var = tk.StringVar(value="")
        self._engine_info_lbl = ttk.Label(
            self._main, textvariable=self._engine_info_var, wraplength=580,
            foreground="#555"
        )
        self._engine_info_lbl.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        # Primary action button
        self._primary_btn = ttk.Button(
            self._main, text=BTN_PRIMARY_IDLE, command=self._on_primary_click
        )
        self._primary_btn.grid(row=3, column=0, pady=(24, 12))

        # Status text
        self._status_var = tk.StringVar(value=MSG_IDLE)
        ttk.Label(
            self._main, textvariable=self._status_var, wraplength=580
        ).grid(row=4, column=0, sticky="ew")

        # ETA / chapter detail text
        self._eta_var = tk.StringVar(value="")
        ttk.Label(
            self._main, textvariable=self._eta_var, foreground="#666"
        ).grid(row=5, column=0, sticky="ew", pady=(2, 8))

        # Progress bar
        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress = ttk.Progressbar(
            self._main,
            variable=self._progress_var,
            maximum=1000,  # sub-percent resolution
            mode="determinate",
        )
        self._progress.grid(row=6, column=0, sticky="ew", pady=(0, 12))

        # Footer row (toggle details / open folder / help)
        footer = ttk.Frame(self._main)
        footer.grid(row=7, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        self._toggle_btn = ttk.Button(
            footer, text=BTN_SHOW_LOG, command=self._toggle_details
        )
        self._toggle_btn.grid(row=0, column=0, sticky="w")
        self._open_folder_btn = ttk.Button(
            footer,
            text=BTN_OPEN_FOLDER,
            command=self._open_output_folder,
            state=tk.DISABLED,
        )
        self._open_folder_btn.grid(row=0, column=1)
        self._help_lbl = ttk.Label(
            footer, text=LNK_HELP, foreground="#0366d6", cursor="hand2"
        )
        self._help_lbl.grid(row=0, column=2, sticky="e")
        self._help_lbl.bind("<Button-1>", self._open_help)

        # Details / log panel (hidden by default)
        self._details_frame = ttk.Frame(self._main)
        # Gridded on toggle_details()
        self._log_text = scrolledtext.ScrolledText(
            self._details_frame,
            height=10,
            wrap=tk.WORD,
            font=("Menlo", 10),
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)
        self._log_text.configure(state=tk.DISABLED)

        # --- populate engines -----------------------------------------
        self._engines_by_label: dict[str, TTSEngine] = {}
        self._populate_engines()

    # ------------------------------------------------------------------
    # Engine population
    # ------------------------------------------------------------------

    def _populate_engines(self) -> None:
        """Fill the engine dropdown with engines whose check_status reports
        available. Subprocess engines (Chatterbox) are promoted to the top
        of the list so they become the default when their venv is
        installed — the rationale is that they're the highest-quality
        option, so offer them first when available."""
        labels: list[str] = []
        self._engines_by_label.clear()

        for engine in list_engines():
            status = engine.check_status()
            if not status.available:
                continue
            label = engine.display_name
            if engine.uses_subprocess:
                labels.insert(0, label)
            else:
                labels.append(label)
            self._engines_by_label[label] = engine

        if not labels:
            self._engine_var.set("")
            self._engine_info_var.set(WARN_NO_ENGINES_AVAILABLE)
            self._primary_btn.config(state=tk.DISABLED)
            return

        self._engine_cb["values"] = labels
        self._engine_var.set(labels[0])
        self._refresh_engine_info()

    def _refresh_engine_info(self) -> None:
        """Update the info label to match the currently-selected engine."""
        label = self._engine_var.get()
        engine_id = self._engine_id_for_label(label)
        info = ENGINE_INFO.get(engine_id, "")
        self._engine_info_var.set(info)

    def _engine_id_for_label(self, label: str) -> str:
        """Map a dropdown label back to an engine id."""
        engine = self._engines_by_label.get(label)
        return engine.id if engine is not None else ""

    def _on_engine_changed(self, _event: object) -> None:
        """Combobox ``<<ComboboxSelected>>`` handler — refreshes the info label."""
        self._refresh_engine_info()

    # ------------------------------------------------------------------
    # Primary button
    # ------------------------------------------------------------------

    def _on_primary_click(self) -> None:
        """Primary button click: start a new job, or cancel the running one."""
        if self._synth_running:
            self._request_cancel()
            return
        self._pick_and_start()

    def _pick_and_start(self) -> None:
        """Prompt the user for a PDF and, if chosen, kick off synthesis."""
        path = filedialog.askopenfilename(
            title=DLG_PICK_PDF_TITLE,
            filetypes=[(DLG_PDF_FILTER_NAME, "*.pdf"), ("Kaikki tiedostot", "*.*")],
        )
        if not path:
            return
        self._pdf_path = Path(path)
        self._output_path = self._pdf_path.with_suffix(".mp3")
        self._status_var.set(f"Valittu: {self._pdf_path.name}")

        engine_id = self._engine_id_for_label(self._engine_var.get())
        if not engine_id:
            messagebox.showerror(DLG_ERROR_TITLE, "Valitse ensin moottori.")
            return

        self._start_synthesis(engine_id)

    # ------------------------------------------------------------------
    # Synthesis dispatch
    # ------------------------------------------------------------------

    def _start_synthesis(self, engine_id: str) -> None:
        """Dispatch synthesis for ``engine_id`` and start polling events.

        Chatterbox-Finnish goes through the subprocess bridge; other engines
        run in-process on a background thread. Either way, events land in
        ``self._event_queue`` and the ``_pump_events`` tick consumes them.
        """
        assert self._pdf_path is not None
        assert self._output_path is not None

        self._set_running_state()
        self._clear_log()
        self._append_log(f"PDF: {self._pdf_path}")
        self._append_log(f"Output: {self._output_path}")
        self._append_log(f"Engine: {engine_id}")

        engine = get_engine(engine_id)
        if engine is not None and engine.uses_subprocess:
            self._start_chatterbox_subprocess()
        else:
            self._start_inprocess_engine(engine_id)

        self.after(self.POLL_INTERVAL_MS, self._pump_events)

    # --- Chatterbox subprocess path ------------------------------------

    def _start_chatterbox_subprocess(self) -> None:
        """Launch the Chatterbox-Finnish runner script in its own venv.

        Fails the job via ``self._fail()`` if the venv or runner script is
        missing. On success, starts a daemon relay thread that forwards
        bridge events onto the main event queue.
        """
        assert self._pdf_path is not None
        python_exe = resolve_chatterbox_python()
        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        if python_exe is None or not runner_script.exists():
            self._fail(
                "Chatterbox-moottoria ei ole asennettu. Asenna se "
                "Asetukset-paneelin \"Asenna moottoreita…\" -painikkeesta."
            )
            return

        out_dir = (_REPO_ROOT / "dist" / "audiobook").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        self._chatterbox_runner = ChatterboxRunner(
            python_exe=str(python_exe),
            script_path=str(runner_script),
            pdf_path=str(self._pdf_path),
            out_dir=str(out_dir),
        )
        try:
            self._chatterbox_runner.start()
        except Exception as exc:
            self._fail(f"Subprocess ei käynnistynyt: {exc}")
            return

        # Spawn a relay thread that drains the bridge's queue onto ours.
        threading.Thread(
            target=self._relay_chatterbox_events,
            daemon=True,
            name="chatterbox-relay",
        ).start()

    def _relay_chatterbox_events(self) -> None:
        """Daemon thread body: drain the bridge queue onto ``_event_queue``."""
        runner = self._chatterbox_runner
        assert runner is not None
        while not runner.finished:
            ev = runner.poll_event(timeout=0.2)
            if ev is not None:
                self._event_queue.put(ev)

    # --- In-process Edge-TTS / Piper path ------------------------------

    def _start_inprocess_engine(self, engine_id: str) -> None:
        """Start Edge-TTS / Piper synthesis on a daemon background thread."""
        engine = get_engine(engine_id)
        if engine is None:
            self._fail(f"Moottoria '{engine_id}' ei löytynyt.")
            return
        threading.Thread(
            target=self._run_inprocess,
            args=(engine,),
            daemon=True,
            name=f"tts-{engine_id}",
        ).start()

    def _run_inprocess(self, engine: TTSEngine) -> None:
        """Background thread — must not touch Tkinter directly.

        Pushes events onto ``self._event_queue``; the ``_pump_events`` tick
        marshals them back to the UI thread.
        """
        try:
            assert self._pdf_path is not None
            assert self._output_path is not None
            self._event_queue.put(
                ProgressEvent(kind="log", raw_line=MSG_PARSING)
            )
            book = parse_pdf(str(self._pdf_path))
            if not book.full_text.strip():
                raise ValueError("PDF ei sisällä tekstiä (tiedosto voi olla skannattu).")

            voice_id = engine.default_voice("fi")
            if voice_id is None:
                raise RuntimeError("Moottorilla ei ole suomenkielistä ääntä.")

            def progress_cb(current: int, total: int, msg: str) -> None:
                self._event_queue.put(
                    ProgressEvent(
                        kind="chunk",
                        total_done=current,
                        total_chunks=max(total, 1),
                        raw_line=msg,
                    )
                )

            engine.synthesize(
                book.full_text,
                str(self._output_path),
                voice_id,
                "fi",
                progress_cb,
            )
            self._event_queue.put(
                ProgressEvent(
                    kind="full_done", output_path=str(self._output_path)
                )
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=0))
        except Exception as exc:
            self._event_queue.put(
                ProgressEvent(kind="error", raw_line=str(exc))
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=1))

    # ------------------------------------------------------------------
    # Event pump — Tk main thread
    # ------------------------------------------------------------------

    def _pump_events(self) -> None:
        """Drain queued events on the Tk main thread and reschedule self."""
        had_exit = False
        while True:
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(ev)
            if ev.kind == "exit":
                had_exit = True
        if not had_exit and self._synth_running:
            self.after(self.POLL_INTERVAL_MS, self._pump_events)

    def _handle_event(self, ev: ProgressEvent) -> None:
        """Apply one ``ProgressEvent`` to the UI (progress bar, status, log)."""
        if ev.raw_line:
            self._append_log(ev.raw_line)

        if ev.kind == "setup_total":
            self._eta_var.set(
                f"Yhteensä {ev.total_chunks} palaa synteesissä."
            )
        elif ev.kind == "setup_cached":
            self._progress_var.set(
                (ev.total_done / max(ev.total_chunks, 1)) * 1000
            )
            self._eta_var.set(
                f"Jatketaan välimuistista: {ev.total_done}/{ev.total_chunks} palaa valmiina."
            )
        elif ev.kind == "chunk":
            if ev.total_chunks > 0:
                self._progress_var.set(
                    (ev.total_done / ev.total_chunks) * 1000
                )
            if ev.chapter_total > 0:
                self._status_var.set(
                    f"Luku {ev.chapter_idx}/{ev.chapter_total}, "
                    f"pala {ev.chunk_idx}/{ev.chunk_total}"
                )
                if ev.elapsed_s or ev.eta_s:
                    self._eta_var.set(
                        f"Kulunut {int(ev.elapsed_s // 60)} min — "
                        f"jäljellä noin {int(ev.eta_s // 60)} min"
                    )
            else:
                # In-process engine path (simplified callback).
                self._status_var.set(ev.raw_line or "Synteesi käynnissä…")
        elif ev.kind == "full_done" or ev.kind == "chapter_done":
            if ev.output_path:
                self._output_path = Path(ev.output_path)
        elif ev.kind == "done":
            self._progress_var.set(1000)
        elif ev.kind == "error":
            pass  # handled on exit
        elif ev.kind == "signal":
            self._cancel_requested = True
        elif ev.kind == "exit":
            self._on_synth_exit(ev.returncode)

    def _on_synth_exit(self, returncode: int) -> None:
        """Finalise UI state when the synthesis worker exits.

        ``returncode`` is 0 for success; non-zero is treated as failure
        unless ``self._cancel_requested`` is set (then we report cancelled).
        """
        self._synth_running = False
        self._primary_btn.config(text=BTN_PRIMARY_IDLE, state=tk.NORMAL)
        if returncode == 0 and not self._cancel_requested:
            self._progress_var.set(1000)
            path = (
                self._output_path.name if self._output_path else ""
            )
            self._status_var.set(MSG_DONE_FMT.format(path=path))
            self._open_folder_btn.config(state=tk.NORMAL)
            messagebox.showinfo(
                DLG_DONE_TITLE,
                MSG_DONE_FMT.format(path=self._output_path or ""),
            )
        elif self._cancel_requested:
            self._status_var.set(MSG_CANCELLED)
            self._cancel_requested = False
        else:
            tail = ""
            if self._chatterbox_runner is not None:
                tail = "\n".join(self._chatterbox_runner.tail_lines(15))
            self._status_var.set("Virhe — katso loki.")
            messagebox.showerror(
                DLG_ERROR_TITLE,
                MSG_ERROR_FMT.format(
                    err=tail or "Tuntematon virhe. Avaa loki näkyviin."
                ),
            )
        self._chatterbox_runner = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _request_cancel(self) -> None:
        """Ask the current job to stop. In-process engines finish the current chunk first."""
        self._cancel_requested = True
        self._primary_btn.config(text=BTN_CANCELLING, state=tk.DISABLED)
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()
        # For in-process engines there is no clean cancel — the thread
        # will run until the current chunk finishes and the next event
        # carries the signal.

    # ------------------------------------------------------------------
    # Output folder
    # ------------------------------------------------------------------

    def _open_output_folder(self) -> None:
        """Open the output MP3's containing folder in the OS file manager."""
        if self._output_path is None:
            return
        folder = self._output_path.parent
        if not folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    # ------------------------------------------------------------------
    # Details / log panel
    # ------------------------------------------------------------------

    def _toggle_details(self) -> None:
        """Show or hide the collapsible log panel."""
        if self._details_visible:
            self._details_frame.grid_remove()
            self._toggle_btn.config(text=BTN_SHOW_LOG)
        else:
            self._details_frame.grid(
                row=8, column=0, sticky="nsew", pady=(12, 0)
            )
            self._toggle_btn.config(text=BTN_HIDE_LOG)
        self._details_visible = not self._details_visible

    def _clear_log(self) -> None:
        """Wipe the log text widget, toggling its read-only state around it."""
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _append_log(self, line: str) -> None:
        """Append ``line`` plus newline to the log widget and auto-scroll."""
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, line + "\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Help link
    # ------------------------------------------------------------------

    def _open_help(self, _event: object = None) -> None:
        """Open the bundled README in the browser, or the GitHub README as fallback."""
        help_path = _REPO_ROOT / "README.md"
        if help_path.exists():
            webbrowser.open(help_path.as_uri())
        else:
            webbrowser.open(
                "https://github.com/MikkoNumminen/AudiobookMaker"
                "#readme"
            )

    # ------------------------------------------------------------------
    # UI state helpers
    # ------------------------------------------------------------------

    def _set_running_state(self) -> None:
        """Flip widgets to the busy state (primary button becomes Cancel, etc.)."""
        self._synth_running = True
        self._cancel_requested = False
        self._primary_btn.config(text=BTN_PRIMARY_CANCEL, state=tk.NORMAL)
        self._open_folder_btn.config(state=tk.DISABLED)
        self._progress_var.set(0)
        self._status_var.set(MSG_STARTING)
        self._eta_var.set("")

    def _fail(self, message: str) -> None:
        """Abort the current job, surface ``message`` in the status bar and a dialog."""
        self._synth_running = False
        self._primary_btn.config(text=BTN_PRIMARY_IDLE, state=tk.NORMAL)
        self._status_var.set(message)
        messagebox.showerror(DLG_ERROR_TITLE, message)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def self_test() -> int:
    """Headless sanity check: import engines, construct + destroy the window.

    Used by CI smoke tests (the Windows Actions runner calls
    ``AudiobookMakerLauncher.exe --self-test``) and by local verification
    that the frozen .exe can at least reach its main loop without crashing.
    Returns 0 on success, non-zero on any failure.
    """
    try:
        engines = list_engines()
        print(f"[self-test] engines registered: {[e.id for e in engines]}", flush=True)
        app = LauncherApp()
        app.update_idletasks()
        print(
            f"[self-test] window title={app.title()!r} "
            f"geometry={app.geometry()!r}",
            flush=True,
        )
        values = list(app._engine_cb["values"])
        print(f"[self-test] engine dropdown: {values}", flush=True)
        app.destroy()
        print("[self-test] OK", flush=True)
        return 0
    except Exception as exc:
        print(f"[self-test] FAILED: {exc!r}", flush=True, file=sys.stderr)
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    """Launcher CLI entry point.

    Accepts an optional ``argv`` (defaults to ``sys.argv[1:]``). If
    ``--self-test`` is present, runs the headless self-check; otherwise
    shows the Tk window and returns 0 once the user closes it.
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--self-test" in argv:
        return self_test()
    app = LauncherApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
