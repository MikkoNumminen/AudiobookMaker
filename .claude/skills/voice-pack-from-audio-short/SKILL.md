---
name: voice-pack-from-audio-short
description: Few-shot voice cloning for audio sources under five minutes — ffprobe duration check, ECAPA diarization, per-speaker transcript validation, few-shot packaging, and ear-check synth. Use whenever the user says "copy this voice quickly", "I have a short clip", "make a fast pack", or hands over an audio source under five minutes with a clone request. For sources over five minutes, use the voice-clone-finnish skill instead (LoRA training path).
---

# voice-pack-from-audio-short

Package a few-shot voice pack from a short audio source (under five minutes).
Short clips have too little data for LoRA training — the adapter either crashes
during export or converges to noise. The few-shot path is correct here and
this skill makes it the obvious default.

## Why this skill exists

Without a documented threshold and path, sessions reach for the heavy LoRA
pipeline from `voice-clone-finnish` on 90-second clips and either crash at
the dataset export step (too few utterances) or produce a low-quality adapter
that sounds worse than a bare reference clip. Three failure modes recur:

1. **Wrong tier.** A session picks `reduced_lora` on a 2-minute source
   because the tier table in `voice-clone-finnish` covers it. The export
   runs, the train collapses at epoch 1, the result is unusable. The fix
   is to skip training entirely and go straight to `few-shot` packaging.

2. **Single-speaker assumption.** Short clips feel like monologues even
   when they are not. Multi-speaker default applies at any source length
   (CLAUDE.md "Voice-extraction default" rule). Always diarize.

3. **Pyannote conflation on short clips.** Short clips give the diarizer
   fewer segments to cluster, which makes conflation more likely, not less.
   ECAPA is the correct first choice here, not the fallback.

## Preconditions

- `.venv-chatterbox/Scripts/python.exe` exists.
- `ffmpeg` and `ffprobe` are on PATH.
- `~/.cache/huggingface/token` has a token with the
  `pyannote/speaker-diarization-3.1` license accepted (the chunked
  analyzer may load pyannote modules even when `--diarizer ecapa` is set).
- `nvidia-smi` shows available VRAM. CPU fallback works but is slow.
- The user has given you a local file path. Never fetch from URLs.

## Hard rules — read before every step

- **Default to multi-speaker.** Always run diarization. Ask the operator
  how many speakers they hear before starting Step 2.
- **Tier is always `few-shot`.** Never use `reduced_lora` or `full_lora`
  on a sub-five-minute source. If the source turns out to be over five
  minutes, stop and hand off to `voice-clone-finnish`.
- **All artefacts live in `.local/`.** Never commit source audio,
  transcripts, ref clips, or pack files. Never write the cloned person's
  real name into any tracked file — commits, docs, tests, PR bodies.
- **Never install copyright-derived packs.** Packs trained on copyrighted
  audio stay in `.local/voice_packs/` only. Do not register them in
  `~/.audiobookmaker/voice_packs/` or the GUI Voice dropdown
  (`feedback_no_installing_copyright_derived_packs.md`).
- **One ML subprocess at a time.** Whisper + ECAPA on a 12 GB GPU fills
  it. Do not run synth concurrently with analyze.

## Step 0 — ask the operator before any analysis

Short clips make speaker count even more critical than on long sources.
Ask before Step 1:

1. "How many distinct voices do you hear in this clip?" Use the answer as
   `num_speakers=N` in Step 2. If they answer "I don't know", default to 2.
2. "What is the gender mix?" Use this as a post-pick sanity check in Step 5.
3. "Anything to skip?" Some short clips open with music or off-mic sound.

Do not proceed without an answer to question 1.

## Step 1 — ffprobe duration and normalize to 16 kHz mono WAV

Confirm the source is under five minutes. If it is over five minutes, stop
and point the user at the `voice-clone-finnish` skill.

```bash
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "<source>"
```

If duration is under 300 s, proceed. Then normalize:

```bash
mkdir -p .local/voice_runs
ffmpeg -y -i "<source>" -ac 1 -ar 16000 .local/voice_runs/source_short.wav
```

## Step 2 — analyze with ECAPA diarizer

Use the chunked orchestrator even for short clips — it handles the
faster-whisper native crash that can still occur on some short inputs.
Set `chunk_seconds` to the full duration so the clip is one chunk, but
keep `workers=1` for the VRAM gate.

```python
from src.voice_pack_chunked_subproc import run_chunked_analyze, ChunkedProgress
from pathlib import Path

def cb(ev: ChunkedProgress) -> None:
    print(f'[{ev.stage}] {ev.message}', flush=True)

result = run_chunked_analyze(
    wav=Path('.local/voice_runs/source_short.wav'),
    out_dir=Path('.local/voice_runs/short_ecapa'),
    chunk_seconds=300.0,   # whole clip in one chunk
    workers=1,
    num_speakers=2,        # from operator answer in Step 0
    diarizer='ecapa',      # ECAPA first — not a fallback on short clips
    asr_device='cuda',
    progress_cb=cb,
)
```

Outputs in `.local/voice_runs/short_ecapa/`:
`transcripts.jsonl`, `speakers.yaml`, `report.md`.

## Step 3 — validate diarization by transcript

Read random chunks per speaker label and confirm the text is coherent
and consistent within each label.

```python
import json, random
random.seed(7)
by_spk = {}
with open('.local/voice_runs/short_ecapa/transcripts.jsonl', encoding='utf-8') as f:
    for line in f:
        c = json.loads(line)
        by_spk.setdefault(c['speaker'], []).append(c)
for spk in sorted(by_spk):
    chunks = by_spk[spk]
    print(f'{spk} ({len(chunks)} chunks):')
    for c in random.sample(chunks, min(3, len(chunks))):
        print(f"  [{c['start']:.1f}s] {c['text'][:120]}")
```

Bad signs: one label contains both questions and answers; two labels both
read as the same voice role. If labels are bad, retry with
`diarizer='pyannote'`. If pyannote also fails, offer manual ref-segment
selection from a transcript timestamp the operator picks by ear.

## Step 4 — pick and validate reference clips

Pick a ref clip per speaker and write the top-5 alternates to disk.

```python
from src.voice_pack.reference_picker import (
    pick_reference_clip, _default_audio_reader, _default_audio_writer,
)
import yaml

run_dir = Path('.local/voice_runs/short_ecapa')
src = Path('.local/voice_runs/source_short.wav')
refs_dir = Path('.local/voice_runs/refs_short')
refs_dir.mkdir(parents=True, exist_ok=True)

speakers = yaml.safe_load((run_dir / 'speakers.yaml').read_text(encoding='utf-8'))
for spk in speakers:
    sid = spk['speaker']
    rep = pick_reference_clip(
        transcripts=run_dir / 'transcripts.jsonl',
        speaker_id=sid, wav_source=src, out_path=refs_dir / f'{sid}.wav',
        audio_reader=_default_audio_reader, audio_writer=_default_audio_writer,
        top_k=5,
    )
    print(f'{sid}: {rep.selected_start:.1f}-{rep.selected_end:.1f}s')
    for i, c in enumerate(rep.candidates[:5]):
        _default_audio_writer(src, c.start, c.end, refs_dir / f'{sid}_alt{i}.wav')
```

After picking, read the chunk's transcript text in `transcripts.jsonl` and
confirm it belongs to the expected speaker role. The picker scores by
duration / position / RMS — it does not check diarization label accuracy.
Keep the five alternates; Step 5 may need one.

## Step 5 — package as few-shot pack

One pack per speaker. Use generic labels (`speaker_00_local`,
`speaker_01_local`) — never the cloned person's real name.

```bash
.venv-chatterbox/Scripts/python.exe scripts/voice_pack_package.py \
  --out .local/voice_packs/speaker_00_short \
  --name "speaker_00_local" \
  --language fi \
  --tier few_shot \
  --tier-reason "source under 5 min — few-shot only, no LoRA" \
  --total-source-minutes <N> \
  --reference .local/voice_runs/refs_short/SPEAKER_00.wav
```

Replace `<N>` with the per-speaker total from `speakers.yaml`
(`total_seconds / 60`, rounded). Repeat for each detected speaker.

The pack goes to `.local/voice_packs/<slug>/`. It stays there.

## Step 6 — ear-check synth

Use a content-neutral Finnish sentence (Kalevala excerpt, weather-report
text, or user-supplied text). No digits, no URLs, no English loanwords —
they trip the Finnish T3 finetune's phoneme path.

Write the test text inside the run dir:

```bash
cat > .local/voice_runs/test_short.txt <<'EOF'
Kanerva kukkii kesällä kirkkaan sinisellä taivaalla.
Tuuli tuo terveisiä kaukaisista maista.
EOF
```

Then synthesize (30 s is sufficient for an ear check):

```bash
.venv-chatterbox/Scripts/python.exe scripts/generate_chatterbox_audiobook.py \
  --text-file .local/voice_runs/test_short.txt \
  --out .local/voice_runs/synth_speaker_00_short \
  --ref-audio .local/voice_runs/refs_short/SPEAKER_00.wav \
  --language fi --device cuda
```

Note: no `--voice-pack` flag here. Few-shot uses only the reference clip.
That is the expected behaviour — do not treat the missing adapter as a bug.

## Step 7 — present results and ask for ear check

Report to the user:
- Detected speaker count and per-speaker duration totals.
- A short transcript snippet per speaker for visual confirmation.
- The synth WAV paths for listening.

If the synth sounds wrong (wrong gender, wrong person, wrong prosody):

1. Re-synthesize with `SPEAKER_NN_alt1.wav` through `_alt4.wav` in turn.
   The picker's heuristic can land on an emphatic or whispered clip; an
   alternate often fixes it without re-running the analyzer.
2. If all alternates fail, re-run analyze with `diarizer='pyannote'` and
   validate transcripts again.
3. If the source has same-gender speakers and the T3 finetune's single-
   voice basin is pulling both clones toward the same timbre, document
   the limitation and ask the user whether they want to move to a longer
   source (which would qualify for the LoRA path in `voice-clone-finnish`).

## Things NOT to do

- **Do not run LoRA training on a sub-five-minute source.** The dataset
  export will produce too few utterances and training will collapse or
  produce noise. Few-shot is the correct tier at this length.
- **Do not skip diarization** because the source "sounds like one person".
  Multi-speaker is the default at any length. Always check.
- **Do not use pyannote as the first diarizer.** ECAPA is first on short
  clips because pyannote has fewer segments to work with and conflates
  more aggressively.
- **Do not install the pack** into `~/.audiobookmaker/voice_packs/` if
  the source is copyrighted. The pack stays in `.local/`.
- **Do not commit anything** from `.local/`. No source audio, no ref
  clips, no packs, no synth output.
- **Do not write the cloned person's real name** in any filename, commit
  message, PR body, or doc file.
- **Do not run synth concurrently** with the analyze step. Serial only
  (CLAUDE.md resource discipline — one heavy ML subprocess at a time).
- **Do not proceed past Step 1** if the source is over five minutes.
  Hand off to `voice-clone-finnish` — that skill covers the LoRA path.
