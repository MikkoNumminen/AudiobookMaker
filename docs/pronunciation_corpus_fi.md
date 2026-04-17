# Finnish Pronunciation Failure Corpus

This file is the single place to collect Finnish mispronunciations produced by the
Chatterbox-Finnish T3 model. Think of it as a bug tracker, but for sounds instead
of code. Every time the model says something wrong, add a row to the table below.

**Goal:** Reach 20 documented failures across at least 3 categories. Once there,
we have enough evidence to write targeted normalizer passes instead of guessing.

---

## How to add a new entry

1. Listen to the output audio and write down exactly what you heard (phonetically
   as a Finnish speaker would write it — no IPA required).
2. Find the matching category from the taxonomy below. If nothing fits, use `other`.
3. Append a row to the table. Increment `#` by one. Fill every column you can.
   Leave `Notes` blank rather than leaving the row out.

---

## Failure category taxonomy

Each category has a short **slug** (the `category` column value) and a plain
description of what the model is doing wrong.

| Slug | What goes wrong | Example |
|------|-----------------|---------|
| `vowel-length` | The model stretches a short vowel into a long one (or occasionally shortens a long one). Finnish vowel length is phonemic — it changes meaning. | `lopetti` → `loopetti` |
| `umlaut-drop` | Front rounded vowels (`ä`, `ö`, `y`) collapse into their back equivalents (`a`, `o`, `i`). The model has seen too much back-vowel data and defaults to it. Variants: `ää→aa`, `öö→oo`, `yy→ii`. | `äänikirja` → `aanikirja` |
| `mid-word-pause` | An audible silence appears inside a single word, splitting it in two. This is a T3 attention failure — the model loses track of where a word boundary is. | `löysimme` → `löys imme` |
| `word-boundary-collapse` | Two separate words fuse into one mangled token — phonemes from both words get dropped or merged. | `ennen vain` → `ennenvän` |
| `early-stop-truncation` | The model generates an early end-of-sequence token mid-sentence, cutting the audio short. Typically triggered by long compound words or complex legal/technical terms. | `asianosaisaloitteinen menettely` → `menet` |
| `sibilant-substitution` | The Finnish `s` is replaced by a German-style `sch` sound. This is bleed-through from the multilingual base model's Germanic training data. | `sen` → `schen` |
| `other` | Anything that does not fit the categories above. Describe the failure in the `Notes` column so future contributors can decide whether it deserves its own category. | — |

---

## Collected entries

| # | Word or phrase | Expected pronunciation | Observed output | Category | Source | Date | Notes |
|---|----------------|------------------------|-----------------|----------|--------|------|-------|
| 001 | `löysimme` | löysimme | löys imme | `mid-word-pause` | turo_stressitesti_tulokset_fi | 2026-04-13 | Silence inserted after the first syllable; likely T3 attention drift on the `ys` cluster |
| 002 | `lopetti` | lopetti | loopetti | `vowel-length` | turo_stressitesti_tulokset_fi | 2026-04-13 | Short `o` hallucinated as long `oo`; no obvious grapheme trigger |
| 003 | `ennen vain` | ennen vain | ennenvän | `word-boundary-collapse` | turo_stressitesti_tulokset_fi | 2026-04-13 | Word boundary lost; `ai` in `vain` reduced to `ä`; possibly a tokeniser boundary issue |
| 004 | `äänikirja` | äänikirja | aanikirja | `umlaut-drop` | turo_stressitesti_tulokset_fi | 2026-04-13 | Both `ä` in `ää` dropped to `a`; classic back-vowel collapse at word start |
| 005 | `asianosaisaloitteinen menettely` | asianosaisaloitteinen menettely | menet | `early-stop-truncation` | Turo audiobook ch 5-8 (tier 2 first pass) | 2026-04-13 | Long compound triggered premature EOS; only first two syllables of `menettely` produced |

---

## Next steps when we hit 20 entries

Once the table reaches 20 rows spanning at least 3 categories, we have enough
signal to write targeted fixes. The planned fix lane for each category is:

- **`vowel-length`** — Pass I lexicon respelling. Insert a hyphen to break the
  problematic vowel pair, e.g. `lopetti` → `lop-etti`. The hyphen nudges the
  phoneme boundary and prevents the model from stretching the vowel.

- **`umlaut-drop`** — Pass I lexicon respelling with digraph expansion.
  `ää` → `ä ä` (space-separated) so the model sees each vowel as a distinct
  token and does not silently fall back to the back-vowel default.

- **`mid-word-pause`** — Investigate T3 attention argmax patterns around
  consonant clusters. No fix candidate yet; need to profile attention weights
  on known failing words before writing a normalizer rule.

- **`word-boundary-collapse`** — `FI_TEMPERATURE` was already lowered to 0.5
  in the main branch and that helped. Remaining cases may need explicit
  whitespace tokens or a word-boundary marker injected in the normalizer.

- **`early-stop-truncation`** — Already patched upstream in
  `AlignmentStreamAnalyzer` plus an audio-ratio retry guard. If new cases appear
  after that patch, capture them here and re-open the upstream issue.

- **`sibilant-substitution`** — Still open. No fix candidate. Accumulate more
  examples first to understand which phonetic contexts trigger it.

- **`other`** — Review uncategorised rows at the 20-entry milestone. Decide
  whether each warrants a new slug or maps to an existing category.
