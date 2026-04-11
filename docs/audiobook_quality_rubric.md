# Audiobook Quality Rubric

Practical rubric for A/B comparing TTS samples produced by AudiobookMaker. Score each axis 1–5. Use on a representative 60–120s passage containing at least one paragraph break, one question, one comma-heavy clause, and one foreign loanword.

## Scoring axes

### 1. Pronunciation accuracy
Finnish long vowels (`aa`, `ii`, `uu`), gemination (`kk`, `tt`, `ss`), vowel harmony, and foreign loanwords (names, English terms).
- **1** — Mangled: long vowels shortened, geminates flattened, loanwords unrecognizable.
- **3** — Native words mostly correct; loanwords awkward; occasional `tulee` vs `tule` slips.
- **5** — All words recognizable on first listen, including names and English loans.
- **Failure modes:** `koodi` → `kodi`, `Helsingissä` stress wrong, English names read letter-by-letter.

### 2. Word boundary integrity
Are words kept intact? Syllables dropped or duplicated?
- **1** — Words split across pauses, syllables swallowed, phantom syllables inserted.
- **3** — Rare mid-word hitches, maybe one dropped syllable per minute.
- **5** — Every word is one acoustic unit, no syllable loss.
- **Failure modes:** mid-word silence, truncation at chunk edges, repeated first syllable.

### 3. Sentence-level prosody
Natural rise/fall, question vs statement distinction, stress on content words.
- **1** — Flat monotone or random pitch; questions indistinguishable from statements.
- **3** — Statements sound okay; questions ambiguous; stress sometimes lands on function words.
- **5** — Clear declarative fall, audible question rise, content words foregrounded.
- **Failure modes:** every sentence ends with the same cadence; stress on `on`/`ja`/`se`.

### 4. Pacing within a sentence
No unnatural pauses, no rushing, commas honored.
- **1** — Machine-gun delivery or random long pauses mid-clause.
- **3** — Generally steady; commas sometimes ignored or over-pausing before subordinate clauses.
- **5** — Human-like rhythm, commas create brief breath, no rushed tail-offs.
- **Failure modes:** pause before the last word, comma ignored, sprint through final clause.

### 5. Inter-sentence fluency
Do sentences flow as a paragraph or does each one "reset"?
- **1** — Every sentence starts from pitch zero; sounds like a list of unrelated lines.
- **3** — Some paragraph feel, but obvious reset on ~half the transitions.
- **5** — Paragraph arc is audible; pitch and energy carry across sentence breaks.
- **Failure modes:** identical sentence-initial pitch, uniform inter-sentence gap, no discourse-level intonation.

### 6. Inter-chunk fluency
Are chunk boundaries (the splits AudiobookMaker feeds into TTS) audible?
- **1** — Every chunk seam is a jarring cut, pitch jump, or silence gap.
- **3** — Most seams hidden, 1–2 per minute still audible.
- **5** — You cannot tell where the chunker split the text.
- **Failure modes:** 300ms dead air at seam, voice timbre shifts at seam, breath artifact at seam.

### 7. Voice character stability
Consistent pitch, timbre, speaker identity over the sample.
- **1** — Sounds like multiple speakers, pitch drifts, gender ambiguous.
- **3** — Same speaker throughout but audible fatigue/drift every minute.
- **5** — Rock-solid identity; could be one continuous studio take.
- **Failure modes:** pitch creep upward, timbre thinning mid-paragraph, occasional "other voice" chunk.

### 8. Artifacts
Breaths, hums, clicks, glitches, loops, truncation.
- **1** — Frequent clicks, hums, repetition loops, or cut-off endings.
- **3** — Occasional breath or soft click; nothing that breaks immersion.
- **5** — Clean. No artifacts noticeable on headphones at normal volume.
- **Failure modes:** token repetition loop ("ja ja ja ja"), EOS truncation, DC hum, lip-smack between chunks.

### 9. Listenability for a full book
Could you sit through 6 hours of this without fatigue?
- **1** — Tiring within 2 minutes; would not finish a chapter.
- **3** — Tolerable for a chapter; you would reach for a different narrator for a whole book.
- **5** — You forget you are listening to TTS; would happily do a full novel.
- **Failure modes:** harsh sibilance, boxy midrange, flat affect that becomes hypnotic-not-in-a-good-way.

## A/B test procedure

1. Pick a fixed passage (same source text for both samples, 60–120s).
2. Listen to **Sample A** in full, once, uninterrupted. Score all 9 axes immediately.
3. Listen to **Sample B** in full, once. Score all 9 axes.
4. (Optional) Re-listen to contested axes on both samples for tie-breaking.
5. Tally totals out of 45.
6. Flag any axis where one sample beats the other by **2+ points** — those are the real differentiators; the rest is noise.
7. Write a one-line verdict: which sample wins, and which 2–3 axes drove the decision.

Score sheet template:

| Axis | A | B | Δ |
|---|---|---|---|
| 1 Pronunciation | | | |
| 2 Word boundaries | | | |
| 3 Sentence prosody | | | |
| 4 Pacing | | | |
| 5 Inter-sentence | | | |
| 6 Inter-chunk | | | |
| 7 Voice stability | | | |
| 8 Artifacts | | | |
| 9 Listenability | | | |
| **Total / 45** | | | |

## Reference: samples measured so far

- **Edge-TTS Noora** (`turodokumentti_audiobook_v3.mp3`) — current baseline. The target to beat.
- **Chatterbox multilingual stock** — failed: token-repetition loop forced EOS truncation.
- **Chatterbox Finnish-NLP finetune, default ref, 500ch chunks** — ~49s sample; audible chunk seams (axis 6 failure).
- **Chatterbox Finnish-NLP finetune, Mandela ref, 500ch chunks, cfg=0.9** — ~79s sample; voice character drifted (axis 7), fluency issues (axes 3, 5).
- **Chatterbox Finnish-NLP finetune, default ref, 300ch chunks, -30dB trim, 100ms gap, sentence-start trim** — in progress.
