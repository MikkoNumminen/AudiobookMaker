"""Synthesis orchestration mixin for the AudiobookMaker GUI."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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
        # Animate immediately (indeterminate) instead of sitting at a static 0:
        # the Chatterbox engine can take tens of seconds — minutes on a first
        # run — to load its models before the first chunk, and a frozen 0% bar
        # reads as a hang. The first chunk switches it to a real percentage.
        self._begin_progress_indeterminate()
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
        # Delete any tempfiles the Chatterbox build materialised (a .docx
        # pre-extraction, or a sample / pasted-text snippet). The subprocess
        # has finished reading them by the time the run reaches idle, so they
        # are safe to remove; cleanup() is best-effort and idempotent. Storing
        # only plan.runner used to drop the plan, leaking these on every
        # Chatterbox run with .docx or sample input.
        plan = getattr(self, "_chatterbox_plan", None)
        if plan is not None:
            plan.cleanup()
            self._chatterbox_plan = None
        self._cancel_btn.grid_remove()
        # Idle means nothing is converting — the bar is conversion-only
        # clutter now. Status label stays so "Valmis!" / sample path is
        # still readable. Stop any indeterminate animation and reset to a
        # clean determinate state so the next run starts fresh.
        self._progress_to_determinate()
        self._progress_bar.grid_remove()

    def _begin_progress_indeterminate(self) -> None:
        """Animate the progress bar with no known total yet.

        Used at run start (and while the Chatterbox engine loads) so the run
        shows motion before the first chunk arrives. ``_progress_to_determinate``
        switches it back to a real 0..1 fraction once progress is known.
        """
        self._progress_indeterminate = True
        self._progress_bar.configure(mode="indeterminate")
        self._progress_bar.start()

    def _progress_to_determinate(self) -> None:
        """Switch the bar to a real 0..1 fraction (idempotent)."""
        if getattr(self, "_progress_indeterminate", False):
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_indeterminate = False

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

        # Voice pack root: when the user picked a ``voicepack:<slug>`` voice the
        # subprocess needs --voice-pack <dir> so the runner clones from the
        # pack's clip and loads its LoRA adapter. Resolve it ROBUSTLY (see
        # _selected_voice_pack — independent of the language-filtered
        # re-derivation _current_voice relies on, which could miss and silently
        # synthesize in the default voice: the field bug where an imported pack
        # came out sounding like Grandmom).
        voice_pack_path: Optional[str] = None
        selector = getattr(self, "_selected_voice_pack", None)
        if selector is not None:
            pack = selector()
            if pack is not None:
                voice_pack_path = str(pack.root)

        # Non-silent guard: a pack IS selected but couldn't be resolved to a
        # directory — never degrade to a Grandmom run that looks fine but is the
        # wrong voice. Surface it so the user can re-import the pack.
        pack_selected = getattr(self, "_selection_is_voice_pack", lambda: False)()
        if pack_selected and voice_pack_path is None:
            self._fail(self._s("voice_pack_unresolved"))
            return

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
        from src.synthesis_orchestrator import default_output_dir
        default_out_dir = default_output_dir()

        try:
            plan = build_chatterbox_runner(
                request, runner_script, default_out_dir,
            )
        except ChatterboxBuildError as err:
            self._fail(self._s(err.kind))
            return

        self._chatterbox_runner = plan.runner
        # Keep the whole plan, not just plan.runner — _set_idle_state calls
        # plan.cleanup() to delete the build's tempfiles when the run ends.
        self._chatterbox_plan = plan
        self._append_log(f"Input: {plan.input_label}")
        self._append_log(f"Output: {plan.out_dir}")
        self._append_log("Engine: chatterbox_grandmom")
        # Diagnostic provenance: WHICH script file and WHICH venv python this
        # run uses. A stale runner script, or a second venv (repair fixes one
        # while synthesis uses another), is invisible without these two lines.
        self._append_log(f"Runner: {runner_script}")
        self._append_log(f"Venv: {plan.runner.python_exe}")

        try:
            plan.runner.start()
        except Exception as exc:
            self._fail(self._s("subprocess_failed").format(error=exc))
            return

        # Record the job only once the subprocess is actually up. Saving
        # earlier would leave a "resumable" job behind for a run that never
        # produced a single cached chunk, and Continue would then offer to
        # resume nothing.
        #
        # A sample run is excluded: it is a 500-character snippet, it finishes
        # in seconds, and offering to continue one would be noise.
        if not getattr(self, "_is_sample_run", False):
            recorder = getattr(self, "_record_job_start", None)
            if recorder is not None:
                try:
                    recorder()
                except Exception:
                    logger.exception("could not record job start")

        threading.Thread(
            target=self._relay_chatterbox_events, daemon=True,
            name="chatterbox-relay",
        ).start()

    def _relay_chatterbox_events(self) -> None:
        # Capture the runner reference once at entry. The main thread may
        # clear ``self._chatterbox_runner`` mid-drain (see _set_idle_state
        # around line 121); without this capture the loop would race on
        # the attribute and could dereference None. A local reference is
        # simpler than a lock — the runner object itself is still valid
        # until its subprocess reaps, and we're the last reader.
        runner = self._chatterbox_runner
        if runner is None:
            return
        try:
            while not runner.finished:
                ev = runner.poll_event(timeout=0.2)
                if ev is not None:
                    self._event_queue.put(ev)
            # Final drain: an event (including the terminating 'done'/'exit')
            # can be queued in the instant ``finished`` flips True, after the
            # while-loop's last poll. Without this, that last event is
            # stranded and _pump_events hangs forever in "Converting…".
            while True:
                ev = runner.poll_event(timeout=0.0)
                if ev is None:
                    break
                self._event_queue.put(ev)
        except Exception as exc:
            # Broken pipes, decoder errors, or any other unexpected
            # failure from poll_event must not silently kill the relay
            # thread — the UI would then hang forever waiting for a
            # "done" event that never arrives. Log for diagnostics and
            # enqueue a synthetic error event so _pump_events surfaces
            # the failure via the normal _fail() path.
            logger.exception("Chatterbox relay thread crashed")
            try:
                self._event_queue.put(
                    ProgressEvent(kind="error", raw_line=f"relay: {exc}")
                )
            except Exception:
                # Queue put should not raise for an unbounded Queue, but
                # if something truly broken happens we must not re-raise
                # inside the daemon thread.
                logger.exception("Failed to enqueue relay failure event")

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        self._cancel_flag.set()
        self._cancel_btn.configure(text=self._s("cancelling"), state="disabled")
        if self._chatterbox_runner is not None:
            self._chatterbox_runner.cancel()
