# AudiobookMaker — Chatterbox cheatsheet

Chatterbox engine + cloned voices, end to end. Four steps from bare machine to
a custom-voice Finnish (or English) audiobook.

## Prerequisites

- **GPU:** NVIDIA with 6 GB VRAM or more. CUDA 12-compatible driver required.
- **OS:** Windows 10 or 11 (full path). macOS without NVIDIA cannot run Chatterbox.
- **Disk:** ~15 GB free for the initial model download.

## Step 1 — Install

Source install (developers and power users):

```powershell
git clone https://github.com/MikkoNumminen/AudiobookMaker
cd AudiobookMaker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
audiobookmaker doctor
audiobookmaker engines install chatterbox_fi
```

`engines install` checks for NVIDIA + CUDA 12 and refuses with a clear error
if either is missing. Fix the driver before retrying.

A standalone Windows zip (no Python needed) is planned for a future release.

## Step 2 — First audiobook with Isoäiti / Grandmom

Isoäiti is the bundled Finnish voice. The same voice id (`grandmom`) works for
Finnish and English — `--language` selects the speech model path.

```powershell
# Finnish (Isoäiti)
audiobookmaker convert book.pdf --engine chatterbox_fi --language fi

# English (Grandmom)
audiobookmaker convert book.pdf --engine chatterbox_fi --language en
```

Works with PDF, EPUB, and TXT. Output lands in `.local/audiobooks/` (dev mode)
or next to the installed .exe (frozen mode). The final path is printed when
synthesis finishes.

## Step 3 — Clone a voice from an audio file

Pick the right path based on how much source audio you have:

| Source length | Tier | What happens |
|---------------|------|-------------|
| Under 10 min | `few_shot` | Ref-clip only, no training |
| 10–30 min | `reduced_lora` | Short LoRA fine-tune |
| 30 min+ | `full_lora` | Full LoRA fine-tune |

### Steps common to all tiers

**1. Normalize to 16 kHz mono WAV**

```powershell
ffmpeg -y -i source.mp3 -ac 1 -ar 16000 .local\voice_runs\source.wav
```

**2. Analyze (ASR + diarization)**

```powershell
.venv-chatterbox\Scripts\python.exe scripts\voice_pack_analyze.py `
  --input .local\voice_runs\source.wav `
  --out .local\voice_runs\analysis\ `
  --diarizer ecapa
```

Use `--diarizer ecapa` unless you have an HF_TOKEN and the pyannote license
accepted. ECAPA needs no token and rescues runs where pyannote conflates
similar-timbre speakers.

**3. Validate by transcript**

Open `.local\voice_runs\analysis\transcripts.jsonl` and confirm that
`SPEAKER_00`, `SPEAKER_01`, etc. match the expected speakers. If two labels
hold the same person's lines, re-run analyze with `--diarizer pyannote`
(requires HF_TOKEN) or pick reference clips manually.

---

### `full_lora` / `reduced_lora` path (10 min+)

**4. Export per-speaker dataset**

```powershell
.venv-chatterbox\Scripts\python.exe scripts\voice_pack_export.py `
  --transcripts .local\voice_runs\analysis\transcripts.jsonl `
  --source .local\voice_runs\source.wav `
  --speaker SPEAKER_00 `
  --out .local\voice_runs\dataset\
```

**5. Train LoRA**

```powershell
.venv-chatterbox\Scripts\python.exe scripts\voice_pack_train.py `
  --manifest .local\voice_runs\dataset\manifest.json `
  --out .local\voice_runs\lora\ `
  --language fi `
  --mixed-precision fp16
```

Use `--language en` for an English voice pack.

**6. Package**

```powershell
.venv-chatterbox\Scripts\python.exe scripts\voice_pack_package.py `
  --out .local\voice_packs\my_voice `
  --name "my_voice" `
  --language fi `
  --tier full_lora `
  --tier-reason "30+ min source" `
  --total-source-minutes 35 `
  --sample .local\voice_runs\dataset\wavs\0000.wav `
  --adapter .local\voice_runs\lora\adapter
```

Adjust `--tier` and `--total-source-minutes` to match your source.
`--adapter` accepts either the `.safetensors` file or the PEFT save directory.

---

### `few_shot` path (under ~10 min)

**4. Package directly with a reference clip**

```powershell
.venv-chatterbox\Scripts\python.exe scripts\voice_pack_package.py `
  --out .local\voice_packs\my_voice_short `
  --name "my_voice_short" `
  --language fi `
  --tier few_shot `
  --tier-reason "under 10 min — ref-clip only" `
  --total-source-minutes 4 `
  --reference .local\voice_runs\analysis\refs\SPEAKER_00.wav
```

**Tier spelling:** `few_shot` (underscore), not `few-shot`.

For an interactive walkthrough, invoke the `voice-clone-finnish` skill
(long source / LoRA) or `voice-pack-from-audio-short` (short clip).

---

## Step 4 — Use the cloned voice for a new audiobook

```powershell
audiobookmaker packs import .local\voice_packs\my_voice
audiobookmaker packs list          # find the slug it landed under
audiobookmaker convert book2.epub `
  --engine chatterbox_fi `
  --language fi `
  --voice-pack my_voice
```

The pack installs to `~\.audiobookmaker\voice_packs\`. The desktop GUI sees
it automatically — no separate import needed.

## When something goes wrong

```powershell
audiobookmaker doctor                        # show everything that's wrong
audiobookmaker engines check chatterbox_fi   # exit 0 = ok, exit 2 = not installed
audiobookmaker packs list                    # list what's installed
```

If you see "Chatterbox engine is not installed":

```powershell
audiobookmaker engines install chatterbox_fi
```

If the cloned voice sounds wrong, the most common cause is diarization putting
the wrong speaker in a ref clip. Re-run step 2 with the other `--diarizer`,
or pick a clip by hand from `.local\voice_runs\analysis\refs\`.

## Where files go

| Path | Purpose |
|------|---------|
| `~\.audiobookmaker\config.json` | Saved engine / voice defaults |
| `~\.audiobookmaker\voice_packs\` | Installed packs (GUI + CLI share this) |
| `.local\voice_runs\` | Analyze / train artifacts (gitignored) |
| `.local\voice_packs\` | Packaged but not yet imported packs |
| `.local\audiobooks\` | Converted MP3 output (dev mode) |

Full CLI reference: [docs/CLI.md](CLI.md)
