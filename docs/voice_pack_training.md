# Voice pack training — end-to-end runbook

How to fine-tune a LoRA adapter on top of the multilingual Chatterbox
model from a source audio file. Tuned for the current English-audiobook
use case on a 12 GB RTX 3080 Ti.

## What you need

- CUDA 11.8+ GPU (12 GB is enough at the defaults below).
- A Python env with `torch`, `torchaudio`, `peft`, and `chatterbox`
  installed. In this repo, that's `.venv-chatterbox/`.
- Source audio: any format `ffmpeg` can read (`.m4b`, `.mp3`, `.wav`).

## Pipeline overview

```
<audio file>
   │  (1) ffmpeg extract
   ▼
<clip.wav, 24 kHz mono>
   │  (2) voice_pack_analyze.py      → transcripts.jsonl + speakers.yaml
   ▼
<per-speaker VoiceChunks>
   │  (2b, optional) voice_pack_characters.py
   │                → transcripts_with_characters.jsonl + characters.yaml
   ▼
<per-speaker, per-character VoiceChunks>
   │  (3) voice_pack_export.py       → manifest.json + wavs/
   ▼
<DatasetManifest>
   │  (4) voice_pack_train.py        → adapter/
   ▼
<LoRA adapter ready to load>
   │  (5) voice_pack_package.py      → voice pack directory
   ▼
<installable voice pack>
```

Stage 2b is optional. Skip it if one adapter per reader is enough.
Run it when the source audio has one reader performing multiple
characters (narrator + villain + hero) and you want a separate voice
clone for each character instead of a single averaged blend.

All stages are one-shot CLIs.

## Step-by-step — 1 hour sample

### 1. Extract 1 hour of mono 24 kHz audio

```bash
ffmpeg -i "D:/path/to/source_audiobook.m4b" \
  -t 3600 -ar 24000 -ac 1 sample_1h.wav
```

### 2. Analyze (ASR + diarization)

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_analyze.py \
  --input sample_1h.wav \
  --out analysis_1h/
```

Outputs:
- `analysis_1h/transcripts.jsonl` — one VoiceChunk per line.
- `analysis_1h/speakers.yaml` — speakers sorted by total seconds.
- `analysis_1h/report.md` — human summary with tier suggestions.

Open `report.md` and pick the speaker you want to clone. For a single-
narrator audiobook this is usually `SPEAKER_00` (the narrator), measured
in tens of minutes even in a 1 h sample.

**When you know the cast size, pin it.** Pyannote is good but not
perfect at distinguishing similar-register readers; telling it the
truth avoids the two most common failure modes (splitting one reader
into ghost speakers, or merging two readers into one):

```bash
# Solo narrator — force one speaker.
… voice_pack_analyze.py … --num-speakers 1

# Two-reader audiobook (M + F).
… voice_pack_analyze.py … --num-speakers 2

# Full-cast production, unknown exact size.
… voice_pack_analyze.py … --min-speakers 4 --max-speakers 8
```

`--num-speakers` is exact and overrides the min/max range when both
are supplied. Leave all three unset only when you genuinely don't
know the cast.

### 2b. (Optional) Cluster characters within each speaker

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_characters.py \
  --transcripts analysis_1h/transcripts.jsonl \
  --source sample_1h.wav \
  --out characters_1h/ \
  --max-characters-per-speaker 3
```

Outputs under `characters_1h/`:

- `transcripts_with_characters.jsonl` — same chunks, each with a
  `character` field like `CHAR_A`, `CHAR_B`. Character ids are unique
  per speaker, not globally.
- `characters.yaml` — per-(speaker, character) stats, biggest first.
- `characters_report.md` — human summary with the table and a
  ready-to-paste `voice_pack_export.py` command.

What the knobs do:

- `--distance-threshold 0.25` — cosine distance at which two chunks
  are considered "same character voice". Lower splits subtly-different
  voices apart; higher merges them.
- `--min-character-seconds 60` / `--min-character-chunks 8` — quality
  floor. Clusters below these gates fold into the dominant (narrator)
  cluster so you don't end up with a "CHAR_D has 15 s of audio"
  dead slot.
- `--max-characters-per-speaker N` — budget cap. Keep at most N
  characters per reader, ranked by total duration. Smaller clusters
  fold into the dominant one. Use this to predictably bound GPU cost
  for voice-pack training: each surviving character gets its own LoRA
  adapter at ~30-60 min each on a 3080 Ti. Unset (default) to keep
  every cluster that passes the quality floor.
- `--max-chunks-per-speaker N` — optional cap on how many chunks per
  reader get embedded at all. Helpful for very long source files
  where the embedding step is the slow part. Evenly-spread sampling
  preserves timeline coverage.

After this stage, use `--transcripts` pointing at
`characters_1h/transcripts_with_characters.jsonl` and
`--character CHAR_A` (or whichever label you pick) in Stage 3.

### 3. Export a single-speaker dataset

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_export.py \
  --transcripts analysis_1h/transcripts.jsonl \
  --source sample_1h.wav \
  --speaker SPEAKER_00 \
  --out dataset_1h/
```

If you ran the optional character-clustering stage, point at the
character-aware transcripts and add `--character`:

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_export.py \
  --transcripts characters_1h/transcripts_with_characters.jsonl \
  --source sample_1h.wav \
  --speaker SPEAKER_00 \
  --character CHAR_A \
  --out dataset_char_a/
```

Run one export + train + package chain per character you want to
ship.

Outputs under `dataset_1h/`:
- `manifest.json` — ready for `voice_pack_train.py`.
- `wavs/NNNN.wav` — one clip per VoiceChunk for the chosen speaker.
- `metadata.csv` — LJSpeech-format companion (path|text|emotion|duration).

Every clip gets labelled `neutral` by default. Pass `--emotion-label
happy|sad|angry|unknown` to override for the whole set, or plug a real
emotion classifier in by re-exporting stage 2's chunks with per-clip
labels (the `TaggedChunk.from_chunk` helper is the hook point).

### 4. Train

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_train.py \
  --manifest dataset_1h/manifest.json \
  --out runs/sample_1h/ \
  --batch-size 2 \
  --grad-accum 8 \
  --epochs 3 \
  --mixed-precision fp16 \
  -v
```

Why these knobs on a 3080 Ti:

- `--batch-size 2 --grad-accum 8` → effective batch of 16, stays inside
  12 GB VRAM even on longer clips.
- `--epochs 3` → the full_lora default; early stopping cuts it short if
  loss plateaus (patience 3 eval windows).
- `--mixed-precision fp16` → Ampere's native fast path; bf16 is slower on
  3080 Ti.

Outputs under `runs/sample_1h/`:

- `config.json` — effective hyperparameters.
- `manifest_snapshot.json` — frozen dataset spec.
- `run_command.txt` — exact argv to reproduce the run.
- `training.log` — per-step loss + LR + checkpoint markers.
- `adapter/` — PEFT LoRA adapter ready to load.

Expected: ~30–60 min wall time for 1 h of source audio on a 3080 Ti.

### 5. Package as an installable voice pack

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_package.py \
  --run runs/sample_1h/ \
  --tier full_lora \
  --sample dataset_1h/wavs/0000.wav \
  --out voice_packs/my_narrator/
```

## What still needs a human

- **Speaker selection.** The analyzer gives you a report, not a pick.
  For multi-speaker audiobooks you run steps 3–5 once per speaker you
  want to clone.
- **Emotion tagging.** `voice_pack_export.py` labels every clip
  `neutral` by default. For expressive voices plug in a real emotion
  classifier upstream and write the per-clip labels into the manifest
  directly. Low-effort upgrade: heuristics on text punctuation (`!` →
  angry/happy, long ellipses → sad).
- **Listening check.** Nothing here measures audio quality. After
  training, synthesize a sample and listen. If it's overshooting the
  accent (buzzy, stiff), drop `--lr` to 5e-5 and retrain.

## Troubleshooting

**`NotImplementedError: LoRA training requires torch + peft + ...`** —
you're on a host without the GPU stack, or CUDA isn't visible. Run from
`.venv-chatterbox/` and check `torch.cuda.is_available()`.

**OOM during training** — drop `--batch-size` to 1, raise `--grad-accum`
to keep the effective batch constant.

**"No LoRA parameters found"** — base model's attention module names
don't match `q_proj/k_proj/v_proj/o_proj`. Inspect
`engine.t3.tfmr.named_modules()` and adjust `target_modules` in
`_run_training_impl`.

**Loss goes to `nan` with fp16** — switch to `--mixed-precision bf16`
(slower but numerically more stable on deep transformers).
