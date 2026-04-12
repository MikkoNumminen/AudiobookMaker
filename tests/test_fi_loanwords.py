"""Unit tests for src/fi_loanwords.py — Pass I Finnish loanword respelling.

All tests use inline YAML fixtures written to tmp_path so they are fully
independent of Agent B's data/fi_loanwords.yaml file.  The real YAML path
is monkey-patched via the module-level _YAML_PATH variable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from textwrap import dedent
from typing import Generator
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURE_YAML = dedent("""\
    ismi_stems:
      - human
      - merkantil
      - kapitalist
      - sosialism

    tio_stems:
      - instituu
      - kodifikaa
      - stipula
      - konstitu

    latin_phrases:
      ius commune: jus kommune
      ius proprium: jus proprium
      usus modernus: usus modernus
      usus modernus pandectarum: usus modernus pandektarum

    foreign_names:
      Wittenberg: Vittenberg
      Leiden: Leiden
      Oxford: Oksford
""")


def _write_fixture(tmp_path: Path, content: str = FIXTURE_YAML) -> Path:
    """Write YAML content to a temp file and return its path."""
    p = tmp_path / "fi_loanwords.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _reload_module(yaml_path: Path):
    """Reload fi_loanwords with a patched _YAML_PATH and cleared cache."""
    import src.fi_loanwords as mod
    mod._lexicon_cache = None
    mod._load_attempted = False
    mod._YAML_PATH = yaml_path
    return mod


# ---------------------------------------------------------------------------
# TestYamlLoader
# ---------------------------------------------------------------------------


class TestYamlLoader:
    def test_all_four_sections_populated(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        mod = _reload_module(p)
        lex = mod._load_lexicon()
        assert lex is not None
        assert "human" in lex.ismi_stems
        assert "instituu" in lex.tio_stems
        assert any(k == "ius commune" for k, _ in lex.latin_phrases)
        assert "Wittenberg" in lex.foreign_names

    def test_missing_yaml_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        mod = _reload_module(missing)
        with caplog.at_level(logging.WARNING, logger="src.fi_loanwords"):
            lex = mod._load_lexicon()
        assert lex is None
        assert any("disabled" in r.message for r in caplog.records)

    def test_missing_pyyaml_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        p = _write_fixture(tmp_path)
        mod = _reload_module(p)
        with patch.dict(sys.modules, {"yaml": None}):
            mod._lexicon_cache = None
            mod._load_attempted = False
            with caplog.at_level(logging.WARNING, logger="src.fi_loanwords"):
                lex = mod._load_lexicon()
        assert lex is None
        assert any("PyYAML" in r.message or "disabled" in r.message for r in caplog.records)

    def test_empty_yaml_returns_empty_lexicon(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path, "")
        mod = _reload_module(p)
        lex = mod._load_lexicon()
        # Empty YAML → None from safe_load; treated as empty Lexicon.
        # apply_loanword_respellings should return input unchanged.
        result = mod.apply_loanword_respellings("humanismi")
        assert result == "humanismi"

    def test_malformed_yaml_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        p = _write_fixture(tmp_path, "key: [unclosed")
        mod = _reload_module(p)
        with caplog.at_level(logging.WARNING, logger="src.fi_loanwords"):
            lex = mod._load_lexicon()
        assert lex is None
        assert any("disabled" in r.message for r in caplog.records)

    def test_apply_returns_input_when_yaml_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_file.yaml"
        mod = _reload_module(missing)
        assert mod.apply_loanword_respellings("humanismi") == "humanismi"


# ---------------------------------------------------------------------------
# TestIsmiRespelling
# ---------------------------------------------------------------------------


class TestIsmiRespelling:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> Generator[None, None, None]:
        p = _write_fixture(tmp_path)
        self.mod = _reload_module(p)
        # Ensure lexicon is loaded.
        self.mod._load_lexicon()
        yield

    def test_basic_nominative(self) -> None:
        assert self.mod.apply_loanword_respellings("humanismi") == "humanis-mi"

    def test_genitive_suffix_preserved(self) -> None:
        assert self.mod.apply_loanword_respellings("humanismin") == "humanis-min"

    def test_inessive_suffix_preserved(self) -> None:
        assert self.mod.apply_loanword_respellings("humanismissa") == "humanis-missa"

    def test_capitalisation_preserved(self) -> None:
        result = self.mod.apply_loanword_respellings("Humanismi")
        assert result == "Humanis-mi"

    def test_merkantilismi_respelled(self) -> None:
        result = self.mod.apply_loanword_respellings("merkantilismia")
        assert result == "merkantilis-mia"

    def test_unknown_stem_unchanged(self) -> None:
        # "xyz" not in ismi_stems
        result = self.mod.apply_loanword_respellings("xyzismi")
        assert result == "xyzismi"

    def test_multiple_ismi_words_in_sentence(self) -> None:
        result = self.mod.apply_loanword_respellings(
            "humanismi ja merkantilismi olivat tärkeitä"
        )
        assert "humanis-mi" in result
        assert "merkantilis-mi" in result

    def test_word_boundary_prevents_mid_word_match(self) -> None:
        # "tesismikeskus" — "tesi" is not in stems and the \b anchors should
        # prevent a spurious match even if the substring "ismi" is present.
        # The regex \b(\w+?)ismi(\w*)\b will match "tesismikeskus" as a whole
        # word with stem="tes" — which is NOT in the whitelist — so it stays.
        result = self.mod.apply_loanword_respellings("tesismikeskus")
        assert result == "tesismikeskus"


# ---------------------------------------------------------------------------
# TestTioRespelling
# ---------------------------------------------------------------------------


class TestTioRespelling:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> Generator[None, None, None]:
        p = _write_fixture(tmp_path)
        self.mod = _reload_module(p)
        self.mod._load_lexicon()
        yield

    def test_instituutio_nominative(self) -> None:
        assert self.mod.apply_loanword_respellings("instituutio") == "instituu-tio"

    def test_instituution_genitive(self) -> None:
        assert self.mod.apply_loanword_respellings("instituution") == "instituu-tion"

    def test_kodifikaatio_respelled(self) -> None:
        result = self.mod.apply_loanword_respellings("kodifikaatio")
        assert result == "kodifikaa-tio"

    def test_stipulatio_respelled(self) -> None:
        result = self.mod.apply_loanword_respellings("stipulatio")
        assert result == "stipula-tio"

    def test_valtio_unchanged(self) -> None:
        # "valtio" is native Finnish — "val" must NOT be in tio_stems.
        assert self.mod.apply_loanword_respellings("valtio") == "valtio"

    def test_valtiosta_unchanged(self) -> None:
        assert self.mod.apply_loanword_respellings("valtiosta") == "valtiosta"

    def test_valtion_unchanged(self) -> None:
        assert self.mod.apply_loanword_respellings("valtion") == "valtion"

    def test_unknown_tio_stem_unchanged(self) -> None:
        # "foo" not in tio_stems
        assert self.mod.apply_loanword_respellings("footio") == "footio"


# ---------------------------------------------------------------------------
# TestLatinPhrases
# ---------------------------------------------------------------------------


class TestLatinPhrases:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> Generator[None, None, None]:
        p = _write_fixture(tmp_path)
        self.mod = _reload_module(p)
        self.mod._load_lexicon()
        yield

    def test_ius_commune_substituted(self) -> None:
        assert self.mod.apply_loanword_respellings("ius commune") == "jus kommune"

    def test_ius_proprium_substituted(self) -> None:
        assert self.mod.apply_loanword_respellings("ius proprium") == "jus proprium"

    def test_longer_phrase_matched_before_shorter(self) -> None:
        # "usus modernus pandectarum" must be matched as a unit, not split into
        # "usus modernus" + "pandectarum".
        result = self.mod.apply_loanword_respellings("usus modernus pandectarum")
        assert result == "usus modernus pandektarum"
        # The shorter phrase "usus modernus" alone should also work.
        result2 = self.mod.apply_loanword_respellings("usus modernus")
        assert result2 == "usus modernus"

    def test_case_insensitive_match(self) -> None:
        # Input is "Ius Commune"; replacement is the verbatim YAML value.
        # Design choice: replacement is always the YAML value, NOT case-adapted.
        result = self.mod.apply_loanword_respellings("Ius Commune")
        assert result == "jus kommune"

    def test_latin_phrase_inside_sentence(self) -> None:
        result = self.mod.apply_loanword_respellings(
            "Tämä on ius commune -oppia."
        )
        assert "jus kommune" in result
        assert "Tämä on" in result


# ---------------------------------------------------------------------------
# TestForeignNames
# ---------------------------------------------------------------------------


class TestForeignNames:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> Generator[None, None, None]:
        p = _write_fixture(tmp_path)
        self.mod = _reload_module(p)
        self.mod._load_lexicon()
        yield

    def test_wittenberg_substituted(self) -> None:
        assert self.mod.apply_loanword_respellings("Wittenberg") == "Vittenberg"

    def test_leiden_alone_substituted(self) -> None:
        # The YAML maps Leiden → Leiden (same), so substitution is a no-op here,
        # but the mechanism is exercised.
        assert self.mod.apply_loanword_respellings("Leiden") == "Leiden"

    def test_leidenissa_not_substituted(self) -> None:
        # Declined form — exact-word substitution does NOT handle it.
        # This documents a known limitation of the exact-word approach.
        result = self.mod.apply_loanword_respellings("Leidenissä")
        assert result == "Leidenissä"

    def test_unknown_name_unchanged(self) -> None:
        assert self.mod.apply_loanword_respellings("Göttingen") == "Göttingen"

    def test_name_inside_sentence(self) -> None:
        result = self.mod.apply_loanword_respellings(
            "Hän opiskeli Wittenbergissä ja Oxford."
        )
        # Oxford is in our fixture
        assert "Oksford" in result
        # Wittenbergissä is a declined form — NOT substituted (known limitation)
        assert "Wittenbergissä" in result

    def test_oxford_substituted(self) -> None:
        assert self.mod.apply_loanword_respellings("Oxford") == "Oksford"


# ---------------------------------------------------------------------------
# TestPassIIntegrationWithNormalizer
# ---------------------------------------------------------------------------


class TestPassIIntegrationWithNormalizer:
    @pytest.fixture(autouse=True)
    def _patch_lexicon(self, tmp_path: Path) -> Generator[None, None, None]:
        """Patch the fi_loanwords module's YAML path before importing normalizer."""
        import src.fi_loanwords as lw_mod
        lw_mod._lexicon_cache = None
        lw_mod._load_attempted = False
        p = _write_fixture(tmp_path)
        lw_mod._YAML_PATH = p
        yield
        # Restore to original path after test.
        lw_mod._lexicon_cache = None
        lw_mod._load_attempted = False
        from src.fi_loanwords import _YAML_PATH as orig
        lw_mod._YAML_PATH = Path(__file__).parent.parent / "data" / "fi_loanwords.yaml"

    def test_humanismi_respelled_through_normalizer(self) -> None:
        from src.tts_engine import normalize_finnish_text
        result = normalize_finnish_text("humanismi")
        assert "humanis-mi" in result

    def test_valtio_unchanged_through_normalizer(self) -> None:
        from src.tts_engine import normalize_finnish_text
        result = normalize_finnish_text("valtio")
        assert "valtio" in result
        assert "-tio" not in result

    def test_number_expansion_and_loanword_respelling_both_run(self) -> None:
        from src.tts_engine import normalize_finnish_text
        result = normalize_finnish_text("vuonna 1500 humanismi oli")
        # Number should be expanded (no digit '1' remaining from '1500').
        assert "1500" not in result
        # Loanword should be respelled.
        assert "humanis-mi" in result


# ---------------------------------------------------------------------------
# TestFallbackWhenYamlMissing
# ---------------------------------------------------------------------------


class TestFallbackWhenYamlMissing:
    @pytest.fixture(autouse=True)
    def _patch_missing(self, tmp_path: Path) -> Generator[None, None, None]:
        import src.fi_loanwords as lw_mod
        lw_mod._lexicon_cache = None
        lw_mod._load_attempted = False
        lw_mod._YAML_PATH = tmp_path / "nonexistent.yaml"
        yield
        lw_mod._lexicon_cache = None
        lw_mod._load_attempted = False
        lw_mod._YAML_PATH = Path(__file__).parent.parent / "data" / "fi_loanwords.yaml"

    def test_text_passes_through_unchanged(self) -> None:
        import src.fi_loanwords as lw_mod
        result = lw_mod.apply_loanword_respellings("humanismi instituutio")
        assert result == "humanismi instituutio"

    def test_normalize_finnish_text_still_works(self) -> None:
        from src.tts_engine import normalize_finnish_text
        # Should not raise; number expansion should still work.
        result = normalize_finnish_text("vuonna 1500")
        assert "1500" not in result
        assert "vuonna" in result
