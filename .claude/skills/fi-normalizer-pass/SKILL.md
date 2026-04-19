---
name: fi-normalizer-pass
description: Add, modify, or debug a pass in the Finnish TTS text normalizer (src/tts_normalizer_fi.py). Use this whenever the user says "add a normalizer pass", "new Pass <letter>", "Pass P compound splitter", "fix the Finnish normalizer", "the TTS is mispronouncing <word>", "normalizer pass ordering", or mentions extending the Finnish pipeline for numbers / abbreviations / acronyms / compounds / loanwords / roman numerals / units / currency / percentages. The Finnish pipeline has ~20 passes with load-bearing ordering constraints — shuffling them silently produces wrong output. This skill encodes the constraints so you add the new pass in the right slot the first time.
---

# fi-normalizer-pass

Author, extend, or debug a Finnish text-normalizer pass in
[`src/tts_normalizer_fi.py`](../../../src/tts_normalizer_fi.py).

## Why this skill exists

The Finnish normalizer is a sequence of ~20 passes (A, B, C, D, E, F,
G, H, I, K, L, M, N, plus a few extensions). Each pass rewrites a
specific pattern (centuries, ranges, page abbreviations, loanword
respellings, roman numerals, unit/currency symbols, acronyms, etc.) so
downstream passes see a cleaner input. The ordering is **load-bearing**
— several passes depend on a governor word emitted by an earlier pass
to inflect a number in the correct case, or on an abbreviation being
expanded before a sentence-boundary pass treats its trailing period as
a full stop.

Without knowing the constraints, the natural instinct is "slot the new
pass wherever the diff is smallest" — and the result passes unit tests
but silently breaks sentences that exercise the combination. This skill
gives you the constraint map so the first draft lands in the right
slot.

## The pass inventory (as of 2026-04)

Passes are labelled by letter. Current active letters in the normalizer:

| Letter | What it rewrites | Notes |
|---|---|---|
| A | Bibliographic citations `(Pihlajamäki 2005)`, metadata parens (ISBN, DOI, CC license) | Runs first; output is plain text the rest can operate on. |
| B | Elided-hyphen Finnish compounds (`keski-ja` → `keski- ja`) | Fixes a common typographic error. |
| C | Century / era expressions `1500-luvulla` | Eats digit + suffix together. |
| D | Numeric ranges `42-45` → `42 45` | Normalizes the dash separator. |
| E | Page abbreviations `s. 42` / `ss. 42-45` → `sivu 42` | Emits governor word for G. |
| F | Decimals (comma or dot) `3,14` → `kolme pilkku yksi neljä` | Runs before G so digits are consumed. |
| G | Governor-aware integer expansion via `num2words` | Heart of the pipeline. Scans ±3 words for a governor. |
| H | Compound-number morpheme split (`kolmesataa` → `kolme sataa`) | Operates on G's output. |
| I | Loanword respelling (`humanismi` → `humanis-mi`, `instituutio` → `instituu-tio`) | Operates on G's output. |
| K | Abbreviation expansion (`esim.` → `esimerkiksi`, `ks.` → `katso`) | Must run first, before periods reach sentence logic. |
| L | Roman numeral expansion (`Kustaa II` → `Kustaa toinen`) | Context-aware ordinal vs. cardinal. |
| M | Unit / currency symbols (`5 %` → `5 prosenttia`, `10 €` → `10 euroa`) | Emits governors for G. |
| N | Acronym whitelist + letter-by-letter fallback (`EU`, `YK`, `NATO`) | Runs between K and M. |

When you add a new pass, pick an unused letter (`J`, `O`, `P`, …) and
document it here in the same commit.

## The load-bearing ordering constraints

Read these before picking a slot. Copied from the docstring of
`normalize_finnish_text`:

- **K before C, D, F, G**: K expands abbreviations like `esim.` whose
  trailing periods would otherwise look like sentence-terminal dots.
- **K before L**: L's Roman-numeral detector uses surrounding word
  context to pick ordinal vs. cardinal; leftover abbreviation periods
  shift the tokenizer's view.
- **L before M**: M rewrites `5 %` as `5 prosenttia` and injects
  partitive head nouns. If Roman numerals were still around, `XIV %`
  would leave the Roman pass with an unexpected head noun.
- **M before D, F, G**: M writes governor words (`prosenttia`,
  `kertaa`, `euroa`) next to digits. G's ±3-word scan then picks the
  correct case. Flip the order → G sees a naked digit → wrong case.
- **C before D, G**: C owns century expressions like `1500-luvulla`.
  D or G first would split or cardinalize them wrong.
- **E before G**: E rewrites `s. 42` as `sivu 42` so G's governor
  table matches `sivu` and inflects.
- **D before F**: D normalizes ranges; F handles decimals. Opposite
  order tangles the regexes on `42,5-45,0`.
- **F before G**: F consumes digit-dot-digit; otherwise G eats the
  whole number and the fractional digits vanish.
- **I and H after G**: both operate on num2words output, not raw
  digits.
- **I before H**: I may insert its own hyphens; running H first on a
  compound that later gets respelled double-splits the token.

## Adding a new pass — the recipe

### 1. Design the pass on paper first

Answer before writing any code:

- What input pattern does it match? Write 3–5 concrete examples.
- What does it output? Write the expected rewrite for each example.
- What passes must run **before** it? (look for governors, expanded
  abbreviations, num2words output).
- What passes must run **after** it? (look for passes that consume the
  output pattern it produces).
- Does it need a lexicon? (Pass I, Pass N step 1, Pass K are lexicon-
  driven; ordering inside the lexicon matters if entries overlap.)

### 2. Slot it into the ordering

Pick a letter, then figure out where in the dispatch sequence it goes.
If the new pass needs Pass G's word-form output, slot it at or after
position I/H. If it emits a governor word G needs, slot it before G and
after K. Update the invariants comment in the `normalize_finnish_text`
docstring in the **same** commit — drift between code and comment is
how future authors re-break the ordering.

### 3. Implement

- Add a module-level `_RE_PASS_<letter>` regex if the pattern is
  regex-friendly.
- Add a `_pass_<letter>(text: str) -> str` function (private, no
  language suffix — this module already lives in the `fi` normalizer).
- Wire it into `normalize_finnish_text` at the correct position.
- Keep heavy imports (`num2words`, `libvoikko`, etc.) lazy inside the
  pass function so startup stays fast and missing deps degrade
  gracefully.
- For lexicon-driven passes, keep the lexicon as a module-level
  `dict` / `frozenset` near the top of the file; list entries in a
  deterministic order (alphabetical) so diffs stay readable.

### 4. Tests

Add tests in `tests/test_tts_normalizer_fi.py` (or the dispatcher test
file if the pass is dispatcher-wiring). Minimum set:

- One test per example from step 1.
- One negative test: an input that looks similar but should **not**
  match (so the regex doesn't over-fire).
- One ordering test: an input that exercises the constraint you
  declared (e.g. a sentence where the new pass and Pass G both apply —
  confirms the governor is visible to G).

Run the suite before committing:

```bash
python -m pytest tests/test_tts_normalizer_fi.py tests/test_tts_normalizer_dispatcher.py -x -q --tb=short
```

### 5. Document in the inventory

Update
[`docs/tts_text_normalization_cases.md`](../../../docs/tts_text_normalization_cases.md)
with a new subsection: pattern, examples, ordering constraints. This
doc is the reference readers skim when they hit a pronunciation bug and
want to know which pass owns the phenomenon.

### 6. Commit

Conventional commit, scope `normalizer` or `fi-normalizer`:

```
feat(normalizer): add Pass <letter> for <short description>
```

Body (optional) calls out any new ordering invariant introduced.

## Debugging a mispronunciation

When the user reports "Grandmom says X wrong", don't jump to writing a
new pass. First locate the phenomenon in the existing pipeline:

1. **Reproduce**: call `normalize_finnish_text(input)` in a REPL and
   see the intermediate output. What does the pipeline currently do?
2. **Bisect the pipeline**: if the final output is wrong, run each
   pass in isolation on the input. Find the pass that produces the
   wrong rewrite OR the pass that fails to rewrite when it should.
3. **Classify**:
   - Existing pass under-matches → extend its regex/lexicon.
   - Existing pass over-matches → tighten its regex/lexicon.
   - Phenomenon isn't covered → new pass (go to "Adding a new pass").
   - Pass produces the right rewrite but the TTS engine still reads
     it wrong → problem is in the engine or the prompt, not the
     normalizer. Consider a pronunciation-corpus entry instead (see
     the `pronunciation-corpus-add` skill).

## Things NOT to do

- **Do not reorder existing passes** without explicitly updating the
  invariants docstring in the same commit. Silent reordering is how
  this pipeline regresses.
- **Do not skip the lazy-import pattern**. `num2words` /
  `libvoikko` must stay inside the pass function.
- **Do not hard-code a single book's vocabulary**. The normalizer
  ships to every user — if the lexicon is one failing word per book,
  it belongs in the pronunciation corpus, not in the normalizer.
- **Do not touch Finnish UI strings** in this skill's scope. The
  normalizer is text-in / text-out; display strings live in
  `gui_unified.py`.
