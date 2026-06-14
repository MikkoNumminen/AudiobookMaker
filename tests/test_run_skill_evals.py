"""Tests for scripts/run_skill_evals.py with a mocked model client.

Validates the harness logic — discovery, prompt/response handling, judge
parsing, pass/fail accounting, and the opt-in skip — without making any real
(metered) API call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import run_skill_evals as rse  # noqa: E402


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _FakeClient:
    """Returns a canned candidate for normal calls and a canned verdict for
    judge calls (judge calls carry `output_config`)."""

    def __init__(self, candidate: str, verdict: dict) -> None:
        self._candidate = candidate
        self._verdict = verdict
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        if "output_config" in kwargs:
            return _Response(json.dumps(self._verdict))
        return _Response(self._candidate)


def test_discover_finds_all_skill_evals() -> None:
    suites = rse.discover_skill_evals()
    assert len(suites) >= 10
    for name, skill_md, evals in suites:
        assert skill_md.name == "SKILL.md" and skill_md.exists()
        assert evals and all("prompt" in e and "expected_output" in e for e in evals)


def test_discover_filters_by_skill() -> None:
    suites = rse.discover_skill_evals(only="release-cut")
    assert [s[0] for s in suites] == ["release-cut"]


def test_extract_text_joins_text_blocks() -> None:
    resp = _Response("hello")
    resp.content.append(_Block(" world"))
    assert rse._extract_text(resp) == "hello world"


def test_grade_parses_pass_and_fail() -> None:
    passed, reason = rse.grade(
        _FakeClient("x", {"passed": True, "reason": "looks right"}),
        expected="do the thing",
        candidate="did the thing",
        model="m",
    )
    assert passed is True and reason == "looks right"

    failed, why = rse.grade(
        _FakeClient("x", {"passed": False, "reason": "missed a step"}),
        expected="do the thing",
        candidate="did nothing",
        model="m",
    )
    assert failed is False and why == "missed a step"


def test_run_eval_makes_candidate_then_judge_call() -> None:
    client = _FakeClient("a candidate answer", {"passed": True, "reason": "ok"})
    result = rse.run_eval(
        client,
        skill_md="SKILL BODY",
        eval_case={"id": 1, "name": "case-1", "prompt": "p", "expected_output": "e"},
        model="m",
    )
    assert result == {"id": 1, "name": "case-1", "passed": True, "reason": "ok"}
    # Two calls: candidate (no output_config) then judge (with output_config).
    assert len(client.calls) == 2
    assert "output_config" not in client.calls[0]
    assert "output_config" in client.calls[1]
    assert client.calls[0]["system"] == "SKILL BODY"


class _BadJudgeClient:
    """Judge returns non-JSON — grade() must not crash."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs: object) -> _Response:
        return _Response("not json at all" if "output_config" in kwargs else "candidate")


class _RaisingClient:
    """Every call fails — main() must treat the eval as failed and continue."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs: object) -> _Response:
        raise RuntimeError("model API unavailable")


def test_grade_treats_malformed_verdict_as_failure() -> None:
    passed, reason = rse.grade(_BadJudgeClient(), expected="e", candidate="c", model="m")
    assert passed is False
    assert "could not parse" in reason


def test_main_continues_past_a_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rse, "_make_client", lambda: _RaisingClient())
    # Each eval errors -> counted as a failure -> run completes, returns nonzero.
    assert rse.main(["--skill", "release-cut"]) == 1


def test_main_skips_cleanly_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Should not raise, should not call any real client, returns 0 (opt-in skip).
    assert rse.main(["--skill", "release-cut"]) == 0


def test_main_reports_failures_with_injected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rse, "_make_client", lambda: _FakeClient("ans", {"passed": False, "reason": "no"})
    )
    # One skill, evals all fail -> nonzero exit.
    assert rse.main(["--skill", "release-cut"]) == 1

    monkeypatch.setattr(
        rse, "_make_client", lambda: _FakeClient("ans", {"passed": True, "reason": "ok"})
    )
    assert rse.main(["--skill", "release-cut"]) == 0
