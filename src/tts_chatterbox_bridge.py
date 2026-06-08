"""Chatterbox TTS engine — metadata-only registration.

Chatterbox cannot run in the main app's Python process because it
requires a torch + CUDA stack that conflicts with our other
dependencies. The actual synthesis happens in a separate interpreter
via ``src/launcher_bridge.py::ChatterboxRunner`` that spawns
``scripts/generate_chatterbox_audiobook.py``.

This module registers a ``TTSEngine`` subclass so the rest of the app
(GUI dropdown, Kieli/Moottori/Ääni cascade, availability checks, voice
list) can treat Chatterbox the same as any in-process engine. The
``uses_subprocess = True`` flag tells callers to route synthesis
through the bridge runner instead of calling ``synthesize()`` directly;
the ``synthesize()`` override here raises so any caller that forgets
the check fails loudly rather than silently no-opping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.launcher_bridge import resolve_chatterbox_python
from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
    register_alias,
    register_engine,
)


# Same underlying voice, two code paths inside the subprocess:
#   fi -> T3 Finnish finetune
#   en -> multilingual base + voice-clone reference audio
# See scripts/generate_chatterbox_audiobook.py for the routing.
_CHATTERBOX_LANG_TAGS = {
    "fi": "suomi",
    "en": "English",
}


@register_engine
class ChatterboxEngine(TTSEngine):
    """Metadata-only Chatterbox engine; real work runs via the bridge.

    Serves both Finnish (T3 finetune, no reference clip) and English
    (multilingual base + bundled Grandmom reference WAV, Route B v2)
    — the language router inside the subprocess decides at runtime.
    Canonical engine id is ``chatterbox_grandmom``. The legacy id
    ``chatterbox_fi`` is registered as an alias below so existing
    user configs, env vars, scripts, and release/update paths keep
    working without churn.
    """

    id = "chatterbox_grandmom"
    display_name = "Chatterbox — Isoäiti + Grandmom (paras laatu, NVIDIA)"
    description = (
        "Offline, paras laatu. Kesto ~1–2 h NVIDIA-koneella."
    )
    requires_gpu = True
    requires_internet = False
    supports_voice_cloning = True
    supports_voice_description = False
    uses_subprocess = True

    def check_status(self) -> EngineStatus:
        """Report availability based on bridge venv + runner script presence.

        The bridge refuses to start without the ``.venv-chatterbox``
        environment and the ``scripts/generate_chatterbox_audiobook.py``
        entry point, so we surface both as the gating conditions. Both
        checks are cheap (path existence only), matching the "do not do
        heavy imports" contract on ``check_status``.
        """
        venv_python = resolve_chatterbox_python()
        if venv_python is None:
            return EngineStatus(
                available=False,
                reason=(
                    "Chatterbox engine is not installed.\n"
                    "Chatterbox-moottoria ei ole asennettu.\n"
                    "Install it via the GUI's \"Install engines…\" button "
                    "in the Settings panel\n"
                    "(Asetukset-paneelin \"Asenna moottoreita…\"-painikkeesta), "
                    "or via the CLI:\n"
                    "  audiobookmaker engines install chatterbox_grandmom"
                ),
            )
        # A venv whose python exists but whose install is still mid-flight or
        # was interrupted must NOT report ready — otherwise a Convert launches
        # the runner against a half-built environment (and can corrupt the
        # in-progress pip install). The install writes a sentinel for exactly
        # this window; see engine_installer.is_install_incomplete.
        from src.engine_installer import is_install_incomplete
        if is_install_incomplete(venv_python):
            return EngineStatus(
                available=False,
                reason=(
                    "Chatterbox is still installing, or its last install was "
                    "interrupted. Wait for the install to finish, or reinstall "
                    "it from the Engine manager.\n"
                    "Chatterbox-asennus on kesken tai keskeytyi — odota "
                    "asennuksen valmistumista tai asenna se uudelleen "
                    "moottoreiden hallinnasta."
                ),
            )
        repo_root = Path(__file__).resolve().parent.parent
        runner_script = repo_root / "scripts" / "generate_chatterbox_audiobook.py"
        if not runner_script.exists():
            return EngineStatus(
                available=False,
                reason=(
                    "Chatterbox-skripti puuttuu "
                    "(scripts/generate_chatterbox_audiobook.py)."
                ),
            )
        return EngineStatus(available=True)

    def supported_languages(self) -> set[str]:
        """Finnish via T3 finetune, English via multilingual base + clone."""
        return set(_CHATTERBOX_LANG_TAGS.keys())

    def list_voices(self, language: str) -> list[Voice]:
        """Return the single Grandmom voice, tagged with the target language.

        Chatterbox only ships one voice but the same model works in
        Finnish and English (different code paths inside the subprocess),
        so we surface one per supported language to match the display
        format used by Edge/Piper.
        """
        tag = _CHATTERBOX_LANG_TAGS.get(language)
        if not tag:
            return []
        return [
            Voice(
                id="grandmom",
                display_name=f"Grandmom ({tag})",
                language=language,
                gender="female",
            )
        ]

    def default_voice(self, language: str) -> Optional[str]:
        """Return ``'grandmom'`` for every supported language."""
        return "grandmom" if language in _CHATTERBOX_LANG_TAGS else None

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        language: str,
        progress_cb: Optional[ProgressCallback] = None,
        reference_audio: Optional[str] = None,
        voice_description: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> None:
        """Not callable directly — dispatch via the subprocess bridge."""
        raise RuntimeError(
            "Chatterbox synthesis runs in a subprocess bridge. Callers "
            "must check engine.uses_subprocess and route through "
            "ChatterboxRunner instead of calling synthesize()."
        )


# Back-compat alias: the engine id was ``chatterbox_fi`` before the
# English-mode router landed (PR #ad9791f, 2026-04-15). The canonical
# id is now ``chatterbox_grandmom`` since the same machinery serves
# both Finnish (T3 finetune) and English (multilingual base + bundled
# Grandmom reference clip). Existing user configs, env vars, scripts,
# CLI invocations, and the auto-update path keep using the old name
# transparently via this alias.
register_alias("chatterbox_fi", "chatterbox_grandmom")
