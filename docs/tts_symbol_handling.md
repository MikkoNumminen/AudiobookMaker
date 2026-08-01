# Symbols in text-to-speech — why a minus sign can eat a sentence

This explains one failure mode and the two-layer defence against it.
If you are about to add a character to a normalizer table, read this
first.

## The short version

A speech model does not read characters. It reads *phonemes* — sounds
it was trained to produce. Somewhere between your text and the audio,
every character has to be turned into a sound.

Letters and digits have obvious sounds. `−` does not.

When the model meets a character it has no sound for, it does not stop
and complain. It does something worse: it improvises. And the two
languages this project ships improvise differently.

## What actually happened

A blog post contained this sentence:

```
0 to −90px at 884×900
```

Two of those characters are not what they look like. The `−` is
U+2212 MINUS SIGN, not the hyphen on your keyboard. The `×` is
U+00D7 MULTIPLICATION SIGN, not the letter x.

The Finnish normalizer expanded the numbers around them and handed the
synth this:

```
nolla:sta −yhdeksänkymmentä pikseliin koossa ...neljä×yhdeksänsataa
```

The glyphs are still there, now glued to the middle of words. Then:

**Finnish stopped early.** The model hit the unpronounceable token and
emitted an end-of-speech marker. The rest of the chunk — a whole
clause — was never spoken. The generated audio simply ended mid-thought,
and the MP3 was assembled from it without complaint.

**English silently swallowed them.** No truncation. The sentence was
narrated as *"0 to 90 pecs at 884 900"*. Read that again: the minus
sign is gone, so a negative offset is read aloud as a positive one. The
audio is fluent, confident, and says the opposite of the text.

The English failure is the more dangerous of the two, because nothing
looks wrong. Finnish at least produces a short chunk that a duration
check can catch.

## Why the retry guard did not save us

`scripts/generate_chatterbox_audiobook.py` has a band guard: if a
chunk's audio is too short for its character count, re-roll it. It
fired, retried five times, and shipped the best attempt anyway.

All five retries produced *identical* truncation. That is the tell.
Random sampling noise varies between rolls; a bad input does not. When
every retry agrees, the problem is the text, not the dice.

So the guard is a smoke alarm, not a fix. It tells you something burned.

### And it was deaf to most of the fire

Worse than not fixing it, the guard did not even *notice* most of it.
Its floor, `MIN_AUDIO_S_PER_CHAR`, is an absolute constant that has to
stay low enough to be safe for the fastest text the project narrates.
On a chapter whose healthy rate was 0.070 s/char, nine chunks lost their
closing clause — and six of the nine measured between 0.058 and 0.062,
clearing the floor untouched while being obviously short next to their
neighbours.

A constant cannot know what a given chapter sounds like. **Pass R** now
compares each chunk against the MEDIAN rate of its own chapter, which
costs nothing to compute and is a far better expectation than any
constant can be. Replayed against the real measurements from that
build, it flags all nine.

Median rather than mean, because the outliers being hunted would drag a
mean down toward themselves and hide exactly what needs to stand out.

Both guards are kept, because each is blind where the other sees:

| | Absolute band guard | Relative sweep (Pass R) |
|---|---|---|
| Runs | per chunk, at generation | once per chapter, before assembly |
| Compares against | a fixed floor | the chapter's own median |
| Catches | gross truncation and rambling | chunks short *for this text* |
| Blind to | anything above the floor | a chapter that is >50% broken — the median moves in among the bad chunks |

That last row is why the absolute guard was not simply replaced. If most
of a chapter collapses, the median follows the wreckage and Pass R goes
quiet; but a collapse that severe puts the chunks under the absolute
floor, where the per-chunk guard is still watching.

Pass R also refuses to guess. Under `MEDIAN_SWEEP_MIN_CHUNKS` there is
no meaningful median, so it does nothing rather than re-roll good audio.
And if more than `MEDIAN_SWEEP_MAX_FRACTION` of a chapter is below the
line, the cause is the text or the voice rather than bad luck — it fixes
the worst offenders, ships the rest, and **says so in the log**, because
a silently truncated work list reads exactly like "all clear".

## The two-layer defence

Layer 1 alone would be wishful thinking — it assumes somebody
enumerated every glyph a document might contain. Nobody can finish that
list. So there are two.

### Layer 1 — say the ones that mean something

`src/tts_symbols.py` holds a per-language table: `−` becomes "minus" /
"miinus", `×` becomes "by" / "kertaa", and so on. Each language
normalizer calls `expand_symbols()` as an ordinary pass.

Substitutions are padded with spaces, then the padding is collapsed.
That padding is the entire point. Without it `884×900` becomes
`884by900`, one unpronounceable token instead of three real ones.

### Layer 2 — replace everything else with a space

`strip_unspeakable()` runs in the **dispatcher**, `normalize_text()`,
not inside the language backends. That placement is deliberate: a
future language module cannot forget a step it does not have to
remember.

It walks the finished text and replaces every character in an
unspeakable Unicode category with a space:

| Category | What it holds |
|----------|---------------|
| `Sm` | maths symbols — `−` `±` `≤` `→` |
| `Sc` | currency — `€` `$` `¥` |
| `Sk` | modifier symbols |
| `So` | everything else symbolic — `°` `§` `☃` |
| `No` | vulgar fractions `½ ⅓ ⅞` and superscripts `² ³` |

**`No` is the one that catches people out.** Unicode classifies
fractions and superscripts as *numbers*, not symbols. A gate built on
the `S*` categories alone waves every one of them straight through —
the same bug, one category over. A test in
`tests/test_tts_symbols.py` asserts that every glyph in the layer-1
table is also caught by layer 2, and that test is what found this.

Ordinary digits are `Nd` and are never touched.

### Why a space and not deletion

Deleting `a×b` gives you `ab` — one word that does not exist. Replacing
gives you `a b` — two words that do. Whenever the pipeline has to guess,
it should guess toward "two separate words", because a wrong pause is
survivable and an invented word is not.

## When a glyph goes missing

Layer 2 logs a warning naming every codepoint it dropped:

```
[normalizer fi] dropped 1 unpronounceable symbol(s):
U+2192 RIGHTWARDS ARROW x1. Add a spoken form to src/tts_symbols.py
if any of these should be read aloud.
```

That line is the whole feedback loop. A glyph nobody anticipated shows
up in the run log instead of in a listener's ears. If it should be
spoken, add it to the table in `src/tts_symbols.py`. If it is
decoration, the space was already the right answer and you can ignore
it.

It earned its keep immediately. Running the finished gate back over the
same batch surfaced a second offender in the very file being repaired:

```
[normalizer fi] dropped 15 unpronounceable symbol(s): U+0024 DOLLAR SIGN x15.
```

Finnish writes currency *after* the number — `2,08 $` — but
`_FI_DOLLAR_RE` in `src/tts_normalizer_fi.py` only ever matched the
prefix form `$5`. Fifteen bare dollar signs had been reaching the synth,
and one of them had truncated a chunk in exactly the same way the minus
sign did, swallowing an amount out of the report's headline figures.

Note what the right fix was. The gate would have replaced each `$` with
a space — safe, no truncation, but the *amount is silently no longer a
price*. Layer 2 stops the bleeding; it is not a substitute for saying
the word. `$` now sits in the currency block of `data/fi_units.yaml`
alongside `€` and `£`, where it should have been from the start.

**Treat every line that gate logs as a bug report against layer 1.** A
symbol appearing there means some pass should have claimed it and did
not.

## The related Finnish bug found alongside it

Finnish writes a numeral's case ending after a colon: `20:een`,
`1990:n`, `5:llä`. None of it was handled. Pass G expanded the digits
in nominative and left the colon glued on, so `0:sta` was narrated as
the non-word `nolla:sta`.

Pass V (`_expand_colon_suffixed_numerals`) now reads the intended case
off the suffix and lets num2words spell the whole thing — `0:sta` →
`nollasta`, `20:een` → `kahteenkymmeneen`. num2words already knew every
Finnish case form; nothing had ever asked it for one.

Clock times (`20:30`) and ratios (`1:5`) are deliberately out of scope:
the pass only matches a colon followed by *letters*.

## Rules to work by

1. **Never assume a character is speakable because it renders.** If it
   is not a letter, a digit, or ordinary punctuation, it needs a spoken
   form or it needs to be gone.
2. **Pad substitutions with spaces.** Gluing is the actual failure.
3. **Identical retries mean a bad input.** If a re-roll reproduces the
   same defect, stop tuning sampling and go read the text.
4. **A duration check is a smoke alarm.** It tells you something is
   wrong. It cannot tell you what, and it cannot fix it.
5. **Compare against the neighbours, not against a constant.** A
   threshold that has to be safe for every possible text is too loose
   for any particular one. The same reasoning applies when you are
   checking a finished conversion by hand: use the file's own median.
6. **Verify by transcribing, not by measuring.** Every claim in this
   document about what was spoken came from running Whisper over the
   audio and reading it. Speech rate said "probably fine"; the
   transcript said a clause was missing.

## See also

- `src/tts_symbols.py` — both layers
- `tests/test_tts_symbols.py` — the regression tests, including the
  property test that no symbol survives the dispatcher
- `tests/test_tts_normalizer_fi_colon_suffix.py` — Pass V
- `docs/finnish_tts_failure_inventory.md` — the wider Finnish failure survey
