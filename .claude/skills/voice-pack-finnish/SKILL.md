---
name: voice-pack-finnish
description: Build a Finnish voice pack from a source audio file end-to-end — chunked analyze with ECAPA diarization, transcript validation per speaker, branching to LoRA training (long sources) or few-shot ref-clip packaging (short sources), and ear-check by synth. Use whenever the user says "copy the voices", "extract voices from this clip", "make a voice pack from this audio", "I have a short Finnish clip", or hands you a Finnish podcast / interview / audiobook to mimic. Encodes the empirically-validated Finnish pipeline. CRITICAL — Finnish source only (ASR + finetune model are Finnish-specific); for English / other languages this skill does not apply. Never assume single-speaker, never trust pyannote labels without validation, never install copyright-derived packs to ~/.audiobookmaker.
---

# voice-pack-finnish

Build a Finnish voice pack from a local audio source. Output is per-speaker
reference clips and, when the source is long enough per speaker, a LoRA
adapter you can synthesize against. One skill covers both the short-clip
(few-shot) and long-source (LoRA) paths because Steps 1-4 are identical
and only the packaging branch differs.

## Tier branch — read this first

After Step 1 (`ffprobe` for duration), pick the path by **per-speaker
total minutes** (read from `speakers.yaml` after Step 2):

| Per-speaker total | Tier            | Path                                |
|-------------------|-----------------|-------------------------------------|
| < 1 min           | `skip`          | Not enough audio — abort / get more |
| 1-10 min          | `few_shot`      | Steps 1-5 → **few-shot branch**     |
| 10-30 min         | `reduced_lora`  | Steps 1-7 (LoRA, reduced rank)      |
| ≥ 30 min          | `full_lora`     | Steps 1-7 (LoRA, full rank)         |

Cross-gender targets push toward LoRA — few-shot ref-clip packaging cannot
escape the Finnish T3 finetune's single-voice basin on the gender axis.
If the source is over five minutes AND the user wants a cross-gender pack,
LoRA is required regardless of the table.

Sources under five minutes total skip Step 2's tier table entirely —
they're always `few_shot` because dataset export can't produce enough
utterances for a stable train.

## Why this skill exists

Finnish voice packing has empirically observed failure modes the naive
path keeps tripping over:

1. **Gender transfer is limited under few-shot.** The Finnish T3 finetune
   in `Finnish-NLP/Chatterbox-Finnish` is trained on a single voice
   (Grandmom). Few-shot ref-clip packaging lands all outputs near that
   basin — different refs produce different voices, but cross-gender
   results do not preserve the target's gender. Observed 2026-05-10: a
   female ref clip and a male ref clip both produced male-sounding
   output. The fix is to train a LoRA adapter per speaker, which shifts
   the model's latent space toward the actual target voice.
2. **Pyannote conflates similar-timbre Finnish speakers.** Two adult
   Finnish voices in the same register get split into 2 labels by
   `pyannote/speaker-diarization-3.1`, but the per-chunk labels are
   mixed: chunks that are actually speaker A leak into label B and
   vice versa. Picking a "best" ref clip from a wrong-labelled chunk
   produces a pack of the wrong speaker. The fix is to use the
   ECAPA-TDNN backend (`--diarizer ecapa`).
3. **Long-source native crash.** `voice_pack_analyze` crashes at
   C-level (`STATUS_STACK_BUFFER_OVERRUN`, exit `0xC0000409` / `127`)
   on inputs longer than ~5-10 minutes. The crash is in faster-whisper
   ASR, not the diarizer. The fix is to always go through the chunked
   orchestrator (`run_chunked_analyze`) — it slices the source,
   analyses each chunk as a child subprocess, and falls back to CPU
   on the rare per-chunk crash.
4. **Wrong tier on short clips.** Sessions reach for the heavy LoRA
   pipeline on 90-second clips and either crash at the dataset export
   step (too few utterances) or produce a low-quality adapter that
   sounds worse than a bare reference clip. The fix is to take the
   `few_shot` branch unconditionally for sources under five minutes.

This skill encodes the workaround for all four.

## Hard rules — read before every step

- **Finnish source only.** The chunked analyzer's ASR and the ear-check
  synth model both assume Finnish. Running this skill on English or
  other-language audio produces garbage transcripts and a useless pack.
  If the user hands you non-Finnish audio, stop and surface that — there
  is no English equivalent skill (yet); the language-agnostic parts
  (multi-speaker default, transcript validation, picker, no-install rule)
  are encoded here, but the language-specific flags are not.
- **Default to multi-speaker.** Audio sources are typically podcasts,
  interviews, or conversations — single-speaker is the exception, not
  the rule. Always run diarization. Always check the speaker count.
- **All artefacts live in `.local/`** (gitignored). Never commit any
  source audio, transcript, ref clip, voice pack, or synth output.
  Never write the target person's real name into any tracked file —
  that includes commit messages, PR text, doc files, code comments, and
  test fixtures. The repo's public history must not reveal which
  copyrighted source was used.
- **Never install copyright-derived packs into `~/.audiobookmaker/`.**
  Per the `feedback_no_installing_copyright_derived_packs.md` memory,
  voice packs built from copyrighted audio stay in `.local/` only —
  never registered into the GUI's Voice dropdown.
- **One voice-pack ML subprocess at a time** (per CLAUDE.md "Resource
  discipline"). Two concurrent whisper-large-v3 + pyannote stacks on
  a 12 GB GPU swap-thrash into system RAM and freeze the OS.
  Bisection / training / synth runs are strictly serial.
- **Never download from URLs.** If the source is on YouTube / Spotify /
  a podcast feed, ask the user to download it themselves and hand you a
  local path.

## Preconditions

- `.venv-chatterbox/Scripts/python.exe` exists (not `.venv` — the
  voice-pack pipeline needs the chatterbox venv).
- `ffmpeg` and `ffprobe` are on PATH.
- `~/.cache/huggingface/token` exists with a token that has accepted
  the `pyannote/speaker-diarization-3.1` license — even when using
  ECAPA, some chunked-analyze paths may still load pyannote modules
  during embedding. (Check this file before asking the user for a
  token, per `reference_hf_token_location.md` memory.)
- `nvidia-smi` shows ~10+ GB free VRAM. If `nvidia-smi` is missing or
  shows 0 CUDA devices, the pipeline still works on CPU but is ~10×
  slower — every analyze chunk will hit the CPU fallback path, and
  LoRA training will be impractical (multi-hour). If anything else is
  consuming the GPU, surface the PIDs to the user and wait for their
  call (per `feedback_never_kill_processes.md` memory).
- `.local/` directory exists at the repo root (gitignored). Create
  `.local/voice_runs/` explicitly when Step 1 tells you to.
- The user has handed you a local audio file path. Never fetch from URLs.

## Step 0 — ask the operator before any analyze run

Diarization quality depends massively on a correct speaker-count hint.
Two adult voices in similar register get split into "ghost" speakers or
merged into one when pyannote / ECAPA has to guess; passing the true
cast size avoids both failure modes. **Always ask before Step 1**:

1. **"How many distinct voices do you hear in this clip?"** Their answer
   becomes the `num_speakers=N` argument to `run_chunked_analyze` in
   Step 2. Common answers:
   - 1 → solo audiobook narrator, monologue, voicemail.
   - 2 → interview, two-host podcast, audiobook with M+F readers.
   - 3-4 → podcast with guest, panel, conversation with moderator.
   - 5+ → full-cast production; let pyannote estimate via
     `min_speakers=4, max_speakers=8` and validate by ear.
2. **"What's the gender mix?"** (e.g. *"1 female + 1 male"*, *"2 male"*,
   *"3 male + 1 female"*). Use this as a post-pick sanity check in
   Step 8: if the operator said "1 female ref expected" but the synth
   comes out male, fall back to alt candidates immediately without
   re-running training.
3. **"Anything you want me to skip?"** Some sources start with music
   intros, ad reads, or off-mic chatter. The operator may want you to
   `--ss N` past the first 30-60 s when re-encoding in Step 1.

If the operator answers "I don't know" to (1), default to 2 for
podcast-shaped sources and 1 for audiobook-shaped sources. Do NOT
proceed without an answer to (1) — diarization without a count hint
produces dirty labels on similar-timbre Finnish speakers and the rest
of the pipeline inherits the dirt.

## Step 1 — ffprobe duration, then normalize to 16 kHz mono WAV

Check duration first. The number drives the tier branch and the analyze
chunk shape.

```bash
ffprobe -v error -show_entries stream=codec_name,channels,sample_rate \
  -show_entries format=duration -of default=noprint_wrappers=1 "<source>"
```

- If duration < 300 s, you're on the **short branch** (always `few_shot`).
  Set `chunk_seconds` to the full duration in Step 2 (one chunk).
- If duration ≥ 300 s, you're on the **long branch**. Per-speaker totals
  from `speakers.yaml` will pick the tier after Step 2.

Then create the run dir and re-encode (ffmpeg does NOT auto-create
parent directories):

```bash
mkdir -p .local/voice_runs
ffmpeg -y -i "<source>" -ac 1 -ar 16000 .local/voice_runs/source.wav
```

The MP3 → WAV conversion eliminates a class of pydub-decode failures
inside the analyzer. It also bypasses path-encoding pitfalls of
non-ASCII filenames in shell argv.

## Step 2 — chunked analyze with ECAPA

Use the chunked orchestrator with `diarizer="ecapa"`. Default to
`num_speakers=2` for podcasts / interviews. Override only with operator
input. **ECAPA is the first choice on short clips, not a fallback** —
pyannote has fewer segments to work with on short clips and conflates
more aggressively.

```python
from src.voice_pack_chunked_subproc import run_chunked_analyze, ChunkedProgress
from pathlib import Path

def cb(ev: ChunkedProgress) -> None:
    print(f'[{ev.stage}] {ev.message}', flush=True)

result = run_chunked_analyze(
    wav=Path('.local/voice_runs/source.wav'),
    out_dir=Path('.local/voice_runs/run_ecapa'),
    chunk_seconds=300.0,   # full clip in one chunk for short branch
    workers=1,             # one CUDA chunk at a time — VRAM gate
    num_speakers=2,        # from operator answer in Step 0
    diarizer='ecapa',
    asr_device='cuda',
    progress_cb=cb,
)
```

Wall time on RTX 3080 Ti: ~10 min for a 1 h source with 11 chunks. Each
chunk's whisper ASR is ~40 s; one chunk in ten typically hits the native
crash and the orchestrator falls back to CPU automatically (~5 min CPU
per chunk).

Outputs in `.local/voice_runs/run_ecapa/`:

- `transcripts.jsonl` — one VoiceChunk per line, with `speaker` IDs
  reconciled across chunks via voice embedding.
- `speakers.yaml` — totals + tier per global speaker (the tier-branch
  table at the top of this file uses these numbers).
- `report.md` — human-readable summary.
- `chunks/chunk_NN/` — per-chunk artefacts kept for debugging.

## Step 3 — validate diarization by transcript

**This is the step that catches the conflation bug.** Read 3-5 random
chunks per speaker label. Confirm the text reads like a coherent voice
role:

```python
import json, random
random.seed(7)
by_spk = {}
with open('.local/voice_runs/run_ecapa/transcripts.jsonl', encoding='utf-8') as f:
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

If diarization looks bad under ECAPA, retry with `diarizer='pyannote'`.
If pyannote also fails to separate, surface the issue and offer manual
ref-segment selection from a transcript timestamp the operator picks
by ear.

If diarization looks clean, write a sidecar mapping
`.local/voice_runs/voice_labels.txt` (gitignored) — speaker-ID →
real-name only there, never in tracked code.

## Step 4 — pick reference clips per speaker, write alternates

**The reference picker's scoring (duration, position, RMS-stability)
does NOT include any acoustic gender / pitch-consistency check.** It
can pick a 12-18 s clip where the target happened to be speaking
deeply, emphatically, or whispering — and synthesis will then map that
unusual prosody to the wrong basin. Observed 2026-05-10 on a 30-min
Finnish source: picker chose 1682.0-1694.0s for SPEAKER_00 (female
target), the clip caught the speaker in an emphatic moment, all synth
attempts came out male regardless of LoRA strength. Switching to
candidate alt2 (599.0-615.5s) recovered the gender.

```python
from src.voice_pack.reference_picker import (
    pick_reference_clip, _default_audio_reader, _default_audio_writer,
)
import yaml

run_dir = Path('.local/voice_runs/run_ecapa')
src = Path('.local/voice_runs/source.wav')
out_dir = Path('.local/voice_runs/refs')
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
    # write top-5 alternates for Step 8 fallback
    for i, c in enumerate(rep.candidates[:5]):
        _default_audio_writer(src, c.start, c.end,
                              out_dir / f'{sid}_alt{i}.wav')
```

Then **read the picked chunk's transcript text** to verify it really
belongs to that speaker. The picker scores by duration / position /
RMS — it does not check the chunk's diarization label is correct.

Keep the alternates around — Step 8's synth-validation may need to fall
back to one when the primary pick produces wrong gender / wrong prosody.

## Step 5 — branch by tier

Look at `speakers.yaml` per-speaker totals (`total_seconds / 60`):

### 5a. Few-shot branch (`< 10 min` per speaker, OR source < 5 min total)

Package the ref clip directly. No training, no dataset export.

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_package.py \
  --out .local/voice_packs/speaker_00_few_shot \
  --name "speaker_00_local" \
  --language fi \
  --tier few_shot \
  --tier-reason "source under ~10 min per speaker — few-shot only" \
  --total-source-minutes <N> \
  --reference .local/voice_runs/refs/SPEAKER_00.wav
```

Generic per-speaker label (`speaker_00_local`), never the target's real
name. Repeat for each detected speaker. Pack lands in
`.local/voice_packs/<slug>/` and stays there.

Then jump to Step 8 (ear-check synth). **Few-shot synth uses
`--ref-audio` only, no `--voice-pack` adapter flag.**

### 5b. LoRA branch (`≥ 10 min` per speaker)

Continue to Step 6 (export) → Step 7 (train + package).

## Step 6 — export the dataset (LoRA only)

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_export.py \
  --transcripts .local/voice_runs/run_ecapa/transcripts.jsonl \
  --source .local/voice_runs/source.wav \
  --speaker SPEAKER_00 \
  --out .local/voice_runs/dataset_speaker_00
```

CPU-only, ~1 minute per speaker. Two exports can run in parallel if
both target different `--out` directories — they don't share GPU.

## Step 7 — train + package the LoRA pack (LoRA only)

### 7a. Train the LoRA adapter

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

For cross-gender targets, push harder: `--lora-rank 32 --lora-alpha 32
--epochs 15 --early-stopping-patience 5`. Loss should drop below ~1.0
on a fully converged run; > 4.0 means under-fit.

### 7b. Package — but DO NOT install

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_package.py \
  --out .local/voice_packs/speaker_00_lora \
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
- `--name` — display name. Generic per-speaker label, never the target's
  real name.
- `--tier` — `full_lora` / `reduced_lora` / `few_shot`. Match the tier
  the train script auto-selected (look in `<run>/config.json` for
  `reduced_mode`, or read `<run>/training.log`).
- `--tier-reason` — short string that says why this tier; surfaces in
  pack metadata.
- `--total-source-minutes` — integer minutes from `speakers.yaml`'s
  `total_seconds / 60`.
- `--sample` — one of the per-clip WAVs from the dataset, used as a
  preview sample inside the pack.
- `--adapter` — required for `full_lora` / `reduced_lora`. Either the
  PEFT save directory or the `.safetensors` / `.bin` file inside it.
- `--reference` — required ONLY for `few_shot` tier; path to the
  picked ref WAV.

The pack stays in `.local/`. **Never copy it to
`~/.audiobookmaker/voice_packs/`.** The Voice dropdown in the GUI must
not surface copyright-derived packs.

## Step 8 — synthesize a validation sentence

Use a content-neutral Finnish sentence — Kalevala, weather-report,
public-domain literature, or the user's own writing. **Never use
copyrighted, defamatory, hate-speech, or sexual content** in the
target's voice without their explicit consent for that exact content;
this is a hard line that doesn't move regardless of how the user
frames the request.

The test text must be **plain Finnish**:

- **No digits** — the picker's text-quality heuristic penalizes them
  and the T3 finetune mispronounces them ("12" might come out as
  "yy-kaks" or "twelve"). Spell numbers out: "kaksitoista".
- **No URLs / email addresses** — same reason.
- **No non-Latin scripts** — the T3 finetune wasn't trained on them.
- **Avoid English loanwords** when possible — they trigger the English
  phoneme path on a Finnish model.

Write the test text inside the run dir:

```bash
mkdir -p .local/voice_runs
cat > .local/voice_runs/test_text.txt <<'EOF'
Talttahampaan viuhunta kävi lakukahvilassa kun taiteilija astui sisään.
Asiakas tilasi kaakaon vegevaahdolla.
EOF
```

Then synthesize. The command depends on which branch you took in Step 5.

### LoRA branch synth

```bash
.venv-chatterbox/Scripts/python.exe scripts/generate_chatterbox_audiobook.py \
  --text-file .local/voice_runs/test_text.txt \
  --out .local/voice_runs/synth_speaker_00 \
  --ref-audio .local/voice_runs/refs/SPEAKER_00.wav \
  --voice-pack .local/voice_packs/speaker_00_lora \
  --language fi --device cuda
```

The `--voice-pack` flag loads the LoRA adapter on top of the base + T3
stack. That's what makes cross-gender targets work. Without
`--voice-pack` you're back to few-shot — symptom: target gender not
preserved.

### Few-shot branch synth

```bash
.venv-chatterbox/Scripts/python.exe scripts/generate_chatterbox_audiobook.py \
  --text-file .local/voice_runs/test_text.txt \
  --out .local/voice_runs/synth_speaker_00 \
  --ref-audio .local/voice_runs/refs/SPEAKER_00.wav \
  --language fi --device cuda
```

No `--voice-pack` flag. Few-shot uses only the reference clip. That is
the expected behaviour — do not treat the missing adapter as a bug.

## Step 9 — present + ask for ear-check

Surface to the user:

- The detected speaker count and per-speaker totals (multi-speaker
  visibility — addresses the historical UX gap).
- A short transcript snippet per speaker so they can confirm the labels
  by eye before listening.
- The synthesized sample WAV/MP3 paths for ear-check.

If the user reports the synth is wrong (wrong gender, wrong person,
muddy), the root cause is almost always one of:

1. **Bad ref-clip pick** (most common). The picker's heuristic doesn't
   prevent landing on a clip where the speaker was emphasizing /
   whispering / deep-voiced. Re-synthesize with `SPEAKER_NN_alt1.wav`
   through `_alt4.wav` (the alternate candidates Step 4 wrote out) and
   pick the one whose synth output matches expected gender. This is
   faster than retraining anything.
2. Diarization labels were dirty → re-run with `diarizer='ecapa'` (or
   try `pyannote` if ECAPA was already used), re-validate transcripts,
   re-pick refs.
3. Source was too short for LoRA convergence → reduced_lora's clamp
   (rank=8, 2 epochs) is intentionally conservative; for cross-gender
   targets push to full_lora settings even on sub-30-min sources.
4. Training under-fit → check `training.log` for early-stopping; if the
   run stopped at epoch 1-2, increase `--early-stopping-patience` to 5+
   and retrain.

## When few-shot is "good enough"

- Same-gender ref + Finnish T3 finetune (Grandmom is female, so female
  targets work passably).
- Source < 10 min per speaker (no LoRA tier qualification).
- Quick diagnostic / validation runs before committing to LoRA.

For these cases, the few-shot branch is the correct answer — don't
escalate to LoRA. Document the same-gender / single-voice-basin
limitation clearly when reporting to the user.

## Things NOT to do

- **Do not run LoRA training on a sub-five-minute source.** The dataset
  export will produce too few utterances and training will collapse or
  produce noise. Few-shot is the correct tier at this length.
- **Do not skip diarization** because the source "sounds like one
  person". Multi-speaker is the default at any length. Always check.
- **Do not synthesize defamatory / hateful / sexual content** in the
  target's voice. Real-person voice packs only get to say content the
  target consented to or content-neutral test text. This is a hard line.
- **Do not install packs** into `~/.audiobookmaker/voice_packs/` if the
  source is copyrighted. Packs stay in `.local/`.
- **Do not commit anything** from `.local/`. No source audio, no ref
  clips, no packs, no synth output.
- **Do not write the target's real name** in any filename, commit
  message, PR body, or doc file.
- **Do not run synth concurrently** with the analyze step. Serial only
  (CLAUDE.md resource discipline — one heavy ML subprocess at a time).
- **Do not download from URLs.** Hand local paths only.
- **Do not run this skill on non-Finnish audio.** No English / Swedish /
  other-language equivalent exists yet — the ASR step and ear-check
  model are both Finnish-specific.

## Recovery / debugging

- **Synthesis sounds male regardless of ref:** missing `--voice-pack`
  on the LoRA branch, or LoRA didn't train successfully. Check
  `lora_*.stdout.log` for early-stopping or OOM.
- **Two refs sound the same:** diarization conflation. Re-run with the
  other diarizer (ECAPA ↔ pyannote), re-validate, re-pick.
- **`exit=127` from analyze CLI:** the long-source native crash. Use
  the chunked orchestrator (`run_chunked_analyze`), not the single-shot
  CLI directly.
- **`exit=-1073740791` (`0xC0000409`):** same crash, surfaced under
  PowerShell instead of bash. Same fix.
- **OS freeze during work:** you ran two ML subprocesses concurrently.
  Stop everything, wait for memory to settle, restart with strict
  serial discipline.
