# setup_chatterbox_windows.ps1
# One-command setup for Chatterbox TTS on Windows 11 with NVIDIA GPU.
# Run: powershell -ExecutionPolicy Bypass -File .\setup_chatterbox_windows.ps1

$ErrorActionPreference = "Stop"

# Resolve script directory so we clone/operate next to the script regardless of CWD.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoUrl   = "https://github.com/MikkoNumminen/AudiobookMaker.git"
$RepoDir   = Join-Path $ScriptDir "..\"
$RepoDir   = [System.IO.Path]::GetFullPath($RepoDir)
# If script lives INSIDE the repo already, $RepoDir is the repo root.
# If run standalone, we clone alongside the script instead.
if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    $RepoDir = Join-Path $ScriptDir "AudiobookMaker"
}

$VenvDir      = Join-Path $RepoDir ".venv-qwen"
$ActivatePs1  = Join-Path $VenvDir "Scripts\Activate.ps1"
$PythonInVenv = Join-Path $VenvDir "Scripts\python.exe"

function Write-Header($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

try {
    # -------------------------------------------------------------------------
    Write-Header "Step 1/8: Verifying prerequisites"
    # -------------------------------------------------------------------------

    # Python 3.11.x
    try {
        $pyVersion = (& python --version) 2>&1
    } catch {
        Fail "Python not found. Install Python 3.11.x from https://www.python.org/downloads/release/python-3119/ and re-run."
    }
    if ($pyVersion -notmatch "Python 3\.11\.") {
        Fail "Python 3.11.x required (found: $pyVersion). chatterbox-tts + torch 2.6 have pin issues on 3.9/3.13. Install 3.11 from https://www.python.org/downloads/release/python-3119/"
    }
    Write-Host "  python : $pyVersion"

    # Git
    try {
        $gitVersion = (& git --version) 2>&1
    } catch {
        Fail "Git not found. Install from https://git-scm.com/download/win and re-run."
    }
    Write-Host "  git    : $gitVersion"

    # nvidia-smi
    try {
        $nvidiaOut = (& nvidia-smi) 2>&1
    } catch {
        Fail "nvidia-smi not found. Install the latest NVIDIA GPU driver from https://www.nvidia.com/Download/index.aspx and re-run."
    }
    Write-Host "  nvidia-smi OK"
    # Show first few lines (GPU name + driver/CUDA version)
    $nvidiaOut | Select-Object -First 12 | ForEach-Object { Write-Host "    $_" }

    # -------------------------------------------------------------------------
    Write-Header "Step 2/8: Cloning or updating repository"
    # -------------------------------------------------------------------------

    if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
        Write-Host "  Cloning $RepoUrl into `"$RepoDir`""
        & git clone $RepoUrl "$RepoDir"
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed." }
    } else {
        Write-Host "  Repository exists at `"$RepoDir`" - running git pull"
        Push-Location "$RepoDir"
        try {
            & git pull
            if ($LASTEXITCODE -ne 0) { Fail "git pull failed." }
        } finally {
            Pop-Location
        }
    }

    # Recompute venv paths in case the repo was just cloned.
    $VenvDir      = Join-Path $RepoDir ".venv-qwen"
    $ActivatePs1  = Join-Path $VenvDir "Scripts\Activate.ps1"
    $PythonInVenv = Join-Path $VenvDir "Scripts\python.exe"

    # -------------------------------------------------------------------------
    Write-Header "Step 3/8: Creating or reusing venv (.venv-qwen)"
    # -------------------------------------------------------------------------

    if (-not (Test-Path $PythonInVenv)) {
        Write-Host "  Creating venv at `"$VenvDir`""
        Push-Location "$RepoDir"
        try {
            & python -m venv ".venv-qwen"
            if ($LASTEXITCODE -ne 0) { Fail "venv creation failed." }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  Reusing existing venv at `"$VenvDir`""
    }

    Write-Host "  Activating venv"
    & $ActivatePs1

    # Upgrade pip inside venv
    & $PythonInVenv -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed." }

    # -------------------------------------------------------------------------
    Write-Header "Step 4/8: Installing CUDA-enabled torch 2.6.0 (cu124)"
    # -------------------------------------------------------------------------

    Write-Host "  This forces CUDA torch BEFORE chatterbox-tts so its resolver sees torch as satisfied."
    & $PythonInVenv -m pip install "torch==2.6.0" "torchaudio==2.6.0" --index-url "https://download.pytorch.org/whl/cu124"
    if ($LASTEXITCODE -ne 0) { Fail "CUDA torch install failed." }

    Write-Host "  Verifying CUDA availability..."
    $cudaCheck = & $PythonInVenv -c "import torch; print('AVAILABLE:' + str(torch.cuda.is_available())); print('DEVICE:' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'))"
    Write-Host $cudaCheck
    if ($cudaCheck -notmatch "AVAILABLE:True") {
        Write-Host ""
        Write-Host "WARNING: torch.cuda.is_available() returned False." -ForegroundColor Yellow
        Write-Host "         Synthesis will fall back to CPU and be extremely slow." -ForegroundColor Yellow
        $ans = Read-Host "Proceed anyway? (y/N)"
        if ($ans -ne "y" -and $ans -ne "Y") {
            Fail "Aborted by user due to missing CUDA."
        }
    }

    # -------------------------------------------------------------------------
    Write-Header "Step 5/8: Installing Chatterbox + extras"
    # -------------------------------------------------------------------------

    $pkgs = @("chatterbox-tts", "safetensors", "num2words", "silero-vad", "PyMuPDF", "pydub")
    foreach ($pkg in $pkgs) {
        Write-Host "  Installing $pkg ..."
        & $PythonInVenv -m pip install $pkg
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to install package: $pkg"
        }
    }

    # -------------------------------------------------------------------------
    Write-Header "Step 6/8: Pre-downloading Chatterbox model weights (~5 GB)"
    # -------------------------------------------------------------------------

    Write-Host "  Cache location: $env:USERPROFILE\.cache\huggingface\hub"
    Write-Host "  This can take 10-30 minutes on first run. Progress will stream below."

    $downloadScript = @'
import sys
try:
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
except Exception:
    from chatterbox.tts import ChatterboxTTS as ChatterboxMultilingualTTS
from huggingface_hub import hf_hub_download

print("Loading ChatterboxMultilingualTTS on cuda (triggers snapshot download)...", flush=True)
try:
    tts = ChatterboxMultilingualTTS.from_pretrained(device="cuda")
    print("Base model cached OK.", flush=True)
except Exception as e:
    print(f"WARN: base from_pretrained(cuda) failed: {e}", flush=True)
    print("Retrying on cpu just to cache the files...", flush=True)
    tts = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
    print("Base model cached OK (cpu path).", flush=True)

# Explicit Finnish-NLP safetensors fetch
try:
    path = hf_hub_download(repo_id="Finnish-NLP/Chatterbox-Finnish", filename="t3_mtl23ls_v2.safetensors")
    print(f"Finnish safetensors cached at: {path}", flush=True)
except Exception as e:
    print(f"WARN: Finnish safetensors pre-fetch failed: {e}", flush=True)
    sys.exit(0)
'@

    $tmpDlScript = Join-Path $env:TEMP "chatterbox_prefetch.py"
    Set-Content -Path $tmpDlScript -Value $downloadScript -Encoding UTF8
    & $PythonInVenv $tmpDlScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Model pre-download exited non-zero. Models will download on first run instead." -ForegroundColor Yellow
    }
    Remove-Item $tmpDlScript -ErrorAction SilentlyContinue

    # -------------------------------------------------------------------------
    Write-Header "Step 7/8: Applying Finnish gemination patch"
    # -------------------------------------------------------------------------

    $patchTarget = Join-Path $VenvDir "Lib\site-packages\chatterbox\models\t3\inference\alignment_stream_analyzer.py"
    if (-not (Test-Path $patchTarget)) {
        Write-Host "WARNING: patch target not found: $patchTarget" -ForegroundColor Yellow
        Write-Host "         Skipping patch. The package layout may have changed." -ForegroundColor Yellow
    } else {
        $original = Get-Content -Raw -LiteralPath $patchTarget
        $patched  = $original

        # Idempotency markers: the ORIGINAL patterns we replace.
        # If none of them are found, we assume patch already applied (or file changed upstream) and skip.
        $marker1 = "self.generated_tokens[-2:]"
        $marker2 = ">= 3"
        $marker3 = "generated_tokens) > 8"

        $alreadyPatched = `
            ($patched.Contains("self.generated_tokens[-10:]")) -and `
            ($patched.Contains(">= 10"))                       -and `
            ($patched.Contains("generated_tokens) > 10"))

        if ($alreadyPatched) {
            Write-Host "  Patch already applied - skipping."
        } elseif ( -not ($patched.Contains($marker1) -or $patched.Contains($marker2) -or $patched.Contains($marker3)) ) {
            Write-Host "WARNING: neither original nor patched markers found - upstream file may have changed. Skipping." -ForegroundColor Yellow
        } else {
            if ($patched.Contains($marker1)) {
                $patched = $patched.Replace("self.generated_tokens[-2:]", "self.generated_tokens[-10:]")
            }
            # Replace ">= 3" only in a repetition-check context; use a regex anchored to "count" or comparison.
            # To stay safe, only replace occurrences that are on lines containing 'generated_tokens' or 'repeat'.
            $lines = $patched -split "`n"
            for ($i = 0; $i -lt $lines.Length; $i++) {
                if ($lines[$i] -match "generated_tokens" -or $lines[$i] -match "repeat") {
                    $lines[$i] = $lines[$i] -replace ">=\s*3\b", ">= 10"
                    $lines[$i] = $lines[$i] -replace "generated_tokens\)\s*>\s*8", "generated_tokens) > 10"
                }
            }
            $patched = $lines -join "`n"

            Set-Content -LiteralPath $patchTarget -Value $patched -Encoding UTF8
            Write-Host "  Patch applied to: $patchTarget"
        }
    }

    # -------------------------------------------------------------------------
    Write-Header "Step 8/8: Done"
    # -------------------------------------------------------------------------

    Write-Host ""
    Write-Host "Setup complete. To generate the audiobook:" -ForegroundColor Green
    Write-Host ""
    Write-Host "  cd `"$RepoDir`""
    Write-Host "  .\.venv-qwen\Scripts\Activate.ps1"
    Write-Host "  python scripts\generate_chatterbox_audiobook.py --pdf path\to\turodokumentti.pdf --out dist\audiobook"
    Write-Host ""
    Write-Host "First run will download ~5 GB of model weights (if step 6 was skipped). Resume is automatic." -ForegroundColor Green
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "FATAL: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Read-Host "Press Enter to exit"
    exit 1
}

Read-Host "Press Enter to exit"
