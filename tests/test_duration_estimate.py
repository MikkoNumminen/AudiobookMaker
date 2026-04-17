"""Tests for src.duration_estimate."""

from __future__ import annotations

import pytest

from src.duration_estimate import (
    estimate_audio_duration,
    estimate_wall_time,
    format_duration,
    estimate_job,
)


# ---------- estimate_audio_duration ----------

def test_audio_duration_finnish_rate():
    # 1200 chars -> 60 s at 20 chars/s
    assert estimate_audio_duration(1200, "fi") == pytest.approx(60.0)


def test_audio_duration_english_rate():
    # 1350 chars -> 60 s at 22.5 chars/s
    assert estimate_audio_duration(1350, "en") == pytest.approx(60.0)


def test_finnish_slower_than_english_for_same_chars():
    # Same char count should yield longer audio in Finnish (slower rate)
    n = 10_000
    fi = estimate_audio_duration(n, "fi")
    en = estimate_audio_duration(n, "en")
    assert fi > en


def test_unknown_language_falls_back_to_finnish():
    n = 5000
    assert estimate_audio_duration(n, "zz") == estimate_audio_duration(n, "fi")


def test_audio_duration_zero_and_negative():
    assert estimate_audio_duration(0) == 0.0
    assert estimate_audio_duration(-10) == 0.0


# ---------- estimate_wall_time ----------

def test_wall_time_edge_fast():
    # RTF 5.0: 60 s audio -> 12 s wall
    assert estimate_wall_time(60.0, "edge") == pytest.approx(12.0)


def test_wall_time_piper_fast():
    # RTF 6.0: 60 s audio -> 10 s wall
    assert estimate_wall_time(60.0, "piper") == pytest.approx(10.0)


def test_wall_time_chatterbox_cuda():
    # RTF 0.85: 60 s audio -> ~70.6 s wall
    wall = estimate_wall_time(60.0, "chatterbox_fi", "cuda")
    assert wall == pytest.approx(60.0 / 0.85)


def test_wall_time_chatterbox_cpu_is_much_larger():
    gpu = estimate_wall_time(60.0, "chatterbox_fi", "cuda")
    cpu = estimate_wall_time(60.0, "chatterbox_fi", "cpu")
    assert cpu > gpu * 10  # 20x penalty


def test_wall_time_voxcpm():
    assert estimate_wall_time(60.0, "voxcpm2") == pytest.approx(60.0)


def test_wall_time_unknown_engine_conservative():
    # Unknown engine -> 2x audio seconds
    assert estimate_wall_time(30.0, "mystery") == pytest.approx(60.0)


def test_wall_time_edge_cpu_unaffected():
    # edge is not a GPU engine, so cpu should not trigger the penalty
    wall_cuda = estimate_wall_time(60.0, "edge", "cuda")
    wall_cpu = estimate_wall_time(60.0, "edge", "cpu")
    assert wall_cuda == wall_cpu


def test_wall_time_zero_audio():
    assert estimate_wall_time(0.0, "edge") == 0.0


# ---------- format_duration ----------

def test_format_duration_seconds_bracket():
    assert format_duration(45) == "45 s"
    assert format_duration(1) == "1 s"
    assert format_duration(59) == "59 s"


def test_format_duration_minutes_bracket():
    assert format_duration(60) == "1 min 0 s"
    assert format_duration(12 * 60 + 34) == "12 min 34 s"
    assert format_duration(3599) == "59 min 59 s"


def test_format_duration_hours_bracket():
    assert format_duration(3600) == "1 h 0 min"
    assert format_duration(3600 + 23 * 60) == "1 h 23 min"
    assert format_duration(10 * 3600 + 30 * 60) == "10 h 30 min"


def test_format_duration_zero_and_negative():
    assert format_duration(0) == "0 s"
    assert format_duration(-10) == "0 s"


def test_format_duration_none():
    assert format_duration(None) == "?"


# ---------- estimate_job ----------

def test_estimate_job_keys():
    result = estimate_job(1000, "edge", "fi")
    expected_keys = {
        "audio_seconds",
        "wall_seconds",
        "chars_per_second_synth",
        "audio_human",
        "wall_human",
    }
    assert expected_keys <= set(result.keys())


def test_estimate_job_internally_consistent():
    # wall_seconds ~= audio_seconds / RTF  (RTF 0.85 for chatterbox_fi)
    result = estimate_job(10_000, "chatterbox_fi", "en", "cuda")
    assert result["wall_seconds"] == pytest.approx(result["audio_seconds"] / 0.85)


def test_estimate_job_human_strings_are_strings():
    result = estimate_job(1000, "edge", "fi")
    assert isinstance(result["audio_human"], str)
    assert isinstance(result["wall_human"], str)


# ---------- sanity: Rubicon-scale job ----------

def test_rubicon_scale_sanity():
    # 800k chars, English, chatterbox on cuda
    # Audio: 800_000 / 22.5 = ~35_555 s = ~9.88 h -> about 10 h
    # Wall: audio / 0.85 = ~41_830 s = ~11.6 h -> about 12 h
    result = estimate_job(800_000, "chatterbox_fi", "en", "cuda")
    audio_h = result["audio_seconds"] / 3600.0
    wall_h = result["wall_seconds"] / 3600.0
    assert 9.0 < audio_h < 11.0, f"audio ~{audio_h:.2f} h, expected ~10 h"
    assert 11.0 < wall_h < 13.0, f"wall ~{wall_h:.2f} h, expected ~12 h"
