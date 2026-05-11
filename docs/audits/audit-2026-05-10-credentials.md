# Credential & identity-leak audit — 2026-05-10

Scope: prove that the AudiobookMaker repo and the shipped Windows `.exe`
do not embed, leak, or otherwise expose personal credentials (HF_TOKEN,
Azure / OpenAI / pyannote tokens, signing certs, model weights tied to
copyrighted source audio) or load-bearing personal identity strings.

This is **not** a robustness or correctness audit — see
`audit-2026-04-23.md` for that. The two scopes are deliberately
separated so neither dilutes the other.

## Verdict

**SAFE.** No credentials shipped, no credentials in git history, no
secret-loading code path reachable from the frozen build's entry graph.
No rotation required.

Only personal-identity surface still on origin is the public string
`MikkoNumminen/AudiobookMaker` baked into `src/auto_updater.py:29` and
`src/launcher.py:629`. This is the GitHub repo the auto-updater polls
and cannot be removed without breaking the auto-update lifeline.

## Phase 0 — Scope reconnaissance

| Item                                                                                  | Tied to identity?                | Leaks via                                                                                                                                                       | Severity |
| ------------------------------------------------------------------------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| **HF_TOKEN** (pyannote-3.1 gate + large HF downloads)                                 | Yes — Hugging Face account       | `.env` at repo root (gitignored, 930 B locally). Read by `src/voice_pack/diarize.py:37` and `scripts/voice_pack_analyze.py:45`. Not reachable from frozen .exe. | LOW      |
| **pyannote token**                                                                    | Same HF token                    | Same paths as HF_TOKEN                                                                                                                                          | LOW      |
| **Microsoft / Azure tokens**                                                          | n/a                              | String not present anywhere in repo                                                                                                                             | CLEAN    |
| **OpenAI tokens**                                                                     | n/a                              | String not present anywhere in repo                                                                                                                             | CLEAN    |
| **Code-signing certs** (`.pfx`/`.p12`)                                                | n/a                              | No paths referenced; `codesign_identity=None` in both `.spec` files; README states no signing cert configured                                                   | CLEAN    |
| **Git-commit author email in frozen binary**                                          | Yes — `Mikko Numminen` git ident | `.git/` is not bundled by either `.spec`; PyInstaller only walks the entry graph. Email is visible in origin commits but not embedded in `.exe`.                | LOW      |
| **Hardcoded GitHub username URL** (`MikkoNumminen/AudiobookMaker`)                    | Yes — your GitHub username       | `src/auto_updater.py:29`, `src/launcher.py:629`. Bundled into both frozen builds. Visible to anyone running `strings` on the .exe.                              | MED      |
| **Installer publisher / copyright strings**                                           | No                               | `installer/setup.iss` uses generic `AudiobookMaker Contributors` and `AudiobookMaker` — no personal name                                                        | CLEAN    |
| **`voice_packs/`** (LoRA adapters trained on copyrighted audio)                       | Indirect                         | Directory absent locally; gitignored                                                                                                                            | CLEAN    |
| **`runs/`** (training run logs)                                                       | Indirect                         | Absent locally; gitignored                                                                                                                                      | CLEAN    |
| **`*.safetensors` / `*.pt` / `*.ckpt`** on disk                                       | No (public model weights)        | Only under `.cache/` and `.local/`, both gitignored                                                                                                             | CLEAN    |
| **`.spec datas=` / `binaries=`** pulling in secrets                                   | n/a                              | Full review of both spec files: no `.env`, no `voice_packs/`, no weight files, no `scripts/voice_pack_analyze.py`, no `src/voice_pack/diarize.py`               | CLEAN    |
| **GitHub Actions secrets at release time**                                            | n/a                              | Only `secrets.GITHUB_TOKEN` (auto-issued, repo-scoped, ephemeral). No PATs, no signing keys, no HF/Azure/OpenAI.                                                | CLEAN    |
| **`.env.example`** content                                                            | n/a                              | Line 16: `HF_TOKEN=` (empty placeholder)                                                                                                                        | CLEAN    |

## Phase 1 — Git history scan

```
git log --all -p -S "hf_"           -- .env .env.example   → empty
git log --all -p -S "HF_TOKEN"                              → matches only refer to the env var name, never a value
git log --all -p -S "AZURE"                                 → empty
git log --all -p -S "OPENAI"                                → empty
git log --all -p -S "BEGIN PRIVATE KEY"                     → empty
git log --all --full-history --diff-filter=A
    -- .env "*.pfx" "*.p12" "*.pt" "*.safetensors"          → empty (no add commits ever)
```

Defence-in-depth checks beyond the spec:

```
git log --all --pretty=format:"%H" | xargs git show
    | grep -E "hf_[A-Za-z0-9]{30,}"                         → empty (no literal token value anywhere)
git log --all -p -- .env                                    → empty (.env never tracked)
git log --all -p -- "*.pfx" "*.p12"                         → empty (no cert ever tracked)
```

Every match for the literal string `HF_TOKEN` is a reference to the
environment variable name in code, docs, tests, or UI string keys.
No commit anywhere in any branch assigns a value to `HF_TOKEN=…` other
than the empty placeholder in `.env.example`.

| Credential                       | On origin? | Action required           |
| -------------------------------- | ---------- | ------------------------- |
| HF_TOKEN / pyannote              | No         | No rotation required      |
| Azure / OpenAI / private keys    | No         | No rotation required      |
| `.env`                           | No         | None                      |
| Signing certs                    | No         | None                      |
| Model weights                    | No         | None                      |

## Phase 2 — Frozen-build leak check

### Spec review (`audiobookmaker.spec`)

`datas=` bundles ffmpeg binaries, onnxruntime / piper / pathvalidate
package data, `data/*.yaml` text-normalizer lexicons,
`scripts/generate_chatterbox_audiobook.py`, a handful of pure-Python
modules under `src/`, and assets under `assets/`. **No** `.env`, **no**
`voice_packs/`, **no** weight files, **no** `scripts/voice_pack_analyze.py`,
**no** `src/voice_pack/diarize.py`.

`hiddenimports=` lists Edge-TTS / aiohttp / Piper / onnxruntime / CTk /
tkinterdnd2 / num2words and their submodule trees. **Not present**:
`dotenv`, `python_dotenv`, `pyannote`, `pyannote.audio`, `torch`,
`transformers`, `huggingface_hub`, `voxcpm`.

`audiobookmaker_launcher.spec` goes further and explicitly excludes
`torch`, `torchaudio`, `transformers`, `chatterbox`, `silero_vad`,
`safetensors`.

The bundled `scripts/generate_chatterbox_audiobook.py` sets
`HF_HUB_DISABLE_IMPLICIT_TOKEN=1` at module top, which actively
prevents `huggingface_hub` from silently using any cached token.

### Reachability classification of every env / token read

| Hit                                                                              | Type     | Reachable from frozen `.exe`? | Credential risk |
| -------------------------------------------------------------------------------- | -------- | ----------------------------- | --------------- |
| `os.environ.get("LOCALAPPDATA" / "PROGRAMFILES" / "TEMP" / "APPDATA")`           | shell    | Yes                           | None            |
| `os.environ['PATH'] = …` in `ffmpeg_path.py`                                     | shell    | Yes                           | None            |
| `os.environ.copy()` in `launcher_bridge.py` (subprocess env)                     | shell    | Yes                           | Inherits user env only |
| `os.environ.get("CHATTERBOX_PYTHON")`                                            | shell    | Yes                           | None            |
| `HF_TOKENS_URL = "https://huggingface.co/settings/tokens"`                       | URL const | Yes                          | None            |
| `_HUGGINGFACE_MODEL_ID = "openbmb/VoxCPM2"` in `tts_voxcpm.py`                   | const    | **No** — `engine_registry.py:33` skips `tts_voxcpm` when `sys.frozen` is True | None |
| `env["HF_TOKEN"] = hf_token` in `voice_pack_subproc.py`                          | runtime  | Yes — only sets when caller (GUI modal that asked the user) explicitly passes a token | User-supplied token routed to user-spawned subprocess; no developer token involved |
| `from dotenv import load_dotenv; load_dotenv(_repo_root/".env")` in `voice_pack/diarize.py` | module-top | **No** — `voice_pack/__init__.py` only imports `pack` + `types`; nothing in the frozen entry graph imports `diarize` | None |
| `os.environ.get("HF_TOKEN")` in `voice_pack/diarize.py`                          | function | **No** — same module not reachable | None |
| `from dotenv import load_dotenv` in `scripts/voice_pack_analyze.py`              | module-top | **No** — script not bundled; runs under `.venv-chatterbox` Python in a dev / Voice-Cloner power-user install | None |
| `os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")` in `generate_chatterbox_audiobook.py` | module-top | Yes | Actively *prevents* implicit HF token use |

### Entry-point reachability closure

`src/main.py` → `app_config`, `ffmpeg_path`, `single_instance`, then
lazily `gui_unified` → `engine_registry` → `tts_edge`, `tts_piper`,
`tts_chatterbox_bridge` (the `tts_voxcpm` import is guarded by
`if not getattr(sys, "frozen", False)` and skipped in the bundle).
Lazy method-level imports inside `gui_unified` reach `gui_clone_voice`,
`gui_engine_dialog`, `engine_installer*`, `voice_pack_subproc`.

`voice_pack/__init__.py` exports only `pack` and `types`. `diarize.py`
is never imported by anything in this closure.

`python-dotenv` is not in any reachable import chain and not in
`hiddenimports`, so PyInstaller does not bundle it.

## Why the .exe cannot exfiltrate `HF_TOKEN`

1. `.env` is not bundled (gitignored, never committed, not in `datas`).
2. `python-dotenv` is not bundled (not in `hiddenimports`, not imported
   by any reachable module).
3. The only two `load_dotenv()` call sites in the codebase live in
   `src/voice_pack/diarize.py` and `scripts/voice_pack_analyze.py`,
   both unreachable from the frozen entry graph.
4. The bundled `scripts/generate_chatterbox_audiobook.py` actively
   suppresses implicit HF token use.
5. The Voice Cloner workflow prompts the **user** to paste **their
   own** HF token via a GUI modal — the developer's token is never
   embedded.
6. No code-signing cert is configured; no `.pfx`/`.p12` exists in the
   tree to leak.

## Optional defence-in-depth (not required)

None of these are needed today; record them as future paranoia layers.

1. **CI guard** after `pyinstaller audiobookmaker.spec`: fail the job if
   `dist/AudiobookMaker/_internal/.env` exists, or if a
   `python_dotenv-*.dist-info/` directory ends up under `_internal/`.
   Catches a future spec drift that accidentally bundles either.
2. **Explicit excludes**: add `'dotenv'` and `'python_dotenv'` to
   `excludes=` in both `.spec` files so PyInstaller actively refuses
   to bundle them even if a future hidden import path appears.
3. **Frozen-build guard in `voice_pack/diarize.py`**: wrap the
   module-top `load_dotenv()` call in
   `if not getattr(sys, "frozen", False):` so the import is harmless
   even if a future refactor accidentally drags `diarize.py` into the
   bundle.
