---
name: voice-clone-finnish
description: Clone Finnish voices from an audio file end-to-end — chunked analyze with ECAPA diarization, transcript validation per speaker, LoRA training per speaker (or few-shot ref-clip fallback for short sources), package, and ear-check by synth. Use whenever the user says "copy the voices", "clone these voices", "extract voices from this clip", "make a voice pack from this audio", or hands you a Finnish podcast / interview / audiobook to mimic. Encodes the empirically-validated pipeline so repeated mistakes from few-shot ref-clip cloning never recur. CRITICAL — never assume single-speaker, never trust pyannote labels without validation, never install copyright-derived packs to ~/.audiobookmaker.
---

# voice-clone-finnish

Clone one or more Finnish voices from a source audio file end-to-end. The
output is per-speaker reference clips and (when source size allows) a LoRA
voice pack the user can synthesize against.

## Why this skill exists

Finnish voice cloning has three failure modes the naive few-shot ref-clip
path keeps tripping over:

1. **Gender transfer is limited under few-shot.** The Finnish T3 finetune
   in `Finnish-NLP/Chatterbox-Finnish` is trained on a single voice
   (Grandmom). Few-shot ref-clip cloning lands all outputs near that
   basin — different refs produce different voices, but cross-gender
   clones do not preserve the target's gender. Observed 2026-05-10: a
   female ref clip and a male ref clip both produced male-sounding
   output. The fix is to train a LoRA adapter per speaker, which
   shifts the model's latent space toward the actual cloned voice.
2. **Pyannote conflates similar-timbre Finnish speakers.** Two adult
   Finnish voices in the same register get split into 2 labels by
   `pyannote/speaker-diarization-3.1`, but the per-chunk labels are
   mixed: chunks that are actually speaker A leak into label B and
   vice versa. Picking a "best" ref clip from a wrong-labelled chunk
   produces a clone of the wrong speaker. The fix is to use the
   ECAPA-TDNN backend (`--diarizer ecapa`).
3. **Long-source native crash.** `voice_pack_analyze` crashes at
   C-level (`STATUS_STACK_BUFFER_OVERRUN`, exit `0xC0000409` / `127`)
   on inputs longer than ~5-10 minutes. The crash is in faster-whisper
   ASR, not the diarizer. The fix is to always go through the chunked
   orchestrator (`run_chunked_analyze`) — it slices the source,
   analyses each chunk as a child subprocess, and falls back to CPU
   on the rare per-chunk crash.

This skill encodes the workaround for all three.

## Preconditions

Before starting, check:

- `.venv-chatterbox/Scripts/python.exe` exists (not `.venv` — the
  voice-pack pipeline needs the chatterbox venv).
- `~/.cache/huggingface/token` exists with a token that has accepted
  the `pyannote/speaker-diarization-3.1` license — even when using
  ECAPA, some chunked-analyze paths may still load pyannote modules
  during embedding.
- `nvidia-smi` shows ~10+ GB free VRAM. If anything else is consuming
  the GPU, surface the PIDs to the user and wait for their call (per
  the `feedback_never_kill_processes.md` memory).
- `.local/` directory exists at the repo root (it's gitignored at line
  80 of `.gitignore`).
- The user has handed you a path to a local audio file — never download
  from URLs (that triggers the auto-mode classifier and is generally
  the wrong workflow for copyrighted material).

## Hard rules — read before every step

- **Default to multi-speaker.** Audio sources are typically podcasts,
  interviews, or conversations — single-speaker is the exception, not
  the rule. Always run diarization. Always check the speaker count.
- **All artefacts live in `.local/`** (gitignored). Never commit any
  source audio, transcript, ref clip, voice pack, or synth output.
  Never write the cloned person's real name into any tracked file —
  that includes commit messages, PR text, doc files, code comments,
  and test fixtures. The repo's public history must not reveal which
  copyrighted source was used.
- **Never install copyright-derived packs into `~/.audiobookmaker/`.**
  Per the `feedback_no_installing_copyright_derived_packs.md` memory,
  voice packs trained on copyrighted audio stay in `.local/` only —
  never registered into the GUI's Voice dropdown.
- **One voice-pack ML subprocess at a time** (per CLAUDE.md "Resource
  discipline"). Two concurrent whisper-large-v3 + pyannote stacks on
  a 12 GB GPU swap-thrash into system RAM and freeze the OS.
  Bisection / training / synth runs are strictly serial.

## The pipeline

### Step 1 — normalize the source to 16 kHz mono WAV

The chunked analyzer wants a clean WAV. Re-encode if the source is MP3
or stereo or non-16 kHz. Skip this step only when the source is
already 16 kHz mono WAV.

```bash
ffmpeg -y -i "<source>" -ac 1 -ar 16000 .local/voice_runs/source.wav
```

The MP3 → WAV conversion eliminates a class of pydub-decode failures
inside the analyzer. It also bypasses the path-encoding pitfalls of
non-ASCII filenames in shell argv.

### Step 2 — chunked analyze with ECAPA

Use the chunked orchestrator with `diarizer="ecapa"`. Default to
`num_speakers=2` for podcasts / interviews. Override only if you have
solid evidence of a different cast size.

```python
from src.voice_pack_chunked_subproc import run_chunked_analyze, ChunkedProgress
from pathlib import Path

def cb(ev: ChunkedProgress) -> None:
    print(f'[{ev.stage}] {ev.message}', flush=True)

result = run_chunked_analyze(
    wav=Path('.local/voice_runs/source.wav'),
    out_dir=Path('.local/voice_runs/full_ecapa'),
    chunk_seconds=300.0,
    workers=1,                       # one CUDA chunk at a time — VRAM gate
    num_speakers=2,
    diarizer='ecapa',                # rescues pyannote conflation
    asr_device='cuda',
    progress_cb=cb,
)
```

Wall time on RTX 3080 Ti: ~10 min for a 1 h source with 11 chunks.
Each chunk's whisper ASR is ~40 s; one chunk in ten typically hits the
native crash and the orchestrator falls back to CPU automatically (~5
min on CPU per chunk).

Outputs in `.local/voice_runs/full_ecapa/`:

- `transcripts.jsonl` — one VoiceChunk per line, with `speaker` IDs
  reconciled across chunks via voice embedding.
- `speakers.yaml` — totals + tier per global speaker.
- `report.md` — human-readable summary.
- `chunks/chunk_NN/` — per-chunk artefacts kept for debugging.

### Step 3 — validate diarization by transcript

**This is the step that catches the conflation bug.** Read 3-5 random
chunks per speaker label. Confirm the text reads like a coherent voice
role:

```python
import json, random
random.seed(7)
by_spk = {}
with open('.local/voice_runs/full_ecapa/transcripts.jsonl', encoding='utf-8') as f:
    for line in f:
        c = json.loads(line)
        by_spk.setdefault(c['speaker'], []).append(c)
for spk in sorted(by_spk):
    chunks = by_spk[spk]
    print(f'{spk} ({len(chunks)} chunks):')
    for c in random.sample(chunks, min(5, len(chunks))):
        print(f"  [{c['start']:.1f}s] {c['text'][:130]}")
```

Smoking-gun patterns of bad diarization:

- One label contains both interviewer-questions and guest-answers.
- Two labels' texts both read as the same voice role (both questions,
  both backchannels, both narration).
- The intro segment (first ~30 s) shows the host welcoming the guest
  but the host's text gets the guest's label or vice versa.

If diarization looks bad even under ECAPA: surface the issue, offer
manual ref-segment selection from a transcript timestamp the operator
picks by ear.

If diarization looks clean, write a sidecar mapping
`.local/voice_runs/voice_labels.txt` (gitignored) — speaker-ID →
real-name only there, never in tracked code.

### Step 4 — pick reference clips per speaker, then VALIDATE by synth

**The reference picker's scoring (duration, position, RMS-stability) does
NOT include any acoustic gender / pitch-consistency check.** It can pick
a 12-18 s clip where the target speaker happened to be speaking deeply,
emphatically, or whispering — and voice cloning will then map that
unusual prosody to the wrong basin in latent space. Observed 2026-05-10
on a 30-min Finnish source: picker chose 1682.0-1694.0s for SPEAKER_00
(female target), the clip caught the speaker in an emphatic moment, all
synth attempts came out male regardless of LoRA strength. The fix was to
synthesize with the picked ref BEFORE declaring done and switch to
candidate alt2 (599.0-615.5s) when the gender came out wrong.

```python
from src.voice_pack.reference_picker import (
    pick_reference_clip, _default_audio_reader, _default_audio_writer,
)
import yaml

run_dir = Path('.local/voice_runs/full_ecapa')
src = Path('.local/voice_runs/source.wav')
out_dir = Path('.local/voice_runs/refs_ecapa')
out_dir.mkdir(parents=True, exist_ok=True)

speakers = yaml.safe_load((run_dir / 'speakers.yaml').read_text(encoding='utf-8'))
for spk in speakers:
    sid = spk['speaker']
    rep = pick_reference_clip(
        transcripts=run_dir / 'transcripts.jsonl',
        speaker_id=sid, wav_source=src, out_path=out_dir / f'{sid}.wav',
        audio_reader=_default_audio_reader, audio_writer=_default_audio_writer,
        top_k=5,
    )
    print(f'{sid}: {rep.selected_start:.1f}-{rep.selected_end:.1f}s '
          f'score={rep.selected_score:.2f}')
```

Then **read the picked chunk's transcript text** to verify it really
belongs to that speaker. The picker scores by duration / position /
RMS — it does not check the chunk's diarization label is correct.

**After picking, write all 5 candidates to disk** (the
`pick_reference_clip(top_k=5)` machinery returns them in `report.candidates`):

```python
for i, c in enumerate(rep.candidates[:5]):
    _default_audio_writer(src, c.start, c.end,
                          out_dir / f'{sid}_alt{i}.wav')
```

Keep these alternates around — Step 7's synth-validation step may need
to fall back to one of them when the primary pick produces wrong
gender / wrong prosody.

### Step 5 — pick the right cloning path by tier

Look at `speakers.yaml`. For each speaker:

| Total minutes | Tier            | Path                                        |
|---------------|-----------------|---------------------------------------------|
| ≥ 30 min      | `full_lora`     | LoRA training (full rank)                   |
| 10-30 min     | `reduced_lora`  | LoRA training (reduced rank, early stopping) |
| 1-10 min      | `few_shot`      | Reference clip only — no LoRA                |
| < 1 min       | `skip`          | Not enough audio; abort or get more         |

**Cross-gender voice cloning REQUIRES LoRA.** Few-shot ref-clip
cloning cannot escape the Finnish T3 finetune's single-voice basin
on the gender axis. If the user wants a female voice cloned and the
source has ≥10 min of her audio, train a LoRA — do not declare done
on the few-shot path.

### Step 6 — for each LoRA tier speaker, export → train → package

#### 6a. Export the dataset

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_export.py \
  --transcripts .local/voice_runs/full_ecapa/transcripts.jsonl \
  --source .local/voice_runs/source.wav \
  --speaker SPEAKER_00 \
  --out .local/voice_runs/dataset_speaker_00
```

CPU-only, ~1 minute per speaker. Two exports can run in parallel if
both target different `--out` directories (they don't share GPU).

#### 6b. Train the LoRA adapter

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_train.py \
  --manifest .local/voice_runs/dataset_speaker_00/manifest.json \
  --out .local/voice_runs/lora_speaker_00 \
  --batch-size 2 \
  --grad-accum 8 \
  --epochs 3 \
  --mixed-precision fp16 \
  -v
```

GPU-bound; ~30-45 min wall-clock per speaker on a 3080 Ti for 20-30
min of source. **Strictly serial** per CLAUDE.md resource discipline —
do not run two trains concurrently, do not run synth concurrently.

Background the launch and monitor stdout/stderr separately — do not
use `tee` + `2>&1` on PowerShell (it eats native crashes silently).

#### 6c. Package as installable pack — but DO NOT install

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_package.py \
  --out .local/voice_runs/pack_speaker_00 \
  --name "speaker_00_local" \
  --language fi \
  --tier reduced_lora \
  --tier-reason "10-30 min source per speaker (reduced_lora auto-tier)" \
  --total-source-minutes 22 \
  --sample .local/voice_runs/dataset_speaker_00/wavs/0000.wav \
  --adapter .local/voice_runs/lora_speaker_00/adapter
```

Required flags (per `voice_pack_package.py --help`):

- `--out` — pack root directory (in `.local/`).
- `--name` — display name. Use a generic per-speaker label, never the
  cloned person's real name (repo-hygiene rule).
- `--tier` — `full_lora` / `reduced_lora` / `few_shot`. Match the tier
  the train script auto-selected (look in
  `<run>/config.json` for `reduced_mode`, or read `<run>/training.log`).
- `--tier-reason` — short string that says why this tier; surfaces in
  pack metadata.
- `--total-source-minutes` — integer minutes from `speakers.yaml`'s
  `total_seconds / 60`.
- `--sample` — one of the per-clip WAVs from the dataset, used as a
  preview sample inside the pack.
- `--adapter` — required for `full_lora` / `reduced_lora`. Either the
  PEFT save directory (`.local/voice_runs/lora_speaker_00/adapter`) or
  the `.safetensors` / `.bin` file inside it.
- `--reference` — required ONLY for `few_shot` tier; path to the
  picked ref WAV.

The pack stays in `.local/`. **Never copy it to
`~/.audiobookmaker/voice_packs/`.** The Voice dropdown in the GUI
must not surface copyright-derived packs (per
`feedback_no_installing_copyright_derived_packs.md` memory).

### Step 7 — synthesize a validation sentence

Use a content-neutral Finnish sentence — Kalevala, weather-report,
public-domain literature, or the user's own writing. **Never use
copyrighted, defamatory, hate-speech, or sexual content** in the
cloned person's voice without their explicit consent for that exact
content; this is a hard line that doesn't move regardless of how the
user frames the request.

```bash
.venv-chatterbox/Scripts/python.exe scripts/generate_chatterbox_audiobook.py \
  --text-file .local/voice_runs/test_text.txt \
  --out .local/voice_runs/synth_speaker_00 \
  --ref-audio .local/voice_runs/refs_ecapa/SPEAKER_00.wav \
  --voice-pack .local/voice_runs/pack_speaker_00 \
  --language fi --device cuda
```

The `--voice-pack` flag loads the LoRA adapter on top of the base +
T3 stack. That's what makes cross-gender cloning work. Without
`--voice-pack` you're back to few-shot — symptom: target gender not
preserved.

### Step 8 — present + ask for ear-check

Surface to the user:

- The detected speaker count and per-speaker totals (multi-speaker
  visibility — addresses the historical UX gap).
- A short transcript snippet per speaker so they can confirm the
  labels by eye before listening.
- The synthesized sample WAV/MP3 paths for ear-check.

If the user reports the synth is wrong (wrong gender, wrong person,
muddy), the root cause is almost always one of:

1. **Bad ref-clip pick** (most common, observed repeatedly). The
   picker's heuristic doesn't prevent landing on a clip where the
   speaker was emphasizing / whispering / deep-voiced. Re-synthesize
   with `SPEAKER_NN_alt1.wav` through `_alt4.wav` (the alternate
   candidates Step 4 wrote out) and pick the one whose synth output
   matches expected gender. This is faster than retraining anything.
2. Diarization labels were dirty → re-run with `--diarizer ecapa`,
   re-validate transcripts, re-pick refs.
3. Source was too short for LoRA convergence → reduced_lora's clamp
   (rank=8, 2 epochs) is intentionally conservative; for cross-
   gender targets push to full_lora settings (`--lora-rank 32
   --lora-alpha 32 --epochs 15 --early-stopping-patience 5`) even
   on sub-30-min sources. Loss should drop below ~1.0 on a fully
   converged run; > 4.0 means under-fit.
4. Training under-fit → check `training.log` for early-stopping; if
   the run stopped at epoch 1-2, increase `--early-stopping-patience`
   to 5+ and retrain.

## What this skill does NOT do

- **Does not download from URLs.** If the source is on YouTube /
  Spotify / a podcast feed, ask the user to download it themselves
  and hand you a local path. Auto-mode classifier blocks URL fetches
  for copyrighted material; that's correct behaviour.
- **Does not synthesize defamatory / hateful / sexual content in the
  cloned person's voice.** Real-person voice clones only get to say
  content the cloned person consented to or content-neutral test
  text. This is a hard line.
- **Does not install packs.** `~/.audiobookmaker/voice_packs/` is for
  the user's own / public-domain packs only.
- **Does not commit anything.** All artefacts live in `.local/`. The
  only tracked changes from voice-clone work are bug fixes /
  hardening to the pipeline code itself, never the artefacts.

## When few-shot is acceptable

- Same-gender ref + Finnish T3 finetune (Grandmom is female, so
  female targets work passably).
- Source < 10 min per speaker (no LoRA tier qualification).
- Quick diagnostic / validation runs before committing to LoRA.

For these cases, skip steps 6a-6c and just synthesize with
`--ref-audio` pointing at the picked ref clip. Document the limitation
clearly when reporting to the user.

## Recovery / debugging

- **Synthesis sounds male regardless of ref:** missing `--voice-pack`,
  or LoRA didn't train successfully. Check `lora_*.stdout.log` for
  early-stopping or OOM.
- **Two refs sound the same:** diarization conflation. Re-run with
  `--diarizer ecapa`, re-validate, re-pick.
- **`exit=127` from analyze CLI:** the long-source native crash. Use
  the chunked orchestrator (`run_chunked_analyze`), not the single-
  shot CLI directly.
- **`exit=-1073740791` (`0xC0000409`):** same crash, surfaced under
  PowerShell instead of bash. Same fix.
- **OS freeze during work:** you ran two ML subprocesses concurrently.
  Stop everything via `TaskStop`, wait for memory to settle, restart
  with strict serial discipline.
