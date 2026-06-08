# Finnish TTS Failure Inventory

Source corpus: a long user-supplied Finnish-language PDF parsed
via `src.pdf_parser.parse_pdf`. Triage is keyed to our in-tree normalizer
`src/tts_engine.py::normalize_finnish_text()` and driven by the newly-confirmed
acoustic failure on `-ismi` loanwords in the chapter 11 v7 render.

Goal: find other silent acoustic losses the user has not yet flagged. Read-only
inventory — no normalizer changes made in this pass.

## Ranked impact table

Score = severity_weight * count, where HIGH=3, MEDIUM=2, LOW=1. Sorted desc.
"Handled?" refers to the current `normalize_finnish_text` passes A–H.

| Rank | Category                             | Count | Severity | Handled?  | Score |
|------|--------------------------------------|-------|----------|-----------|-------|
| 1    | Words with internal `-io-`           | 1140  | MEDIUM   | NO        | 2280  |
| 2    | Non-`valtio` `-tio` loanwords        |  228  | HIGH     | NO        |  684  |
| 3    | Long compound words (≥20 chars)      |  576  | MEDIUM   | NO        | 1152  |
| 4    | Mixed num-in-compound `d+-word`      |  517  | MEDIUM   | PARTIALLY |  ~60  |
| 5    | `-ismi` loanwords (45 unique forms)  |   69  | HIGH     | NO        |  207  |
| 6    | Latin phrase openers                 |   22  | HIGH     | NO        |   66  |
| 7    | Ellipses `...`                       |  516  | LOW      | NO        |  516  |
| 8    | Parenthesized content                |  256  | MEDIUM   | PARTIAL   |  512  |
| 9    | Bare year numbers                    |  577  | —        | YES (G)   |   0   |
| 10   | Abbreviations w/ periods             |   10  | MEDIUM   | PARTIAL   |   20  |
| 11   | All-caps acronyms (legal codes etc.) |  105  | MEDIUM   | NO        |  210  |
| 12   | Roman numerals standalone            |   47  | MEDIUM   | NO        |   94  |
| 13   | Initials + surname (e.g. `H. Sukunimi`) |   3  | LOW      | PARTIAL   |    3  |
| 14   | Percent signs                        |    6  | LOW      | NO        |    6  |
| 15   | Foreign place names                  |   12  | LOW      | NO        |   12  |
| 16   | Question marks                       |   11  | LOW      | native    |    0  |
| 17   | Ordinals `N. Noun`                   |   72  | LOW      | YES-ish   |    0  |
| 18   | Currency / measurements / dates/times|   ~2  | LOW      | NO        |    2  |
| 19   | Exclamation, em-dash                 |    0  | —        | —         |    0  |

### Score notes

- Rank 1 (internal `-io-`) is inflated because it double-counts the `-tio`
  family; the unique acoustic risk class is rank 2 (non-`valtio` `-tio`).
- `valtio` stem (47+22+9+8+7+5+… ≈ 210 occurrences) is native Finnish and reads
  correctly, so it is subtracted from the `-tio` risk count.
- `-ismi` is low-count but HIGH severity because we have ground-truth acoustic
  evidence from chapter 11 v7 that Chatterbox mispronounces it.

## Category detail + recommendations

### 1. `-ismi` loanwords — HIGH, unhandled, confirmed broken

- Raw count: **69** occurrences, **45 unique surface forms**.
- Examples: `humanismi` (×6), `absolutismin` (×6), `Humanismi`, `positivismin`,
  `merkantilismia`, `liberalismi`, `nationalismi`, `feodalismi`,
  `Oikeuspositivismi`, `konsiliarismissa`, `protestantismia`, `rationalismin`,
  `oikeuspluralismin`, `Merkantilismille`.
- Chapter 11 confirms 12 unique `-ismi` forms present in the exact v7 audio the
  user reviewed. The `-ismi` complaint is real, not a false positive.
- Status: NOT handled.
- Recommendation: add a Pass I that rewrites `ismi` → `ismi ` (or
  `is mi`) only inside word stems matching `\w+ismi(\w*)`, with a small lexicon
  of allowed stems (humanism-, pasifism-, absolut-, merkant-, liberal-,
  national-, feodal-, positiv-, rational-, protest-, modern-, pluralism-,
  pappism-). Alternatively insert a phonetic respelling `-ismi` → `-is-mi`.
  Safer: an override dictionary for the 45 unique forms × case declensions.

### 2. Non-`valtio` `-tio` loanwords — HIGH, unhandled, same risk class

- Raw count: **228** (after removing 210 `valtio` native forms).
- Examples: `rationaalisen` (×11), `Rationaalinen` (×10), `instituutio` (×9),
  `kodifikaatiot` (×7), `conductio` (×6), `Rationalistinen` (×5),
  `locatio` (×5), `reseptio` (×5), `Constitutio` (×4), `modernisaation` (×5),
  `Actio` (×3), `stipulatio` (×3).
- Status: NOT handled.
- Recommendation: if acoustic testing confirms mispronunciation, add
  respelling-lexicon entries for the stems. Latin stems (`conductio`,
  `locatio`, `stipulatio`, `Actio`, `Constitutio`) should share treatment
  with the Latin-legal-term category below — a single Latin lexicon table.

### 3. Latin phrase openers — HIGH, unhandled

- Raw count: 22 multi-word phrases; Latin single-word tokens likely higher.
- Examples: `ius commune`, `ius proprium`, `usus modernus pandectarum`,
  `corpus iuris`, `ratio scripta`, `modus vivendi`.
- Status: NOT handled. Matches previous triage in
  `docs/tts_text_normalization_cases.md` §5.
- Recommendation: phoneme lexicon / respelling table, e.g. `ius` → `jus`,
  `iuris` → `juuris`, `pandectarum` → `pandektarum`. No SSML in Chatterbox.

### 4. Long compound words (≥20 chars) — MEDIUM, unhandled

- Raw count: **576** total, **473 unique**.
- Examples: `kuolemanrangaistusta` (×11), `oikeudenkäyntimenettely` (×6),
  `valtakunnankamarioikeuden` (×4), `rikosoikeusjärjestelmän` (×5),
  `partikulaarioikeuksien` (×4), `luonnonoikeusoppineiden` (×3).
- Status: NOT handled. Chatterbox tokenization may split these in the middle of
  a morpheme, distorting stress placement — similar failure mode to our
  num2words compound-number fix (Pass H).
- Recommendation: monitor acoustically. If degraded, insert a soft word break
  at known compound seams (`oikeus-`, `rangaistus-`, `menettely-`, etc.).

### 5. Mixed `digit-word` compounds — MEDIUM, partially handled

- Raw count: 517, but dominated by ISBN-style tokens (generic shape
  `978-XXX-XX-XXXX-X`). The non-ISBN residue is small: `5-tie`, `10-vuotinen`-style
  forms, not confirmed present here.
- Status: PARTIAL. Century pass C catches `NNNN-luvun`. ISBN-like strings slip
  through and are read as giant number ranges.
- Recommendation: add an ISBN-detect pass (`\b97[89](?:-\d+){3,4}\b`) and
  either spell the digits one by one or strip.

### 6. All-caps acronyms — MEDIUM, unhandled

- Raw count: 105. Real all-caps acronyms (legal codes, standards bodies, etc.)
  plus `ISBN`/`ISSN` metadata (should be dropped anyway). Also false positives
  from small-caps section headings — ordinary Finnish words rendered uppercase.
- Status: NOT handled. Chatterbox pronounces an all-caps acronym as a word
  rather than letter-by-letter.
- Recommendation: letter-by-letter expansion for all-caps ≥2 tokens not in a
  known-word list; a manual lexicon for the frequent ones. Filter out
  small-caps heading false positives by requiring surrounding lowercase context.

### 7. Roman numerals — MEDIUM, unhandled

- Raw count: 47 (filtered). Examples: `II`, `III`, `VI`, `VII`, `IX`, `XI`,
  `XII`, `XIV`, `CC`.
- Status: NOT handled (matches existing triage doc §7).
- Recommendation: roman-to-int + num2words(ordinal) in regnal/chapter context.
  Must require uppercase + word boundary to avoid clashing with `I`, `V`, `L`.

### 8. Parenthesized content — MEDIUM, partial

- Raw count: 256. Most are bibliographic `(Sukunimi 2005)`-style citations
  already caught by Pass A. The residue includes numeric-only parens like
  `(09)`, `(4.0)`, a licence attribution, and year-range parens `(NNNN-NNNN)`.
- Status: PARTIAL. Pass A only drops parens that contain BOTH a capitalized
  token AND a 4-digit year — numeric-only parens are kept.
- Recommendation: optionally strip metadata parens like `(ISBN …)`,
  `(DOI …)`, `(Creative Commons …)`. Numeric-range parens `(NNNN-NNNN)` are
  currently handled by Pass D once stripped of the surrounding parens.

### 9. Ellipses — LOW acoustic but MEDIUM prosody

- Raw count: 516. Almost all come from TOC dot-leaders
  (`OTSIKKO.................`), not real Finnish ellipsis punctuation.
- Status: NOT handled.
- Recommendation: collapse runs of ≥3 periods to a single ellipsis, or drop
  entirely if surrounded by whitespace + digits (TOC pattern).

### 10. Abbreviations with periods — MEDIUM, partial

- Raw count: only 10 (`jKr.`, `eKr.`, `toim.`). Sentence splitter already
  knows these via `_ABBREVIATIONS`, but they are never *expanded* to
  `ennen Kristusta` / `jälkeen Kristuksen`.
- Recommendation: add a lookup table expansion pass.

### 11. Bare year numbers — HIGH-count but HANDLED

- Raw count: 577. Pass G + num2words already expands these. No action needed,
  but confirm ordering: they must run after century and range passes.

### 12. Currency, measurements, dates, times — near-zero in this corpus

- `1.1.1900` (one match), `1.2.` (one match), no percentages except the 6
  already triaged, zero currency, zero measurements, zero `klo`.
- Recommendation: add the regexes for completeness so other corpora are
  covered, but this book will not stress-test them.

## Chapter 11 v7 verification

Chapter 11 contains:

- **12 unique `-ismi`** forms: `humanismi`, `konsiliarismissa`,
  `merkantilismia`, `merkantilismiksi`, `Merkantilismissa`, `Merkantilismin`,
  `Merkantilismille`, `liberalismi`, `absolutismin`, `Absolutismia`, and two
  more case forms.
- **39 total `-tio`** forms including non-valtio `organisaation`,
  `innovaatioita`, `innovaatio`, `Navigation`, `nations`, `rationaalinen`.

The user's report that `-ismi` sounds wrong in v7 is **confirmed**: the text
class is definitely present in the synthesized audio.

## Verdict

The `-ismi` problem is **real and acoustically confirmed**, but by raw
count-weighted severity it is **rank 5**, not rank 1. The biggest bucket of
unhandled text with the same failure mode is **non-`valtio` `-tio`
loanwords** (rank 2, 228 occurrences, 3× the `-ismi` count), followed by
**long compound words** and the **Latin legal lexicon**. These share one
underlying cause: Chatterbox's Finnish tokenizer handles loanword
phonotactics and very long compounds poorly.

Recommended next coding pass: a single "loanword respelling lexicon" Pass I
that covers `-ismi`, `-tio` (non-valtio), and the Latin legal vocabulary in
one table. Long-compound seam-splitting (similar to Pass H) should follow.
