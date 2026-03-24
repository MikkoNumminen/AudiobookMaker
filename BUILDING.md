# Building AudiobookMaker

This guide covers how to set up a development environment, run tests, and produce a distributable Windows `.exe` and installer.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| pip | bundled with Python | |
| git | any recent version | |
| ffmpeg | any recent build | Required at runtime for audio processing; must be on `PATH` during development |
| PyInstaller | see `requirements.txt` | Installed automatically via pip |
| Inno Setup 6 | 6.x | Windows only — [download here](https://jrsoftware.org/isdl.php) |

> **Note:** Building the `.exe` and installer must be done on Windows. Development and testing can be done on any platform.

---

## Development Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd AudiobookMaker
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
```

**Windows:**
```bash
.venv\Scripts\activate
```

**macOS / Linux:**
```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the application

```bash
python -m src.main
```

---

## Running Tests

```bash
pytest
```

Three tests are skipped automatically when `ffmpeg` is not found on `PATH`. These tests cover audio export functionality and require a working `ffmpeg` installation to run.

To run all tests including the ffmpeg-dependent ones, ensure `ffmpeg` is installed and available:

```bash
ffmpeg -version  # verify ffmpeg is accessible
pytest
```

---

## Building the .exe (Windows only)

### 1. Obtain ffmpeg for Windows

Download a Windows `ffmpeg.exe` build from [ffmpeg.org](https://ffmpeg.org/download.html) and place it at the following path relative to the project root:

```
dist/ffmpeg/ffmpeg.exe
```

This path is where the PyInstaller spec expects to find ffmpeg so it can be bundled into the application.

### 2. Run PyInstaller

```bash
pyinstaller audiobookmaker.spec
```

### 3. Output

The built application will be in:

```
dist/AudiobookMaker/
```

The entry point executable is `dist/AudiobookMaker/AudiobookMaker.exe`.

> **Tip:** If PyInstaller complains about missing hidden imports, see the Troubleshooting section below.

---

## Building the Installer (Windows only)

The installer is created with [Inno Setup 6](https://jrsoftware.org/isdl.php). Complete the `.exe` build step above before proceeding.

### Option A — Inno Setup IDE

1. Open Inno Setup Compiler.
2. Open the file `installer/setup.iss`.
3. Press **Build > Compile** (or `Ctrl+F9`).

### Option B — Command line

```bash
iscc installer/setup.iss
```

### Output

```
AudiobookMaker-Setup-1.0.0.exe
```

The output installer will be placed in the project root (or the output directory configured in `setup.iss`).

---

## Creating the Icon

The application icon is located at `assets/icon.ico`. Any 256x256 `.ico` file will work as a drop-in replacement.

To convert an existing image to `.ico` format:

- **ImageMagick:**
  ```bash
  magick convert input.png -resize 256x256 assets/icon.ico
  ```
- **Online tools:** Search for "PNG to ICO converter" — many free options exist that produce compatible output.

---

## Versioning

When cutting a new release, update the version string in the following two files:

| File | What to update |
|---|---|
| `installer/setup.iss` | `AppVersion` directive and the output filename (e.g. `AudiobookMaker-Setup-1.0.0.exe`) |
| `audiobookmaker.spec` | Version metadata passed to the `EXE` or `BUNDLE` block |

Keep both files in sync so the installer version matches the embedded application version.

---

## Troubleshooting

### PyInstaller: missing hidden imports

If the packaged `.exe` fails at startup with an `ImportError` or `ModuleNotFoundError`, a dependency is not being picked up automatically. Add the missing module to the `hiddenimports` list inside `audiobookmaker.spec`:

```python
hiddenimports=['some.missing.module'],
```

Then rebuild with `pyinstaller audiobookmaker.spec`.

### ffmpeg not found at runtime

The application locates `ffmpeg` using `src/ffmpeg_path.py`, which checks for a bundled copy first (expected alongside the executable after packaging) and falls back to `PATH`.

- **During development:** ensure `ffmpeg` is on your `PATH`.
- **In the packaged build:** ensure `dist/ffmpeg/ffmpeg.exe` was present before running PyInstaller, so it gets included in the bundle.

If users report that ffmpeg is missing after installation, verify that the `dist/ffmpeg/` directory was present at build time and that the `.spec` file includes it in the `datas` or `binaries` list.

### Inno Setup: iscc not found

If running `iscc` from the command line fails, add the Inno Setup installation directory to your `PATH`, for example:

```
C:\Program Files (x86)\Inno Setup 6\
```

Alternatively, use the Inno Setup IDE (Option A above).
