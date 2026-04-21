"""Tests for :mod:`src.gui_clone_voice` — pure controller, no Tk.

Every I/O boundary (analyze subprocess, yaml read, reference picker,
package, install) is injected as a fake so the whole pipeline runs
in-memory. The GUI Toplevel classes are out of scope here — they ship
in Sub-slice 4b and will get their own headless-Tk tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest

from src.gui_clone_voice import (
    CLONE_VOICE_STRINGS,
    SPEAKER_PRESETS,
    STAGE_ANALYZE,
    STAGE_ANALYZE_LINE,
    STAGE_AWAITING_NAMES,
    STAGE_CANCELLED,
    STAGE_DONE,
    STAGE_ERROR,
    STAGE_INSTALL,
    STAGE_PACKAGE,
    STAGE_PICK_REFERENCE,
    STAGE_SPEAKER_DONE,
    STAGE_SPEAKER_SKIPPED,
    STAGE_SPEAKERS_DETECTED,
    STAGE_STARTING,
    CloneVoiceJobConfig,
    CloneVoiceProgress,
    DetectedSpeaker,
    SpeakerNamingDecision,
    _detected_from_yaml,
    clone_voice_string,
    run_clone_voice_job,
    safe_source_display_name,
    suggest_default_name,
)


# ---------------------------------------------------------------------------
# fake analyze result / subproc
# ---------------------------------------------------------------------------


@dataclass
class _FakeAnalyzeResult:
    ok: bool = True
    return_code: int = 0
    transcripts_path: Optional[Path] = None
    speakers_yaml_path: Optional[Path] = None
    report_path: Optional[Path] = None
    log_lines: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _fake_analyze_factory(
    *,
    ok: bool = True,
    error: Optional[str] = None,
    transcripts_path: Optional[Path] = None,
    speakers_yaml_path: Optional[Path] = None,
    progress_events: Sequence[str] = (),
    capture: Optional[dict] = None,
):
    """Return an ``analyze_fn`` stub that records its kwargs + emits canned progress."""

    def _fake(**kwargs):
        if capture is not None:
            capture["kwargs"] = kwargs
        cb = kwargs.get("progress_cb")
        if cb is not None:
            for msg in progress_events:
                cb(_AnalyzeEvent(msg))
        return _FakeAnalyzeResult(
            ok=ok,
            error=error,
            transcripts_path=transcripts_path,
            speakers_yaml_path=speakers_yaml_path,
        )

    return _fake


class _AnalyzeEvent:
    def __init__(self, message: str) -> None:
        self.message = message


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _two_speaker_yaml() -> list[dict]:
    return [
        {
            "speaker": "SPEAKER_00",
            "total_seconds": 1800.0,
            "chunk_count": 150,
            "mean_chunk_seconds": 12.0,
            "quality_tier": "full_lora",
        },
        {
            "speaker": "SPEAKER_01",
            "total_seconds": 600.0,
            "chunk_count": 40,
            "mean_chunk_seconds": 15.0,
            "quality_tier": "reduced_lora",
        },
    ]


class _InstalledPack:
    def __init__(self, root: Path) -> None:
        self.root = root


def _recording_pickers() -> tuple[list[dict], Any, list[dict], Any, list[dict], Any]:
    """Return recording stubs for pick_reference / package / install."""
    picks: list[dict] = []
    pkgs: list[dict] = []
    installs: list[dict] = []

    def _pick(**kwargs):
        picks.append(kwargs)
        out = Path(kwargs["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKEWAV")
        return object()

    def _pkg(**kwargs):
        pkgs.append(kwargs)
        pack_dir = Path(kwargs["out_dir"]) / "slug"
        pack_dir.mkdir(parents=True, exist_ok=True)
        return pack_dir

    def _install(pack_dir: Path):
        installs.append({"pack_dir": Path(pack_dir)})
        return _InstalledPack(root=Path("/installed") / Path(pack_dir).name)

    return picks, _pick, pkgs, _pkg, installs, _install


# ---------------------------------------------------------------------------
# suggest_default_name / _detected_from_yaml
# ---------------------------------------------------------------------------


class TestSuggestDefaultName:
    def test_first_is_narrator_1(self) -> None:
        assert suggest_default_name(0) == "Narrator 1"

    def test_never_leaks_source_identifier(self) -> None:
        # Reinforces CLAUDE.md P0: default names must be generic —
        # "Narrator N" is safe, anything else is not.
        for i in range(5):
            name = suggest_default_name(i)
            assert name.startswith("Narrator ")
            assert not any(ch.isalpha() for ch in name.split()[-1]), name


class TestDetectedFromYaml:
    def test_typed_entries(self) -> None:
        entries = _detected_from_yaml(_two_speaker_yaml())
        assert len(entries) == 2
        assert entries[0].speaker_id == "SPEAKER_00"
        assert entries[0].total_seconds == 1800.0
        assert entries[0].total_minutes == pytest.approx(30.0)
        assert entries[0].chunk_count == 150
        assert entries[0].quality_tier == "full_lora"
        assert entries[0].default_name == "Narrator 1"
        assert entries[1].default_name == "Narrator 2"

    def test_missing_fields_get_safe_defaults(self) -> None:
        entries = _detected_from_yaml([{}])
        assert entries[0].speaker_id == "SPEAKER_00"
        assert entries[0].total_seconds == 0.0
        assert entries[0].chunk_count == 0
        assert entries[0].quality_tier == "skip"


# ---------------------------------------------------------------------------
# end-to-end pipeline
# ---------------------------------------------------------------------------


def _base_config(tmp_path: Path) -> CloneVoiceJobConfig:
    wav = tmp_path / "input.wav"
    wav.write_bytes(b"FAKE")
    return CloneVoiceJobConfig(
        wav_path=wav,
        language="fi",
        num_speakers=2,
        scratch_dir=tmp_path / "scratch",
    )


class TestRunCloneVoiceJob:
    def test_happy_path_two_speakers(self, tmp_path: Path) -> None:
        speakers_yaml = tmp_path / "speakers.yaml"
        transcripts = tmp_path / "transcripts.jsonl"

        picks, pick_fn, pkgs, pkg_fn, installs, install_fn = _recording_pickers()
        events: list[CloneVoiceProgress] = []
        analyze_capture: dict = {}

        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=s.speaker_id, name=f"Voice {i+1}")
                for i, s in enumerate(speakers)
            ],
            progress_cb=events.append,
            wav_display_name="source_audio",
            analyze_fn=_fake_analyze_factory(
                transcripts_path=transcripts,
                speakers_yaml_path=speakers_yaml,
                progress_events=["[voice_pack_analyze] asr: 1.0s"],
                capture=analyze_capture,
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=pick_fn,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )

        assert result.ok is True
        assert len(result.installed_packs) == 2
        assert result.errors == []
        assert result.skipped_speakers == []

        stages = [e.stage for e in events]
        assert stages[0] == STAGE_STARTING
        assert STAGE_ANALYZE in stages
        assert STAGE_ANALYZE_LINE in stages
        assert STAGE_SPEAKERS_DETECTED in stages
        assert STAGE_AWAITING_NAMES in stages
        assert stages.count(STAGE_PICK_REFERENCE) == 2
        assert stages.count(STAGE_PACKAGE) == 2
        assert stages.count(STAGE_INSTALL) == 2
        assert stages.count(STAGE_SPEAKER_DONE) == 2
        assert stages[-1] == STAGE_DONE

        # analyze got the config values through verbatim
        assert analyze_capture["kwargs"]["num_speakers"] == 2
        assert analyze_capture["kwargs"]["wav"] == _base_config(tmp_path).wav_path

        # package called with few_shot + user-chosen name
        assert pkgs[0]["tier"] == "few_shot"
        assert pkgs[0]["name"] == "Voice 1"
        assert pkgs[0]["language"] == "fi"

        # every pick went next to the speaker's scratch dir
        for p in picks:
            out = Path(p["out_path"])
            assert out.name == "reference.wav"
            assert out.exists()

    def test_analyze_failure_aborts_with_error(self, tmp_path: Path) -> None:
        events: list[CloneVoiceProgress] = []
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda _: [],  # never called
            progress_cb=events.append,
            analyze_fn=_fake_analyze_factory(ok=False, error="boom"),
        )
        assert result.ok is False
        assert result.error == "boom"
        assert events[-1].stage == STAGE_ERROR

    def test_empty_speakers_yaml_errors(self, tmp_path: Path) -> None:
        events: list[CloneVoiceProgress] = []
        speakers_yaml = tmp_path / "speakers.yaml"
        transcripts = tmp_path / "transcripts.jsonl"
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda _: [],
            progress_cb=events.append,
            analyze_fn=_fake_analyze_factory(
                transcripts_path=transcripts, speakers_yaml_path=speakers_yaml,
            ),
            read_speakers_yaml_fn=lambda p: [],
        )
        assert result.ok is False
        assert "no speakers" in (result.error or "").lower()
        assert events[-1].stage == STAGE_ERROR

    def test_user_returns_no_decisions_maps_to_cancelled(self, tmp_path: Path) -> None:
        events: list[CloneVoiceProgress] = []
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda _: [],  # user bailed in modal
            progress_cb=events.append,
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
        )
        assert result.cancelled is True
        assert result.ok is False
        assert events[-1].stage == STAGE_CANCELLED

    def test_include_false_skips_that_speaker(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, pkg_fn, installs, install_fn = _recording_pickers()
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(
                    speaker_id=speakers[0].speaker_id, name="Keep", include=True,
                ),
                SpeakerNamingDecision(
                    speaker_id=speakers[1].speaker_id, name="Drop", include=False,
                ),
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=pick_fn,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )
        assert len(result.installed_packs) == 1
        assert "SPEAKER_01" in result.skipped_speakers
        assert len(pkgs) == 1

    def test_blank_name_is_treated_as_skip(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, pkg_fn, installs, install_fn = _recording_pickers()
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=speakers[0].speaker_id, name="  "),
                SpeakerNamingDecision(speaker_id=speakers[1].speaker_id, name="B"),
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=pick_fn,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )
        assert result.skipped_speakers == ["SPEAKER_00"]
        assert len(result.installed_packs) == 1

    def test_missing_decision_for_detected_speaker_skips(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, pkg_fn, installs, install_fn = _recording_pickers()
        result = run_clone_voice_job(
            _base_config(tmp_path),
            # Only name the first — second is silently dropped.
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=speakers[0].speaker_id, name="Only"),
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=pick_fn,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )
        assert "SPEAKER_01" in result.skipped_speakers
        assert len(result.installed_packs) == 1

    def test_reference_pick_failure_skips_that_speaker_only(
        self, tmp_path: Path
    ) -> None:
        calls = {"n": 0}
        picks, _ok_pick, pkgs, pkg_fn, installs, install_fn = _recording_pickers()

        def _pick(**kwargs):
            calls["n"] += 1
            if kwargs["speaker_id"] == "SPEAKER_00":
                raise RuntimeError("no clean clip")
            return _ok_pick(**kwargs)

        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=s.speaker_id, name=f"N{i}")
                for i, s in enumerate(speakers)
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=_pick,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )
        assert len(result.installed_packs) == 1
        assert any("no clean clip" in e for e in result.errors)

    def test_package_failure_skips_that_speaker_only(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, _ok_pkg, installs, install_fn = _recording_pickers()

        def _pkg(**kwargs):
            if kwargs["name"] == "Alpha":
                raise ValueError("bad tier")
            return _ok_pkg(**kwargs)

        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=speakers[0].speaker_id, name="Alpha"),
                SpeakerNamingDecision(speaker_id=speakers[1].speaker_id, name="Bravo"),
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=pick_fn,
            package_fn=_pkg,
            install_fn=install_fn,
        )
        assert len(result.installed_packs) == 1
        assert any("bad tier" in e for e in result.errors)

    def test_install_failure_skips_that_speaker_only(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, pkg_fn, installs, _ok_install = _recording_pickers()

        def _install(pack_dir):
            if "slug" in str(pack_dir):
                installs.append({"pack_dir": pack_dir})
                if len(installs) == 1:
                    raise OSError("disk full")
                return _ok_install(pack_dir)
            return _ok_install(pack_dir)

        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=s.speaker_id, name=f"X{i}")
                for i, s in enumerate(speakers)
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=pick_fn,
            package_fn=pkg_fn,
            install_fn=_install,
        )
        # First install raised → we get 1 successful install out of 2.
        assert len(result.installed_packs) == 1
        assert any("disk full" in e for e in result.errors)

    def test_cancel_before_analyze(self, tmp_path: Path) -> None:
        events: list[CloneVoiceProgress] = []
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda _: [],
            progress_cb=events.append,
            cancel_check_fn=lambda: True,  # instant cancel
            analyze_fn=_fake_analyze_factory(),
        )
        assert result.cancelled is True
        assert events[-1].stage == STAGE_CANCELLED

    def test_cancel_between_speakers(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, pkg_fn, installs, install_fn = _recording_pickers()
        state = {"cancelled_after_first": False, "calls": 0}

        def _cancel() -> bool:
            state["calls"] += 1
            # Let analyze + awaiting_names + first speaker through,
            # then trip cancel before the second.
            return state["cancelled_after_first"]

        def _pick(**kwargs):
            # Once we've picked the first speaker's reference, flip cancel.
            out = pick_fn(**kwargs)
            if kwargs["speaker_id"] == "SPEAKER_00":
                state["cancelled_after_first"] = True
            return out

        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=s.speaker_id, name=f"N{i}")
                for i, s in enumerate(speakers)
            ],
            cancel_check_fn=_cancel,
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
            pick_reference_fn=_pick,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )
        assert result.cancelled is True
        # First speaker finished before cancel trip.
        assert len(result.installed_packs) == 1

    def test_no_source_path_leaked_in_progress_messages(self, tmp_path: Path) -> None:
        """CLAUDE.md P0 regression: events must never contain raw wav path."""
        events: list[CloneVoiceProgress] = []
        cfg = _base_config(tmp_path)
        # Pretend the filename itself is something copyright-sensitive.
        secret_path = tmp_path / "Some_Copyrighted_Book.m4b"
        secret_path.write_bytes(b"FAKE")
        cfg.wav_path = secret_path

        run_clone_voice_job(
            cfg,
            request_names_fn=lambda _: [],
            progress_cb=events.append,
            wav_display_name="source_audio",  # redacted basename
            analyze_fn=_fake_analyze_factory(),
        )

        raw_path_str = str(secret_path)
        raw_stem = secret_path.stem
        for ev in events:
            assert raw_path_str not in ev.message
            assert raw_stem not in ev.message

    def test_missing_output_paths_errors(self, tmp_path: Path) -> None:
        """Analyze that returns ok=True but no yaml path is treated as a hard error."""
        events: list[CloneVoiceProgress] = []
        result = run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda _: [],
            progress_cb=events.append,
            # No transcripts_path / speakers_yaml_path given.
            analyze_fn=_fake_analyze_factory(),
        )
        assert result.ok is False
        assert events[-1].stage == STAGE_ERROR
        assert "missing" in (result.error or "").lower()

    def test_language_passed_to_package(self, tmp_path: Path) -> None:
        picks, pick_fn, pkgs, pkg_fn, installs, install_fn = _recording_pickers()
        cfg = _base_config(tmp_path)
        cfg.language = "en"
        run_clone_voice_job(
            cfg,
            request_names_fn=lambda speakers: [
                SpeakerNamingDecision(speaker_id=speakers[0].speaker_id, name="EnglishVoice"),
            ],
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml()[:1],
            pick_reference_fn=pick_fn,
            package_fn=pkg_fn,
            install_fn=install_fn,
        )
        assert pkgs[0]["language"] == "en"
        assert pkgs[0]["tier"] == "few_shot"

    def test_speakers_detected_event_carries_list(self, tmp_path: Path) -> None:
        events: list[CloneVoiceProgress] = []
        run_clone_voice_job(
            _base_config(tmp_path),
            request_names_fn=lambda _: [],
            progress_cb=events.append,
            analyze_fn=_fake_analyze_factory(
                transcripts_path=tmp_path / "t.jsonl",
                speakers_yaml_path=tmp_path / "s.yaml",
            ),
            read_speakers_yaml_fn=lambda p: _two_speaker_yaml(),
        )
        detected_events = [e for e in events if e.stage == STAGE_SPEAKERS_DETECTED]
        assert len(detected_events) == 1
        speakers = detected_events[0].extra["speakers"]
        assert len(speakers) == 2
        assert all(isinstance(s, DetectedSpeaker) for s in speakers)


# ---------------------------------------------------------------------------
# i18n string table
# ---------------------------------------------------------------------------


class TestCloneVoiceStrings:
    def test_fi_and_en_have_same_keys(self) -> None:
        """Parity check: every clone-voice string must exist in both languages."""
        fi_keys = set(CLONE_VOICE_STRINGS["fi"].keys())
        en_keys = set(CLONE_VOICE_STRINGS["en"].keys())
        assert fi_keys == en_keys, {
            "missing_in_en": sorted(fi_keys - en_keys),
            "missing_in_fi": sorted(en_keys - fi_keys),
        }

    def test_lookup_returns_ui_lang_string(self) -> None:
        assert clone_voice_string("fi", "clone_voice_start") == "Aloita"
        assert clone_voice_string("en", "clone_voice_start") == "Start"

    def test_lookup_falls_through_to_fi_for_unknown_lang(self) -> None:
        assert clone_voice_string("de", "clone_voice_start") == "Aloita"

    def test_lookup_returns_key_for_unknown_key(self) -> None:
        # Visible fallback so missing keys are obvious in dev.
        assert clone_voice_string("fi", "no_such_key") == "no_such_key"


class TestSpeakerPresets:
    def test_preset_keys(self) -> None:
        assert set(SPEAKER_PRESETS) == {"1", "2", "range", "auto"}

    def test_exact_one_and_two(self) -> None:
        assert SPEAKER_PRESETS["1"] == (1, None, None)
        assert SPEAKER_PRESETS["2"] == (2, None, None)

    def test_range_maps_to_min_max(self) -> None:
        assert SPEAKER_PRESETS["range"] == (None, 3, 8)

    def test_auto_is_all_none(self) -> None:
        assert SPEAKER_PRESETS["auto"] == (None, None, None)


# ---------------------------------------------------------------------------
# safe_source_display_name (CLAUDE.md P0 copyright hygiene)
# ---------------------------------------------------------------------------


class TestSafeSourceDisplayName:
    def test_neutral_short_name_passes_through(self) -> None:
        assert safe_source_display_name(Path("interview.wav")) == "interview.wav"

    def test_audiobook_hint_redacts(self) -> None:
        assert (
            safe_source_display_name(Path("My_Audiobook_Ch1.m4b"))
            == "source_audio"
        )

    def test_year_pattern_redacts(self) -> None:
        # "_19xx" / "_20xx" often appear in author-year-publisher filenames.
        assert (
            safe_source_display_name(Path("Some_Book_Holland_Tom_2003.epub"))
            == "source_audio"
        )

    def test_long_name_redacts(self) -> None:
        long = "a" * 50 + ".wav"
        assert safe_source_display_name(Path(long)) == "source_audio"

    def test_unabridged_hint_redacts(self) -> None:
        assert (
            safe_source_display_name(Path("rubicon_unabridged.m4b"))
            == "source_audio"
        )

    def test_case_insensitive_hint(self) -> None:
        assert (
            safe_source_display_name(Path("MyChapter1.wav"))
            == "source_audio"
        )


# ---------------------------------------------------------------------------
# modal smoke tests (headless Tk)
# ---------------------------------------------------------------------------


@pytest.fixture
def ctk_root():
    """Provide a hidden CTk root window for modal tests.

    Skips the test on environments without a display (CI runners that
    block DISPLAY). Windows is fine — Tk initialises without a visible
    window via ``withdraw()``.
    """
    try:
        import customtkinter as ctk
    except ImportError:
        pytest.skip("customtkinter not available")
    try:
        root = ctk.CTk()
    except Exception as exc:
        pytest.skip(f"No display available: {exc}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


class TestPreAnalyzeModal:
    def test_builds_without_error(self, ctk_root) -> None:
        from src.gui_clone_voice import PreAnalyzeModal

        modal = PreAnalyzeModal(ctk_root, ui_lang="fi")
        # Modal exists, widgets wired up.
        assert modal._preset_var.get() == "auto"
        modal._top.destroy()

    def test_cancel_returns_none(self, ctk_root) -> None:
        from src.gui_clone_voice import PreAnalyzeModal

        modal = PreAnalyzeModal(ctk_root, ui_lang="fi")
        modal._on_cancel()
        assert modal._result is None

    def test_start_auto_preset_yields_all_nones(self, ctk_root) -> None:
        from src.gui_clone_voice import PreAnalyzeModal

        modal = PreAnalyzeModal(ctk_root, ui_lang="en")
        modal._preset_var.set("auto")
        modal._on_start()
        lang, num, mn, mx = modal._result
        assert lang in ("fi", "en")
        assert (num, mn, mx) == (None, None, None)

    def test_start_exact_preset_yields_num(self, ctk_root) -> None:
        from src.gui_clone_voice import PreAnalyzeModal

        modal = PreAnalyzeModal(ctk_root, ui_lang="en")
        modal._preset_var.set("2")
        modal._on_start()
        _lang, num, mn, mx = modal._result
        assert (num, mn, mx) == (2, None, None)

    def test_start_range_preset_yields_min_max(self, ctk_root) -> None:
        from src.gui_clone_voice import PreAnalyzeModal

        modal = PreAnalyzeModal(ctk_root, ui_lang="fi")
        modal._preset_var.set("range")
        modal._on_start()
        _lang, num, mn, mx = modal._result
        assert (num, mn, mx) == (None, 3, 8)


class TestNamingGridModal:
    def _speakers(self) -> list[DetectedSpeaker]:
        return [
            DetectedSpeaker(
                speaker_id="SPEAKER_00", total_seconds=600.0, chunk_count=40,
                quality_tier="reduced_lora", default_name="Narrator 1",
            ),
            DetectedSpeaker(
                speaker_id="SPEAKER_01", total_seconds=300.0, chunk_count=20,
                quality_tier="few_shot", default_name="Narrator 2",
            ),
        ]

    def test_builds_one_row_per_speaker(self, ctk_root) -> None:
        from src.gui_clone_voice import NamingGridModal

        modal = NamingGridModal(ctk_root, self._speakers(), ui_lang="en")
        assert len(modal._name_vars) == 2
        assert modal._name_vars[0].get() == "Narrator 1"
        assert modal._name_vars[1].get() == "Narrator 2"
        modal._top.destroy()

    def test_save_collects_edited_names(self, ctk_root) -> None:
        from src.gui_clone_voice import NamingGridModal

        modal = NamingGridModal(ctk_root, self._speakers(), ui_lang="en")
        modal._name_vars[0].set("Alice")
        modal._name_vars[1].set("Bob")
        modal._on_save()
        assert [d.name for d in modal._result] == ["Alice", "Bob"]
        assert all(d.include for d in modal._result)

    def test_unchecked_include_drops_speaker(self, ctk_root) -> None:
        from src.gui_clone_voice import NamingGridModal

        modal = NamingGridModal(ctk_root, self._speakers(), ui_lang="en")
        modal._name_vars[0].set("Keep")
        modal._include_vars[1].set(False)
        modal._on_save()
        assert modal._result[0].include is True
        assert modal._result[1].include is False

    def test_cancel_returns_empty(self, ctk_root) -> None:
        from src.gui_clone_voice import NamingGridModal

        modal = NamingGridModal(ctk_root, self._speakers(), ui_lang="fi")
        modal._on_cancel()
        assert modal._result == []

    def test_blank_name_stripped(self, ctk_root) -> None:
        from src.gui_clone_voice import NamingGridModal

        modal = NamingGridModal(ctk_root, self._speakers(), ui_lang="en")
        modal._name_vars[0].set("   ")
        modal._on_save()
        assert modal._result[0].name == ""


class TestHfTokenPromptModal:
    def test_builds_without_error(self, ctk_root) -> None:
        from src.gui_clone_voice import HfTokenPromptModal

        modal = HfTokenPromptModal(ctk_root, ui_lang="en")
        # Entry exists and starts empty; Save/Cancel are wired.
        assert modal._token_var.get() == ""
        assert modal._save_btn is not None
        assert modal._cancel_btn is not None
        modal._top.destroy()

    def test_cancel_returns_none(self, ctk_root) -> None:
        from src.gui_clone_voice import HfTokenPromptModal

        modal = HfTokenPromptModal(ctk_root, ui_lang="fi")
        modal._on_cancel()
        assert modal._result is None

    def test_save_with_token_returns_token(self, ctk_root) -> None:
        from src.gui_clone_voice import HfTokenPromptModal

        modal = HfTokenPromptModal(ctk_root, ui_lang="en")
        modal._token_var.set("  hf_abc123  ")
        modal._on_save()
        # Leading/trailing whitespace stripped — users paste from the HF
        # UI which sometimes includes a trailing space.
        assert modal._result == "hf_abc123"

    def test_save_empty_returns_none(self, ctk_root) -> None:
        from src.gui_clone_voice import HfTokenPromptModal

        modal = HfTokenPromptModal(ctk_root, ui_lang="en")
        modal._token_var.set("   ")
        modal._on_save()
        # Empty / whitespace-only key is the same as cancel — avoids
        # pushing an empty string through to the HF verify step.
        assert modal._result is None

    def test_browser_buttons_call_open_fn_with_canonical_urls(self, ctk_root) -> None:
        from src.gui_clone_voice import (
            HF_PYANNOTE_MODEL_URL,
            HF_SIGNUP_URL,
            HF_TOKENS_URL,
            HfTokenPromptModal,
        )

        calls: list[str] = []
        modal = HfTokenPromptModal(
            ctk_root, ui_lang="en", open_browser_fn=calls.append,
        )
        # Find the three "Open *" buttons by iterating children and
        # invoking their command. Modal keeps them as a flat pack, so
        # the three consecutive CTkButton children after the step labels
        # are the ones we want.
        import customtkinter as ctk

        buttons = [
            w for w in modal._top.winfo_children()
            if isinstance(w, ctk.CTkButton)
        ]
        # Of the buttons on the top level, the first three are the
        # browser-launchers (Save/Cancel live inside the button-row
        # frame, not directly on the top).
        for btn in buttons[:3]:
            btn.invoke()
        assert calls == [HF_SIGNUP_URL, HF_PYANNOTE_MODEL_URL, HF_TOKENS_URL]
        modal._top.destroy()


class TestHfTokenPromptStrings:
    def test_all_hf_keys_present_in_both_languages(self) -> None:
        required = {
            "hf_token_title", "hf_token_barney_intro",
            "hf_token_step_1", "hf_token_step_2",
            "hf_token_step_3", "hf_token_step_4",
            "hf_token_open_signup", "hf_token_open_model_page",
            "hf_token_open_tokens", "hf_token_entry_label",
            "hf_token_save", "hf_token_cancel",
        }
        for lang in ("fi", "en"):
            missing = required - set(CLONE_VOICE_STRINGS[lang].keys())
            assert not missing, (lang, missing)

    def test_installer_and_modal_urls_match(self) -> None:
        # Keep the modal's URL constants in sync with the installer's.
        from src.engine_installer_voice_cloner import (
            HF_PYANNOTE_MODEL_URL as installer_model,
            HF_SIGNUP_URL as installer_signup,
            HF_TOKENS_URL as installer_tokens,
        )
        from src.gui_clone_voice import (
            HF_PYANNOTE_MODEL_URL as modal_model,
            HF_SIGNUP_URL as modal_signup,
            HF_TOKENS_URL as modal_tokens,
        )
        assert installer_signup == modal_signup
        assert installer_model == modal_model
        assert installer_tokens == modal_tokens
