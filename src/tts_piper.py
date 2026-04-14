"""Piper TTS engine adapter.

Piper is an offline neural text-to-speech system that runs on CPU, with
voice models hosted on HuggingFace. It produces noticeably better-sounding
speech than Edge-TTS for Finnish and avoids the online-API dependency.

Model files are ~60MB per voice and are downloaded to a user-local cache
directory on first use. Nothing is bundled with the installer.

This module uses lazy imports for `piper` so that the app can start even
if piper-tts isn't installed — check_status() will then report the
missing dependency to the GUI.
"""

from __future__ import annotations

import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
    register_engine,
)
from src.tts_engine import combine_audio_files, split_text_into_chunks


# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PiperVoiceSpec:
    """Metadata + download URLs for one Piper voice."""

    id: str                 # e.g. 'fi_FI-harri-medium'
    display_name: str
    language: str           # 'fi', 'en'
    gender: str
    url_lang: str           # URL path component, e.g. 'fi' or 'en'
    url_locale: str         # URL path component, e.g. 'fi_FI' or 'en_US'
    url_voice: str          # URL path component, e.g. 'harri'
    url_quality: str        # URL path component, e.g. 'medium' or 'high'

    @property
    def onnx_filename(self) -> str:
        return f"{self.id}.onnx"

    @property
    def json_filename(self) -> str:
        return f"{self.id}.onnx.json"

    def build_url(self, filename: str) -> str:
        return (
            "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
            f"{self.url_lang}/{self.url_locale}/{self.url_voice}/"
            f"{self.url_quality}/{filename}"
        )


# A small hand-picked list of high quality voices. Keeping the catalogue
# short keeps the GUI dropdown clean and avoids the (very long) full list
# of locale variants offered by the upstream Piper project.
_PIPER_VOICES: list[_PiperVoiceSpec] = [
    _PiperVoiceSpec(
        id="fi_FI-harri-medium",
        display_name="Harri (suomi, mies)",
        language="fi",
        gender="male",
        url_lang="fi",
        url_locale="fi_FI",
        url_voice="harri",
        url_quality="medium",
    ),
    _PiperVoiceSpec(
        id="en_US-lessac-medium",
        display_name="Lessac (English US, female)",
        language="en",
        gender="female",
        url_lang="en",
        url_locale="en_US",
        url_voice="lessac",
        url_quality="medium",
    ),
    _PiperVoiceSpec(
        id="en_US-ryan-high",
        display_name="Ryan (English US, male)",
        language="en",
        gender="male",
        url_lang="en",
        url_locale="en_US",
        url_voice="ryan",
        url_quality="high",
    ),
    _PiperVoiceSpec(
        id="en_US-amy-medium",
        display_name="Amy (English US, female)",
        language="en",
        gender="female",
        url_lang="en",
        url_locale="en_US",
        url_voice="amy",
        url_quality="medium",
    ),
    _PiperVoiceSpec(
        id="en_US-danny-low",
        display_name="Danny (English US, male)",
        language="en",
        gender="male",
        url_lang="en",
        url_locale="en_US",
        url_voice="danny",
        url_quality="low",
    ),
    _PiperVoiceSpec(
        id="en_GB-alan-medium",
        display_name="Alan (English GB, male)",
        language="en",
        gender="male",
        url_lang="en",
        url_locale="en_GB",
        url_voice="alan",
        url_quality="medium",
    ),
    _PiperVoiceSpec(
        id="en_GB-alba-medium",
        display_name="Alba (English GB, female)",
        language="en",
        gender="female",
        url_lang="en",
        url_locale="en_GB",
        url_voice="alba",
        url_quality="medium",
    ),
    _PiperVoiceSpec(
        id="de_DE-thorsten-medium",
        display_name="Thorsten (Deutsch, männlich)",
        language="de",
        gender="male",
        url_lang="de",
        url_locale="de_DE",
        url_voice="thorsten",
        url_quality="medium",
    ),
    _PiperVoiceSpec(
        id="de_DE-eva_k-x_low",
        display_name="Eva (Deutsch, weiblich)",
        language="de",
        gender="female",
        url_lang="de",
        url_locale="de_DE",
        url_voice="eva_k",
        url_quality="x_low",
    ),
]

_VOICES_BY_ID: dict[str, _PiperVoiceSpec] = {v.id: v for v in _PIPER_VOICES}


# ---------------------------------------------------------------------------
# Model cache location
# ---------------------------------------------------------------------------


def _cache_dir() -> Path:
    """Directory where downloaded Piper voices are cached.

    Uses ~/.audiobookmaker/piper_voices to avoid a dependency on
    platformdirs. The directory is created on demand.
    """
    path = Path.home() / ".audiobookmaker" / "piper_voices"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_voice_cached(spec: _PiperVoiceSpec) -> bool:
    cache = _cache_dir()
    return (cache / spec.onnx_filename).exists() and (cache / spec.json_filename).exists()


def _download_file(
    url: str,
    destination: Path,
    progress_cb: Optional[ProgressCallback],
    label: str,
) -> None:
    """Download `url` to `destination` with optional progress reporting.

    Writes to a temporary file first and only moves it into place on
    success so a half-downloaded model never pollutes the cache.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AudiobookMaker/1.0"})
        with urllib.request.urlopen(req) as response:  # nosec B310 (https only)
            total = int(response.headers.get("Content-Length") or 0)
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=destination.name, suffix=".part", dir=str(destination.parent)
            )
            os.close(tmp_fd)
            try:
                with open(tmp_path, "wb") as out:
                    downloaded = 0
                    chunk_size = 1024 * 64
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total:
                            progress_cb(downloaded, total, label)
                os.replace(tmp_path, destination)
            except (OSError, urllib.error.URLError):
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc


def download_voice(
    voice_id: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> None:
    """Download a Piper voice to the local cache.

    No-op if the voice is already cached.
    """
    spec = _VOICES_BY_ID.get(voice_id)
    if spec is None:
        raise ValueError(f"Unknown Piper voice id: {voice_id}")

    if _is_voice_cached(spec):
        return

    cache = _cache_dir()
    json_dest = cache / spec.json_filename
    onnx_dest = cache / spec.onnx_filename

    _download_file(
        spec.build_url(spec.json_filename),
        json_dest,
        progress_cb,
        f"Ladataan ääntä: {spec.display_name} (config)",
    )
    _download_file(
        spec.build_url(spec.onnx_filename),
        onnx_dest,
        progress_cb,
        f"Ladataan ääntä: {spec.display_name} (malli)",
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@register_engine
class PiperTTSEngine(TTSEngine):
    """Offline CPU-only Piper neural TTS."""

    id = "piper"
    display_name = "Piper (offline, better quality, no GPU needed)"
    description = (
        "Offline neural TTS that runs on CPU. Voice models "
        "(~60 MB each) are downloaded on first use."
    )
    requires_gpu = False
    requires_internet = False  # only when downloading a new voice
    supports_voice_cloning = False

    # --------------------------------------------------------------------- #
    # Status
    # --------------------------------------------------------------------- #

    def check_status(self) -> EngineStatus:
        try:
            import piper  # noqa: F401
            # Force the real import chain (voice -> espeakbridge .pyd) so
            # bundling issues are caught here rather than at synth time.
            from piper import PiperVoice  # noqa: F401
        except ImportError as exc:
            return EngineStatus(
                available=False,
                reason=f"Piper import failed: {exc}. Try: pip install piper-tts",
            )
        except Exception as exc:
            return EngineStatus(
                available=False,
                reason=f"Piper load error: {type(exc).__name__}: {exc}",
            )

        # If at least one voice is cached, we're fully ready; otherwise the
        # GUI should show a 'Download voices' button.
        any_cached = any(_is_voice_cached(spec) for spec in _PIPER_VOICES)
        if not any_cached:
            return EngineStatus(
                available=True,
                needs_download=True,
                reason="No Piper voices downloaded yet.",
            )
        return EngineStatus(available=True)

    # --------------------------------------------------------------------- #
    # Voices
    # --------------------------------------------------------------------- #

    def list_voices(self, language: str) -> list[Voice]:
        return [
            Voice(
                id=spec.id,
                display_name=spec.display_name,
                language=spec.language,
                gender=spec.gender,
            )
            for spec in _PIPER_VOICES
            if spec.language == language
        ]

    def default_voice(self, language: str) -> Optional[str]:
        for spec in _PIPER_VOICES:
            if spec.language == language:
                return spec.id
        return None

    # --------------------------------------------------------------------- #
    # Synthesis
    # --------------------------------------------------------------------- #

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
        # Piper does not support voice cloning or voice description; both
        # parameters are silently ignored for interface compatibility.
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text.")

        if not voice_id:
            voice_id = self.default_voice(language) or ""
        spec = _VOICES_BY_ID.get(voice_id)
        if spec is None:
            raise ValueError(f"Unknown Piper voice id: {voice_id}")

        # Lazy import so the main app does not load onnxruntime at startup.
        from piper import PiperVoice

        # Ensure the voice is cached.
        if not _is_voice_cached(spec):
            if progress_cb:
                progress_cb(0, 0, f"Ladataan ääntä: {spec.display_name}…")
            download_voice(spec.id, progress_cb)

        cache = _cache_dir()
        model_path = cache / spec.onnx_filename
        config_path = cache / spec.json_filename

        voice = PiperVoice.load(str(model_path), config_path=str(config_path))

        chunks = split_text_into_chunks(text)
        if not chunks:
            raise ValueError("Text produced no chunks after splitting.")

        with tempfile.TemporaryDirectory(prefix="piper_") as tmp_dir:
            chunk_paths: list[str] = []
            total = len(chunks)
            for i, chunk in enumerate(chunks):
                if progress_cb:
                    progress_cb(i, total, f"Syntetisoidaan pala {i + 1}/{total}…")
                chunk_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
                import wave

                with wave.open(chunk_path, "wb") as wav_file:
                    voice.synthesize_wav(chunk, wav_file)
                chunk_paths.append(chunk_path)

            if progress_cb:
                progress_cb(total, total, "Yhdistetään äänitiedostot…")
            combine_audio_files(chunk_paths, output_path)

        if progress_cb:
            progress_cb(total, total, "Valmis!")
