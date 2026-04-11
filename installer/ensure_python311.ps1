<#
.SYNOPSIS
    Ensures Python 3.11 is installed per-user on Windows, downloading and
    silently installing python-3.11.9-amd64.exe from python.org if missing.

.DESCRIPTION
    Used by the AudiobookMaker Launcher installer (installer/launcher.iss) as
    a bootstrap step before running post_install_chatterbox.py. Chatterbox
    requires system Python 3.11 (torch CUDA cu124 wheels are 3.11-only).

    PowerShell is used instead of Python because the installer cannot assume
    any Python interpreter exists yet — PowerShell 5.1+ is guaranteed on
    Windows 10 1809+ and Windows 11, which matches the installer's
    MinVersion=10.0.17763.

    The script is idempotent: if Python 3.11 is already available it exits 0
    immediately without downloading anything. It never requires admin rights
    (PrivilegesRequired=lowest in launcher.iss is preserved).

.PARAMETER Help
    Show usage information and exit.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ensure_python311.ps1

.NOTES
    Exit codes:
        0  success — Python 3.11 is available (already installed or freshly installed)
        1  generic failure
        2  download failed
        3  silent installer returned non-zero
        4  post-install verification failed
#>

[CmdletBinding()]
param(
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'  # disable Invoke-WebRequest's slow GUI progress

$PythonVersion    = '3.11.9'
$InstallerUrl     = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$InstallerSize    = 26216840  # bytes, from python.org directory listing
$PerUserPython    = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$PerUserLauncher  = Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'

function Show-Help {
    @"
ensure_python311.ps1 — bootstrap system Python 3.11 for AudiobookMaker

USAGE
    powershell -ExecutionPolicy Bypass -File ensure_python311.ps1 [-Help]

BEHAVIOR
    1. Detects Python 3.11 via (in order):
         a) py -3.11 --version
         b) %LOCALAPPDATA%\Programs\Python\Python311\python.exe --version
         c) HKCU\Software\Python\PythonCore\3.11\InstallPath registry key
    2. If found, prints "[ensure-python] already installed at <path>" and exits 0.
    3. Otherwise downloads python-$PythonVersion-amd64.exe (~25 MB) to %TEMP%
       with streaming progress to stdout.
    4. Runs it with /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
       Include_doc=0 Include_test=0 Include_pip=1 — per-user, no UAC.
    5. Re-runs detection to verify. Prints "[ensure-python] installed at <path>"
       on success.

EXIT CODES
    0  success        1  generic        2  download        3  install        4  verify
"@ | Write-Host
}

if ($Help) { Show-Help; exit 0 }

function Write-Stage { param([string]$Msg) Write-Host "[ensure-python] $Msg" }

# -- 1. Detection ------------------------------------------------------------

function Find-Python311 {
    # (a) py -3.11 launcher
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $py -and (Test-Path $PerUserLauncher)) {
        $py = @{ Source = $PerUserLauncher }
    }
    if ($py) {
        try {
            $out = & $py.Source -3.11 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $exe = ($out | Select-Object -First 1).Trim()
                if (Test-Path $exe) { return $exe }
            }
        } catch { }
    }

    # (b) Known per-user install path
    if (Test-Path $PerUserPython) {
        try {
            $ver = & $PerUserPython -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -and $ver.Trim() -eq '3.11') {
                return $PerUserPython
            }
        } catch { }
    }

    # (c) HKCU registry (PEP 514)
    try {
        $key = 'HKCU:\Software\Python\PythonCore\3.11\InstallPath'
        if (Test-Path $key) {
            $installPath = (Get-ItemProperty -Path $key -ErrorAction Stop).'(default)'
            if (-not $installPath) {
                $installPath = (Get-ItemProperty -Path $key).ExecutablePath
            }
            if ($installPath) {
                $exe = if ($installPath -like '*.exe') { $installPath } else { Join-Path $installPath 'python.exe' }
                if (Test-Path $exe) { return $exe }
            }
        }
    } catch { }

    return $null
}

$existing = Find-Python311
if ($existing) {
    Write-Stage "already installed at $existing"
    exit 0
}

# -- 2. Download -------------------------------------------------------------

$tempDir = Join-Path $env:TEMP 'audiobookmaker-py311'
if (-not (Test-Path $tempDir)) { New-Item -ItemType Directory -Path $tempDir | Out-Null }
$installerPath = Join-Path $tempDir "python-$PythonVersion-amd64.exe"

Write-Stage "downloading $InstallerUrl"
Write-Stage ("target {0:N1} MB -> {1}" -f ($InstallerSize / 1MB), $installerPath)

try {
    # Force TLS 1.2 — required for python.org on older .NET defaults.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $req  = [System.Net.HttpWebRequest]::Create($InstallerUrl)
    $req.UserAgent = 'AudiobookMaker-Launcher/ensure-python311'
    $resp = $req.GetResponse()
    $total = [int64]$resp.ContentLength
    if ($total -le 0) { $total = $InstallerSize }

    $inStream  = $resp.GetResponseStream()
    $outStream = [System.IO.File]::Open($installerPath, 'Create', 'Write', 'None')
    $buffer    = New-Object byte[] 131072  # 128 KiB
    $read      = 0
    $soFar     = [int64]0
    $lastMb    = -1

    do {
        $read = $inStream.Read($buffer, 0, $buffer.Length)
        if ($read -gt 0) {
            $outStream.Write($buffer, 0, $read)
            $soFar += $read
            $mb = [int]($soFar / 1MB)
            if ($mb -ne $lastMb) {
                Write-Host ("[download] {0:N1}/{1:N1} MB" -f ($soFar / 1MB), ($total / 1MB))
                $lastMb = $mb
            }
        }
    } while ($read -gt 0)

    $outStream.Close()
    $inStream.Close()
    $resp.Close()
} catch {
    Write-Stage "download failed: $($_.Exception.Message)"
    exit 2
}

if (-not (Test-Path $installerPath)) {
    Write-Stage "download produced no file"
    exit 2
}
$actualSize = (Get-Item $installerPath).Length
Write-Stage ("downloaded {0} bytes" -f $actualSize)
if ($actualSize -lt 10MB) {
    Write-Stage "download truncated, aborting"
    exit 2
}

# -- 3. Silent install -------------------------------------------------------

Write-Stage "running silent per-user installer (no UAC)"
$logPath = Join-Path $tempDir 'python-install.log'
$procArgs = @(
    '/quiet',
    '/log', $logPath,
    'InstallAllUsers=0',
    'PrependPath=1',
    'Include_launcher=1',
    'InstallLauncherAllUsers=0',  # critical — launcher ALSO per-user, no UAC
    'Include_doc=0',
    'Include_test=0',
    'Include_pip=1',
    'Include_tcltk=1',
    'SimpleInstall=1',
    'SimpleInstallDescription=AudiobookMaker bootstrap'
)

try {
    $proc = Start-Process -FilePath $installerPath -ArgumentList $procArgs -Wait -PassThru -WindowStyle Hidden
    $code = $proc.ExitCode
} catch {
    Write-Stage "installer failed to launch: $($_.Exception.Message)"
    exit 3
}

if ($code -ne 0) {
    Write-Stage "installer exited with code $code (log: $logPath)"
    exit 3
}

# -- 4. Verify ---------------------------------------------------------------

# Refresh PATH for this process in case the new python.exe / py.exe location
# is needed for the verification lookup.
$machinePath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path    = "$machinePath;$userPath"

$verified = Find-Python311
if (-not $verified) {
    Write-Stage "install finished but Python 3.11 still not detectable"
    exit 4
}

Write-Stage "installed at $verified"

# Best-effort cleanup of the downloaded installer (keep log for debugging).
try { Remove-Item -Force $installerPath -ErrorAction SilentlyContinue } catch { }

exit 0
