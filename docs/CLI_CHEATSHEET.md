# AudiobookMaker — clone a voice and make an audiobook

A fresh `git clone` ships with one voice: **Isoäiti** (Finnish) / **Grandmom**
(English). This walks you from clone to audiobook in any other voice in three
steps.

You need a Windows machine with an NVIDIA GPU (6 GB VRAM+) and a CUDA 12 driver.
About 15 GB of disk for the one-time model download.

---

## Step 1 — Install

```powershell
git clone https://github.com/MikkoNumminen/AudiobookMaker
cd AudiobookMaker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
audiobookmaker-cli engines install chatterbox_fi
```

> **The CLI command is `audiobookmaker-cli` (with the hyphen).** The
> installed Windows app ships an `AudiobookMaker.exe` that, on the case-
> insensitive Windows filesystem, can shadow a bare `audiobookmaker.exe`
> console script and silently launch the GUI when the user means the CLI.
> The hyphenated `audiobookmaker-cli` is immune to that collision, so the
> command in this cheatsheet always lands on the CLI regardless of whether
> the GUI is also installed on the machine.

`engines install chatterbox_fi` is the long one — it downloads the Chatterbox
model (~15 GB) and sets up the GPU venv. It refuses with a clear error if you
don't have NVIDIA + CUDA 12.

You can already make an audiobook with the bundled voice:

```powershell
audiobookmaker-cli convert mybook.pdf --engine chatterbox_fi --language fi
```

`--language fi` = Isoäiti. `--language en` = Grandmom. PDF, EPUB, and TXT all
work as input.

---

## Step 2 — Get a voice

Pick **ONE** of these two paths.

### Path A — Someone gave you a voice pack folder

```powershell
audiobookmaker-cli packs import path\to\the\pack
audiobookmaker-cli packs list
```

`packs list` prints the name (slug) of the pack you just installed. Use that
name in step 3.

### Path B — You have an audio recording, clone it

You need any audio file with ~15 seconds of clean speech from one person.
Trim and normalize:

```powershell
ffmpeg -y -i myvoice.mp3 -ss 5 -t 15 -ac 1 -ar 16000 myvoice_clip.wav
```

(`-ss 5 -t 15` = take 15 seconds starting 5 seconds in. Adjust if the start
of your file has noise.)

Package it as a few-shot voice pack:

```powershell
.venv-chatterbox\Scripts\python.exe scripts\voice_pack_package.py `
  --out .local\voice_packs\myvoice `
  --name myvoice `
  --language fi `
  --tier few_shot `
  --tier-reason "personal voice clip" `
  --total-source-minutes 0.25 `
  --sample myvoice_clip.wav `
  --reference myvoice_clip.wav
```

Install it:

```powershell
audiobookmaker-cli packs import .local\voice_packs\myvoice
```

Now `audiobookmaker-cli packs list` shows `myvoice` alongside the bundled voices.

---

## Step 3 — Make an audiobook with that voice

```powershell
audiobookmaker-cli convert mybook.pdf --engine chatterbox_fi --language fi --voice-pack myvoice
```

Works with PDF, EPUB, TXT. The final MP3 path is printed when synthesis
finishes.

For English, use `--language en` instead. The voice clone carries across
both languages.

---

## Step 4 — Tune the run (optional)

These flags work on `convert`, `sample`, and `preview`.

**Speed.** Talk faster or slower without re-recording.
```powershell
audiobookmaker-cli convert mybook.pdf --speed fast    # +25%
audiobookmaker-cli convert mybook.pdf --speed slow    # -25%
```
Keywords: `slow`, `normal`, `fast`, `xfast`. Engines that don't support
rate (Piper, Chatterbox) ignore the flag.

**Voice style** (engines that support free-text descriptions, e.g.
VoxCPM2). Ignored by Edge-TTS and Chatterbox.
```powershell
audiobookmaker-cli convert mybook.pdf --voice-description "a calm narrator"
```

**Per-chapter output** (one MP3 per chapter). Edge-TTS only today.
```powershell
audiobookmaker-cli convert mybook.epub --engine edge --output-mode per-chapter --output .\chapters\
ls .\chapters\
# 01_Foreword.mp3, 02_Chapter_1.mp3, 03_Chapter_2.mp3, ...
```

**Resume vs fresh start.** Default is to overwrite the output file and
reuse cached chunks. To skip if the output already exists (handy in
batch loops), or start clean:
```powershell
audiobookmaker-cli convert mybook.pdf --overwrite skip    # exit 0 if output exists
audiobookmaker-cli convert mybook.pdf --overwrite fresh   # wipe chunk cache first
```

**Chunk size** (Chatterbox only). The Chatterbox engine splits text
into ~300-character chunks by default (the upstream fluency sweet
spot). For short inputs where you want a single autoregressive run
with no chunk boundaries — handy when you're hitting boundary
hallucinations — raise it so the whole text fits in one chunk:
```powershell
audiobookmaker-cli convert short.txt --engine chatterbox_fi --chunk-chars 500
```
Edge-TTS and Piper ignore this flag. See
[english_grandmom.md](english_grandmom.md) for context on when this
helps and when it doesn't.

**Pipe text or files in.** A `-` in place of the input means "read
stdin." Binary inputs (`pdf`/`epub`) need `--input-format`:
```powershell
type mybook.txt | audiobookmaker-cli convert - --input-format txt
curl -s https://example.com/poem.txt | audiobookmaker-cli preview -
```

**Watch the run.** Standard verbosity / log-level controls at the root:
```powershell
audiobookmaker-cli -v convert mybook.pdf            # INFO
audiobookmaker-cli -vv convert mybook.pdf           # DEBUG
audiobookmaker-cli --log-level debug convert ...    # explicit
```

**Short forms.** `c` for `convert`, `s` for `sample`, `p` for `preview`;
`-q` for `--quiet`, `-j` for `--json`:
```powershell
audiobookmaker-cli c mybook.pdf -q
audiobookmaker-cli c mybook.pdf -j | jq -r 'select(.kind=="done") | .output_path'
```

---

## Step 5 — Sanity-check + scripting

**Before you start a long run, see what would happen:**
```powershell
audiobookmaker-cli convert mybook.pdf --dry-run --json
```
Emits one JSON line with the resolved engine, voice, output path,
output-mode, rate, and so on — no synthesis.

**Disk-space preflight.** `convert` aborts with exit code 2 if the
output drive doesn't have room (matches what the GUI does before
synthesis). Skip it with `--dry-run`.

**JSON for scripts.** Each subcommand documents its own `--json` shape
under `--help`; `convert`/`sample`/`preview` emit one ProgressEvent per
line (chunk, chapter_done, done, error, …), list commands emit one
domain object per line.

**Filing a bug.** Pre-fills the title/body with version + OS + engine:
```powershell
audiobookmaker-cli report-bug             # opens the URL in your browser
audiobookmaker-cli report-bug --print     # just prints the URL
```

---

That's the whole route.
