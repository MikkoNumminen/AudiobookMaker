<#
One-shot dev bootstrap for AudiobookMaker (Windows PowerShell).

Idempotent: re-running it does not duplicate a PATH entry or rebuild the
venv from scratch. It only does the work that is still missing.

What it does, in order:
  1. Create .venv in the repo root if it isn't there yet.
  2. Using that venv's python/pip, install runtime deps (requirements.txt if
     present) and the package itself in editable mode (pip install -e .).
  3. Find the venv's Scripts dir; if it isn't on the USER PATH, persist it with
     [Environment]::SetEnvironmentVariable at User scope (never Machine) and
     tell you to open a fresh shell.
  4. Run scripts/check_cli_install.py to print shim / PATH / GUI-shadow status,
     then `audiobookmaker-cli --version` if the command resolves.
  5. Print a success + next-steps message.

Usage (PowerShell, from the repo root or anywhere):
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap_dev.ps1

NOTE: step 2's editable install pulls the runtime dependency tree, which on a
GPU box includes PyTorch -- that download is large. This is expected dev setup.
#>

# Stop on the first error so a half-finished setup never looks like a success.
$ErrorActionPreference = 'Stop'

# ── Locate the repo root (this script lives in <root>\scripts) ──────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir '..')).Path
Set-Location -LiteralPath $Root

$VenvDir = Join-Path $Root '.venv'

# ── 0. Fail loudly if there is no Python at all ─────────────────────────────
# Prefer the `py` launcher (it can pick a specific version), fall back to a
# bare `python` on PATH. We only need it to create the venv; after that we use
# the venv's own interpreter directly.
function Find-BootstrapPython {
    $py = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($py) { return @('py', '-3') }
    $python = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($python) { return @($python.Source) }
    return $null
}

$BootstrapPy = Find-BootstrapPython
if ($null -eq $BootstrapPy) {
    Write-Error ("No Python interpreter found on PATH (looked for 'py' launcher and 'python'). " +
                 "Install Python 3.11+ and re-run: powershell -File scripts\bootstrap_dev.ps1")
    exit 1
}

$BootstrapExe = $BootstrapPy[0]
$BootstrapArgs = @()
if ($BootstrapPy.Count -gt 1) { $BootstrapArgs = $BootstrapPy[1..($BootstrapPy.Count - 1)] }

$pyVersion = & $BootstrapExe @BootstrapArgs --version 2>&1
Write-Host "Using '$($BootstrapPy -join ' ')' to bootstrap (Python: $pyVersion)."

# ── 1. Create .venv if missing ──────────────────────────────────────────────
if (Test-Path -LiteralPath $VenvDir) {
    Write-Host "Found existing virtualenv at '$VenvDir' -- reusing it."
} else {
    Write-Host "Creating virtualenv at '$VenvDir'..."
    & $BootstrapExe @BootstrapArgs -m venv "$VenvDir"
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create the virtualenv."; exit 1 }
}

# The venv's Scripts dir + interpreter (Windows layout).
$VenvScripts = Join-Path $VenvDir 'Scripts'
$VenvPy = Join-Path $VenvScripts 'python.exe'
if (-not (Test-Path -LiteralPath $VenvPy)) {
    Write-Error ("Could not find the venv interpreter at '$VenvPy'. " +
                 "Delete '$VenvDir' and re-run to recreate it.")
    exit 1
}

# ── 2. Install deps into the venv ───────────────────────────────────────────
Write-Host "Upgrading pip in the venv..."
& $VenvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed."; exit 1 }

$Requirements = Join-Path $Root 'requirements.txt'
if (Test-Path -LiteralPath $Requirements) {
    Write-Host "Installing runtime deps from requirements.txt..."
    & $VenvPy -m pip install -r "$Requirements"
    if ($LASTEXITCODE -ne 0) { Write-Error "Installing requirements.txt failed."; exit 1 }
} else {
    Write-Host "No requirements.txt found -- skipping runtime deps."
}

Write-Host "Installing the package in editable mode (pip install -e .)..."
& $VenvPy -m pip install -e "$Root"
if ($LASTEXITCODE -ne 0) { Write-Error "Editable install (pip install -e .) failed."; exit 1 }

# ── 3. Ensure the venv Scripts dir is on the USER PATH (persist if not) ──────
# Idempotent: read the current persisted USER PATH, split on ';', and only add
# the Scripts dir if no existing entry matches it (case-insensitive, trimmed).
# We write ONLY the User scope -- never Machine -- so no admin rights are needed
# and we never touch system-wide state.
$PathPersisted = $false
$UserPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($null -eq $UserPath) { $UserPath = '' }

$existing = $UserPath.Split(';') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
$alreadyOnUserPath = $false
foreach ($entry in $existing) {
    if ($entry.TrimEnd('\') -ieq $VenvScripts.TrimEnd('\')) {
        $alreadyOnUserPath = $true
        break
    }
}

if ($alreadyOnUserPath) {
    Write-Host "The venv Scripts dir is already on the persisted USER PATH."
    $PathPersisted = $true
} else {
    if ([string]::IsNullOrEmpty($UserPath)) {
        $NewUserPath = $VenvScripts
    } else {
        $NewUserPath = "$VenvScripts;$UserPath"
    }
    [Environment]::SetEnvironmentVariable('PATH', $NewUserPath, 'User')
    # Reflect it in THIS process so the version check below can find the shim.
    $env:PATH = "$VenvScripts;$env:PATH"
    Write-Host "Added the venv Scripts dir to your USER PATH: '$VenvScripts'"
    $PathPersisted = $true
}

# ── 4. Print shim / PATH / GUI-shadow status, then the CLI version ──────────
Write-Host ""
Write-Host "Running the CLI install check (shim / PATH / GUI-shadow status)..."
# Run through the venv interpreter so the diagnostics reflect the env we just
# set up. A non-zero exit here just means the shim isn't resolvable yet
# (expected before a fresh shell), so don't let it abort the bootstrap.
& $VenvPy (Join-Path $Root 'scripts\check_cli_install.py')

Write-Host ""
$cli = Get-Command 'audiobookmaker-cli' -ErrorAction SilentlyContinue
if ($cli) {
    Write-Host "audiobookmaker-cli resolves at: $($cli.Source)"
    & audiobookmaker-cli --version
} else {
    Write-Host "audiobookmaker-cli is not resolvable in THIS shell yet."
    Write-Host "It was installed into '$VenvScripts'."
}

# ── 5. Success + next steps ─────────────────────────────────────────────────
Write-Host ""
Write-Host "Bootstrap complete."
Write-Host ""
Write-Host "Next steps:"
if ($PathPersisted) {
    Write-Host "  - Open a FRESH PowerShell window so the updated USER PATH takes"
    Write-Host "    effect, then run: audiobookmaker-cli --version"
}
Write-Host "  - The Chatterbox GPU engine installs separately. See"
Write-Host "    docs\QUICKSTART_DEV.md for the .venv-chatterbox setup."
Write-Host "  - Day-to-day, activate the env with: .venv\Scripts\Activate.ps1"
