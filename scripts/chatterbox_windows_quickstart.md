# Chatterbox Windows Quickstart

From a fresh Windows 11 PC with an RTX 3080 Ti to a full Finnish audiobook MP3 on disk. Minimum friction, no fluff.

## Before you start

### Hardware check

- NVIDIA GPU with **8 GB+ VRAM** (RTX 3080 Ti has 12 GB, you're fine)
- **16 GB+ system RAM**
- **20 GB free disk** on the drive you're installing to
- Stable internet (first run downloads ~5 GB of models)

### Prerequisites (install manually, in this order)

1. **Python 3.11** — download from <https://www.python.org/downloads/release/python-3119/>
   - Use the "Windows installer (64-bit)" at the bottom of the page
   - **Tick "Add python.exe to PATH"** on the first installer screen
   - Do NOT use Python 3.13 (Chatterbox deps don't build yet) and do NOT use the Microsoft Store version (sandboxed paths cause trouble)
2. **Git for Windows** — <https://git-scm.com/download/win>
   - Accept defaults; the installer is long but harmless
3. **NVIDIA drivers** — latest Game Ready or Studio driver from <https://www.nvidia.com/Download/index.aspx>
   - If your driver is older than 12 months, update it. CUDA 12.1 wheels need driver >= 530.
4. **(Optional) VS Code** — <https://code.visualstudio.com/> — handy for viewing logs and editing the PDF path

Reboot after installing drivers.

## Step-by-step

### 1. Open PowerShell in your project folder

Press `Win+E`, navigate to where you want the project (e.g. `C:\Users\<you>\Documents`), right-click empty space, choose **Open in Terminal**. A blue PowerShell window appears.

Verify Python and Git are visible:

```powershell
python --version
git --version
```

Both should print a version number. If `python` is not recognized, jump to Troubleshooting.

### 2. Clone the repo and run the setup script

```powershell
git clone https://github.com/mikkokemppainen/AudiobookMaker.git
cd AudiobookMaker
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_chatterbox_windows.ps1
```

This creates a virtual environment, installs PyTorch with CUDA 12.1, installs Chatterbox, and applies the Finnish gemination patch. Expect **10-20 minutes** of download and install on first run.

When it finishes, you should see a line like `CUDA available: True`. If it says `False`, jump to Troubleshooting.

### 3. Copy your PDF into the project folder

From PowerShell, if the PDF is in Downloads:

```powershell
Copy-Item "$env:USERPROFILE\Downloads\mybook.pdf" .\mybook.pdf
```

Or just drag-and-drop the PDF into the AudiobookMaker folder in Explorer.

### 4. Run the generator

```powershell
.\venv\Scripts\Activate.ps1
python .\scripts\generate_chatterbox_audiobook.py --pdf .\mybook.pdf --out .\mybook.mp3
```

**Expected output layout** when the run completes:

```
mybook.mp3              <- full concatenated audiobook
mybook.chunks\          <- per-chunk WAV cache (safe to delete after)
mybook.chapters\        <- per-chapter MP3s
```

**Resume after Ctrl-C or reboot:** just run the exact same command again. The script skips any chunk whose WAV already exists in `mybook.chunks\`, so it picks up where it stopped. Do not delete the `.chunks` folder until the final MP3 is confirmed good.

### 5. Listen or transfer

Double-click `mybook.mp3` in Explorer to verify it plays in the Windows default player. Scrub to a random point, then to the end, to make sure the file isn't truncated.

## Troubleshooting

### "python is not recognized as an internal or external command"

Python wasn't added to PATH during install. Uninstall Python from **Settings > Apps**, re-run the installer, and **tick "Add python.exe to PATH"** on the first screen. Then open a new PowerShell window.

### "torch.cuda.is_available() returned False"

Either the NVIDIA driver is too old, or pip installed the CPU-only torch wheel by mistake. Fix:

```powershell
.\venv\Scripts\Activate.ps1
pip uninstall -y torch torchaudio
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
python -c "import torch; print(torch.cuda.is_available())"
```

If it still prints `False`, update your NVIDIA driver and reboot.

### "running scripts is disabled on this system"

PowerShell's execution policy is blocking the setup script. Run this in the same window, then retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This only affects the current window — it's the safe choice.

### Out of VRAM or out of RAM mid-run

Something else is eating the GPU or memory. Close: browsers with many tabs, any game or game launcher, DaVinci Resolve, Premiere, OBS, Stable Diffusion UIs. Then re-run — the script will resume from the last saved chunk.

### Generation hangs at "Sampling 0/1000"

The Finnish gemination patch didn't apply. Open this file in an editor:

```
venv\Lib\site-packages\chatterbox\models\s3gen\alignment_stream_analyzer.py
```

Find the line containing `self.alignment = self.alignment[-2000:]` and confirm it's present. If the file has a different structure, re-run `.\scripts\setup_chatterbox_windows.ps1` — it reapplies the patch.

## Time and size expectations

- **First-run download:** ~5 GB (Chatterbox base model + Finnish finetune). Cached in `%USERPROFILE%\.cache\huggingface` afterwards.
- **Synthesis time:** ~80-110 minutes for a 180-page Finnish PDF on a 3080 Ti, producing ~8.9 hours of final audio. Longer books scale roughly linearly.
- **Disk usage during run:** ~1 GB for the chunk cache.
- **Final MP3:** ~200 MB for an 8-9 hour book at the default bitrate.

## After you're done

### Transfer to Mac or phone

Just copy the MP3. No special tool needed.

- **To Mac:** plug in a USB stick, drag `mybook.mp3` to it, or upload to iCloud Drive / Dropbox / Google Drive from the browser.
- **To phone:** email it to yourself, or drop it in a cloud folder, or connect the phone via USB and drag it into the Music folder.

### Clean up

After you have verified the final MP3 plays end-to-end:

```powershell
Remove-Item -Recurse -Force .\mybook.chunks
```

Keep `mybook.chapters\` if you want per-chapter files; delete it otherwise.

### Update to a newer version

```powershell
cd AudiobookMaker
git pull
.\scripts\setup_chatterbox_windows.ps1
```

The setup script is idempotent — it only re-installs what changed.
