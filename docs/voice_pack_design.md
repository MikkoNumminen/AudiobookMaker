# Voice-pack design rationale

This is an internal dev doc. It explains the *why* behind the voice-pack
(voice-cloning) system — the choices that shaped it and the trade-offs each one
carries. For the step-by-step runbook, see
[`docs/voice_pack_training.md`](voice_pack_training.md). For credentials and the
dev-environment setup, see [`docs/DEVELOPER_SETUP.md`](DEVELOPER_SETUP.md).

---

## The thesis: build the clone path on the engine we already ship

The voice-cloning system is built on Chatterbox LoRA fine-tuning. The central
reason is a deliberate one: a LoRA adapter trained on top of Chatterbox is
*designed* to run through the same model as every other Chatterbox synthesis
job. A cloned voice is meant to be nothing more than the base Chatterbox model
with a small, speaker-specific adapter bolted on — no second engine to load, no
second set of weights to keep in VRAM, no forked inference loop to maintain.

The payoff of that design: cloning adds no VRAM cost beyond the adapter itself,
and every correctness fix to the core Chatterbox engine benefits cloned voices
for free. (This is the *dev pipeline's* design rationale — see **What the app
actually uses** at the bottom for which parts the app runs.)

The main alternative considered was Coqui XTTS v2. It was ruled out for two
reasons. First, its license (CPML) is non-commercial only, which would block any
future paid build. Second, its upstream wound down and it is no longer actively
maintained. Chatterbox is MIT-licensed and actively maintained by Resemble AI,
so it is the safer long-term bet.

---

## The four-stage pipeline

The pipeline is linear: each stage writes a clean artefact that the next stage
reads. Nothing re-reads the source audio after stage 1.

```
source audio
    │
    ▼  (1) voice_pack_analyze.py     [GPU: ASR + diarization]
transcripts.jsonl + speakers.yaml + report.md
    │
    ▼  (2) voice_pack_export.py      [audio I/O only]
manifest.json + clip wavs
    │
    ▼  (3) voice_pack_train.py       [GPU: LoRA fine-tune]
adapter.pt (LoRA weights)
    │
    ▼  (4) voice_pack_package.py
installable voice pack directory
```

**Stage 1 — Analyze.** The source audio is transcribed (faster-whisper
`large-v3`) and diarized (who-spoke-when). The two streams are intersected into
per-speaker `VoiceChunk` records — one sentence, one speaker, one time range.
Outputs are a `transcripts.jsonl` you can open in a text editor, a
`speakers.yaml` with per-speaker totals, and a `report.md` that tells you which
speakers have enough audio to be worth cloning. This stage loads a full ML stack
and must never run alongside another ML subprocess (see **GPU discipline**).

**Stage 2 — Export.** For one chosen speaker, the export stage slices the source
audio into individual clip WAVs and writes a `manifest.json`. Pure audio I/O —
no GPU, no model loading. An optional stage 2b (`voice_pack_characters.py`)
acoustically sub-clusters one reader's chunks, for sources where a single
narrator performs several distinct character voices. Skip it when one adapter
per reader is enough.

**Stage 3 — Train.** A LoRA adapter is fine-tuned on the exported clips. It
targets the attention projection layers (`q_proj`, `k_proj`, `v_proj`, `o_proj`)
of the Chatterbox T3 transformer. Training takes on the order of tens of minutes
per hour of source audio on a 12 GB GPU.

**Stage 4 — Package.** The trained adapter plus a short preview clip are bundled
into a voice pack directory the GUI can load via **Import voice pack**. The pack
is a small directory with a `meta.yaml`, a `sample.wav` preview, and either an
`adapter.pt` (the LoRA tiers) or a `reference.wav` (the few-shot tier). It does
**not** contain the multi-gigabyte base model — that is a shared dependency
downloaded once per machine.

For the full command-line runbook, see
[`docs/voice_pack_training.md`](voice_pack_training.md).

---

## The tier system: how much clean audio you start with decides what you get

Not every source yields the same clone quality. Each detected speaker is sorted
into one of four tiers by how many seconds of clean audio they contribute. The
thresholds live in `src/voice_pack/types.py` (`classify_quality_tier`) and are
the single source of truth — the CLI, the training harness, and the GUI all read
the same constants.

| Tier | Source duration | What happens |
|---|---|---|
| `full_lora` | ≥ 30 min | Full LoRA fine-tune (rank 32 by default). Best quality. |
| `reduced_lora` | 10–30 min | Reduced-rank LoRA (rank 8) with tighter early stopping. Flagged "experimental quality" in the GUI. |
| `few_shot` | 1–10 min | No training. The best ~15 s reference clip is extracted and saved; the base model clones from that clip at synthesis time. Lower quality but usable. |
| `skip` | < 1 min | Ignored — not enough audio to produce anything useful. |

`reduced_lora` exists because some sources genuinely sit between "not enough to
train" and "plenty." It is best-effort; treat its output as a prototype, not a
finished voice. `few_shot` requires no training at all — the pack carries only
the reference clip, and the reference picker (below) works hard to make that one
clip clean, because the entire voice is encoded in it.

---

## Adapter footprint

A LoRA adapter stores only the weight deltas for the attention projection layers
it targets, at low rank (8 for `reduced_lora`, 32 for `full_lora`). That is a
small fraction of the multi-gigabyte base model, so `adapter.pt` files are small
and voice packs stay portable. A pack directory holds only the speaker-specific
layer; the base weights are shared across all packs on a machine.

---

## Emotional range

A clone is only as expressive as its training data. If every training clip is
flat neutral narration, the clone defaults to flat neutral narration no matter
what knobs you turn at synthesis time.

A tagging step (`src/voice_pack/emotion.py`, a SpeechBrain IEMOCAP classifier)
labels each clip `neutral` / `angry` / `happy` / `sad` / `unknown`. The dataset
stage uses those labels to rebalance the training set so minority emotions —
rare in typical narration — still imprint instead of being drowned out by
neutral clips.

At synthesis time, expression is steered by two Chatterbox knobs:
`exaggeration` and `cfg_weight`. These are not post-processing effects; they are
guidance parameters in the model's generation process. `src/voice_pack/`
`expression.py` defines five named presets — whisper, calm, neutral, intense,
shout — as `(exaggeration, cfg_weight)` pairs, plus a markup syntax
(`{{expr:whisper}}`) for annotating a manuscript per sentence. A directive
sticks until replaced; `{{expr:default}}` clears back to the plan default.

The floor: no knob conjures an emotion the adapter never saw in training. Clip
diversity in the source matters as much as the settings at synthesis time.

---

## Multi-speaker handling: diarization and the reference picker

Most sources have more than one speaker, so the pipeline defaults to
multi-speaker — always run diarization, even on a source you believe is
single-narrator, because the speaker count is information you want before
committing to training.

**Diarization** answers "who spoke when." The default backend is pyannote
`speaker-diarization-3.1` (pinned to a specific model revision in
`src/voice_pack/diarize.py`), which is gated on Hugging Face and needs an HF
token plus an accepted license. When pyannote is unavailable, or when it
conflates two similar-timbre speakers into one label, the fallback is SpeechBrain's
ECAPA-TDNN speaker-embedding model (`--diarizer ecapa`) — public, no HF account
needed. Pyannote is more accurate on clearly distinct speakers; ECAPA has
rescued cases where pyannote merged two readers of similar register. If you know
the cast size, pass it (`--num-speakers 2`) — it prevents the two common
failures: splitting one reader into ghost speakers, or merging two readers into
one.

**The reference picker** (`src/voice_pack/reference_picker.py`) selects the best
short clip per speaker for the `few_shot` tier, scoring candidates on: a
**duration** window; **position** (skip the first/last few seconds — intros and
outros); **text quality** (reject digits, all-caps acronyms, too-few/too-many
words — these mispronounce); **RMS stability** (consistent volume across the
clip); and **pitch consistency** (median F0 via windowed autocorrelation, to
avoid outlier clips where the speaker was unusually emphatic).

**Validate transcripts before declaring success.** Diarization returns speaker
*labels*, not *identities* — two clips both labelled `SPEAKER_00` are only useful
if they actually hold the same voice. Read the chunk text for each picked
reference and confirm it reads like the expected speaker. A label collision (two
people sharing one label) produces unusable training data that looks fine until
you synthesize and listen.

---

## The ~5-hour quality ceiling

More source audio produces better clones — up to a point. Past roughly five
hours of clean source, gains become marginal: training runs longer, VRAM
pressure rises, the adapter grows, but the output sounds essentially the same.
For dev work, one to two hours is enough to land in `full_lora` territory and
hear a recognizable clone; two to three hours is a comfortable target for a
finished pack. This is a practical observation, not a hard limit — sources with
unusually wide emotional or stylistic range benefit from more data than plain
narration does.

---

## GPU discipline

Each analyze or train subprocess loads faster-whisper `large-v3`, pyannote 3.1,
and Chatterbox at once — roughly 6 GB VRAM and 2 GB RAM per process on a 12 GB
card. Two concurrent runs swap-thrash the GPU allocator into system RAM and can
freeze the machine. The pipeline enforces one-at-a-time with a machine-wide lock
(`src/process_lock.py`, `single_ml_subprocess_lock`): the analyze and train CLIs
acquire it before loading any model and refuse to start if another ML subprocess
holds it. Do not parallelize analyze/train across terminals or concurrent agents
on one machine.

---

## What the app actually uses (scope)

Voice-pack **creation is a dev-only CLI tool — the app never creates a clone.**
The app is the simple "make an audiobook" surface; the advanced kit (this whole
pipeline) lives on the dev side. What the app does with packs and clips:

- **Reference-clip path — wired.** The GUI accepts a reference clip (the
  `Ref. audio:` field) or an imported pack via **Import voice pack**, and feeds
  it to Chatterbox's reference-audio path in `src/tts_chatterbox_bridge.py`,
  which conditions generation on that clip. An imported pack contributes its
  reference clip; this is what plays in the app today, for every pack tier.
- **LoRA-adapter inference — not wired, and not an app feature.** The dev
  pipeline produces `adapter.pt` files for `full_lora` / `reduced_lora` packs,
  but the synthesis bridge never loads a trained adapter at generation time — it
  uses the reference-audio path for all packs. Wiring in-app adapter playback is
  **not** on the roadmap: cloning is a dev-side tool, so the trained adapters
  exist for dev experimentation, not as an app-shipped voicing path.
