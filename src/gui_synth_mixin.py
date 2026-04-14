"""Synthesis orchestration mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import queue
import re
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from src.launcher_bridge import ChatterboxRunner, ProgressEvent, resolve_chatterbox_python
from src.pdf_parser import parse_pdf
from src.tts_base import TTSEngine, get_engine

def _parse_book_any(path: str):
    """Dispatch to the right parser by extension.

    Kept as a tiny local helper so the mixin doesn't import
    ``gui_unified`` (that would create a circular import).
    """
    from src.epub_parser import parse_epub
    from pathlib import Path as _P
    ext = _P(path).suffix.lower()
    if ext == ".epub":
        return parse_epub(path)
    if ext == ".txt":
        from src.pdf_parser import BookMetadata, Chapter, ParsedBook
        txt = _P(path).read_text(encoding="utf-8", errors="replace")
        meta = BookMetadata(
            title=_P(path).stem.replace("_", " ").title(),
            num_pages=1, file_path=str(path),
        )
        ch = Chapter(title=meta.title or "Text", content=txt,
                     page_start=1, page_end=1, index=0)
        return ParsedBook(metadata=meta, chapters=[ch])
    return parse_pdf(path)
from src.tts_engine import TTSConfig, chapters_to_speech

if TYPE_CHECKING:
    from src.tts_base import Voice

    class _SynthHost(Protocol):
        """Static contract describing host attributes SynthMixin reads/writes."""

        # Run-state flags
        _synth_running: bool
        _cancel_requested: bool
        _cancel_flag: threading.Event

        # Widgets (CTk/Tk — typed as Any to avoid heavy stub deps)
        _listen_btn: Any
        _convert_btn: Any
        _cancel_btn: Any
        _open_folder_btn: Any
        _progress_bar: Any
        _status_label_val: Any
        _eta_label: Any
        _text_widget: Any
        _output_mode_cb: Any
        _speed_cb: Any
        _log_text: Any

        # Tk variables
        _ref_audio_var: Any
        _voice_desc_var: Any
        _out_var: Any

        # Input/output state
        _input_mode: str
        _pdf_path: Optional[str]
        _text_has_placeholder: bool
        _output_path: Optional[str]
        _output_user_chosen: bool
        _ui_lang: str

        # Runtime plumbing
        _chatterbox_runner: Optional[ChatterboxRunner]
        _event_queue: "queue.Queue[ProgressEvent]"
        POLL_INTERVAL_MS: int

        # Methods
        def _s(self, key: str) -> str: ...
        def after(self, ms: int, func: Optional[Callable[..., Any]] = ...) -> str: ...
        def _fail(self, msg: str) -> None: ...
        def _clear_log(self) -> None: ...
        def _append_log(self, line: str) -> None: ...
        def _append_log_error(self, line: str) -> None: ...
        def _append_log_warning(self, line: str) -> None: ...
        def _append_log_success(self, line: str) -> None: ...
        def _current_voice(self) -> "Optional[Voice]": ...
        def _current_language(self) -> str: ...

    _Base = _SynthHost
else:
    _Base = object


# Lines that represent a successful chunk/chapter progress step. Mirrors the
# regex in gui_unified.py so both code paths color progress lines green.
_PROGRESS_SUCCESS_RE = re.compile(
    r"\[chapter\s+\d+/\d+\]\s+(?:chunk\s+\d+/\d+|idx=\d+)"
)


class SynthMixin(_Base):
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
        epub_path = None

        if self._input_mode == "pdf":
            if not self._pdf_path:
                self._fail(self._s("no_pdf"))
                return
            # The "Kirja" tab accepts PDF/EPUB/TXT — pick the right CLI
            # flag for the Chatterbox subprocess based on extension.
            from pathlib import Path as _P
            ext = _P(self._pdf_path).suffix.lower()
            if ext == ".epub":
                epub_path = str(self._pdf_path)
            elif ext == ".txt":
                text_path = str(self._pdf_path)
            else:
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
            self._fail(self._s("chatterbox_venv_missing"))
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

        # Language routing: EN -> base multilingual model + bundled ref clip
        # (produces native English with Grandmom timbre). FI -> Finnish T3
        # finetune (current default). See memory/project_english_grandmom.md.
        language = self._current_language()

        self._chatterbox_runner = ChatterboxRunner(
            python_exe=str(python_exe),
            script_path=str(runner_script),
            pdf_path=pdf_path,
            text_path=text_path,
            epub_path=epub_path,
            out_dir=str(out_dir),
            extra_args=extra_args,
            language=language,
        )

        input_label = pdf_path or epub_path or text_path or "text"
        self._append_log(f"Input: {input_label}")
        self._append_log(f"Output: {out_dir}")
        self._append_log("Engine: chatterbox_fi")

        try:
            self._chatterbox_runner.start()
        except Exception as exc:
            self._fail(self._s("subprocess_failed").format(error=exc))
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
                raise RuntimeError(
                    self._s("engine_not_found_id").format(engine_id=engine_id)
                )

            self._event_queue.put(
                ProgressEvent(kind="log", raw_line=self._s("reading_input"))
            )

            if input_mode == "pdf":
                assert pdf_path is not None
                book = _parse_book_any(pdf_path)
                text = book.full_text
                if not text.strip():
                    raise ValueError(self._s("pdf_no_text"))
            else:
                text = input_text or ""

            if not text:
                raise ValueError(self._s("no_text_to_synth"))

            voice = self._current_voice()
            if voice is None:
                # Fallback to engine default.
                voice_id = engine.default_voice(self._current_language())
                if voice_id is None:
                    raise RuntimeError(self._s("engine_no_voice_for_lang"))
            else:
                voice_id = voice.id

            lang = self._current_language()
            ref_audio = self._ref_audio_var.get() or None
            voice_desc = self._voice_desc_var.get() or None
            mode = OUTPUT_MODES[self._ui_lang].get(self._output_mode_cb.get(), "single")
            assert self._output_path is not None

            def progress_cb(current: int, total: int, msg: str) -> None:
                if self._cancel_flag.is_set():
                    raise InterruptedError(self._s("user_cancelled_synth"))
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
                book = _parse_book_any(self._pdf_path)
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
                    raise RuntimeError(self._s("chapters_only_edge"))
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
                ProgressEvent(kind="signal", raw_line=self._s("cancelled"))
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
            # Auto-detect severity from the content so WARNING lines
            # from the Chatterbox runner show up yellow and errors red.
            line = ev.raw_line
            upper = line.upper()
            if ev.kind == "error" or "ERROR:" in upper or "TRACEBACK" in upper or "\u2718" in line:
                self._append_log_error(line)
            elif "WARNING" in upper or "WARN:" in upper or "FUTUREWARNING" in upper or "DEPRECATIONWARNING" in upper:
                self._append_log_warning(line)
            elif (
                "\u2714" in line
                or "DONE" in upper
                or "VALMIS" in upper
                or _PROGRESS_SUCCESS_RE.search(line) is not None
            ):
                self._append_log_success(line)
            else:
                self._append_log(line)

        if ev.kind == "setup_total":
            self._eta_label.configure(
                text=self._s("total_chunks").format(n=ev.total_chunks)
            )
        elif ev.kind == "setup_cached":
            self._progress_bar.set(ev.total_done / max(ev.total_chunks, 1))
            self._eta_label.configure(
                text=self._s("cache_resume").format(
                    done=ev.total_done, total=ev.total_chunks
                )
            )
        elif ev.kind == "chunk":
            if ev.total_chunks > 0:
                self._progress_bar.set(ev.total_done / ev.total_chunks)
            if ev.chapter_total > 0:
                self._status_label_val.configure(
                    text=self._s("chapter_chunk_status").format(
                        ci=ev.chapter_idx, ct=ev.chapter_total,
                        chi=ev.chunk_idx, cht=ev.chunk_total,
                    )
                )
                if ev.elapsed_s or ev.eta_s:
                    self._eta_label.configure(
                        text=self._s("elapsed_eta").format(
                            elapsed=int(ev.elapsed_s // 60),
                            eta=int(ev.eta_s // 60),
                        )
                    )
            else:
                self._status_label_val.configure(
                    text=ev.raw_line or self._s("synth_in_progress")
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
            self._append_log_error(f"\u2718 {self._s('cancelling')}")
            self._cancel_requested = False
        else:
            tail = ""
            if self._chatterbox_runner is not None:
                tail = "\n".join(self._chatterbox_runner.tail_lines(15))
            self._status_label_val.configure(text=f"{self._s('error')} \u2014 log")
            self._append_log_error(
                self._s("error_exit_code").format(
                    error=self._s("error"), rc=returncode
                )
            )
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
