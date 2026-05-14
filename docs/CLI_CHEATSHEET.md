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

That's the whole route.
