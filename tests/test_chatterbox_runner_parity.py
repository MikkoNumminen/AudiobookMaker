"""Parity guard: every Chatterbox synthesis goes through ONE core.

The retired Launcher hand-built ``ChatterboxRunner`` directly, WITHOUT
``--voice-pack`` / ``--language`` — so an imported voice pack was silently
dropped and the audio fell back to the default voice. That was the structural
cause of "the GUI sounds worse than dev": a fix to the shared orchestrator
never reached that divergent path.

These tests lock the invariant so it can't come back:

1. ``ChatterboxRunner`` may only be constructed by
   ``synthesis_orchestrator.build_chatterbox_runner`` (a source grep) — the one
   place that threads voice-pack / ref-audio / chunk-chars / language into the
   runner. The GUI (``gui_synth_mixin``) and the CLI (``cli/convert``) both call
   that factory, so they cannot drift.
2. A request with those fields set produces a runner argv that carries all of
   them — and a bare request stays clean (default chunk size not passed).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.synthesis_orchestrator import ChatterboxRequest, build_chatterbox_runner

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Only the factory (build_chatterbox_runner) and the module that DEFINES the
# class (its docstring shows a usage example) may name ``ChatterboxRunner(``.
_ALLOWED = {"synthesis_orchestrator.py", "launcher_bridge.py"}


def test_no_hand_built_chatterbox_runner_outside_factory() -> None:
    src = _REPO_ROOT / "src"
    offenders = sorted(
        py.relative_to(_REPO_ROOT).as_posix()
        for py in src.rglob("*.py")
        if py.name not in _ALLOWED
        and "ChatterboxRunner(" in py.read_text(encoding="utf-8")
    )
    assert not offenders, (
        "ChatterboxRunner must only be built by "
        "synthesis_orchestrator.build_chatterbox_runner (the single site that "
        "threads --voice-pack / --ref-audio / --chunk-chars / --language into "
        "the runner). A hand-built runner silently drops the imported voice "
        f"pack — exactly the retired Launcher bug. Offending files: {offenders}"
    )


def _plan(tmp_path: Path, **overrides):
    runner_script = tmp_path / "generate_chatterbox_audiobook.py"
    runner_script.write_text("# stub", encoding="utf-8")
    pdf = tmp_path / "book.pdf"
    pdf.write_text("x", encoding="utf-8")
    req = ChatterboxRequest(
        input_mode="pdf",
        pdf_path=str(pdf),
        language=overrides.get("language", "fi"),
        reference_audio=overrides.get("reference_audio"),
        chunk_chars=overrides.get("chunk_chars", 300),
        voice_pack_path=overrides.get("voice_pack_path"),
        output_path_hint=str(tmp_path / "out.mp3"),
    )
    with patch(
        "src.synthesis_orchestrator.resolve_chatterbox_python",
        return_value=tmp_path / "python.exe",
    ):
        return build_chatterbox_runner(req, runner_script, tmp_path / "out")


def test_factory_threads_voice_pack_ref_and_language(tmp_path) -> None:
    plan = _plan(
        tmp_path,
        voice_pack_path="/packs/lotta",
        language="en",
        reference_audio="/ref.wav",
        chunk_chars=280,
    )
    argv = plan.runner.extra_args
    assert "--voice-pack" in argv and "/packs/lotta" in argv
    assert "--ref-audio" in argv and "/ref.wav" in argv
    assert "--chunk-chars" in argv and "280" in argv
    assert plan.runner.language == "en"


def test_factory_omits_unset_optionals(tmp_path) -> None:
    # No pack / ref / custom chunk size: the argv stays clean (the runner's
    # default 300 is intentionally not passed) and language carries through.
    plan = _plan(tmp_path)
    argv = plan.runner.extra_args
    assert "--voice-pack" not in argv
    assert "--ref-audio" not in argv
    assert "--chunk-chars" not in argv
    assert plan.runner.language == "fi"
