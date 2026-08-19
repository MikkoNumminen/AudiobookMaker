# English Grandmom — how the voice works, and why it has prosody quirks

Grandmom (Isoäiti in Finnish) is the default voice that ships with
AudiobookMaker. She speaks both Finnish and English, but **the two
languages go through different pipelines under the hood.** This doc
explains how the English path works, why its prosody can drift toward
Finnish rhythm, and what you can do about it. It also records one
quirk that was blamed on the engine for months and turned out to be
our own bug (see "Correction" below).

## The two-pipeline reality

Grandmom is one persona, two engines:

| Language | Engine path                                                | Notes                                            |
|----------|------------------------------------------------------------|--------------------------------------------------|
| Finnish  | `Finnish-NLP/Chatterbox-Finnish` T3 finetune (Finnish-only)| **Model's default voice** when called with no reference clip |
| English  | Chatterbox **multilingual base model** + Grandmom reference WAV | English text + Grandmom timbre, see below       |

Important: **nobody recorded Grandmom.** She is the voice the
Finnish-NLP Chatterbox-Finnish model produces *by default* when you
call it without a reference audio clip. The model itself was finetuned
on Finnish speech data upstream by Finnish-NLP; the specific character
that emerges as the default — warm, elderly, narrator-paced — is what
this project brands "Grandmom" / "Isoäiti." The naming is a project
decision; the voice character is whatever the model happens to produce
by default.

The English path cannot use the same trick directly. The Chatterbox
**multilingual base model** has its own default voices when called
with no reference — but those defaults are *not* Grandmom. They are
different characters. So we use **Route B v2**: feed the base model
a short reference clip of Finnish Grandmom (`assets/voices/grandmom_reference.wav`)
to copy her timbre, while letting the base model handle the English
text and prosody.

## Route B v2 — how English Grandmom is assembled at runtime

When you run:

```powershell
audiobookmaker-cli convert mybook.txt --engine chatterbox_grandmom --language en
```

…here is what happens behind the scenes:

1. The CLI loads the **multilingual base Chatterbox model** (not the
   Finnish T3 finetune). The multilingual base speaks English natively.
2. It loads `assets/voices/grandmom_reference.wav` — a short clip
   (~10-15 s) of Finnish Grandmom, **synthesized in advance from the
   Finnish-NLP finetune itself** and bundled with the app. It is not
   a recording of any person.
3. The base model is asked to generate English audio **conditioned on
   that reference clip**. The reference is used for voice timbre
   (pitch, formants, vocal character), not for content.
4. Output: English text spoken with Grandmom's voice.

The trick works because Chatterbox's reference-conditioning is
voice-content-agnostic — the model copies *who is speaking*, then
overlays *what they're saying* from the input text.

## Why English Grandmom is not retrained natively

The obvious question: why not just produce an **English Chatterbox
finetune** that has its own default voice, the same way Finnish-NLP
produced the Finnish one? Two real obstacles:

**1. Chatterbox does not synthesize voices from text descriptions.**

You cannot tell Chatterbox *"warm elderly female narrator, slight
rasp, audiobook pacing"* and get a voice from those words. Chatterbox
voice character comes from one of three sources only:

- The finetune's emergent default voice (no reference, no LoRA).
- A reference audio clip used at inference time (zero-shot cloning).
- A LoRA adapter trained on real audio (the voice-pack pipeline).

**This app already supports description-prompted voices — just
through a different engine.** The **VoxCPM2** engine (dev-only,
NVIDIA GPU required) accepts free-text voice descriptions via the
`--voice-description` flag and steers the output toward whatever
character you describe. So a description-prompted English voice in
the spirit of Grandmom — *"warm elderly female narrator, slight
rasp, audiobook pacing"* — is possible right now, just not on the
Chatterbox engine. See the
[VoxCPM2 section in the README](../README.md#voxcpm2-developer-install-only-nvidia-gpu)
for install steps and honest expectations. (Description prompts work
for broad characteristics — gender, age, tone — and are weaker on
specific cross-language accents.)

So if you wanted an "English Grandmom" character that comes for free
**with the Chatterbox engine specifically**, you would need to
**finetune the multilingual base on English audio data**, the same
way Finnish-NLP finetuned it on Finnish. The model's post-finetune
default voice would then become whatever character emerges from that
English data.

**2. The post-finetune default would not be Finnish Grandmom.**

Finnish Grandmom is the default character that emerged from
Finnish-NLP's Finnish training corpus. A separate finetune on English
data would emerge with a *different* default character. You could
*name* it "English Grandmom" — but acoustic similarity to Finnish
Grandmom is not guaranteed. It would be a new voice that shares the
brand but not necessarily the timbre.

To get a Finnish-Grandmom-like default in an English model, you would
need to either:

- Find / record English training data that happens to land near
  Finnish Grandmom's acoustic basin. Hours of audio from a narrator
  with that specific character. Not impossible, but a real recording
  project.
- Voice-convert an existing English narrator's audio to Grandmom's
  timbre before training. Adds a generation step with its own
  artefacts.

Neither is on the roadmap. Route B v2 (reference-clip cloning at
inference time) is the realistic answer until someone takes on the
recording project.

## Known limitation — Finnish prosody bleed-through

The Grandmom reference clip is, by construction, a clip of Finnish
speech (synthesized from the Finnish-NLP finetune). When the
multilingual base model is conditioned on that reference, it copies
more than just timbre — it also copies some of the **prosody patterns**
of Finnish: how sentences are paced, how punctuation translates to
audio breaks, the rhythm of stressed syllables.

The base model is supposed to override that prosody with English
patterns when generating English audio, but it does not do so 100%.
Some Finnish rhythm leaks through. Observable symptoms:

- **Periods that do not produce pauses.** English readers expect a
  clear pause at `.` before a new sentence starts. Finnish narration
  often runs sentences together with shorter terminal pauses, and
  English Grandmom inherits some of that.
- **Question/exclamation prosody is muted.** The Finnish reference
  rarely contains heavy question-pitch rises or exclamation
  emphasis, so the English output reads as more level than a
  dedicated English voice would.

These are a property of the autoregressive multilingual TTS
architecture combined with a cross-language reference clip, not
something a code change can remove.

### Correction: the "up." quirk was our bug, not the model's

This section used to carry a third symptom, reported on 2026-05-17
as a word-level engine failure: sentence-final `"up."` slurring
through the period, sometimes with an invented filler word after
it. It listed a workaround, which was to scan English text for
sentence-final `"up."` and reword it before synthesis.

That diagnosis was wrong, and the workaround is obsolete. The cause
was in our own text normalizer. Dotted abbreviations were built from
their literal text with no leading anchor, so the `p.` abbreviation
matched the last two characters of **any** word ending in `p`:

    "wake up. Then"                  ->  "wake upage Then"
    "without a gap. Changing pages"  ->  "without a gapage Changing pages"

The period was consumed by the match, which is why the sentences ran
together, and the model was faithfully narrating a word that does not
exist. Every English word ending in `p` before a sentence period was
affected, not just `"up"`. Rewording appeared to fix it only because
any replacement ending avoided the trigger.

Fixed by anchoring dotted abbreviations to a word start (commit
`ccc8713`), with regression coverage in
`tests/test_tts_normalizer_en_abbrev_anchoring.py`.

Two things worth carrying forward from this:

- **A duration check could never have caught it.** "gapage" takes
  about as long to say as "gap", so the chunk stayed a perfectly
  normal length while saying nonsense. It was found by transcribing
  the audio and reading it against the source.
- **The hallucinated-filler observation is not evidence of anything.**
  It was recorded on text the normalizer was corrupting, so the model
  was improvising over a malformed sentence. Whether English Grandmom
  invents filler tokens on clean input is untested; treat it as an
  open question rather than a known trait.

## Why "period → pause" is not just a rule we can add

Autoregressive neural TTS like Chatterbox **has no symbolic punctuation
handling**. The model does not run `if char == '.': insert_silence(200ms)`.
It is a neural network that predicts audio tokens one at a time, from
the text + reference + previous audio. Whether a period produces a
pause depends entirely on what statistical patterns it learned during
training.

This is different from older rule-based TTS (Piper, classical
concatenative systems) where punctuation maps deterministically to
silence durations. Chatterbox traded that determinism for the natural
prosody, voice cloning, and multilingual coverage you get from a
trained neural model. The cost is that prosody on punctuation is
*statistical, not symbolic* — sometimes the model gets it right,
sometimes it does not.

The fact that Chatterbox does not support SSML (the markup language
that lets you write `<break time="200ms"/>` for explicit pauses)
removes the standard production workaround. There is no in-band way to
tell the model "pause here."

## Working around the limitation today

Three knobs are available right now:

### 1. `--chunk-chars N` — keep the text in one chunk

The CLI's `--chunk-chars` flag controls how aggressively long text is
split into separate synthesis calls. The default for Chatterbox is 300
(the upstream-consensus fluency sweet spot). If your text is shorter
than your chunk size, the entire text is synthesized as one
autoregressive run with no chunk boundaries to defend.

```powershell
audiobookmaker-cli convert short_text.txt --engine chatterbox_grandmom `
  --language en --chunk-chars 500
```

This eliminates *chunk-boundary* hallucinations (where the model
cold-starts on chunk N+1 and improvises tokens). It does not fix
mid-chunk prosody failures — those are intrinsic to the model.

### 2. Stronger pause cues in the text

Replace `". "` between sentences with `"... "` or `" — "`. Chatterbox
often (but not always) renders ellipsis and em-dash as longer pauses
than a bare period — worth trying when prosody on punctuation matters.
Caveat: this changes the audio meaning — ellipsis reads as hesitation,
em-dash as a deliberate break. Use sparingly where prosody matters
more than the punctuation's semantic feel.

### 3. Re-roll

Chatterbox is non-deterministic. The same input often produces
different audio on different runs. If a run hallucinates, wiping the
chunk cache and re-rolling fixes it about half the time:

```powershell
audiobookmaker-cli convert mytext.txt --engine chatterbox_grandmom `
  --language en --overwrite fresh
```

`--overwrite fresh` wipes the per-chunk WAV cache so the model
generates fresh audio instead of reusing the bad one.

## Structural fixes (not built yet)

Two engineering changes would fix this properly:

1. **Post-processing silence insertion at sentence boundaries.** After
   synthesis, detect `. ! ?` in the source text, map them to time
   positions in the WAV (via forced alignment or by re-synthesizing
   each sentence separately), and splice ~200 ms of silence at those
   points. Moderate change in
   [src/synthesis_orchestrator.py](../src/synthesis_orchestrator.py) —
   actual size depends on whether forced alignment is used (real
   dependency + integration) or sentence-level re-synthesis (changes
   the chunker contract). Fixes the period→pause issue; does not fix
   the hallucinated-token issue.
2. **ASR validation + auto-reroll.** After synthesis, run Whisper over
   the output WAV, diff the transcript against the source text, and
   re-roll the chunk if the Word Error Rate exceeds a threshold.
   Catches both period-merging and hallucinated tokens — it is the
   definitive fix but expensive (every chunk runs through ASR).
   Several days of engineering work.

Neither is on the roadmap today. Open an issue if you hit this often
enough that the workarounds above are not enough.

## Where to look in the code

- Route B v2 implementation: `src/tts_chatterbox_bridge.py`
  (multilingual base model path, English-mode branch).
- Reference clip: `assets/voices/grandmom_reference.wav` (tracked in
  the repo).
- Chunking defaults: `scripts/generate_chatterbox_audiobook.py`
  (`--chunk-chars default=300`).
- Voice pack training pipeline (Finnish path): see
  [voice_pack_training.md](voice_pack_training.md).

## See also

- [`docs/CLI_CHEATSHEET.md`](CLI_CHEATSHEET.md) — Step 4 covers
  `--chunk-chars` and `--overwrite` knobs.
- The auto-memory file `project_english_grandmom.md` (per-user
  memory, not in repo) — historical decision record naming this
  approach "Route B v2."
