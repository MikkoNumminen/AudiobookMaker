"""Unit tests for scripts/compare_engines.py.

These tests cover the enumerate / skip / dry-run planning logic without
performing any synthesis: no GPU engine runs, no Edge-TTS network call, no
files are written. Engines are stubbed with light fakes (or the real
registry is read) and ``engine.synthesize`` is never invoked in dry-run
paths.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.tts_base import EngineStatus, TTSEngine, Voice

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "compare_engines.py"


def _load_module():
    """Import scripts/compare_engines.py as a module under its own name."""
    spec = importlib.util.spec_from_file_location("compare_engines", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["compare_engines"] = module
    spec.loader.exec_module(module)
    return module


compare_engines = _load_module()


# ---------------------------------------------------------------------------
# Fake engines
# ---------------------------------------------------------------------------


class _FakeEngine(TTSEngine):
    """Minimal in-process engine whose availability/voice are configurable."""

    id = "fake"
    display_name = "Fake"
    description = "for tests"

    def __init__(
        self,
        *,
        engine_id: str = "fake",
        available: bool = True,
        reason: str = "",
        voice: str | None = "fake-voice",
        uses_subprocess: bool = False,
    ) -> None:
        self.id = engine_id
        self._available = available
        self._reason = reason
        self._voice = voice
        self.uses_subprocess = uses_subprocess
        self.synth_calls: list[dict] = []

    def check_status(self) -> EngineStatus:
        return EngineStatus(available=self._available, reason=self._reason)

    def list_voices(self, language: str) -> list[Voice]:
        if self._voice is None:
            return []
        return [Voice(id=self._voice, display_name=self._voice, language=language)]

    def default_voice(self, language: str) -> str | None:
        return self._voice

    def synthesize(self, *args, **kwargs) -> None:  # noqa: D401
        self.synth_calls.append(kwargs or {"args": args})


class _RaisingStatusEngine(_FakeEngine):
    def check_status(self) -> EngineStatus:
        raise RuntimeError("status boom")


class _RaisingVoiceEngine(_FakeEngine):
    def default_voice(self, language: str) -> str | None:
        raise RuntimeError("voice boom")


# ---------------------------------------------------------------------------
# plan_engine
# ---------------------------------------------------------------------------


class TestPlanEngine:
    def test_available_engine_gets_labeled_output_path(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _FakeEngine(engine_id="edge", voice="fi-FI-NooraNeural"),
            "fi",
            tmp_path,
        )
        assert plan.available
        assert plan.voice == "fi-FI-NooraNeural"
        assert plan.output_path == tmp_path / "edge__fi-FI-NooraNeural.mp3"
        assert plan.skip_reason is None

    def test_unavailable_engine_is_skipped_with_reason(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _FakeEngine(
                engine_id="piper",
                available=False,
                reason="Install required: pip install piper-tts",
            ),
            "fi",
            tmp_path,
        )
        assert not plan.available
        assert plan.output_path is None
        assert "piper-tts" in plan.skip_reason

    def test_multiline_reason_is_collapsed_to_first_line(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _FakeEngine(
                engine_id="chatterbox_grandmom",
                available=False,
                reason="Not installed.\nEi asennettu.\nmore detail",
            ),
            "fi",
            tmp_path,
        )
        assert plan.skip_reason == "Not installed."

    def test_available_but_no_voice_for_language_is_skipped(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _FakeEngine(engine_id="piper", voice=None),
            "de",
            tmp_path,
        )
        assert not plan.available
        assert "no default voice" in plan.skip_reason
        assert "de" in plan.skip_reason

    def test_check_status_exception_becomes_skip(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _RaisingStatusEngine(engine_id="boom"), "fi", tmp_path
        )
        assert not plan.available
        assert "status boom" in plan.skip_reason

    def test_default_voice_exception_becomes_skip(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _RaisingVoiceEngine(engine_id="boom"), "fi", tmp_path
        )
        assert not plan.available
        assert "voice boom" in plan.skip_reason

    def test_subprocess_flag_is_carried_into_plan(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _FakeEngine(engine_id="chatterbox_grandmom", uses_subprocess=True),
            "fi",
            tmp_path,
        )
        assert plan.uses_subprocess is True

    def test_exotic_voice_id_is_filename_safe(self, tmp_path) -> None:
        plan = compare_engines.plan_engine(
            _FakeEngine(engine_id="weird", voice="a/b\\c d:e"),
            "fi",
            tmp_path,
        )
        # No path separators or colons survive into the filename.
        name = plan.output_path.name
        assert "/" not in name and "\\" not in name and ":" not in name
        assert name == "weird__a-b-c-d-e.mp3"


# ---------------------------------------------------------------------------
# build_plans against the real registry
# ---------------------------------------------------------------------------


class TestBuildPlansRealRegistry:
    def test_enumerates_every_registered_engine(self, tmp_path) -> None:
        from src.tts_base import registered_ids

        plans = compare_engines.build_plans("fi", tmp_path)
        plan_ids = {p.engine_id for p in plans}
        assert plan_ids == set(registered_ids())

    def test_edge_and_piper_are_available_and_planned(self, tmp_path) -> None:
        # Edge-TTS and Piper are the always-installed in-process engines, so
        # they should always plan an output path without any synthesis.
        plans = {p.engine_id: p for p in compare_engines.build_plans("fi", tmp_path)}
        assert plans["edge"].available
        assert plans["edge"].output_path.name.startswith("edge__")
        assert plans["piper"].available

    def test_no_files_written_during_planning(self, tmp_path) -> None:
        compare_engines.build_plans("fi", tmp_path)
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# main() — dry run performs no synthesis and writes nothing
# ---------------------------------------------------------------------------


class TestMainDryRun:
    def test_dry_run_returns_zero_and_writes_nothing(
        self, tmp_path, capsys, monkeypatch
    ) -> None:
        fakes = [
            _FakeEngine(engine_id="edge", voice="fi-FI-NooraNeural"),
            _FakeEngine(engine_id="piper", voice="fi_FI-harri-medium"),
            _FakeEngine(
                engine_id="chatterbox_grandmom", uses_subprocess=True
            ),
            _FakeEngine(
                engine_id="voxcpm2",
                available=False,
                reason="Install required: pip install voxcpm",
            ),
        ]
        monkeypatch.setattr(compare_engines, "list_engines", lambda: fakes)

        out_dir = tmp_path / "engine_compare"
        rc = compare_engines.main(
            ["--dry-run", "--language", "fi", "--out", str(out_dir)]
        )
        assert rc == 0

        # Dry run must NEVER call synthesize on any engine.
        assert all(not f.synth_calls for f in fakes)
        # And it must not create the output directory or any files.
        assert not out_dir.exists()

        captured = capsys.readouterr().out
        assert "DRY RUN" in captured
        assert "edge" in captured
        assert "voxcpm2" in captured
        # Available engines show a [plan] line; unavailable show [skip].
        assert "[plan] edge" in captured
        assert "[skip] voxcpm2" in captured

    def test_dry_run_against_real_registry_writes_nothing(
        self, tmp_path, capsys
    ) -> None:
        out_dir = tmp_path / "engine_compare"
        rc = compare_engines.main(["--dry-run", "--out", str(out_dir)])
        assert rc == 0
        assert not out_dir.exists()


# ---------------------------------------------------------------------------
# main() — non-dry-run drives synthesize() but only on in-process fakes
# ---------------------------------------------------------------------------


class TestMainSynthDispatch:
    def test_inprocess_engine_synthesize_is_called(
        self, tmp_path, monkeypatch
    ) -> None:
        edge = _FakeEngine(engine_id="edge", voice="fi-FI-NooraNeural")
        monkeypatch.setattr(compare_engines, "list_engines", lambda: [edge])

        out_dir = tmp_path / "engine_compare"
        rc = compare_engines.main(
            ["--text", "Hei.", "--language", "fi", "--out", str(out_dir)]
        )
        assert rc == 0
        assert len(edge.synth_calls) == 1
        call = edge.synth_calls[0]
        assert call["voice_id"] == "fi-FI-NooraNeural"
        assert call["language"] == "fi"
        assert call["text"] == "Hei."
        assert call["output_path"].endswith("edge__fi-FI-NooraNeural.mp3")
        assert out_dir.exists()

    def test_one_engine_failure_does_not_stop_the_rest(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        good = _FakeEngine(engine_id="edge", voice="v1")

        class _Boom(_FakeEngine):
            def synthesize(self, *args, **kwargs):
                raise RuntimeError("synth exploded")

        bad = _Boom(engine_id="piper", voice="v2")
        monkeypatch.setattr(
            compare_engines, "list_engines", lambda: [bad, good]
        )

        rc = compare_engines.main(["--out", str(tmp_path)])
        assert rc == 0
        # The good engine still synthesized despite the bad one failing first.
        assert len(good.synth_calls) == 1
        captured = capsys.readouterr()
        assert "synth exploded" in (captured.out + captured.err)

    def test_subprocess_engine_does_not_call_inprocess_synthesize(
        self, tmp_path, monkeypatch
    ) -> None:
        # A subprocess engine must route through the bridge, never the
        # in-process synthesize() (which raises by contract). We stub the
        # bridge dispatch so no real subprocess spawns.
        sub = _FakeEngine(engine_id="chatterbox_grandmom", uses_subprocess=True)
        monkeypatch.setattr(compare_engines, "list_engines", lambda: [sub])

        calls: list[str] = []
        monkeypatch.setattr(
            compare_engines,
            "_synthesize_subprocess",
            lambda engine, text, plan, language: calls.append(plan.engine_id),
        )

        rc = compare_engines.main(["--out", str(tmp_path)])
        assert rc == 0
        # The subprocess path was taken; the in-process synthesize was not.
        assert calls == ["chatterbox_grandmom"]
        assert sub.synth_calls == []


# ---------------------------------------------------------------------------
# text resolution
# ---------------------------------------------------------------------------


class TestTextResolution:
    def test_default_sample_used_when_no_text(self) -> None:
        args = compare_engines.parse_args([])
        assert compare_engines._resolve_text(args) == \
            compare_engines.DEFAULT_SAMPLE_TEXT

    def test_text_flag_wins(self) -> None:
        args = compare_engines.parse_args(["--text", "  hello  "])
        assert compare_engines._resolve_text(args) == "hello"

    def test_input_file_is_read(self, tmp_path) -> None:
        f = tmp_path / "snippet.txt"
        f.write_text("from a file\n", encoding="utf-8")
        args = compare_engines.parse_args(["--input", str(f)])
        assert compare_engines._resolve_text(args) == "from a file"

    def test_text_and_input_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            compare_engines.parse_args(["--text", "x", "--input", "y.txt"])


# ---------------------------------------------------------------------------
# output directory resolution
# ---------------------------------------------------------------------------


class TestOutDirResolution:
    def test_default_is_engine_compare_under_canonical_root(self) -> None:
        from src.synthesis_orchestrator import default_output_dir

        args = compare_engines.parse_args([])
        out = compare_engines._resolve_out_dir(args)
        assert out.name == "engine_compare"
        assert out.parent == default_output_dir().resolve()

    def test_default_is_never_repo_root_or_dist(self) -> None:
        args = compare_engines.parse_args([])
        out = compare_engines._resolve_out_dir(args)
        assert out != _REPO_ROOT
        assert "dist" not in out.parts

    def test_explicit_out_is_honored(self, tmp_path) -> None:
        args = compare_engines.parse_args(["--out", str(tmp_path / "ab")])
        out = compare_engines._resolve_out_dir(args)
        assert out == (tmp_path / "ab").resolve()
