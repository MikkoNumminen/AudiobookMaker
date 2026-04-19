---
name: pronunciation-corpus-add
description: Append a Finnish pronunciation failure (a word the Chatterbox Grandmom voice mispronounces) to the project's pronunciation corpus at docs/pronunciation_corpus_fi.md. Use this whenever the user or a tester (Turo is the canonical one) reports that Grandmom said a Finnish word wrong — e.g. "Grandmom pronounces X as Y", "add this to the corpus", "log this pronunciation bug", "the TTS messed up <word>". Also use when the user pastes a transcript of what was heard vs. what was written. This corpus is the evidence base for targeted Pass I / lexicon fixes; entries must be structured so patterns across reports are visible at a glance.
---

# pronunciation-corpus-add

Append one or more Finnish pronunciation-failure entries to the
project's corpus at
[`docs/pronunciation_corpus_fi.md`](../../../docs/pronunciation_corpus_fi.md).

## Why this skill exists

Finnish TTS quality improves in two ways:

1. **Structural fixes** — extend the normalizer (see the
   `fi-normalizer-pass` skill). These pay off across every book.
2. **Lexicon fixes** — patch one word's respelling in Pass I or a
   similar lookup table. These pay off when the same word keeps
   appearing in reports.

Deciding between (1) and (2) requires data. The corpus is that data:
every time a tester reports a mispronunciation, we add the word with
its failure category so that when the file has 20+ entries we can see
which categories are dominating and aim a fix there.

Turo is the canonical external tester; his reports are ground-truth
quality data. Other testers may chime in; treat any report the same way.

## The corpus file shape

The corpus file lives at
`docs/pronunciation_corpus_fi.md`. If it does not yet exist, create it
with this scaffold:

```markdown
# Finnish pronunciation failure corpus

A running log of Finnish words the Chatterbox Grandmom voice has
mispronounced in practice. Entries drive targeted fixes: when a
failure category accumulates enough entries, that category becomes a
candidate for a Pass I lexicon extension or a new normalizer pass.

**Target:** ≥20 concrete entries across ≥3 failure categories before
attempting a targeted fix.

## Categories

1. **Loanword suffix** — `-ismi`, `-tio`, `-aalinen`, etc. produce
   English-flavoured stress or vowel quality.
2. **Compound seam** — 20+ character compounds read as one blob
   instead of two morphemes.
3. **Consonant cluster** — `st`, `sk`, `sp`, or word-final `s` sound
   Germanic (`sch`) instead of Finnish.
4. **Vowel length / quality** — short/long vowel confused; front/back
   vowel swapped.
5. **Accent placement** — stress falls on a non-initial syllable.

## Entries

<!-- New entries appended below. Keep chronological order. -->
```

Each entry is a fixed-shape block. The structure matters — downstream
analysis scripts grep for the `**Heard as:**` line and the
`**Category:**` tag.

```markdown
### <word in context>

- **Date:** YYYY-MM-DD
- **Reporter:** <Turo | Mikko | user handle>
- **Source:** <book / chapter / file name>
- **Written:** `<the exact written form, inside backticks>`
- **Heard as:** `<phonetic transcription of what the TTS produced>`
- **Category:** <one of the 5 categories above>
- **Notes:** <optional: surrounding context, likely cause, related
  entries>
```

## Adding an entry

### 1. Parse the report

The user's report usually looks like one of:

- "Grandmom said `instituutio` like `instituushio`." — direct pair.
- "Turo says the word `kommunikaatio` came out wrong in chapter 3 of
  Rubicon — it sounded like `kommunikaasjo`." — attribution + source.
- A pasted audio transcript with errors circled.

Extract:

- The written form (exactly as it appears in the source text).
- What was heard (phonetic, as faithful as you can).
- Date (today unless the user gives a different one — convert
  relative dates like "yesterday" to absolute `YYYY-MM-DD`).
- Reporter (Turo if unspecified and the user is relaying a Turo
  report; otherwise ask).
- Source (book, chapter, file name). Ask if the user didn't supply it
  and it isn't obvious from context.

### 2. Pick a category

Use the 5 categories listed in the scaffold. If a report truly
doesn't fit, don't invent a new category silently — ask the user:
"this doesn't match any of the five existing categories. Would you
like to add a new one (`<proposed name>`), or does it actually fit
`<closest match>`?"

Common mis-categorisations:

- `instituutio` → `instituushio` is **loanword suffix** (the `-tio`
  ending), not "consonant cluster" despite the `sh` sound.
- A long compound split wrong mid-word (`kansanedustajakandidaatti`)
  is **compound seam**, not "vowel length".

### 3. Read, append, commit

```bash
git pull --ff-only origin master
```

Open `docs/pronunciation_corpus_fi.md`, append the new block at the
bottom of the "Entries" section (chronological order — newest last),
save.

Pre-commit hook treats docs-only commits as test-skip, so the
commit is fast:

```bash
git add docs/pronunciation_corpus_fi.md
git commit -m "docs(corpus): log <word> mispronunciation (<category>)"
git push origin master
```

Conventional commit scope is `corpus` for these entries. Keep the
subject short — the block itself carries the detail.

### 4. Batch multiple reports

If the user pastes 5 reports at once, append all 5 entries in one
commit with a single subject:

```
docs(corpus): log 5 new mispronunciations from Rubicon chapter 3
```

One commit per reporting session is fine; splitting per-word inflates
history unnecessarily.

## When the corpus hits a milestone

When the entry count crosses a threshold, surface it to the user:

- **At 10 entries**: note which category is most populous so far.
- **At 20 entries across ≥3 categories**: remind the user this was
  the trigger for attempting a targeted fix. Typical follow-up is
  either (a) a Pass I lexicon extension with the top-category words,
  or (b) a new normalizer pass if a structural pattern is visible.

Don't volunteer a fix until the threshold is met — the TODO.md claim
for "targeted Pass I fix" is gated on exactly this evidence.

## Things NOT to do

- **Do not silently re-synthesise** when a user flags a typo in an
  already-generated audiobook. Ask first: "do you want me to log this
  as a pronunciation entry, or re-synthesise from a corrected
  source?" The two actions feed different workflows.
- **Do not merge entries** — even if two testers report the same
  word. Each entry stands alone with its own reporter and date. The
  signal we want is "how often does this recur across independent
  reports," and merged entries hide that.
- **Do not transliterate the "Heard as" line to IPA** unless the user
  explicitly asks. Faithful phonetic Finnish ASCII (e.g.
  `instituushio`) is more legible to the user at skim speed.
- **Do not add a corpus entry for an English word Grandmom mangled.**
  The Finnish corpus is for Finnish-on-Finnish failures. English
  misreadings under the Finnish engine are a different workflow.
