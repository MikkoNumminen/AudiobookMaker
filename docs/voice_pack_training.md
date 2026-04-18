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
   │  (2) voice_pack_analyze.py   → transcripts.jsonl + speakers.yaml
   ▼
<per-speaker VoiceChunks>
   │  (3) emotion-tag + export  → manifest.json + wavs/
   ▼          (not yet a CLI — see "Bridge" below)
<DatasetManifest>
   │  (4) voice_pack_train.py   → adapter/
   ▼
<LoRA adapter ready to load>
   │  (5) voice_pack_package.py → voice pack directory
   ▼
<installable voice pack>
```

Stages 1, 2, 4, 5 have CLIs today. Stage 3 needs a small Python driver
until the bridge CLI lands.

## Step-by-step — 1 hour sample

### 1. Extract 1 hour of mono 24 kHz audio

```bash
ffmpeg -i "D:/satellite/Dual Class 3 - A LitRPG Adventure.m4b" \
  -t 3600 -ar 24000 -ac 1 dual_class_1h.wav
```

### 2. Analyze (ASR + diarization)

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_analyze.py \
  --audio dual_class_1h.wav \
  --out analysis_1h/
```

Outputs:
- `analysis_1h/transcripts.jsonl` — one VoiceChunk per line.
- `analysis_1h/speakers.yaml` — speakers sorted by total seconds.
- `analysis_1h/report.md` — human summary with tier suggestions.

Open `report.md` and pick the speaker you want to clone. For a LitRPG
audiobook this is usually `SPEAKER_00` (the narrator), measured in
tens of minutes even in a 1 h sample.

### 3. Emotion-tag + export a single-speaker dataset

Temporary bridge — until the export CLI lands, run this snippet:

```python
# save as bridge_export.py, run with .venv-chatterbox/Scripts/python.exe
import json
from pathlib import Path
from src.voice_pack.types import VoiceChunk, TaggedChunk
from src.voice_pack.dataset import export_dataset

SPEAKER = "SPEAKER_00"                          # from report.md
SOURCE = Path("dual_class_1h.wav")
ANALYSIS = Path("analysis_1h/transcripts.jsonl")
OUT = Path("dataset_1h/")

tagged: list[TaggedChunk] = []
with ANALYSIS.open("r", encoding="utf-8") as fh:
    for line in fh:
        raw = json.loads(line)
        if raw["speaker"] != SPEAKER:
            continue
        vc = VoiceChunk(**raw)
        # Simplest emotion pass: everything "neutral". Swap in a real
        # classifier here if you want emotion-balanced training.
        tagged.append(TaggedChunk(chunk=vc, emotion="neutral"))

manifest = export_dataset(
    tagged,
    source_audio_path=SOURCE,
    out_dir=OUT,
    sample_rate_hz=24000,
)
print(f"Exported {len(manifest.clips)} clips, "
      f"{manifest.total_seconds / 60:.1f} min, tier target: full_lora")
```

### 4. Train

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_train.py \
  --manifest dataset_1h/manifest.json \
  --out runs/dual_class_1h/ \
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

Outputs under `runs/dual_class_1h/`:

- `config.json` — effective hyperparameters.
- `manifest_snapshot.json` — frozen dataset spec.
- `run_command.txt` — exact argv to reproduce the run.
- `training.log` — per-step loss + LR + checkpoint markers.
- `adapter/` — PEFT LoRA adapter ready to load.

Expected: ~30–60 min wall time for 1 h of source audio on a 3080 Ti.

### 5. Package as an installable voice pack

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_package.py \
  --run runs/dual_class_1h/ \
  --tier full_lora \
  --sample dataset_1h/wavs/0000.wav \
  --out voice_packs/dual_class_narrator/
```

## What still needs a human

- **Speaker selection.** The analyzer gives you a report, not a pick.
  For multi-speaker audiobooks you run steps 3–5 once per speaker you
  want to clone.
- **Emotion tagging.** The bridge snippet above labels every clip
  "neutral". For expressive voices you'd plug in a real emotion classifier
  between analyze and export. Low-effort upgrade: use heuristics on text
  punctuation (`!` → angry/happy, long ellipses → sad).
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
