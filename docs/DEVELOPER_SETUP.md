# Developer setup — gated features

AudiobookMaker is built so the default install path needs nothing
beyond Python and ffmpeg. A few features are gated behind external
licenses or third-party credentials; this document lists which ones,
what you have to do to enable each, and which files are deliberately
absent from a fresh clone.

The maintainer's credentials are not bundled in this repo in any form
(no token, no API key, no encrypted secrets, no pre-trained adapters).
A clean checkout enables only the public features below until you add
your own `HF_TOKEN`.

## Works without credentials

- **Edge-TTS** — Microsoft cloud voices (Finnish: Noora, English:
  Jenny and others). Needs an internet connection because the
  synthesis runs on Microsoft's servers; nothing to set up locally.
- **Piper** — fully offline neural TTS. First time you pick a voice
  the app downloads a small ONNX model (~60 MB) to
  `~/.audiobookmaker/piper_voices/`; after that it works without
  internet.

## Features that require your own credentials

| Feature | What it does | What you need | License link |
|---|---|---|---|
| pyannote speaker diarization | Tells "who spoke when" across a long audio source; default backend for `scripts/voice_pack_analyze.py` and the GUI "Clone voice from file…" flow. | `HF_TOKEN` env var + accepted license on the gated repo. | [huggingface.co/pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) |
| VoxCPM2 engine | Experimental multilingual TTS with voice cloning. Hidden from the frozen GUI; available from source only. | Manual `pip install voxcpm` (not in `requirements.txt`); `HF_TOKEN` raises rate limits on the ~8 GB model download. | [huggingface.co/openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) |
| Chatterbox voice-clone reference picker | Lets you point at a `.wav`/`.mp3` and use it as the cloned voice. Dev mode only — hidden in frozen builds. | NVIDIA GPU with ≥ 8 GB VRAM, `.venv-chatterbox` with `torch` + `chatterbox` + `peft`, and a Finnish or English reference WAV. | n/a — uses public `Finnish-NLP/Chatterbox-Finnish` model. |
| voice-pack pipeline | End-to-end clones a speaker into a LoRA adapter from a source audio file: `voice_pack_analyze` → `voice_pack_export` → `voice_pack_train` → `voice_pack_package`. | Everything above, plus `HF_TOKEN` for the pyannote step. Falls back to ECAPA-TDNN with `--diarizer ecapa` when no token is available. | same as pyannote row |
| ECAPA-TDNN diarization (fallback) | Same role as pyannote — diarises long audio for the voice-pack analyse step — without the gated-license dance. | Nothing extra. Pass `--diarizer ecapa` to `voice_pack_analyze`; speechbrain weights auto-download on first use. | n/a — `speechbrain/spkrec-ecapa-voxceleb` is public. |

Quality on ECAPA is slightly worse than pyannote on similar-timbre
speakers, but the setup is much simpler — no HF account needed at all.

## Setting up HF_TOKEN

The same token works for both pyannote (gated) and VoxCPM2 / Chatterbox
(public but rate-limited without it).

1. Create a Hugging Face account at
   [huggingface.co/join](https://huggingface.co/join).
2. Visit [huggingface.co/pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   while signed in, and click **Agree and access repository**. Without
   this step the token can never load the model regardless of scope.
3. Generate a personal access token at
   [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
   The default **Read** scope is enough — do not grant Write unless
   you plan to push models from this machine.
4. Copy `.env.example` to `.env` at the repo root and paste your
   token on the `HF_TOKEN=` line. `.env` is gitignored;
   `.env.example` is tracked.
5. Verify the token is picked up:

   ```bash
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(bool(os.getenv('HF_TOKEN')))"
   ```

   Expected output: `True`. If you get `False`, double-check that the
   file is named exactly `.env` (no extension) and lives at the repo
   root. Note: if `HF_TOKEN` is also set in your OS environment
   (e.g. via shell rc or system env vars), the command prints `True`
   even when `.env` is missing — `load_dotenv()` is silently a no-op
   if there's no file to read, and `os.getenv` then falls back to the
   process environment.

Frozen `.exe` builds do **not** read `.env` — they rely on
`os.environ` only. End-user installs therefore can't accidentally
inherit a developer's token; the variable has to be set in the
Windows environment if it is needed at all.

## What does NOT ship in this repo and why

Gitignored at the repo root, deliberately absent from a fresh clone:

- **`.env`** — your `HF_TOKEN`. Privacy: a token is a credential.
  Bundling one would expose the maintainer's account to every
  downstream user.
- **`.venv/`, `.venv-*/`** — Python virtual environments. Size + OS
  portability: they are machine-specific.
- **`voice_packs/`, `runs/`** — trained LoRA voice packs and their
  training run directories. Copyright: voice packs trained on
  copyrighted audiobook narration cannot be redistributed even when
  the pipeline that produced them is open source.
- **`*.safetensors`, `*.pt`, `*.ckpt`** — model checkpoints.
  License + size: third-party model weights have their own licenses
  (sometimes non-redistributable), and bundling them would balloon
  the repo beyond GitHub's limits anyway. Each engine downloads its
  weights on first use.
- **`.local/`** — third-party source material for testing
  (audiobooks, podcasts, reference clips). Copyright: per `CLAUDE.md`,
  nothing copyrighted is ever pushed to GitHub, even small samples.
- **`out/`** — generated MP3s and synthesis logs from your dev work.
  These are your output, not the repo's.
- **`TODO.md`** — per-machine scratch list. Per `CLAUDE.md`, it is
  intentionally local-only.

A fresh clone of this repo runs Edge-TTS and Piper out of the box and
errors out cleanly on any feature that needs a credential, with a
message pointing at this document. The maintainer's keys do not live
in CI either — the release workflow only uses GitHub's default
`GITHUB_TOKEN` for publishing the release; it does **not** carry an
`HF_TOKEN` of its own. Gated features (pyannote, VoxCPM2's rate-
limited fetch) are exercised only on developer machines that have
their own token; CI test runs use mocks or skip the gated paths.
