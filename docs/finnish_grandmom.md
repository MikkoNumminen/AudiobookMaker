# Finnish Grandmom (Isoäiti) — how the voice was created and how it runs

Finnish Grandmom — labelled **Isoäiti** in the GUI — is the default
Finnish voice that ships with AudiobookMaker. She is the *Finnish-mode
counterpart* to English Grandmom: the same brand, but produced by a
completely different pipeline at runtime. This doc explains where the
voice came from, why she sounds the way she does, and what the runtime
pipeline does to turn Finnish text into her speaking it.

For the English-mode counterpart, see
[english_grandmom.md](english_grandmom.md). For the project decision
that ties the two together, see the auto-memory note
`project_isoaiti_finnish_grandmom.md`.

## How Finnish Grandmom was created — short version

**Nobody recorded her.** Finnish Grandmom is the **default voice the
[Finnish-NLP/Chatterbox-Finnish](https://huggingface.co/Finnish-NLP/Chatterbox-Finnish)
model produces when you call it with no reference audio clip.** The
character — warm, elderly, narrator-paced — is whatever emerged as the
model's default when its upstream maintainers (the Finnish-NLP
organisation on Hugging Face) finetuned the base Chatterbox model on
Finnish speech data.

In other words: this project did not train a voice. It picked up an
already-existing public Finnish TTS model, ran it without conditioning
on any reference clip, and the voice that came out — the one we now
call "Grandmom" / "Isoäiti" — was just the natural default of that
model.

The name is a project decision. The voice character is a property of
the upstream finetune.

## Why a Finnish finetune exists at all

The Chatterbox **multilingual base model** can in principle produce
audio in any of its supported languages, but its Finnish output is
not audiobook-grade. Several systematic problems break Finnish on the
base model:

- **Wrong ä / ö phonemes.** The base model approximates these as
  English-style vowels, which is immediately wrong to a Finnish ear.
- **Collapsed vowel and consonant length distinction.** Finnish is
  phonemically length-sensitive — *tuli* (fire), *tuuli* (wind), and
  *tulli* (customs) are three different words distinguished by vowel
  and consonant duration. The base multilingual model flattens these.
- **Wrong stress placement.** Finnish has strict word-initial primary
  stress; the base model often misplaces it onto syllables that "feel"
  English-stressed.
- **Missing diphthongs.** Finnish diphthongs like `yö`, `ie`, `uo`,
  `äy`, `öy` are not in the base model's English-leaning prior and
  come out flattened to single vowels.

A Finnish finetune solves all four. The Finnish-NLP team built one —
the T3 finetune layered on top of the base Chatterbox model — and
made it public on Hugging Face. AudiobookMaker uses it directly.

## The runtime pipeline (what happens when you click Convert)

When you select **Finnish** as the language and **Chatterbox** as the
engine, the synthesis path is:

```
input text (Finnish)
   │
   ▼
src/tts_normalizer_fi.py    (16-pass Finnish text normalizer)
   │  - normalize -ismi / -tio stems
   │  - expand abbreviations (esim, mm, ks, jne, ...)
   │  - spell out ordinals and Roman numerals
   │  - split fused compound-word seams
   │  - handle acronyms, Latin phrases, common loan words
   │
   ▼
src/tts_chatterbox_bridge.py  (Finnish-mode branch)
   │  - load Finnish-NLP/Chatterbox-Finnish T3 finetune
   │  - call model with NO reference clip → emits "Grandmom" by default
   │  - 300-char chunked autoregressive synthesis
   │
   ▼
audio chunks → ffmpeg concat → MP3
```

Two pieces of project-specific code make this work:

1. **The Finnish normalizer
   ([`src/tts_normalizer_fi.py`](../src/tts_normalizer_fi.py)).** Finnish
   TTS pronunciation gets weird in predictable ways. Numbers, dates,
   abbreviations, and acronyms all need to be rewritten to their
   spoken-Finnish form before the model sees them, or the model will
   either skip them or pronounce them as English. The normalizer is
   16 passes that handle the empirically observed failure modes. It
   has 400+ unit tests. This is the difference between "robotic" and
   "audiobook-grade" output.
2. **The Chatterbox bridge language router
   ([`src/tts_chatterbox_bridge.py`](../src/tts_chatterbox_bridge.py)).**
   The same `chatterbox_fi` engine handles both Finnish (T3 finetune,
   no reference) and English (multilingual base + reference clip).
   The router picks the path by the `--language` flag.

Compared to English Grandmom (Route B v2), the Finnish path has fewer
moving parts: the model is purpose-built for the language, and no
reference clip is needed.

## Why Finnish Grandmom does not have the same prosody quirks as English Grandmom

[english_grandmom.md](english_grandmom.md) documents a class of
failures where Finnish-style prosody bleeds into the English output —
periods don't always pause, sentence-final words like "up." can
slur, the model sometimes hallucinates filler tokens at transitions.

Finnish Grandmom does not have those specific issues because:

- **No cross-language reference clip.** The Finnish path uses the T3
  finetune directly, no conditioning audio. There is no "wrong
  language's prosody" to leak through.
- **The finetune is trained on Finnish prosody.** Punctuation,
  sentence transitions, and rhythm are learned from Finnish speech
  data, so they match Finnish reader expectations.

What Finnish Grandmom **does** struggle with is its own set of
language-specific failures: certain rare words, technical vocabulary,
foreign loan words, names with non-Finnish letterforms, and edge
cases in the normalizer. These are tracked separately.

## Known Finnish-specific failures

When Finnish Grandmom mispronounces a word, the canonical place to log
it is [docs/pronunciation_corpus_fi.md](pronunciation_corpus_fi.md) —
the project's pronunciation corpus. Each entry records the input word,
what the model said instead, and (where known) which normalizer pass
should have caught it. This corpus is the evidence base for the next
round of normalizer fixes; it is updated whenever a tester (Turo is
the canonical external one) reports a mispronunciation.

The skill [`.claude/skills/pronunciation-corpus-add/`](../.claude/skills/pronunciation-corpus-add/SKILL.md)
encodes the appending ritual so the corpus stays in a structured form
that patterns across reports are visible at a glance.

If you hit a mispronunciation, log it. Don't silently re-synth — the
corpus is more valuable than any single fixed file.

## Voice packs on top of Finnish Grandmom

Finnish Grandmom is the model's *default* voice. The voice-pack
pipeline lets you train **per-speaker LoRA adapters** on top of the
same T3 finetune base — they are peers of Grandmom, not replacements
for her. When you load a Finnish voice pack, the runtime stack
becomes:

```
T3 finetune + LoRA adapter   (instead of T3 finetune alone)
```

The same Finnish normalizer runs in front of both. Switching between
Grandmom and a voice-pack persona is a one-click change in the Voice
dropdown.

For the voice-pack pipeline (analyze → export → train → package), see
[voice_pack_training.md](voice_pack_training.md) and the
[`voice-pack-finnish` skill](../.claude/skills/voice-pack-finnish/SKILL.md).

## Where to look in the code

- Finnish text normalizer:
  [`src/tts_normalizer_fi.py`](../src/tts_normalizer_fi.py) plus the
  400+ tests in [`tests/test_tts_normalizer_fi.py`](../tests/test_tts_normalizer_fi.py).
- Chatterbox bridge with language router:
  [`src/tts_chatterbox_bridge.py`](../src/tts_chatterbox_bridge.py).
- Engine installer that fetches the Finnish-NLP model on first use:
  [`src/engine_installer.py`](../src/engine_installer.py).
- Pronunciation corpus:
  [`docs/pronunciation_corpus_fi.md`](pronunciation_corpus_fi.md).

## See also

- [english_grandmom.md](english_grandmom.md) — same persona, English
  pipeline (Route B v2).
- [voice_pack_training.md](voice_pack_training.md) — train your own
  LoRA voice pack on top of the same T3 finetune base.
- [`docs/CLI_CHEATSHEET.md`](CLI_CHEATSHEET.md) — running the
  Chatterbox engine from the command line.
- Upstream model:
  [Finnish-NLP/Chatterbox-Finnish](https://huggingface.co/Finnish-NLP/Chatterbox-Finnish).
