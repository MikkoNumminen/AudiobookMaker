# Architecture

A reading guide to how AudiobookMaker fits together. Read this once at
the start of a session and you won't have to re-grep for where things
live.

## Bird's-eye view

```mermaid
flowchart LR
    User[User] --> GUI[Tkinter GUI<br/>gui_unified.py]
    GUI --> Engines{TTS engine<br/>registry}
    Engines -->|in-process| Edge[Edge-TTS<br/>tts_edge.py]
    Engines -->|in-process| Piper[Piper<br/>tts_piper.py]
    Engines -->|subprocess| CB[Chatterbox<br/>scripts/generate_<br/>chatterbox_audiobook.py]
    Edge --> FF[ffmpeg<br/>dist/ffmpeg/]
    Piper --> FF
    CB --> FF
    FF --> MP3[MP3 at<br/>install root]
    GU[GitHub Releases] -.->|auto-update poll<br/>every 5 min| GUI
```

The GUI is a single Tk window. It hands off the text + voice choice to
one of the TTS engines. Edge-TTS and Piper run in-process; Chatterbox
runs as a subprocess in its own Python 3.11 venv because of heavy ML
deps. All engines write chunks that ffmpeg stitches into a final MP3
stored next to `AudiobookMaker.exe`.

## GUI layer

`src/gui_unified.py` defines `UnifiedApp`, which inherits from two
mixins plus `customtkinter.CTk`:

```mermaid
classDiagram
    class CTk { +run() }
    class SynthMixin {
      +_start_inprocess_engine()
      +_start_chatterbox_subprocess()
      +_handle_event(ProgressEvent)
    }
    class UpdateMixin {
      +_check_update_worker()
      +_on_update_click()
      +_download_update_worker()
    }
    class UnifiedApp {
      +_build_header_bar()
      +_build_engine_bar()
      +_refresh_voice_list()
      +_on_convert_click()
      +_default_output_dir()
    }
    UnifiedApp --|> SynthMixin
    UnifiedApp --|> UpdateMixin
    UnifiedApp --|> CTk
```

Why mixins: orchestration (synthesis pump, update banner) is stateful
and ~500 lines each — keeping them on the main class would bloat
`gui_unified.py` past readable. Mixins use `typing.Protocol` to declare
the attributes they expect the host to provide, so type-checking still
works.

Further extracted pieces:
- `src/gui_engine_dialog.py` — "Asenna moottoreita…" modal view
- `src/gui_synth_mixin.py` — synthesis orchestration
- `src/gui_update_mixin.py` — auto-update banner + download

## TTS engine registry

All engines plug into a single registry in `src/tts_base.py`:

```mermaid
flowchart TD
    R[_REGISTRY<br/>src/tts_base.py] -->|register_engine| E[EdgeTTSEngine]
    R -->|register_engine| P[PiperTTSEngine]
    R -->|register_engine| V[VoxCPMTTSEngine]
    Base[TTSEngine<br/>abstract base] --- E
    Base --- P
    Base --- V
    Base -->|check_status<br/>list_voices<br/>default_voice<br/>synthesize| GUI
```

Each engine implements four methods:

| Method | Purpose |
|--------|---------|
| `check_status()` | Is this engine installed + ready? Returns `EngineStatus` |
| `list_voices(lang)` | Voices available for a given language |
| `default_voice(lang)` | Opinionated default per language |
| `synthesize(text, voice_id, out_path, …)` | Do the work |

Chatterbox is not registered — it runs as a separate process driven by
`src/launcher_bridge.py` + `scripts/generate_chatterbox_audiobook.py`
and is selected in the GUI via a hardcoded `"chatterbox_fi"` branch.
This is a deliberate split: Chatterbox needs PyTorch + CUDA + a 7 GB
model, all installed into its own venv so the main app bundle stays
~200 MB.

## Text pipeline

```mermaid
flowchart LR
    PDF[PDF] --> Parser[pdf_parser.py<br/>PyMuPDF]
    Text[Plain text] --> Chunker
    Parser --> Chunker[tts_chunking.py<br/>chapter + chunk splits]
    Chunker --> Norm[tts_normalizer_fi.py<br/>16 passes]
    Norm --> Engine[engine.synthesize]
    Engine --> Wav[chunk WAV/MP3]
    Wav --> Audio[tts_audio.py<br/>ffmpeg concat]
    Audio --> Final[book.mp3]
```

- `pdf_parser.py` — PyMuPDF, extracts chapters heuristically
- `tts_chunking.py` — splits long text at sentence boundaries under a
  length cap the engine can handle
- `tts_normalizer_fi.py` — 16 transformation passes that make Finnish
  abbreviations, numbers, case endings, and dates readable. Covered by
  400+ unit tests; see [`tts_text_normalization_cases.md`](tts_text_normalization_cases.md)
- `tts_audio.py` — thin wrapper around `pydub` + bundled ffmpeg

## Subprocess & cross-process messaging

Chatterbox synthesis is a separate process. The GUI talks to it via
`src/launcher_bridge.py`:

```mermaid
flowchart LR
    subgraph Main[Main process - AudiobookMaker.exe]
      App[UnifiedApp]
      Runner[ChatterboxRunner]
      Queue[(event queue)]
      App --> Runner
      Runner --> Queue
      Queue --> App
    end
    subgraph CB[Chatterbox venv]
      Script[generate_chatterbox_<br/>audiobook.py]
      Model[PyTorch + CUDA]
      Script --> Model
    end
    Runner -->|Popen stdout| Script
    Script -.->|stdout lines<br/>parsed to<br/>ProgressEvent| Runner
```

The subprocess emits structured lines on stdout. `ChatterboxLineParser`
in `launcher_bridge.py` turns them into `ProgressEvent` dataclasses
(chunk/chapter/setup/exit). A reader thread pumps them into a
`queue.Queue` that the GUI drains on its Tk `after()` timer. Backpressure
is handled by the queue; cancellation flows the other way via
`threading.Event`.

## Auto-update

```mermaid
sequenceDiagram
    participant App as AudiobookMaker.exe
    participant GH as GitHub Releases API
    participant Inno as Inno Setup
    App->>GH: GET /releases/latest
    GH-->>App: tag_name, assets, sha256 in body
    alt newer version
      App->>App: show Päivitä-nyt banner
      App->>GH: download installer .exe
      App->>App: verify SHA-256
      App->>App: write pending marker
      App->>Inno: start Setup.exe /VERYSILENT
      App->>App: os._exit(0)
      Inno-->>App: new exe on disk
      App->>App: launch, check marker
      alt marker version == running version
        App->>App: clear marker
      else
        App->>App: offer visible-installer fallback
      end
    end
```

- `src/auto_updater.py` — GitHub API polling, download, integrity check,
  pending-marker lifecycle, installer invocation
- `installer/setup.iss` — Inno Setup script. PrivilegesRequired=lowest
  (installs to `%LOCALAPPDATA%\Programs\AudiobookMaker`). Registry-based
  auto-uninstall of any prior version before installing.
- `.github/workflows/build-release.yml` — on tag push (`v*`), builds the
  PyInstaller bundle on a Windows runner, wraps in Inno Setup, uploads
  as a Release asset.

Version numbering: `APP_VERSION` in `src/auto_updater.py` is the source
of truth. CI rewrites it from the git tag at build time, so dev-mode
runs use the committed value (useful for local testing).

## Cleanup of old installs

`src/cleanup.py` runs silently on startup. Scans known install paths
(AppData, Program Files, `C:\AudiobookMaker`, `D:\AudiobookMaker`,
`D:\koodaamista\AudiobookMakerApp`) and orphan Start-Menu / desktop /
taskbar shortcuts. For each old install:

1. Rescues any user MP3s (root or legacy `audiobooks/` subfolder) into
   the current install's output dir
2. Runs `unins000.exe /VERYSILENT` if available
3. Falls back to `shutil.rmtree`

Users never lose audiobooks to cleanup.

## File layout reference

```
src/
  main.py                    # entry point, single-instance guard
  gui_unified.py             # UnifiedApp, i18n strings, banner, widgets
  gui_synth_mixin.py         # synthesis orchestration
  gui_update_mixin.py        # auto-update banner + download
  gui_engine_dialog.py       # engine install/manage modal view
  auto_updater.py            # GitHub polling, download, apply_update()
  cleanup.py                 # old-install detection + MP3 rescue
  single_instance.py         # mutex against multiple app copies
  launcher_bridge.py         # ChatterboxRunner + ProgressEvent
  engine_installer.py        # in-app Chatterbox installer
  system_checks.py           # GPU, disk, Python 3.11 detection
  ffmpeg_path.py             # bundled-ffmpeg PATH wiring + pydub patching
  tts_base.py                # TTSEngine ABC + _REGISTRY
  tts_edge.py                # Edge-TTS adapter
  tts_piper.py               # Piper adapter
  tts_voxcpm.py              # VoxCPM2 adapter (dev only)
  tts_chunking.py            # sentence-aware text splitting
  tts_normalizer_fi.py       # Finnish text → speakable form
  tts_audio.py               # pydub/ffmpeg wrappers
  tts_engine.py              # TTSConfig + chapters_to_speech pipeline
  pdf_parser.py              # PyMuPDF chapter extraction
  fi_loanwords.py            # loanword respelling lookup
  app_config.py              # settings persistence
  voice_recorder.py          # in-app mic capture for cloning

scripts/
  generate_chatterbox_audiobook.py  # runs in the Chatterbox venv

installer/
  setup.iss                  # Inno Setup script

audiobookmaker.spec          # PyInstaller spec (main app bundle)
```

## Updating this document

When you change any of these boundaries — a new engine, a new mixin, a
new subprocess, a change in how the updater decides things — update the
matching Mermaid block and the relevant prose paragraph in the same
commit. The doc loses its value if it drifts.
