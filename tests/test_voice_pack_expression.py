"""Tests for :mod:`src.voice_pack.expression`."""

from __future__ import annotations

import pytest

from src.voice_pack.expression import (
    BUILT_IN_PRESETS,
    ExpressionDirective,
    ExpressionPlan,
    ExpressionPreset,
    is_valid_preset_name,
    parse_markup,
)


# ---------------------------------------------------------------------------
# ExpressionPreset
# ---------------------------------------------------------------------------


class TestExpressionPreset:
    def test_preset_clamps_exaggeration_high(self) -> None:
        p = ExpressionPreset("x", 3.0, 0.5)
        assert p.exaggeration == 2.0

    def test_preset_clamps_exaggeration_low(self) -> None:
        p = ExpressionPreset("x", -0.5, 0.5)
        assert p.exaggeration == 0.0

    def test_preset_clamps_cfg_weight(self) -> None:
        high = ExpressionPreset("x", 0.5, 2.0)
        low = ExpressionPreset("x", 0.5, -0.1)
        assert high.cfg_weight == 1.0
        assert low.cfg_weight == 0.0

    def test_preset_to_dict(self) -> None:
        p = ExpressionPreset("shout", 1.2, 0.7)
        d = p.to_dict()
        assert d == {"name": "shout", "exaggeration": 1.2, "cfg_weight": 0.7}


# ---------------------------------------------------------------------------
# BUILT_IN_PRESETS
# ---------------------------------------------------------------------------


class TestBuiltInPresets:
    def test_builtin_presets_all_named_consistently(self) -> None:
        for key, preset in BUILT_IN_PRESETS.items():
            assert key == preset.name

    def test_builtin_presets_monotonic(self) -> None:
        order = ["whisper", "calm", "neutral", "intense", "shout"]
        values = [BUILT_IN_PRESETS[n].exaggeration for n in order]
        assert values == sorted(values)
        # strictly increasing
        for a, b in zip(values, values[1:]):
            assert b > a


# ---------------------------------------------------------------------------
# ExpressionPlan.resolve_for
# ---------------------------------------------------------------------------


class TestExpressionPlanResolve:
    def test_resolve_uses_plan_default_when_no_directive(self) -> None:
        plan = ExpressionPlan()
        neutral = BUILT_IN_PRESETS["neutral"]
        assert plan.resolve_for(5) == (neutral.exaggeration, neutral.cfg_weight)

    def test_resolve_uses_preset_from_directive(self) -> None:
        plan = ExpressionPlan(
            directives=[ExpressionDirective(sentence_index=2, preset="shout")]
        )
        shout = BUILT_IN_PRESETS["shout"]
        assert plan.resolve_for(2) == (shout.exaggeration, shout.cfg_weight)

    def test_resolve_explicit_overrides_preset(self) -> None:
        plan = ExpressionPlan(
            directives=[
                ExpressionDirective(
                    sentence_index=0, preset="calm", exaggeration=1.1
                )
            ]
        )
        exag, cfg = plan.resolve_for(0)
        assert exag == 1.1
        assert cfg == BUILT_IN_PRESETS["calm"].cfg_weight  # 0.4

    def test_resolve_unknown_preset_falls_back_to_neutral(self) -> None:
        plan = ExpressionPlan(
            directives=[
                ExpressionDirective(sentence_index=0, preset="nonexistent")
            ]
        )
        neutral = BUILT_IN_PRESETS["neutral"]
        assert plan.resolve_for(0) == (neutral.exaggeration, neutral.cfg_weight)

    def test_resolve_last_directive_wins_for_same_index(self) -> None:
        plan = ExpressionPlan(
            directives=[
                ExpressionDirective(sentence_index=1, preset="calm"),
                ExpressionDirective(sentence_index=1, preset="shout"),
            ]
        )
        shout = BUILT_IN_PRESETS["shout"]
        assert plan.resolve_for(1) == (shout.exaggeration, shout.cfg_weight)

    def test_resolve_unaffected_indices_use_default(self) -> None:
        plan = ExpressionPlan(
            directives=[ExpressionDirective(sentence_index=3, preset="shout")]
        )
        neutral = BUILT_IN_PRESETS["neutral"]
        expected = (neutral.exaggeration, neutral.cfg_weight)
        for idx in (0, 1, 2, 4, 5):
            assert plan.resolve_for(idx) == expected


# ---------------------------------------------------------------------------
# ExpressionPlan.presets
# ---------------------------------------------------------------------------


class TestExpressionPlanPresets:
    def test_presets_custom_shadows_builtin(self) -> None:
        custom = ExpressionPreset("shout", 0.1, 0.1)
        plan = ExpressionPlan(custom_presets={"shout": custom})
        assert plan.presets()["shout"] is custom
        # and resolve respects it
        plan.directives.append(
            ExpressionDirective(sentence_index=0, preset="shout")
        )
        assert plan.resolve_for(0) == (0.1, 0.1)

    def test_presets_merged_dict(self) -> None:
        custom = ExpressionPreset("my_preset", 0.5, 0.5)
        plan = ExpressionPlan(custom_presets={"my_preset": custom})
        merged = plan.presets()
        for key in BUILT_IN_PRESETS:
            assert key in merged
        assert "my_preset" in merged


# ---------------------------------------------------------------------------
# parse_markup
# ---------------------------------------------------------------------------


class TestParseMarkup:
    def test_parse_markup_no_directives_passthrough(self) -> None:
        text = "Hello world. This is fine.\nAnother line."
        cleaned, plan = parse_markup(text)
        assert cleaned == text
        assert plan.directives == []

    def test_parse_markup_strips_directive_lines(self) -> None:
        text = (
            "{{expr:shout}}\n"
            "A loud sentence.\n"
            "{{expr:default}}\n"
            "Calm one.\n"
            "Another calm one."
        )
        cleaned, _plan = parse_markup(text)
        assert "{{expr:" not in cleaned

    def test_parse_markup_single_preset_applies_to_following_sentence(self) -> None:
        # Preset sticks until changed -- so BOTH following sentences
        # receive the directive until an explicit ``{{expr:default}}``
        # resets state.
        text = "{{expr:shout}}\nHELLO!\nCalm again."
        _cleaned, plan = parse_markup(text)
        by_idx = {d.sentence_index: d for d in plan.directives}
        assert 0 in by_idx and by_idx[0].preset == "shout"
        assert 1 in by_idx and by_idx[1].preset == "shout"

    def test_parse_markup_default_clears_state(self) -> None:
        text = "{{expr:shout}}\nA.\n{{expr:default}}\nB."
        _cleaned, plan = parse_markup(text)
        by_idx = {d.sentence_index: d for d in plan.directives}
        assert 0 in by_idx and by_idx[0].preset == "shout"
        assert 1 not in by_idx

    def test_parse_markup_inline_kwargs(self) -> None:
        text = "{{expr:whisper exag=0.1 cfg=0.3}}\nHi."
        _cleaned, plan = parse_markup(text)
        assert len(plan.directives) == 1
        d = plan.directives[0]
        assert d.preset == "whisper"
        assert d.exaggeration == 0.1
        assert d.cfg_weight == 0.3

    def test_parse_markup_ignores_unknown_kwarg(self) -> None:
        text = "{{expr:shout foo=bar}}\nBoom."
        _cleaned, plan = parse_markup(text)
        assert len(plan.directives) == 1
        d = plan.directives[0]
        assert d.preset == "shout"
        assert d.exaggeration is None
        assert d.cfg_weight is None

    def test_parse_markup_malformed_does_not_crash(self) -> None:
        # Two malformed directive forms
        text_a = "{{expr:}}\nHello."
        text_b = "{{expr: }}\nHello."
        for text in (text_a, text_b):
            cleaned, plan = parse_markup(text)
            assert "{{expr:" not in cleaned
            # malformed directive leaves state untouched -> no directives
            assert plan.directives == []

    def test_parse_markup_preserves_blank_lines_in_cleaned_text(self) -> None:
        text = "Line one.\n\nLine two.\n\nLine three."
        cleaned, _plan = parse_markup(text)
        # three sentences separated by blank lines -> cleaned preserves them
        assert "\n\n" in cleaned
        assert cleaned.count("\n\n") == 2

    def test_parse_markup_sentence_numbering_matches_cleaned(self) -> None:
        text = (
            "{{expr:shout}}\n"
            "First sentence.\n"
            "{{expr:whisper}}\n"
            "Second sentence.\n"
            "Third sentence."
        )
        _cleaned, plan = parse_markup(text)
        indices = sorted({d.sentence_index for d in plan.directives})
        assert indices == [0, 1, 2]


# ---------------------------------------------------------------------------
# is_valid_preset_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("shout", True),
        ("my_preset", True),
        ("x1", True),
        ("1bad", False),
        ("", False),
        ("Has Space", False),
        ("emoji\U0001f600", False),
    ],
)
def test_is_valid_preset_name(name: str, expected: bool) -> None:
    assert is_valid_preset_name(name) is expected
