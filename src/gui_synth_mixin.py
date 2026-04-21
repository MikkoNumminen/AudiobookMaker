"""Synthesis orchestration mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from src.launcher_bridge import ChatterboxRunner, ProgressEvent
from src.synthesis_orchestrator import (
    ChatterboxBuildError,
    ChatterboxRequest,
    build_chatterbox_runner,
)

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
        _sample_btn: Any
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


class SynthMixin(_Base):
    """Mixin providing synthesis orchestration (in-process and subprocess).

    Expects the host class to provide:
    - Various state attributes (_synth_running, _cancel_flag, etc.)
    - UI widget references
    - self._s(key), self.after(ms, cb), etc.
    """

    def _set_running_state(self) -> None:
        self._synth_running = True
        # Hosts that render a "done in Xs" strip read this attribute
        # from _update_done_strip; hosts that don't simply ignore it.
        # Setting unconditionally keeps the two run-state paths unified.
        self._synth_started_at = datetime.now()
        self._cancel_requested = False
        self._cancel_flag.clear()
        self._listen_btn.configure(state="disabled")
        self._convert_btn.configure(state="disabled")
        self._sample_btn.configure(state="disabled")
        self._cancel_btn.grid()
        self._open_folder_btn.configure(state="disabled")
        self._progress_bar.grid()
        self._progress_bar.set(0)
        self._status_label_val.configure(
            text=self._s("making_sample") if getattr(self, "_is_sample_run", False)
            else self._s("converting")
        )
        self._eta_label.configure(text="")
        self._clear_log()

    def _set_idle_state(self) -> None:
        self._synth_running = False
        self._listen_btn.configure(state="normal")
        self._convert_btn.configure(state="normal")
        self._sample_btn.configure(state="normal")
        # Re-enable the Open-folder button so the user can browse the
        # output the moment synthesis returns control. Runner reference
        # is cleared here too — _on_synth_exit double-clears, which is
        # harmless and keeps belt-and-suspenders for non-exit idle paths
        # (e.g. successful "done" event handling in UnifiedApp).
        self._open_folder_btn.configure(state="normal")
        self._chatterbox_runner = None
        self._cancel_btn.grid_remove()
        # Idle means nothing is converting — the bar is conversion-only
        # clutter now. Status label stays so "Valmis!" / sample path is
        # still readable.
        self._progress_bar.grid_remove()

    # ---- Chatterbox subprocess ----------------------------------------

    def _start_chatterbox_subprocess(
        self,
        text_override: Optional[str] = None,
        output_basename_override: Optional[str] = None,
    ) -> None:
        """Spawn the Chatterbox runner. ``text_override`` lets the
        sample flow inject a 500-char snippet without changing the
        widget. ``output_basename_override`` controls the temp file
        stem so the runner produces ``<out_dir>/<stem>/00_full.mp3``.

        Widget state is captured on the main thread and frozen into a
        :class:`ChatterboxRequest`; the actual tempfile + argv assembly
        happens inside :func:`build_chatterbox_runner` in the orchestrator.
        """
        from src.gui_unified import _REPO_ROOT

        # Gather widget state before handing off.
        content: Optional[str] = None
        if text_override is None and self._input_mode == "text":
            content = self._text_widget.get("1.0", tk.END).strip()
            if self._text_has_placeholder:
                content = ""

        out_var = self._out_var.get() if hasattr(self, "_out_var") else ""
        output_path_hint = (
            out_var
            if out_var and out_var not in ("Ei valittu", "Not selected", "")
            else None
        )

        # Chatterbox chunk size override. 300 chars is the runner's built-in
        # default — we only pass the flag when the user dialed it away.
        chunk_chars = 300
        chunk_var = getattr(self, "_chunk_chars_var", None)
        if chunk_var is not None:
            try:
                chunk_chars = int(chunk_var.get())
            except (ValueError, tk.TclError):
                chunk_chars = 300

        # Voice pack root: when the user picked a ``voicepack:<slug>`` voice
        # the subprocess needs --voice-pack <dir> so it can load the bundled
        # LoRA / metadata alongside the reference clip. The Ref. ääni field
        # is already populated separately by _effective_reference_audio for
        # the sample.wav / reference.wav path.
        voice_pack_path: Optional[str] = None
        voice = self._current_voice() if hasattr(self, "_current_voice") else None
        voice_id = getattr(voice, "id", None) if voice is not None else None
        resolver = getattr(self, "_resolve_voice_pack", None)
        if voice_id and resolver is not None:
            pack = resolver(voice_id)
            if pack is not None:
                voice_pack_path = str(pack.root)

        request = ChatterboxRequest(
            input_mode=self._input_mode,
            pdf_path=self._pdf_path,
            input_text=content,
            text_override=text_override,
            output_basename_override=output_basename_override,
            output_path_hint=output_path_hint,
            reference_audio=self._ref_audio_var.get() or None,
            chunk_chars=chunk_chars,
            # Language routing: EN -> base multilingual model + bundled ref
            # clip. FI -> Finnish T3 finetune.
            # See memory/project_english_grandmom.md.
            language=self._current_language(),
            voice_pack_path=voice_pack_path,
        )

        runner_script = _REPO_ROOT / "scripts" / "generate_chatterbox_audiobook.py"
        default_out_dir = Path.home() / "Documents" / "AudiobookMaker"

        try:
            plan = build_chatterbox_runner(
                request, runner_script, default_out_dir,
            )
        except ChatterboxBuildError as err:
            self._fail(self._s(err.kind))
            return

        self._chatterbox_runner = plan.runner
        self._append_log(f"Input: {plan.input_label}")
        self._append_log(f"Output: {plan.out_dir}")
        self._append_log("Engine: chatterbox_fi")

        try:
            plan.runner.start()
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

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        self._cancel_flag.set()
        self._cancel_btn.configure(text=self._s("cancelling"), state="disabled")
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()
