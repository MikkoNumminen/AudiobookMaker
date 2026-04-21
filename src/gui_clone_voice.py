"""Clone-voice-from-file orchestration — pure controller, no Tk.

This module owns the *logic* of the clone-voice feature: it drives
analyze (via :mod:`src.voice_pack_subproc`), enumerates detected
speakers, asks the UI layer to name them, picks a reference clip per
named speaker, packages few_shot voice packs, and installs them into
``~/.audiobookmaker/voice_packs/``.

The GUI layer (Sub-slice 4b) supplies a small number of callbacks:

* ``request_names_fn(speakers) -> list[SpeakerNamingDecision]`` — blocks
  the controller until the user closes the naming modal. Tests pass a
  pre-canned decision list.
* ``progress_cb(event)`` — stream of :class:`CloneVoiceProgress` events
  the GUI forwards to the main log box via ``_append_log_*`` helpers.

Every genuine I/O boundary (analyze subprocess, yaml read, reference
picker, package, install) is injected so the test module can run the
whole pipeline in-memory against fakes. The real GUI uses the module-
level defaults.

Copyright hygiene (CLAUDE.md P0 rule): the controller never echoes the
raw source filename into progress events. ``wav_display_name`` is
derived from the basename only, and the GUI is expected to pass it
through a redaction helper before logging. Default suggested speaker
names are ``Narrator 1``, ``Narrator 2`` — never anything derived from
the source file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence


# ---------------------------------------------------------------------------
# public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectedSpeaker:
    """One entry from the analyze stage's ``speakers.yaml``.

    ``speaker_id`` is the pyannote label (``SPEAKER_00`` etc.).
    ``default_name`` is what the naming modal pre-fills — always a
    generic ``Narrator N`` string so nothing copyrighted leaks in.
    """

    speaker_id: str
    total_seconds: float
    chunk_count: int
    quality_tier: str
    default_name: str

    @property
    def total_minutes(self) -> float:
        return self.total_seconds / 60.0


@dataclass(frozen=True)
class SpeakerNamingDecision:
    """What the user chose for one detected speaker.

    ``include`` False means "skip this speaker, don't build a pack" —
    the naming modal has a per-row checkbox for this.
    """

    speaker_id: str
    name: str
    include: bool = True


@dataclass
class CloneVoiceJobConfig:
    """Inputs the GUI collects in the pre-analyze modal."""

    wav_path: Path
    language: str  # "fi" or "en"
    num_speakers: Optional[int] = None
    min_speakers: Optional[int] = None
    max_speakers: Optional[int] = None
    diarizer: Optional[str] = None  # "ecapa" | None (pyannote default)
    scratch_dir: Optional[Path] = None  # forced to .local/clone_scratch/<ts> by GUI


# Stage identifiers for CloneVoiceProgress.stage. Distinct from
# voice_pack_subproc's analyze stages so the GUI can route log lines to
# the right section. ANALYZE_* are re-emitted verbatim from the
# subprocess stream; the rest are controller-level stages.
STAGE_STARTING = "starting"
STAGE_ANALYZE = "analyze"
STAGE_ANALYZE_LINE = "analyze_line"
STAGE_SPEAKERS_DETECTED = "speakers_detected"
STAGE_AWAITING_NAMES = "awaiting_names"
STAGE_PICK_REFERENCE = "pick_reference"
STAGE_PACKAGE = "package"
STAGE_INSTALL = "install"
STAGE_SPEAKER_DONE = "speaker_done"
STAGE_SPEAKER_SKIPPED = "speaker_skipped"
STAGE_DONE = "done"
STAGE_ERROR = "error"
STAGE_CANCELLED = "cancelled"


@dataclass(frozen=True)
class CloneVoiceProgress:
    """One progress event the GUI forwards to the log box.

    ``stage`` is one of the ``STAGE_*`` constants above. ``message`` is
    a plain-English one-liner safe to show unredacted (never contains
    the raw source path — the controller uses ``wav_display_name`` only).
    ``speaker_id`` is set on per-speaker stages; ``pack_path`` is set on
    the INSTALL stage once install_pack returns.
    """

    stage: str
    message: str
    speaker_id: Optional[str] = None
    pack_path: Optional[Path] = None
    extra: dict = field(default_factory=dict)


@dataclass
class CloneVoiceJobResult:
    """Summary returned when the job finishes (success, cancel, or error)."""

    ok: bool
    installed_packs: list[Path] = field(default_factory=list)
    skipped_speakers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error: Optional[str] = None
    cancelled: bool = False


# ---------------------------------------------------------------------------
# callback type aliases
# ---------------------------------------------------------------------------


ProgressCallback = Callable[[CloneVoiceProgress], None]
"""Invoked for every controller-level event. Never called after the
final DONE/ERROR/CANCELLED event."""

RequestNamesFn = Callable[[Sequence[DetectedSpeaker]], Sequence[SpeakerNamingDecision]]
"""Blocks until the user closes the naming modal. Returning an empty
sequence is treated as "cancel — don't build any packs" (maps to
CANCELLED, not ERROR, because the user chose to back out)."""

CancelCheckFn = Callable[[], bool]
"""Called at every controller checkpoint. Return True to abort the
job. The GUI wires this to a threading.Event."""


# ---------------------------------------------------------------------------
# defaults for injected I/O
# ---------------------------------------------------------------------------


def _default_analyze_fn(**kwargs: Any) -> Any:  # pragma: no cover - thin shim
    from src.voice_pack_subproc import run_analyze

    return run_analyze(**kwargs)


def _default_read_speakers_yaml(path: Path) -> list[dict]:  # pragma: no cover
    import yaml

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        raise ValueError(f"speakers.yaml must be a list, got {type(data).__name__}")
    return data


def _default_pick_reference_fn(**kwargs: Any) -> Any:  # pragma: no cover
    from src.voice_pack.reference_picker import pick_reference_clip

    return pick_reference_clip(**kwargs)


def _default_package_fn(**kwargs: Any) -> Path:  # pragma: no cover
    from scripts.voice_pack_package import package

    return package(**kwargs)


def _default_install_fn(pack_dir: Path) -> Any:  # pragma: no cover
    from src.voice_pack.pack import install_pack

    return install_pack(pack_dir)


# ---------------------------------------------------------------------------
# utilities
# ---------------------------------------------------------------------------


def suggest_default_name(index: int) -> str:
    """Return the generic default name for the ``index``-th detected speaker.

    1-indexed so the first narrator is ``Narrator 1``. Always generic —
    CLAUDE.md forbids leaking any source-derived identifiers.
    """
    return f"Narrator {index + 1}"


def _dec_by_id(
    decisions: Sequence[SpeakerNamingDecision], speaker_id: str
) -> Optional[SpeakerNamingDecision]:
    for d in decisions:
        if d.speaker_id == speaker_id:
            return d
    return None


def _detected_from_yaml(entries: Sequence[dict]) -> list[DetectedSpeaker]:
    """Turn a speakers.yaml payload into typed DetectedSpeaker entries.

    Ordering preserved from the yaml (biggest speaker first — the
    analyze stage already sorts that way). ``default_name`` is assigned
    by enumeration index so Narrator 1 = the biggest speaker.
    """
    out: list[DetectedSpeaker] = []
    for i, entry in enumerate(entries):
        speaker_id = str(entry.get("speaker") or f"SPEAKER_{i:02d}")
        total_seconds = float(entry.get("total_seconds") or 0.0)
        chunk_count = int(entry.get("chunk_count") or 0)
        quality_tier = str(entry.get("quality_tier") or "skip")
        out.append(
            DetectedSpeaker(
                speaker_id=speaker_id,
                total_seconds=total_seconds,
                chunk_count=chunk_count,
                quality_tier=quality_tier,
                default_name=suggest_default_name(i),
            )
        )
    return out


# ---------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------


def run_clone_voice_job(
    config: CloneVoiceJobConfig,
    *,
    request_names_fn: RequestNamesFn,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_check_fn: Optional[CancelCheckFn] = None,
    wav_display_name: Optional[str] = None,
    # injected I/O (all optional for prod, overridden in tests)
    analyze_fn: Callable[..., Any] = _default_analyze_fn,
    read_speakers_yaml_fn: Callable[[Path], list[dict]] = _default_read_speakers_yaml,
    pick_reference_fn: Callable[..., Any] = _default_pick_reference_fn,
    package_fn: Callable[..., Path] = _default_package_fn,
    install_fn: Callable[[Path], Any] = _default_install_fn,
    # packaging inputs — the GUI supplies these so prod and tests share
    # the same controller surface
    voice_packs_root: Optional[Path] = None,
) -> CloneVoiceJobResult:
    """Run the full clone-voice pipeline end-to-end.

    The GUI layer calls this on a worker thread so the controller's
    blocking ``request_names_fn`` callback can be satisfied by a modal
    marshalling through ``root.after(0, …)``.

    Parameters
    ----------
    config
        What the pre-analyze modal collected.
    request_names_fn
        Called once after analyze completes to ask the UI for names.
    progress_cb
        Optional. Every controller event is forwarded here.
    cancel_check_fn
        Optional. Called at each checkpoint; truthy return aborts.
    wav_display_name
        Safe-to-log basename of the source audio. The controller never
        looks at ``config.wav_path`` for display purposes; only this.
        Defaults to ``source_audio`` when None — callers should pass a
        redacted basename.
    """

    def emit(event: CloneVoiceProgress) -> None:
        if progress_cb is not None:
            progress_cb(event)

    def cancelled() -> bool:
        return bool(cancel_check_fn and cancel_check_fn())

    display = wav_display_name or "source_audio"
    scratch_dir = config.scratch_dir or (config.wav_path.parent / ".clone_scratch")
    scratch_dir = Path(scratch_dir)

    result = CloneVoiceJobResult(ok=False)

    emit(
        CloneVoiceProgress(
            stage=STAGE_STARTING,
            message=f"Starting clone-voice pipeline for {display}.",
        )
    )

    if cancelled():
        emit(CloneVoiceProgress(stage=STAGE_CANCELLED, message="Cancelled."))
        result.cancelled = True
        return result

    # --- analyze --------------------------------------------------------
    emit(
        CloneVoiceProgress(
            stage=STAGE_ANALYZE,
            message="Listening to the file and figuring out who speaks when…",
        )
    )

    def _analyze_progress(analyze_event: Any) -> None:
        # Forward as ANALYZE_LINE events so the GUI can render subprocess
        # stdout without the controller needing to know the analyze
        # event schema beyond "has .message".
        emit(
            CloneVoiceProgress(
                stage=STAGE_ANALYZE_LINE,
                message=getattr(analyze_event, "message", str(analyze_event)),
            )
        )

    analyze_result = analyze_fn(
        wav=config.wav_path,
        out_dir=scratch_dir,
        num_speakers=config.num_speakers,
        min_speakers=config.min_speakers,
        max_speakers=config.max_speakers,
        diarizer=config.diarizer,
        progress_cb=_analyze_progress,
    )

    if not getattr(analyze_result, "ok", False):
        err = getattr(analyze_result, "error", None) or "Analyze failed."
        result.error = err
        result.errors.append(err)
        emit(CloneVoiceProgress(stage=STAGE_ERROR, message=err))
        return result

    if cancelled():
        emit(CloneVoiceProgress(stage=STAGE_CANCELLED, message="Cancelled."))
        result.cancelled = True
        return result

    speakers_yaml_path = getattr(analyze_result, "speakers_yaml_path", None)
    transcripts_path = getattr(analyze_result, "transcripts_path", None)
    if speakers_yaml_path is None or transcripts_path is None:
        err = "Analyze finished but output paths are missing."
        result.error = err
        result.errors.append(err)
        emit(CloneVoiceProgress(stage=STAGE_ERROR, message=err))
        return result

    try:
        raw_entries = read_speakers_yaml_fn(Path(speakers_yaml_path))
    except Exception as exc:
        err = f"Could not read speakers.yaml: {exc}"
        result.error = err
        result.errors.append(err)
        emit(CloneVoiceProgress(stage=STAGE_ERROR, message=err))
        return result

    detected = _detected_from_yaml(raw_entries)
    if not detected:
        err = "Analyze detected no speakers in the file."
        result.error = err
        result.errors.append(err)
        emit(CloneVoiceProgress(stage=STAGE_ERROR, message=err))
        return result

    emit(
        CloneVoiceProgress(
            stage=STAGE_SPEAKERS_DETECTED,
            message=f"Detected {len(detected)} speaker(s).",
            extra={"speakers": list(detected)},
        )
    )

    # --- ask for names --------------------------------------------------
    emit(
        CloneVoiceProgress(
            stage=STAGE_AWAITING_NAMES,
            message="Waiting for you to name each voice…",
        )
    )
    decisions = list(request_names_fn(detected))
    if not decisions:
        emit(
            CloneVoiceProgress(
                stage=STAGE_CANCELLED,
                message="No names chosen — nothing to install.",
            )
        )
        result.cancelled = True
        return result

    # Any detected speaker the user didn't return a decision for counts
    # as a skip. Same for include=False decisions.
    approved: list[tuple[DetectedSpeaker, SpeakerNamingDecision]] = []
    for spk in detected:
        dec = _dec_by_id(decisions, spk.speaker_id)
        if dec is None or not dec.include or not dec.name.strip():
            result.skipped_speakers.append(spk.speaker_id)
            emit(
                CloneVoiceProgress(
                    stage=STAGE_SPEAKER_SKIPPED,
                    message=f"Skipping {spk.speaker_id} (not named).",
                    speaker_id=spk.speaker_id,
                )
            )
            continue
        approved.append((spk, dec))

    # --- per-speaker pipeline ------------------------------------------
    for spk, dec in approved:
        if cancelled():
            emit(CloneVoiceProgress(stage=STAGE_CANCELLED, message="Cancelled."))
            result.cancelled = True
            return result

        speaker_scratch = scratch_dir / spk.speaker_id
        speaker_scratch.mkdir(parents=True, exist_ok=True)
        reference_out = speaker_scratch / "reference.wav"

        emit(
            CloneVoiceProgress(
                stage=STAGE_PICK_REFERENCE,
                message=f"Picking a clean reference clip for {dec.name}…",
                speaker_id=spk.speaker_id,
            )
        )
        try:
            pick_reference_fn(
                transcripts=Path(transcripts_path),
                speaker_id=spk.speaker_id,
                wav_source=config.wav_path,
                out_path=reference_out,
            )
        except Exception as exc:
            err = f"Reference pick failed for {dec.name}: {exc}"
            result.errors.append(err)
            emit(
                CloneVoiceProgress(
                    stage=STAGE_SPEAKER_SKIPPED,
                    message=err,
                    speaker_id=spk.speaker_id,
                )
            )
            continue

        emit(
            CloneVoiceProgress(
                stage=STAGE_PACKAGE,
                message=f"Building voice pack for {dec.name}…",
                speaker_id=spk.speaker_id,
            )
        )
        try:
            pack_dir = package_fn(
                out_dir=speaker_scratch / "pack",
                name=dec.name,
                language=config.language,
                tier="few_shot",
                tier_reason=(
                    "Auto-picked few_shot clip from user-supplied audio; "
                    "deeper-quality training not yet wired up."
                ),
                total_source_minutes=spk.total_minutes,
                sample_path=reference_out,
                reference_path=reference_out,
            )
        except Exception as exc:
            err = f"Packaging failed for {dec.name}: {exc}"
            result.errors.append(err)
            emit(
                CloneVoiceProgress(
                    stage=STAGE_SPEAKER_SKIPPED,
                    message=err,
                    speaker_id=spk.speaker_id,
                )
            )
            continue

        emit(
            CloneVoiceProgress(
                stage=STAGE_INSTALL,
                message=f"Installing {dec.name} into your voice library…",
                speaker_id=spk.speaker_id,
                pack_path=Path(pack_dir),
            )
        )
        try:
            install_kwargs = {}
            if voice_packs_root is not None:
                install_kwargs["root"] = voice_packs_root
            installed = install_fn(Path(pack_dir), **install_kwargs) if install_kwargs else install_fn(Path(pack_dir))
        except Exception as exc:
            err = f"Install failed for {dec.name}: {exc}"
            result.errors.append(err)
            emit(
                CloneVoiceProgress(
                    stage=STAGE_SPEAKER_SKIPPED,
                    message=err,
                    speaker_id=spk.speaker_id,
                )
            )
            continue

        installed_root = getattr(installed, "root", installed)
        installed_path = Path(installed_root) if installed_root is not None else Path(pack_dir)
        result.installed_packs.append(installed_path)

        emit(
            CloneVoiceProgress(
                stage=STAGE_SPEAKER_DONE,
                message=f"Added {dec.name} to your voices.",
                speaker_id=spk.speaker_id,
                pack_path=installed_path,
            )
        )

    # --- done -----------------------------------------------------------
    result.ok = bool(result.installed_packs)
    if result.ok:
        emit(
            CloneVoiceProgress(
                stage=STAGE_DONE,
                message=(
                    f"Added {len(result.installed_packs)} voice(s). "
                    "Check the Voice dropdown."
                ),
            )
        )
    else:
        msg = "Finished but no voices were installed."
        result.error = result.error or msg
        emit(CloneVoiceProgress(stage=STAGE_ERROR, message=msg))
    return result


# ---------------------------------------------------------------------------
# i18n strings (FI / EN parity)
# ---------------------------------------------------------------------------


CLONE_VOICE_STRINGS: dict[str, dict[str, str]] = {
    "fi": {
        "clone_voice_btn": "Kloonaa \u00e4\u00e4ni tiedostosta\u2026",
        "clone_voice_title": "Kloonaa \u00e4\u00e4ni tiedostosta",
        "clone_voice_intro": (
            "Valitse \u00e4\u00e4nitiedosto, niin kuuntelemme sen ja teemme "
            "jokaisesta puhujasta oman \u00e4\u00e4nen. Annat jokaiselle "
            "\u00e4\u00e4nelle nimen ennen tallennusta."
        ),
        "clone_voice_language_q": "Kieli:",
        "clone_voice_language_fi": "Suomi",
        "clone_voice_language_en": "Englanti",
        "clone_voice_speakers_q": "Montako puhujaa tiedostossa on?",
        "clone_voice_speakers_1": "1 (yksi kertoja)",
        "clone_voice_speakers_2": "2 (kaksi kertojaa)",
        "clone_voice_speakers_range": "3\u20138 (n\u00e4ytelm\u00e4 tai haastattelu)",
        "clone_voice_speakers_auto": "Tunnista automaattisesti",
        "clone_voice_start": "Aloita",
        "clone_voice_cancel": "Peruuta",
        "clone_voice_pick_file_title": "Valitse \u00e4\u00e4nitiedosto",
        "clone_voice_naming_title": "Nime\u00e4 \u00e4\u00e4net",
        "clone_voice_naming_intro": (
            "Tunnistimme n\u00e4m\u00e4 puhujat. Anna kullekin nimi jota haluat "
            "k\u00e4ytt\u00e4\u00e4 \u00c4\u00e4ni-valikossa. Jos et halua "
            "tallentaa jotakin, poista ruksi Tallenna-sarakkeesta."
        ),
        "clone_voice_naming_col_speaker": "Puhuja",
        "clone_voice_naming_col_minutes": "Kesto",
        "clone_voice_naming_col_chunks": "Pätkiä",
        "clone_voice_naming_col_name": "Nimi",
        "clone_voice_naming_col_include": "Tallenna",
        "clone_voice_naming_save": "Tallenna \u00e4\u00e4net",
        "clone_voice_minutes_fmt": "{min:.1f} min",
        "clone_voice_not_installed_title": "Kloonaustoiminto puuttuu",
        "clone_voice_not_installed_body": (
            "Voice Cloner ei ole asennettu. Avaa Moottoreiden hallinta ja "
            "asenna Voice Cloner sielt\u00e4."
        ),
    },
    "en": {
        "clone_voice_btn": "Clone voice from file\u2026",
        "clone_voice_title": "Clone voice from file",
        "clone_voice_intro": (
            "Pick an audio file and we'll listen to it and make a voice "
            "for each person speaking. You'll name each voice before we "
            "save it."
        ),
        "clone_voice_language_q": "Language:",
        "clone_voice_language_fi": "Finnish",
        "clone_voice_language_en": "English",
        "clone_voice_speakers_q": "How many speakers are in the file?",
        "clone_voice_speakers_1": "1 (one narrator)",
        "clone_voice_speakers_2": "2 (two narrators)",
        "clone_voice_speakers_range": "3\u20138 (play or interview)",
        "clone_voice_speakers_auto": "Detect automatically",
        "clone_voice_start": "Start",
        "clone_voice_cancel": "Cancel",
        "clone_voice_pick_file_title": "Pick an audio file",
        "clone_voice_naming_title": "Name the voices",
        "clone_voice_naming_intro": (
            "We found these speakers. Give each one a name you want to "
            "see in the Voice menu. Uncheck Save to skip any you don't "
            "want."
        ),
        "clone_voice_naming_col_speaker": "Speaker",
        "clone_voice_naming_col_minutes": "Duration",
        "clone_voice_naming_col_chunks": "Chunks",
        "clone_voice_naming_col_name": "Name",
        "clone_voice_naming_col_include": "Save",
        "clone_voice_naming_save": "Save voices",
        "clone_voice_minutes_fmt": "{min:.1f} min",
        "clone_voice_not_installed_title": "Voice Cloner missing",
        "clone_voice_not_installed_body": (
            "Voice Cloner is not installed. Open the Engine Manager and "
            "install Voice Cloner from there."
        ),
    },
}


def clone_voice_string(ui_lang: str, key: str) -> str:
    """Look up ``key`` in the clone-voice string table for ``ui_lang``.

    Falls through to FI if the requested language has no entry, and
    returns the key itself if neither language defines it (so missing
    keys show up visibly in the UI during development).
    """
    table = CLONE_VOICE_STRINGS.get(ui_lang) or CLONE_VOICE_STRINGS["fi"]
    return table.get(key) or CLONE_VOICE_STRINGS["fi"].get(key) or key


# ---------------------------------------------------------------------------
# modal dialogs
# ---------------------------------------------------------------------------


def _ctk_or_raise():
    """Lazy-import customtkinter so the pure-logic tests above stay Tk-free."""
    import customtkinter as ctk  # type: ignore[import-not-found]

    return ctk


# Each speaker-count preset maps to (num_speakers, min_speakers, max_speakers).
SPEAKER_PRESETS: dict[str, tuple[Optional[int], Optional[int], Optional[int]]] = {
    "1": (1, None, None),
    "2": (2, None, None),
    "range": (None, 3, 8),
    "auto": (None, None, None),
}


class PreAnalyzeModal:
    """Pre-analyze modal asking for language + speaker count.

    Blocking: call :meth:`show` which runs the dialog and returns a
    (language, num_speakers, min_speakers, max_speakers) tuple or None
    if the user cancelled. Kept as a thin wrapper around a CTkToplevel
    so tests can instantiate it without running the mainloop (the
    ``_build`` method is side-effect-free beyond widget creation).
    """

    def __init__(self, parent, ui_lang: str = "fi") -> None:
        self._parent = parent
        self._ui_lang = ui_lang
        self._result: Optional[tuple[str, Optional[int], Optional[int], Optional[int]]] = None
        self._preset_key: str = "auto"
        self._language: str = ui_lang if ui_lang in ("fi", "en") else "fi"
        ctk = _ctk_or_raise()
        self._top = ctk.CTkToplevel(parent)
        self._top.title(self._s("clone_voice_title"))
        self._top.geometry("480x360")
        self._top.transient(parent)
        self._build()

    def _s(self, key: str) -> str:
        return clone_voice_string(self._ui_lang, key)

    def _build(self) -> None:
        ctk = _ctk_or_raise()
        import tkinter as tk

        pad = 12
        intro = ctk.CTkLabel(
            self._top,
            text=self._s("clone_voice_intro"),
            wraplength=440,
            justify="left",
            anchor="w",
        )
        intro.pack(fill=tk.X, padx=pad, pady=(pad, 6))

        # Language row
        lang_row = ctk.CTkFrame(self._top, fg_color="transparent")
        lang_row.pack(fill=tk.X, padx=pad, pady=4)
        ctk.CTkLabel(lang_row, text=self._s("clone_voice_language_q")).pack(
            side=tk.LEFT
        )
        self._lang_var = tk.StringVar(value=self._language)
        lang_values = [self._s("clone_voice_language_fi"), self._s("clone_voice_language_en")]
        self._lang_name_to_code = {
            self._s("clone_voice_language_fi"): "fi",
            self._s("clone_voice_language_en"): "en",
        }
        initial_lang_name = (
            self._s("clone_voice_language_fi")
            if self._language == "fi"
            else self._s("clone_voice_language_en")
        )
        self._lang_cb = ctk.CTkComboBox(
            lang_row, values=lang_values, state="readonly", width=160,
        )
        self._lang_cb.set(initial_lang_name)
        self._lang_cb.pack(side=tk.LEFT, padx=(8, 0))

        # Speaker-count radios
        ctk.CTkLabel(
            self._top, text=self._s("clone_voice_speakers_q"),
        ).pack(anchor="w", padx=pad, pady=(10, 2))

        self._preset_var = tk.StringVar(value="auto")
        preset_frame = ctk.CTkFrame(self._top, fg_color="transparent")
        preset_frame.pack(fill=tk.X, padx=pad)
        for key, label_key in (
            ("1", "clone_voice_speakers_1"),
            ("2", "clone_voice_speakers_2"),
            ("range", "clone_voice_speakers_range"),
            ("auto", "clone_voice_speakers_auto"),
        ):
            ctk.CTkRadioButton(
                preset_frame, text=self._s(label_key),
                variable=self._preset_var, value=key,
            ).pack(anchor="w", pady=1)

        # Buttons
        btn_row = ctk.CTkFrame(self._top, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=pad, pady=(pad, pad))
        self._start_btn = ctk.CTkButton(
            btn_row, text=self._s("clone_voice_start"),
            command=self._on_start, width=140,
        )
        self._start_btn.pack(side=tk.RIGHT)
        self._cancel_btn = ctk.CTkButton(
            btn_row, text=self._s("clone_voice_cancel"),
            command=self._on_cancel, width=120,
            fg_color="#555", hover_color="#444",
        )
        self._cancel_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def _on_start(self) -> None:
        name = self._lang_cb.get()
        lang = self._lang_name_to_code.get(name, "fi")
        preset = self._preset_var.get()
        num, mn, mx = SPEAKER_PRESETS.get(preset, (None, None, None))
        self._result = (lang, num, mn, mx)
        self._top.destroy()

    def _on_cancel(self) -> None:
        self._result = None
        self._top.destroy()

    def show(self) -> Optional[tuple[str, Optional[int], Optional[int], Optional[int]]]:
        """Run the modal blocking. Returns the config tuple or None."""
        self._top.grab_set()
        self._top.wait_window()
        return self._result


class NamingGridModal:
    """Per-speaker naming grid.

    Builds one row per :class:`DetectedSpeaker` with minutes/chunks
    labels, a name entry pre-filled with ``Narrator N``, an include
    checkbox, and a Save button. Blocking: :meth:`show` returns a list
    of :class:`SpeakerNamingDecision` or an empty list if the user
    cancelled the modal.
    """

    def __init__(
        self,
        parent,
        speakers: Sequence[DetectedSpeaker],
        *,
        ui_lang: str = "fi",
    ) -> None:
        self._parent = parent
        self._ui_lang = ui_lang
        self._speakers = list(speakers)
        self._result: list[SpeakerNamingDecision] = []
        ctk = _ctk_or_raise()
        self._top = ctk.CTkToplevel(parent)
        self._top.title(self._s("clone_voice_naming_title"))
        self._top.geometry("560x420")
        self._top.transient(parent)
        self._name_vars: list[Any] = []
        self._include_vars: list[Any] = []
        self._build()

    def _s(self, key: str) -> str:
        return clone_voice_string(self._ui_lang, key)

    def _build(self) -> None:
        ctk = _ctk_or_raise()
        import tkinter as tk

        pad = 12
        intro = ctk.CTkLabel(
            self._top,
            text=self._s("clone_voice_naming_intro"),
            wraplength=520,
            justify="left",
            anchor="w",
        )
        intro.pack(fill=tk.X, padx=pad, pady=(pad, 6))

        grid = ctk.CTkFrame(self._top)
        grid.pack(fill=tk.BOTH, expand=True, padx=pad, pady=6)

        # Header row
        for col, key in enumerate((
            "clone_voice_naming_col_speaker",
            "clone_voice_naming_col_minutes",
            "clone_voice_naming_col_chunks",
            "clone_voice_naming_col_name",
            "clone_voice_naming_col_include",
        )):
            ctk.CTkLabel(
                grid, text=self._s(key), font=ctk.CTkFont(weight="bold"),
            ).grid(row=0, column=col, padx=6, pady=(6, 2), sticky="w")

        for i, spk in enumerate(self._speakers, start=1):
            ctk.CTkLabel(grid, text=spk.speaker_id).grid(
                row=i, column=0, padx=6, pady=2, sticky="w",
            )
            minutes_text = self._s("clone_voice_minutes_fmt").format(
                min=spk.total_minutes,
            )
            ctk.CTkLabel(grid, text=minutes_text).grid(
                row=i, column=1, padx=6, pady=2, sticky="w",
            )
            ctk.CTkLabel(grid, text=str(spk.chunk_count)).grid(
                row=i, column=2, padx=6, pady=2, sticky="w",
            )

            name_var = tk.StringVar(value=spk.default_name)
            self._name_vars.append(name_var)
            entry = ctk.CTkEntry(grid, textvariable=name_var, width=200)
            entry.grid(row=i, column=3, padx=6, pady=2, sticky="ew")

            include_var = tk.BooleanVar(value=True)
            self._include_vars.append(include_var)
            ctk.CTkCheckBox(
                grid, text="", variable=include_var, width=20,
            ).grid(row=i, column=4, padx=6, pady=2)

        # Buttons
        btn_row = ctk.CTkFrame(self._top, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=pad, pady=(pad, pad))
        self._save_btn = ctk.CTkButton(
            btn_row, text=self._s("clone_voice_naming_save"),
            command=self._on_save, width=160,
        )
        self._save_btn.pack(side=tk.RIGHT)
        self._cancel_btn = ctk.CTkButton(
            btn_row, text=self._s("clone_voice_cancel"),
            command=self._on_cancel, width=120,
            fg_color="#555", hover_color="#444",
        )
        self._cancel_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def _collect_decisions(self) -> list[SpeakerNamingDecision]:
        out: list[SpeakerNamingDecision] = []
        for spk, name_var, include_var in zip(
            self._speakers, self._name_vars, self._include_vars,
        ):
            out.append(
                SpeakerNamingDecision(
                    speaker_id=spk.speaker_id,
                    name=name_var.get().strip(),
                    include=bool(include_var.get()),
                )
            )
        return out

    def _on_save(self) -> None:
        self._result = self._collect_decisions()
        self._top.destroy()

    def _on_cancel(self) -> None:
        self._result = []
        self._top.destroy()

    def show(self) -> list[SpeakerNamingDecision]:
        self._top.grab_set()
        self._top.wait_window()
        return self._result


# ---------------------------------------------------------------------------
# filename redaction (CLAUDE.md P0)
# ---------------------------------------------------------------------------


# Keywords that strongly suggest a copyrighted source — if the basename
# contains any of these (case-insensitive), we fall back to a generic
# placeholder instead of echoing the real filename. This is the first
# line of defence; the controller above also never echoes the raw path.
_COPYRIGHT_HINTS = (
    "audiobook", "unabridged", "chapter",
    # Common author-year-publisher patterns that leak titles.
    "_19", "_20",  # "_1999_", "_2003_", etc.
)


def safe_source_display_name(path: Path) -> str:
    """Return a log-safe display name for the source audio file.

    Returns the basename when it looks neutral; falls back to
    ``source_audio`` when the name smells like a copyrighted-source
    identifier. The GUI feeds this into every log-message template so
    the main log box never carries the raw filename.
    """
    name = path.name
    lower = name.lower()
    if any(hint in lower for hint in _COPYRIGHT_HINTS):
        return "source_audio"
    if len(name) > 40:
        return "source_audio"
    return name
