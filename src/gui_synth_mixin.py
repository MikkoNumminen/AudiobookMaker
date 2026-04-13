"""Synthesis orchestration mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING, Optional

from src.launcher_bridge import ChatterboxRunner, ProgressEvent, resolve_chatterbox_python
from src.pdf_parser import parse_pdf
from src.tts_base import TTSEngine, get_engine
from src.tts_engine import TTSConfig, chapters_to_speech

if TYPE_CHECKING:
    pass


class SynthMixin:
    """Mixin providing synthesis orchestration (in-process and subprocess).

    Expects the host class to provide:
    - Various state attributes (_synth_running, _cancel_flag, etc.)
    - UI widget references
    - self._s(key), self.after(ms, cb), etc.
    """

    def _set_running_state(self) -> None:
        self._synth_running = True
        self._cancel_requested = False
        self._cancel_flag.clear()
        self._listen_btn.configure(state="disabled")
        self._convert_btn.configure(state="disabled")
        self._cancel_btn.grid()
        self._open_folder_btn.configure(state="disabled")
        self._progress_bar.set(0)
        self._status_label_val.configure(text=self._s("converting"))
        self._eta_label.configure(text="")
        self._clear_log()

    def _set_idle_state(self) -> None:
        self._synth_running = False
        self._listen_btn.configure(state="normal")
        self._convert_btn.configure(state="normal")
        self._cancel_btn.grid_remove()

    # ---- Chatterbox subprocess ----------------------------------------

    def _start_chatterbox_subprocess(self) -> None:
        from src.gui_unified import _REPO_ROOT

        pdf_path = None
        text_path = None

        if self._input_mode == "pdf":
            if not self._pdf_path:
                self._fail(self._s("no_pdf"))
                return
            pdf_path = str(self._pdf_path)
        else:
            content = self._text_widget.get("1.0", tk.END).strip()
            if not content or self._text_has_placeholder:
                self._fail(self._s("no_text"))
                return
            # Write text to a temp file for the subprocess.
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8",
            )
            tmp.write(content)
            tmp.close()
            text_path = tmp.name

        python_exe = resolve_chatterbox_python()
        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        if python_exe is None or not runner_script.exists():
            self._fail(
                "Chatterbox-venviä ei löytynyt. Asenna se ensin "
                "suorittamalla scripts/setup_chatterbox_windows.bat."
            )
            return

        # Use output path's parent directory, or a sensible default.
        out_var = self._out_var.get() if hasattr(self, '_out_var') else ""
        if out_var and out_var not in ("Ei valittu", "Not selected", ""):
            out_dir = Path(out_var).parent
        else:
            out_dir = Path.home() / "Documents" / "AudiobookMaker"
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        extra_args: list[str] = []
        ref_audio = self._ref_audio_var.get()
        if ref_audio:
            extra_args.extend(["--ref-audio", ref_audio])

        self._chatterbox_runner = ChatterboxRunner(
            python_exe=str(python_exe),
            script_path=str(runner_script),
            pdf_path=pdf_path,
            text_path=text_path,
            out_dir=str(out_dir),
            extra_args=extra_args,
        )

        input_label = pdf_path or text_path or "text"
        self._append_log(f"Input: {input_label}")
        self._append_log(f"Output: {out_dir}")
        self._append_log("Engine: chatterbox_fi")

        try:
            self._chatterbox_runner.start()
        except Exception as exc:
            self._fail(f"Subprocess ei käynnistynyt: {exc}")
            return

        threading.Thread(
            target=self._relay_chatterbox_events, daemon=True,
            name="chatterbox-relay",
        ).start()

    def _relay_chatterbox_events(self) -> None:
        runner = self._chatterbox_runner
        assert runner is not None
        while not runner.finished:
            ev = runner.poll_event(timeout=0.2)
            if ev is not None:
                self._event_queue.put(ev)

    # ---- In-process engine thread -------------------------------------

    def _start_inprocess_engine(self, engine_id: str) -> None:
        self._append_log(f"Engine: {engine_id}")
        self._append_log(f"Output: {self._output_path}")
        # Capture input on the main thread (thread-safe) before spawning.
        input_mode = self._input_mode
        pdf_path = self._pdf_path
        input_text = None
        if input_mode == "text" and not self._text_has_placeholder:
            input_text = self._text_widget.get("1.0", tk.END).strip()
        threading.Thread(
            target=self._run_inprocess,
            args=(engine_id, input_mode, pdf_path, input_text),
            daemon=True, name=f"tts-{engine_id}",
        ).start()

    def _run_inprocess(
        self, engine_id: str, input_mode: str,
        pdf_path: Optional[str], input_text: Optional[str],
    ) -> None:
        """Background thread. Communicates with UI only via event queue."""
        from src.gui_unified import OUTPUT_MODES, SPEED_OPTIONS

        try:
            engine = get_engine(engine_id)
            if engine is None:
                raise RuntimeError(f"Moottoria '{engine_id}' ei löytynyt.")

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line="Luetaan syötettä\u2026")
            )

            if input_mode == "pdf":
                assert pdf_path is not None
                book = parse_pdf(pdf_path)
                text = book.full_text
                if not text.strip():
                    raise ValueError(self._s("pdf_no_text"))
            else:
                text = input_text or ""

            if not text:
                raise ValueError("Ei tekstiä syntetisoitavaksi.")

            voice = self._current_voice()
            if voice is None:
                # Fallback to engine default.
                voice_id = engine.default_voice(self._current_language())
                if voice_id is None:
                    raise RuntimeError(
                        "Moottorilla ei ole ääntä valitulle kielelle."
                    )
            else:
                voice_id = voice.id

            lang = self._current_language()
            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None
            mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_cb.get(), "single")
            assert self._output_path is not None

            def progress_cb(current: int, total: int, msg: str) -> None:
                if self._cancel_flag.is_set():
                    raise InterruptedError("Käyttäjä peruutti synteesin.")
                self._event_queue.put(
                    ProgressEvent(
                        kind="chunk",
                        total_done=current,
                        total_chunks=max(total, 1),
                        raw_line=msg,
                    )
                )

            if mode == "chapters" and self._input_mode == "pdf":
                assert self._pdf_path is not None
                book = parse_pdf(self._pdf_path)
                if engine_id == "edge":
                    chapters = [(ch.title, ch.content) for ch in book.chapters]
                    rate = SPEED_OPTIONS[self._ui_lang].get(self._speed_cb.get(), "+0%")
                    config = TTSConfig(
                        language=lang, voice=voice_id, rate=rate
                    )
                    chapters_to_speech(
                        chapters, self._output_path, config, progress_cb
                    )
                else:
                    raise RuntimeError(
                        "Lukukohtainen tulostus on tällä hetkellä tuettu "
                        "vain Edge-TTS-moottorilla."
                    )
            else:
                engine.synthesize(
                    text, self._output_path, voice_id, lang,
                    progress_cb,
                    reference_audio=ref_audio,
                    voice_description=voice_desc,
                )

            self._event_queue.put(
                ProgressEvent(
                    kind="full_done", output_path=str(self._output_path)
                )
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=0))

        except InterruptedError:
            self._event_queue.put(
                ProgressEvent(kind="signal", raw_line="Peruutettu.")
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=0))
        except Exception as exc:
            self._event_queue.put(
                ProgressEvent(kind="error", raw_line=str(exc))
            )
            self._event_queue.put(ProgressEvent(kind="exit", returncode=1))

    # ------------------------------------------------------------------
    # Event pump (Tk main thread)
    # ------------------------------------------------------------------

    def _pump_events(self) -> None:
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
        if ev.raw_line:
            self._append_log(ev.raw_line)

        if ev.kind == "setup_total":
            self._eta_label.configure(
                text=f"Yhteensä {ev.total_chunks} palaa synteesissä."
            )
        elif ev.kind == "setup_cached":
            self._progress_bar.set(ev.total_done / max(ev.total_chunks, 1))
            self._eta_label.configure(
                text=f"Jatketaan välimuistista: "
                f"{ev.total_done}/{ev.total_chunks} palaa valmiina."
            )
        elif ev.kind == "chunk":
            if ev.total_chunks > 0:
                self._progress_bar.set(ev.total_done / ev.total_chunks)
            if ev.chapter_total > 0:
                self._status_label_val.configure(
                    text=f"Luku {ev.chapter_idx}/{ev.chapter_total}, "
                    f"pala {ev.chunk_idx}/{ev.chunk_total}"
                )
                if ev.elapsed_s or ev.eta_s:
                    self._eta_label.configure(
                        text=f"Kulunut {int(ev.elapsed_s // 60)} min \u2014 "
                        f"jäljellä noin {int(ev.eta_s // 60)} min"
                    )
            else:
                self._status_label_val.configure(
                    text=ev.raw_line or "Synteesi käynnissä\u2026"
                )
        elif ev.kind in ("full_done", "chapter_done"):
            if ev.output_path:
                self._output_path = ev.output_path
        elif ev.kind == "done":
            self._progress_bar.set(1.0)
        elif ev.kind == "signal":
            self._cancel_requested = True
        elif ev.kind == "exit":
            self._on_synth_exit(ev.returncode)

    def _on_synth_exit(self, returncode: int) -> None:
        self._set_idle_state()

        if returncode == 0 and not self._cancel_requested:
            self._progress_bar.set(1.0)
            out_name = (
                Path(self._output_path).name if self._output_path else ""
            )
            self._status_label_val.configure(text=f"{self._s('done')} {out_name}")
            self._open_folder_btn.configure(state="normal")
            messagebox.showinfo(self._s("done"), f"{out_name}")
        elif self._cancel_requested:
            self._status_label_val.configure(text=self._s("cancelling"))
            self._cancel_requested = False
        else:
            tail = ""
            if self._chatterbox_runner is not None:
                tail = "\n".join(self._chatterbox_runner.tail_lines(15))
            self._status_label_val.configure(text=f"{self._s('error')} \u2014 log")
            messagebox.showerror(
                self._s("error"),
                tail or self._s("error"),
            )

        self._chatterbox_runner = None

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        self._cancel_flag.set()
        self._cancel_btn.configure(text=self._s("cancelling"), state="disabled")
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()
