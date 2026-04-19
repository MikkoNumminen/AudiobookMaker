"""Synthesis orchestration — GUI-agnostic business logic.

This module owns the non-UI parts of turning a book into an audiobook:

- Book loading (dispatch to the right parser by extension)
- Output-path derivation and collision-bumping
- Default output directory resolution (frozen vs dev mode)

The GUI (``src/gui_unified.py``) is the only caller today, but the logic
here has zero tkinter, customtkinter, or widget dependencies — so it can
be unit-tested without spinning up a window, and a future CLI or web UI
can share the same code paths.

Later phases will extend the module to own in-process engine dispatch
(``_run_inprocess``) and subprocess dispatch (Chatterbox bridge) so
``UnifiedApp`` becomes a thin adapter. See ``docs/AUDIT_REPORT.md`` §1
"UnifiedApp is a god-object".
"""

from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.epub_parser import parse_epub
from src.launcher_bridge import (
    ChatterboxRunner,
    ProgressEvent,
    resolve_chatterbox_python,
)
from src.pdf_parser import BookMetadata, Chapter, ParsedBook, parse_pdf
from src.tts_base import get_engine


# ---------------------------------------------------------------------------
# Book loading
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
# Output-path derivation
# ---------------------------------------------------------------------------


def default_output_dir() -> Path:
    """Return the default folder where generated MP3s go.

    Installed (frozen) mode: next to the running ``.exe`` (install root).
    Dev mode: ``./out/`` under the current working directory.

    The two modes are kept deliberately different: frozen builds drop
    output next to the installed .exe so non-technical users find their
    audiobooks in the place they expect, while dev work stays inside the
    repo's gitignored ``out/`` directory so nothing escapes into the
    developer's Documents folder or the repo root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd() / "out"


def suggest_output_path(
    input_mode: str,
    pdf_path: str | None,
    out_dir: Path | None = None,
) -> str:
    """Return a default output path for a conversion.

    Every path lands inside ``out_dir`` (or :func:`default_output_dir`
    when ``out_dir`` is ``None``). Never sibling-to-input, never at the
    repo root — the canonical output directory is the single source of
    truth for where generated material goes.

    - ``input_mode == "pdf"`` with a path: ``<out_dir>/<book-stem>.mp3``
      (``book.pdf`` -> ``<out_dir>/book.mp3``). Keeps the book-stem
      naming so users can tell which book produced the MP3; loses the
      original parent directory.
    - Otherwise (text-paste mode or no path): auto-increment
      ``texttospeech_1.mp3``, ``texttospeech_2.mp3``, ... inside ``out_dir``.

    The auto-increment scan stops at the first free slot — it does not
    skip gaps. So if ``texttospeech_3.mp3`` exists but ``_2`` doesn't, the
    caller gets ``_1`` back (or ``_2`` if ``_1`` also exists).

    The helper creates the output directory when it doesn't exist so the
    caller can immediately write to the returned path.
    """
    base_dir = out_dir if out_dir is not None else default_output_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    if input_mode == "pdf" and pdf_path:
        return str(base_dir / (Path(pdf_path).stem + ".mp3"))
    n = 1
    while True:
        candidate = base_dir / f"texttospeech_{n}.mp3"
        if not candidate.exists():
            return str(candidate)
        n += 1


_TRAILING_NUMBER_RE = re.compile(r"^(.*?)_(\d+)$")


def next_available_numbered_path(output_path: str) -> str:
    """Bump ``output_path`` to the next free numbered sibling.

    When the file already exists, returns the lowest-numbered sibling that
    doesn't exist yet. The numbering rule mirrors what the GUI has always
    done:

    - ``texttospeech_3.mp3`` (exists) -> ``texttospeech_4.mp3``
    - ``book.mp3`` (exists)           -> ``book_2.mp3``
    - ``book_5.mp3`` (exists)         -> ``book_6.mp3``

    When the path does *not* exist, it is returned unchanged — no-op is
    cheap at the call site.
    """
    target = Path(output_path)
    if not target.exists():
        return output_path

    stem = target.stem
    suffix = target.suffix or ".mp3"
    parent = target.parent

    # Split trailing _N off the stem, defaulting to 1 if not numbered.
    match = _TRAILING_NUMBER_RE.match(stem)
    if match:
        base, n = match.group(1), int(match.group(2))
    else:
        base, n = stem, 1

    while True:
        n += 1
        candidate = parent / f"{base}_{n}{suffix}"
        if not candidate.exists():
            return str(candidate)


# ---------------------------------------------------------------------------
# In-process synthesis (Edge, Piper, VoxCPM, ...)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InprocessRequest:
    """Everything needed to run one in-process synthesis job.

    The GUI captures widget state on the main thread and freezes it into
    a request before handing the work to a background thread. That keeps
    tkinter widgets from being read off-thread.
    """

    engine_id: str
    language: str
    input_mode: str  # "pdf" or "text"
    output_path: Optional[str] = None
    voice_id: Optional[str] = None
    pdf_path: Optional[str] = None
    input_text: Optional[str] = None
    reference_audio: Optional[str] = None
    voice_description: Optional[str] = None


EventSink = Callable[[ProgressEvent], None]
"""Callable that receives each ProgressEvent. The GUI wires this to its
``queue.Queue.put``; tests wire it to a list append."""


def run_inprocess_synthesis(
    request: InprocessRequest,
    on_event: EventSink,
) -> None:
    """Run a TTS synthesis job in-process, emitting ``ProgressEvent``s.

    This is the GUI-free core of what used to live on
    ``UnifiedApp._run_inprocess``. Designed to run on a background thread
    (the engine's ``synthesize()`` can block for minutes); events are
    emitted via the callable so the host doesn't need to know whether
    the consumer is a tkinter queue, a test list, or a future CLI.

    Events emitted (``kind`` values):
      - ``log``   — status updates ("Reading input...", "Synthesizing...")
      - ``chunk`` — per-chunk progress (``total_done`` / ``total_chunks``)
      - ``done``  — final success with ``output_path`` on the event
      - ``error`` — any exception, with the message on ``raw_line``

    All exceptions are caught and converted to an error event — callers
    never see an unhandled throw, which matches the previous GUI
    behavior where the worker thread always posted an error event.
    """
    try:
        engine = get_engine(request.engine_id)
        if engine is None:
            raise RuntimeError(f"Engine '{request.engine_id}' not found.")

        on_event(ProgressEvent(kind="log", raw_line="Reading input..."))

        if request.input_mode == "pdf":
            if request.pdf_path is None:
                raise ValueError("pdf input_mode requires pdf_path.")
            book = parse_book(request.pdf_path)
            text = book.full_text
        else:
            text = request.input_text or ""

        if not text:
            raise ValueError("No text to synthesize.")

        voice_id = request.voice_id
        if voice_id is None:
            voice_id = engine.default_voice(request.language)
            if voice_id is None:
                raise RuntimeError(
                    "No voice available for the selected language."
                )

        on_event(ProgressEvent(
            kind="log",
            raw_line=f"Synthesizing ({len(text)} chars)...",
        ))

        # Resolve output path. Fall back to <default_output_dir>/output.mp3
        # so a caller that forgot to pick one still gets something.
        out = (
            Path(request.output_path)
            if request.output_path
            else default_output_dir() / "output.mp3"
        )
        out.parent.mkdir(parents=True, exist_ok=True)

        def progress_cb(current: int, total: int, msg: str = "") -> None:
            on_event(ProgressEvent(
                kind="chunk",
                total_done=current,
                total_chunks=total,
                raw_line=msg or f"Chunk {current}/{total}",
            ))

        engine.synthesize(
            text=text,
            output_path=str(out),
            voice_id=voice_id,
            language=request.language,
            progress_cb=progress_cb,
            reference_audio=request.reference_audio,
            voice_description=request.voice_description,
        )

        on_event(ProgressEvent(
            kind="done",
            output_path=str(out),
            raw_line=f"Saved: {out}",
        ))

    except Exception as exc:
        on_event(ProgressEvent(kind="error", raw_line=str(exc)))


# ---------------------------------------------------------------------------
# Chatterbox subprocess (out-of-process engine)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatterboxRequest:
    """Frozen state needed to build a Chatterbox subprocess runner.

    Mirrors :class:`InprocessRequest` for the subprocess path. The GUI
    reads widgets on the main thread, freezes them here, then hands the
    request to :func:`build_chatterbox_runner` which does the tempfile
    + argv assembly.
    """

    input_mode: str  # "pdf" or "text"
    pdf_path: Optional[str] = None  # file on "pdf" mode (pdf/epub/txt)
    input_text: Optional[str] = None  # raw text on "text" mode
    text_override: Optional[str] = None  # sample snippet — wins over the others
    output_basename_override: Optional[str] = None  # tempfile stem for samples
    output_path_hint: Optional[str] = None  # chosen MP3 path; parent = out_dir
    reference_audio: Optional[str] = None
    chunk_chars: int = 300  # only passed to CLI when != default
    language: str = "fi"


class ChatterboxBuildError(Exception):
    """Raised when a ChatterboxRequest can't be turned into a runnable subprocess.

    The ``kind`` attribute is a machine-readable key the GUI translates via
    ``self._s(kind)``. Known kinds:

      - ``no_pdf``: input_mode=="pdf" but pdf_path is missing
      - ``no_text``: input_mode=="text" but the text is empty/blank
      - ``chatterbox_venv_missing``: chatterbox venv or runner script not found
    """

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass
class ChatterboxPlan:
    """Result of a successful :func:`build_chatterbox_runner` call.

    The runner is ready to ``start()``. ``out_dir`` and ``input_label``
    are exposed so the GUI can log them before launching.
    """

    runner: ChatterboxRunner
    out_dir: Path
    input_label: str


def build_chatterbox_runner(
    request: ChatterboxRequest,
    runner_script: Path,
    default_out_dir: Path,
) -> ChatterboxPlan:
    """Assemble a ready-to-start Chatterbox subprocess from ``request``.

    Raises :class:`ChatterboxBuildError` on any validation failure — the
    caller catches and maps the ``kind`` to its own i18n layer. This keeps
    the orchestrator free of tkinter / messagebox dependencies.

    Ordering of the input branches matches the pre-extraction GUI code:
    a ``text_override`` always wins (sample flow), otherwise ``input_mode``
    decides which CLI flag the runner gets (``--pdf`` / ``--epub`` /
    ``--text-file``).
    """
    pdf_path: Optional[str] = None
    text_path: Optional[str] = None
    epub_path: Optional[str] = None

    if request.text_override is not None:
        # Sample path: always route the snippet through a temp .txt.
        prefix = (
            f"{request.output_basename_override}_"
            if request.output_basename_override
            else "abm_"
        )
        tmp = tempfile.NamedTemporaryFile(
            mode="w", prefix=prefix, suffix=".txt",
            delete=False, encoding="utf-8",
        )
        tmp.write(request.text_override)
        tmp.close()
        text_path = tmp.name
    elif request.input_mode == "pdf":
        if not request.pdf_path:
            raise ChatterboxBuildError("no_pdf")
        ext = Path(request.pdf_path).suffix.lower()
        if ext == ".epub":
            epub_path = request.pdf_path
        elif ext == ".txt":
            text_path = request.pdf_path
        else:
            pdf_path = request.pdf_path
    else:
        content = (request.input_text or "").strip()
        if not content:
            raise ChatterboxBuildError("no_text")
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
        )
        tmp.write(content)
        tmp.close()
        text_path = tmp.name

    python_exe = resolve_chatterbox_python()
    if python_exe is None or not runner_script.exists():
        raise ChatterboxBuildError("chatterbox_venv_missing")

    if request.output_path_hint:
        out_dir = Path(request.output_path_hint).parent
    else:
        out_dir = default_out_dir
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    extra_args: list[str] = []
    if request.reference_audio:
        extra_args.extend(["--ref-audio", request.reference_audio])
    # Only pass --chunk-chars when it diverges from the runner's default so
    # default runs keep clean logs.
    if request.chunk_chars != 300:
        extra_args.extend(["--chunk-chars", str(request.chunk_chars)])

    runner = ChatterboxRunner(
        python_exe=str(python_exe),
        script_path=str(runner_script),
        pdf_path=pdf_path,
        text_path=text_path,
        epub_path=epub_path,
        out_dir=str(out_dir),
        extra_args=extra_args,
        language=request.language,
    )

    input_label = pdf_path or epub_path or text_path or "text"
    return ChatterboxPlan(
        runner=runner, out_dir=out_dir, input_label=input_label,
    )
