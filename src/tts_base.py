"""Abstract base class and registry for TTS engines.

All engines (Edge-TTS, Piper, XTTS, Qwen3, ...) implement the `TTSEngine`
interface so the rest of the app can switch between them without knowing
the details of any single engine.

Heavy engines (XTTS, Qwen3, ...) are expected to do lazy imports inside
their modules so that app startup stays fast even when torch or CUDA is
installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional

# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], None]
"""Callback(current, total, message) used for progress reporting.

- current/total may represent chunks, percent, or bytes depending on the
  phase. message is a short human-readable status line.
- For downloads, total may be the content-length in bytes and current the
  bytes received so far.
"""


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Voice:
    """A single voice offered by an engine."""

    id: str
    """Engine-specific identifier, e.g. 'fi-FI-NooraNeural' or 'fi_FI-harri-medium'."""

    display_name: str
    """Human-readable name shown in the GUI, e.g. 'Noora (suomi, nainen)'."""

    language: str
    """Short language code ('fi', 'en', ...) this voice speaks."""

    gender: str = ""
    """Optional: 'female' / 'male' / '' if unknown."""


@dataclass
class EngineStatus:
    """Runtime availability information for a TTS engine."""

    available: bool
    """True when the engine can be used right now (deps ok, models present)."""

    reason: str = ""
    """If not available, a short human-readable explanation for the GUI.
    E.g. 'Install required: pip install piper-tts' or 'Requires NVIDIA GPU'."""

    needs_download: bool = False
    """True when the engine is otherwise available but still needs to
    download models before synthesis will work. The GUI should show a
    'Download voices' button in this state."""


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class TTSEngine(ABC):
    """Contract every TTS engine in the app must satisfy."""

    # --- class-level metadata; subclasses override these ---

    id: ClassVar[str]
    """Short stable identifier, e.g. 'edge', 'piper', 'xtts', 'qwen'."""

    display_name: ClassVar[str]
    """Full human-readable name shown in the GUI."""

    description: ClassVar[str]
    """One-line description shown next to the engine in the GUI."""

    requires_gpu: ClassVar[bool] = False
    """True when the engine cannot run usefully without a CUDA GPU."""

    requires_internet: ClassVar[bool] = False
    """True when the engine calls an online API during synthesis."""

    supports_voice_cloning: ClassVar[bool] = False
    """True when the engine accepts a reference_audio sample to clone."""

    supports_voice_description: ClassVar[bool] = False
    """True when the engine accepts a free-text voice_description prompt
    (e.g. 'a young woman with a gentle voice') to steer the generated
    voice. Engines that do not support this should silently ignore the
    parameter."""

    # --- instance methods; subclasses must implement ---

    @abstractmethod
    def check_status(self) -> EngineStatus:
        """Return the engine's current runtime status.

        Called every time the GUI is refreshed or before synthesis, so
        implementations should be cheap and should NOT perform heavy
        imports unless absolutely necessary.
        """

    @abstractmethod
    def list_voices(self, language: str) -> list[Voice]:
        """Return voices offered for a language.

        May return an empty list if models have not been downloaded yet;
        the GUI will then show a download prompt. Must not raise.
        """

    def supported_languages(self) -> set[str]:
        """Return the set of short language codes this engine can speak.

        Drives the Kieli → Moottori → Ääni funnel in the GUI: engines
        that do not list the currently selected language are hidden from
        the engine dropdown.

        Default returns ``{"fi"}`` for back-compat with any third-party
        engine that predates this contract; in-tree engines override to
        advertise their real coverage. Must not raise.
        """
        return {"fi"}

    @abstractmethod
    def default_voice(self, language: str) -> Optional[str]:
        """Return the voice id picked by default for `language`.

        Returns None when no voice is available for that language.
        """

    @abstractmethod
    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        language: str,
        progress_cb: Optional[ProgressCallback] = None,
        reference_audio: Optional[str] = None,
        voice_description: Optional[str] = None,
    ) -> None:
        """Synthesize `text` to an MP3 file at `output_path`.

        Args:
            text: The full text to speak. Engines are responsible for
                chunking if their backend has a per-request size limit.
            output_path: Destination MP3 path. Will be overwritten.
            voice_id: Engine-specific voice identifier from `list_voices()`.
            language: Short language code, e.g. 'fi' or 'en'.
            progress_cb: Optional callback for progress updates.
            reference_audio: Optional path to a short reference WAV/MP3 for
                voice-cloning engines. Ignored by engines that do not clone.
            voice_description: Optional free-text description of the desired
                voice (e.g. 'a warm baritone elderly male voice'). Engines
                without support must silently ignore this parameter.

        Raises:
            ValueError: If text is empty or voice_id is unknown.
            RuntimeError: If synthesis fails at runtime.
        """


# ---------------------------------------------------------------------------
# Engine registry
# ---------------------------------------------------------------------------


_REGISTRY: dict[str, type[TTSEngine]] = {}


def register_engine(engine_cls: type[TTSEngine]) -> type[TTSEngine]:
    """Decorator / function to register an engine class.

    Usage:
        @register_engine
        class MyEngine(TTSEngine):
            id = "my"
            ...
    """
    if not hasattr(engine_cls, "id") or not engine_cls.id:
        raise ValueError(f"{engine_cls.__name__} must define a non-empty 'id'")
    if engine_cls.id in _REGISTRY:
        raise ValueError(f"Engine id '{engine_cls.id}' already registered")
    _REGISTRY[engine_cls.id] = engine_cls
    return engine_cls


def get_engine(engine_id: str) -> Optional[TTSEngine]:
    """Return an engine instance by id, or None if unknown."""
    cls = _REGISTRY.get(engine_id)
    return cls() if cls else None


def list_engines() -> list[TTSEngine]:
    """Return one fresh instance of every registered engine.

    Engines are returned in registration order, which by convention means
    Edge-TTS first (default), then Piper, then GPU engines.
    """
    return [cls() for cls in _REGISTRY.values()]


def registered_ids() -> list[str]:
    """Return the ids of all registered engines in registration order."""
    return list(_REGISTRY.keys())
