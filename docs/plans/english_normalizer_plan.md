# Plan: English Text Normalizer + Language-Safe Dispatcher

**Status:** Awaiting user approval before implementation.
**Goal:** Make Finnish/English normalization mix-ups architecturally impossible, and give English its own audiobook-grade normalizer.

---

## 1. The bug, restated

`scripts/generate_chatterbox_audiobook.py:331` calls `normalize_finnish_text(text)` unconditionally — no language check. Every English run through Chatterbox gets Finnish-only rewrites: Roman numerals expanded as Finnish ordinals (`IV → neljäs`), case-inflected number forms, loanword respelling. The 1-hour Rubicon English audiobook has these baked into the audio.

The main GUI path through [src/tts_engine.py:275](src/tts_engine.py#L275) *does* guard with `if config.language == "fi"`, so this is a Chatterbox-script-specific leak. Two of the three normalize call sites are unguarded:

| Call site | Guarded? |
|---|---|
| [src/tts_engine.py:276](src/tts_engine.py#L276) | yes (`config.language == "fi"`) |
| [scripts/generate_chatterbox_audiobook.py:331](scripts/generate_chatterbox_audiobook.py#L331) | **no** |
| [dev_chatterbox_fi.py:369,438](dev_chatterbox_fi.py#L369) | no (dev script, FI-only by design) |

Fixing only the Chatterbox script would patch the symptom. The plan below makes the bug class impossible.

---

## 2. Approach summary

**Single dispatcher + hard language guards + per-language modules.**

```
caller ──► normalize_text(text, lang) ──► tts_normalizer_fi  (lang="fi")
                                      └─► tts_normalizer_en  (lang="en")
```

Each per-language module raises `LanguageMismatchError` if invoked through the dispatcher with the wrong `_lang` kwarg. Cross-contamination tests assert that English text fed into the Finnish normalizer does not produce Finnish suffixes, and vice versa.

**English normalizer** is built in pure Python, rules inspired by NVIDIA NeMo's English text-processing grammars (Apache 2.0, freely reimplementable). No `pynini`, no FST runtime, no NeMo dependency. Uses lightweight pure-Python helpers: `inflect`, `num2words` (already a dep), `roman`.

**Finnish normalizer** is left as-is structurally. Only adds the language guard. 419 existing tests stay green.

---

## 3. New files

| Path | Purpose | Lines |
|---|---|---|
| `src/tts_normalizer.py` | Dispatcher. Exports `normalize_text(text, lang, **opts)` and `LanguageMismatchError`. | ~80 |
| `src/tts_normalizer_en.py` | English normalizer, passes A–K (phase 1) then L–S (phase 2). | ~500 phase 1 / +400 phase 2 |
| `data/en_abbreviations.yaml` | Lookup for `Mr.`/`Dr.`/`St.`/etc. | ~200 rows |
| `data/en_roman_numeral_context.yaml` | Whitelist of words that legitimize Roman expansion (`Chapter`, `Pope`, `Henry`, …). Prevents bare `I` pronoun being read as "one". | ~80 rows |
| `tests/test_tts_normalizer_en.py` | Per-pass unit tests + Rubicon fixture. | ~600 |
| `tests/test_tts_normalizer_dispatcher.py` | Routing + cross-contamination tests. | ~200 |
| `docs/NORMALIZER_EN.md` | Pass list + NeMo correspondence + known gaps. | ~150 |

## 4. Modified files

- `src/tts_normalizer_fi.py` — add `_lang: str | None = None` kwarg to `normalize_finnish_text`. If passed and not `"fi"`, raise `LanguageMismatchError`. Default `None` keeps existing tests green.
- `src/tts_engine.py` — line 275 becomes `text = normalize_text(text, config.language, ...)`. Re-exports updated.
- `scripts/generate_chatterbox_audiobook.py` — line 331 becomes `content = normalize_text(content, args.language)`.
- `requirements.txt` — add `inflect>=7.0`, `num2words>=0.5.13`, `roman>=4.2`. All pure Python, Windows-safe.
- `audiobookmaker.spec` — add hidden imports + new YAML datas.

## 5. Dispatcher API

```python
# src/tts_normalizer.py
class LanguageMismatchError(ValueError): ...

SUPPORTED_LANGS = ("fi", "en")

def normalize_text(
    text: str,
    lang: str,
    *,
    year_shortening: str = "radio",   # FI-only; ignored for EN
    drop_citations: bool = True,      # FI-only; ignored for EN
) -> str:
    """Dispatch to the per-language normalizer. Raises ValueError on
    unknown lang. Each backend enforces its own language guard."""
```

Routing: lazy import (`fi` → `tts_normalizer_fi`, `en` → `tts_normalizer_en`). Always passes `_lang=lang` to the backend.

## 6. Language-guard mechanism

**Hard raise, not silent passthrough** — silent passthrough is exactly the bug we are fixing.

```python
_MY_LANG = "fi"

def normalize_finnish_text(text: str, *, _lang: str | None = None, **kw) -> str:
    if _lang is not None and _lang != _MY_LANG:
        raise LanguageMismatchError(
            f"normalize_finnish_text called with lang={_lang!r}; "
            f"this module only handles {_MY_LANG!r}. "
            f"Use src.tts_normalizer.normalize_text."
        )
    ...
```

Why kwarg, not positional: 419 existing FI tests use `normalize_finnish_text(text)` — they stay green. New code routes through dispatcher, which always supplies `_lang`.

A CI AST check enforces "no production code calls per-language normalizers directly; all paths go through `normalize_text`." Tests and `dev_*.py` scripts are exempted.

## 7. English normalizer pass list

Modeled on NeMo English TN categories. Order is fixed.

### Phase 1 — must-ship for audiobooks

| Pass | Name | Examples |
|---|---|---|
| A | Metadata strip | drop ISBN, DOI, `Copyright ©`, CC-license parens |
| B | Whitespace/quote cleanup | smart quotes, NBSP, `...` → `…`, TOC dot leaders |
| C | Abbreviations | `Mr./Mrs./Dr./Prof./St./vs./etc./i.e./e.g./a.m./p.m./No./Vol./Ch.` (YAML lookup) |
| D | Roman numerals in context | `Chapter IV` → "Chapter four"; `Louis XIV` → "Louis the fourteenth". Requires whitelist context — never expands bare `I`/`A`/`MIX` |
| E | Ordinal digits | `1st, 2nd, 21st` via `num2words(n, to='ordinal')` |
| F | Years | `1917` → "nineteen seventeen", `2004` → "two thousand four", `1920s` → "nineteen twenties", `1914–1918` → "nineteen fourteen to nineteen eighteen" |
| G | Cardinal integers | bare integers via `num2words`, handles `1,234` and negatives |
| H | Decimals | `3.14` → "three point one four" |
| I | Fractions | `1/2` → "one half", `3/4` → "three quarters" |
| J | Sentence-terminal period fix | clean boundaries for chunker |
| K | Final whitespace collapse | |

### Phase 2 — ship after phase 1 is stable

| Pass | Name | Notes |
|---|---|---|
| L | Currency | `$5.99`, `£10`, `€1.5M` |
| M | Units | `5 km`, `32 °F`, `100 mph`, plural-aware |
| N | Time of day | `3:45 p.m.`, `14:00` |
| O | Dates | `Jan 5, 1901`, `5/1/2020` |
| P | Telephone | digit-by-digit |
| Q | Scientific notation | `3.2e10`, `10^6` |
| R | URLs / emails | `@` → "at", `.` → "dot" |
| S | Acronyms | `FBI` → "F B I", with pronounceable whitelist (`NASA`, `NATO`) |

Phase 2 passes are independent and shippable individually.

## 8. Test strategy

**Layer 1 — per-pass unit tests** (`tests/test_tts_normalizer_en.py`): one `TestPassX` class per pass, ≥10 cases each, including idempotence (`f(f(x)) == f(x)`), empty string, and one adversarial case. Target: ~250 tests across 11 phase-1 passes, matching FI's density (~26 tests/pass).

**Layer 2 — cross-contamination tests** (`tests/test_tts_normalizer_dispatcher.py`):
- `normalize_text("IV", "en")` must NOT contain `neljäs`, `viides`, or any `-ssa`/`-sta`/`-lla`-suffix.
- `normalize_text("1500-luvulla", "fi")` still produces correct Finnish.
- `normalize_finnish_text("Chapter IV", _lang="en")` raises `LanguageMismatchError`.
- `normalize_text("x", "de")` raises `ValueError`.
- Snapshot test: 20-sentence English Rubicon excerpt through both normalizers; assert FI output contains Finnish-only sentinels absent from EN output.

**Layer 3 — AST enforcement** (`tests/test_dispatcher_enforced.py`): grep+AST check that no production file under `src/` or `scripts/` calls `normalize_finnish_text` or `normalize_english_text` directly. All routes through `normalize_text`. Tests and `dev_*.py` exempt.

## 9. Migration sequence — three PRs

**PR 1 — Dispatcher + language guards (no behavior change for FI).**
- Add `src/tts_normalizer.py` routing only `fi` (raises `NotImplementedError` for `en` initially).
- Add `_lang` kwarg + guard to `normalize_finnish_text`.
- Add dispatcher tests with FI-side cross-contamination.
- Switch `scripts/generate_chatterbox_audiobook.py` and `src/tts_engine.text_to_speech` to call `normalize_text(text, language)`. For `en`, dispatcher raises `NotImplementedError` — bypassed at call site with a temporary `if language=="fi"` shim until PR 2 lands.
- **This alone fixes the reported bug**: English runs no longer get Finnish normalization. They get nothing for now — same outcome as a clean unnormalized read, vastly better than mis-normalized.

**PR 2 — English normalizer phase 1 (atomic).**
- Add `src/tts_normalizer_en.py` (passes A–K), YAML data, tests.
- Wire into dispatcher (replace `NotImplementedError`).
- Remove the temporary shim from PR 1.
- Update `requirements.txt` and `.spec` files.
- Add a 10-minute Rubicon English regression fixture.

**PR 3 — English phase 2 (incremental).**
- Each pass L–S is its own commit with ≥15 tests.

## 10. Risks and tradeoffs

- **Roman-numeral false positives.** `MIX`, `DID`, `CIVIL` are valid Roman strings. Mitigation: require context whitelist + min length 2 + adjacent capitalized proper-noun. Residual risk accepted; spot-listening + blacklist.
- **Year vs. cardinal disambiguation.** `In 1917` vs. `1917 pages`. Heuristic: `1000 ≤ n ≤ 2099` AND preposition/sentence-start AND no following unit. Residual: 4-digit page numbers may misfire — pass A strips citation parens first.
- **`num2words` year mode.** Default is "one thousand nine hundred seventeen"; we want pair-reading. Use `to='year'` or hand pair logic.
- **Abbreviation ambiguity.** `St.` saint vs. street — context rule covers ~90%, residual is acceptable TTS.
- **PyInstaller weight.** `inflect` + `num2words` + `roman` are ~2 MB total, pure Python. No surprises.
- **Coverage gap after phase 1.** No currency, units, dates, URLs. Acceptable for novels; technical books need phase 2. Documented in `NORMALIZER_EN.md`.
- **Language-guard loophole.** `normalize_finnish_text(text)` without `_lang` still runs — by design for back-compat. CI AST check (Layer 3) closes the production-code loophole.
- **Why not NeMo directly?** Apache 2.0 license is fine, but `pynini` (the FST runtime NeMo depends on) has no PyPI wheel for Windows. Would break our PyInstaller build. Plus NeMo doesn't support Finnish anyway. NeMo-as-future-upgrade is in TODO.md.

---

## 11. Critical file references

- [src/tts_normalizer.py](src/tts_normalizer.py) — new dispatcher
- [src/tts_normalizer_en.py](src/tts_normalizer_en.py) — new EN normalizer
- [src/tts_normalizer_fi.py](src/tts_normalizer_fi.py) — add language guard
- [scripts/generate_chatterbox_audiobook.py:331](scripts/generate_chatterbox_audiobook.py#L331) — route via dispatcher
- [src/tts_engine.py:275](src/tts_engine.py#L275) — route via dispatcher

---

**Awaiting permission to start with PR 1.**
