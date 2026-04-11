; AudiobookMaker Launcher — Inno Setup script
;
; Builds AudiobookMaker-Launcher-Setup-<version>.exe, a per-user
; Windows installer for the simple launcher (src/launcher.py). This is
; a SECOND installer, distinct from installer/setup.iss which ships the
; advanced-mode GUI (src/gui.py).
;
; Engines are offered as optional components; the wizard lets the user
; pick which ones to install:
;
;   * engine_edge        Edge-TTS (online Noora) — no extra install
;   * engine_piper       Piper Harri (offline CPU) — ~60 MB voice download
;   * engine_chatterbox  Chatterbox Finnish (GPU, best quality) — ~15 GB,
;                        pulls in a separate Python 3.11 venv at
;                        C:\AudiobookMaker\.venv-chatterbox
;
; Per-user install to %LOCALAPPDATA%\Programs\AudiobookMaker-Launcher\
; so the wizard does not need admin / trigger a UAC prompt.
;
; Build requirements:
;   * Inno Setup 6 (ISCC.exe)
;   * dist\AudiobookMakerLauncher\ produced by
;     ``pyinstaller audiobookmaker_launcher.spec``
;
; Version is injected by the CI workflow via the MyAppVersion #define
; below — edit only the default here.

#define MyAppName        "AudiobookMaker Launcher"
#define MyAppVersion     "0.1.0"
#define MyAppPublisher   "AudiobookMaker Project"
#define MyAppURL         "https://github.com/MikkoNumminen/AudiobookMaker"
#define MyAppExeName     "AudiobookMakerLauncher.exe"

[Setup]
; A fresh GUID distinct from installer/setup.iss so Windows does not
; mistake the launcher install for an upgrade of the main app.
AppId={{B7E1D4A6-2F3C-4C8B-9E5D-2A4F6C1B9E3F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\AudiobookMaker-Launcher
DefaultGroupName=AudiobookMaker Launcher
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; No PrivilegesRequiredOverridesAllowed — strictly per-user to keep
; the no-UAC-prompt contract. The wizard will not offer an "install
; for all users" escape hatch.
;
; x64 (not x64compatible) — x64compatible requires Inno Setup >= 6.3.
; Our CI installs Inno Setup via choco which may resolve to an older
; version. x64 works on Inno Setup 6.0+.
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
OutputBaseFilename=AudiobookMaker-Launcher-Setup-{#MyAppVersion}
OutputDir=Output
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=yes
UsePreviousTasks=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Code-signing is intentionally NOT configured yet. Once a cert exists
; in the CI runner, uncomment the line below and set SignTool in CI.
; SignTool=signtool

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Finnish language file from the unofficial translations repo. The
; CI workflow downloads it into Inno Setup's Languages\Unofficial\
; directory before compilation. If that download fails, the CI step
; strips this whole line before invoking iscc so the fallback compile
; succeeds with English only.
Name: "finnish"; MessagesFile: "compiler:Languages\Unofficial\Finnish.isl"

[Types]
Name: "full";    Description: "Kaikki — Edge-TTS, Piper ja Chatterbox (suositus)"
Name: "compact"; Description: "Vain verkkomoottori (Edge-TTS Noora)"
Name: "custom";  Description: "Mukautettu"; Flags: iscustom

[Components]
Name: "main"; \
    Description: "Sovellus (pakollinen)"; \
    Types: full compact custom; \
    Flags: fixed
Name: "engine_edge"; \
    Description: "Edge-TTS Noora (online, nopea, ei vaadi levytilaa)"; \
    Types: full compact
Name: "engine_piper"; \
    Description: "Piper Harri (offline CPU, ~60 MB lataus)"; \
    Types: full
Name: "engine_chatterbox"; \
    Description: "Chatterbox suomi (GPU, paras laatu, ~15 GB lataus)"; \
    Types: full

[Tasks]
Name: "desktopicon"; \
    Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; \
    Flags: unchecked

[Files]
; Core launcher — always installed.
Source: "..\dist\AudiobookMakerLauncher\*"; \
    DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs; \
    Components: main

; Chatterbox post-install script — only if the component is selected.
Source: "post_install_chatterbox.py"; \
    DestDir: "{app}\installer"; \
    Flags: ignoreversion; \
    Components: engine_chatterbox

; Python 3.11 bootstrap — PowerShell, no Python needed to run it.
; Downloads python.org's per-user silent installer and invokes it
; with no UAC. See installer/ensure_python311.ps1 for details.
Source: "ensure_python311.ps1"; \
    DestDir: "{app}\installer"; \
    Flags: ignoreversion; \
    Components: engine_chatterbox

; Piper voice downloader.
Source: "download_piper_voice.py"; \
    DestDir: "{app}\installer"; \
    Flags: ignoreversion; \
    Components: engine_piper

[Icons]
Name: "{group}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Ohje"; \
    Filename: "{app}\docs\turo_ohjeet_fi.md"
Name: "{group}\Poista {#MyAppName}"; \
    Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
; Piper voice download (~60 MB). Uses PowerShell instead of the py
; launcher so it works regardless of whether Python is installed.
; Piper ONNX files are fetched directly from HuggingFace.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""& {{ \
        New-Item -ItemType Directory -Force -Path '{userappdata}\AudiobookMaker\piper_voices\fi_FI-harri-medium' | Out-Null; \
        $base = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/fi/fi_FI/harri/medium/'; \
        foreach ($f in @('fi_FI-harri-medium.onnx','fi_FI-harri-medium.onnx.json')) {{ \
            $dst = Join-Path '{userappdata}\AudiobookMaker\piper_voices\fi_FI-harri-medium' $f; \
            if (-not (Test-Path $dst)) {{ Invoke-WebRequest -Uri ($base + $f) -OutFile $dst -UseBasicParsing }} \
        }} \
    }}"""; \
    StatusMsg: "Ladataan Piper Harri -ääntä (~60 MB)…"; \
    Flags: runhidden waituntilterminated; \
    Components: engine_piper

; STEP A — ensure system Python 3.11 exists. If missing, this
; downloads python.org's silent per-user installer and runs it. No
; UAC, no manual steps. Idempotent — exits fast if already installed.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\ensure_python311.ps1"""; \
    StatusMsg: "Varmistetaan Python 3.11 (ladataan tarvittaessa ~25 MB)…"; \
    Flags: waituntilterminated; \
    Components: engine_chatterbox

; STEP B — Chatterbox post-install. Calls python.exe at the fixed
; per-user install path ensured by the previous step, because the
; PATH refresh from python.org's installer does NOT propagate into
; our currently-running Inno Setup process. Runs VISIBLE so the user
; can see the ~15-45 min pip/download progress.
Filename: "{localappdata}\Programs\Python\Python311\python.exe"; \
    Parameters: """{app}\installer\post_install_chatterbox.py"" --venv-path ""C:\AudiobookMaker\.venv-chatterbox"""; \
    StatusMsg: "Asennetaan Chatterbox — tämä kestää 15–45 min…"; \
    Flags: waituntilterminated; \
    Components: engine_chatterbox

; Launch the launcher once install is done.
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]

{ -- GPU detection (NVIDIA-aware, multi-source) -- }
{                                                                       }
{ Tries three sources in order:                                          }
{   1. nvidia-smi --query-gpu=name,driver_version,memory.total          }
{      (authoritative when present; gives us the full triple).          }
{   2. PowerShell WMI (Get-CimInstance Win32_VideoController) — works   }
{      on every Windows 10+ machine because powershell.exe is always    }
{      on PATH. Detects non-NVIDIA GPUs by name match so we can show    }
{      the user what they *do* have.                                    }
{   3. WMIC as a last-ditch fallback (removed in Win11 24H2+).          }

type
  TGpuStatus = record
    HasNvidiaGpu:  Boolean;
    DriverVersion: Double;
    GpuName:       String;
    VramMB:        Integer;
    OtherGpuName:  String;
  end;

function RunAndCapture(const CmdLine: String; const OutName: String;
                      var Lines: TArrayOfString): Boolean;
var
  TmpFile: String;
  ResultCode: Integer;
begin
  Result := False;
  TmpFile := ExpandConstant('{tmp}\' + OutName);
  if not Exec(ExpandConstant('{cmd}'),
              '/C ' + CmdLine + ' > "' + TmpFile + '" 2>nul',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then Exit;
  if ResultCode <> 0 then Exit;
  if not LoadStringsFromFile(TmpFile, Lines) then Exit;
  Result := GetArrayLength(Lines) > 0;
end;

function SplitCsvField(const S: String; Index: Integer): String;
var
  i, Field, Start: Integer;
begin
  Result := '';
  Field := 0;
  Start := 1;
  for i := 1 to Length(S) do
  begin
    if S[i] = ',' then
    begin
      if Field = Index then
      begin
        Result := Trim(Copy(S, Start, i - Start));
        Exit;
      end;
      Field := Field + 1;
      Start := i + 1;
    end;
  end;
  if Field = Index then
    Result := Trim(Copy(S, Start, Length(S) - Start + 1));
end;

{ Locale-safe float parser. StrToFloat uses the system locale, so on a }
{ Finnish Windows it rejects "551.86" (expects "551,86") and the       }
{ driver check silently returns 0. We parse manually.                   }
function SafeParseFloat(const S: String): Double;
var
  i, DotPos: Integer;
  IntPart, FracPart: String;
  IntVal, FracVal: Integer;
  FracDiv: Double;
begin
  Result := 0.0;
  if Length(S) = 0 then Exit;
  DotPos := 0;
  for i := 1 to Length(S) do
    if (S[i] = '.') or (S[i] = ',') then
    begin
      DotPos := i;
      Break;
    end;
  if DotPos = 0 then
  begin
    try
      Result := StrToIntDef(Trim(S), 0);
    except
      Result := 0.0;
    end;
    Exit;
  end;
  IntPart := Trim(Copy(S, 1, DotPos - 1));
  FracPart := Trim(Copy(S, DotPos + 1, Length(S) - DotPos));
  IntVal := StrToIntDef(IntPart, 0);
  FracVal := StrToIntDef(FracPart, 0);
  FracDiv := 1.0;
  for i := 1 to Length(FracPart) do
    FracDiv := FracDiv * 10.0;
  Result := IntVal + (FracVal / FracDiv);
end;

function LooksLikeNvidia(const S: String): Boolean;
begin
  Result := Pos('nvidia', Lowercase(S)) > 0;
end;

procedure DetectGpuStatus(var Status: TGpuStatus);
var
  Lines: TArrayOfString;
  Line, Name: String;
  i: Integer;
begin
  Status.HasNvidiaGpu  := False;
  Status.DriverVersion := 0.0;
  Status.GpuName       := '';
  Status.VramMB        := 0;
  Status.OtherGpuName  := '';

  { 1. nvidia-smi — authoritative. }
  if RunAndCapture(
       'nvidia-smi --query-gpu=name,driver_version,memory.total '
       + '--format=csv,noheader,nounits',
       'nvidia_smi.txt', Lines) then
  begin
    Line := Trim(Lines[0]);
    if Line <> '' then
    begin
      Status.GpuName       := SplitCsvField(Line, 0);
      Status.DriverVersion := SafeParseFloat(SplitCsvField(Line, 1));
      Status.VramMB        := StrToIntDef(SplitCsvField(Line, 2), 0);
      if Status.GpuName <> '' then
      begin
        Status.HasNvidiaGpu := True;
        Exit;
      end;
    end;
  end;

  { 2. PowerShell WMI — lists every video controller regardless of driver. }
  if not RunAndCapture(
       'powershell.exe -NoProfile -NonInteractive -Command '
       + '"Get-CimInstance Win32_VideoController | '
       + 'Select-Object -ExpandProperty Name"',
       'gpu_ps.txt', Lines) then
  begin
    { 3. WMIC fallback (deprecated in Win11 24H2+). }
    RunAndCapture('wmic path win32_videocontroller get name',
                  'gpu_wmic.txt', Lines);
  end;

  for i := 0 to GetArrayLength(Lines) - 1 do
  begin
    Name := Trim(Lines[i]);
    if (Name = '') or (CompareText(Name, 'Name') = 0) then Continue;
    if LooksLikeNvidia(Name) then
    begin
      Status.HasNvidiaGpu := True;
      Status.GpuName      := Name;
      Exit;
    end
    else if Status.OtherGpuName = '' then
      Status.OtherGpuName := Name;
  end;
end;

function ChatterboxSelected: Boolean;
begin
  Result := IsComponentSelected('engine_chatterbox');
end;

{ -- Pre-install checks -- }

function InitializeSetup: Boolean;
begin
  Result := True;
  { Windows version check is enforced by MinVersion= in [Setup],  }
  { so we don't duplicate it here. Disk space and NVIDIA checks  }
  { run later in NextButtonClick after the component page so we  }
  { know what the user picked.                                    }
end;

{ Format a Double with one decimal place using manual formatting so      }
{ we avoid FloatToStr's locale sensitivity (it prints "551,86" on       }
{ Finnish Windows which is cosmetically wrong in error dialogs).         }
function FormatVersion(V: Double): String;
var
  IntPart, FracPart: Integer;
begin
  IntPart := Trunc(V);
  FracPart := Trunc((V - IntPart) * 100);
  if FracPart < 10 then
    Result := IntToStr(IntPart) + '.0' + IntToStr(FracPart)
  else
    Result := IntToStr(IntPart) + '.' + IntToStr(FracPart);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  FreeBytes: Int64;
  TotalBytes: Int64;
  Needed: Int64;
  GpuStatus: TGpuStatus;
  Msg: String;
  DriveRoot: String;
begin
  Result := True;

  { Only validate when leaving the component selection page. }
  if CurPageID <> wpSelectComponents then Exit;

  { Disk space: 2 GB base, 16 GB if Chatterbox selected. }
  if ChatterboxSelected then
    Needed := Int64(16) * Int64(1024) * Int64(1024) * Int64(1024)
  else
    Needed := Int64(2) * Int64(1024) * Int64(1024) * Int64(1024);

  FreeBytes := 0;
  TotalBytes := 0;
  { ExtractFileDrive returns e.g. 'C:' without a trailing backslash,    }
  { but GetSpaceOnDisk64 REQUIRES a trailing backslash ('C:\'). Without }
  { it the call returns False and the disk-space check silently no-ops. }
  DriveRoot := AddBackslash(ExtractFileDrive(ExpandConstant('{localappdata}')));
  if GetSpaceOnDisk64(DriveRoot, FreeBytes, TotalBytes) then
  begin
    if FreeBytes < Needed then
    begin
      MsgBox(
        'Levytilaa ei riitä. Tarvitaan '
        + IntToStr(Needed div (Int64(1024) * Int64(1024) * Int64(1024)))
        + ' GB, vapaana '
        + IntToStr(FreeBytes div (Int64(1024) * Int64(1024) * Int64(1024)))
        + ' GB. Vapauta tilaa ja yritä uudelleen.',
        mbCriticalError,
        MB_OK
      );
      Result := False;
      Exit;
    end;
  end;

  { GPU + driver + VRAM checks — only when Chatterbox is selected. }
  if ChatterboxSelected then
  begin
    DetectGpuStatus(GpuStatus);

    if not GpuStatus.HasNvidiaGpu then
    begin
      if GpuStatus.OtherGpuName <> '' then
        Msg := 'Chatterbox vaatii NVIDIA-näytönohjaimen.' + #13#10
             + 'Löysimme näytönohjaimen: ' + GpuStatus.OtherGpuName + #13#10#13#10
             + 'Se ei ole yhteensopiva. Valitse Edge-TTS tai Piper '
             + 'Chatterboxin sijaan.'
      else
        Msg := 'Emme löytäneet näytönohjainta lainkaan. Chatterbox '
             + 'vaatii NVIDIA-näytönohjaimen.';
      Msg := Msg + #13#10#13#10
           + 'Jatketaanko asennusta ILMAN Chatterboxia? '
           + '(Valitse "Ei" peruaksesi asennuksen.)';
      if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
      { User chose to proceed without Chatterbox — deselect it. }
      { Syntax: "!name" means unselect; "name" means select.    }
      WizardSelectComponents('!engine_chatterbox');
    end
    else if (GpuStatus.DriverVersion > 0.0) and (GpuStatus.DriverVersion < 550.0) then
    begin
      MsgBox(
        'NVIDIA-ajuri on liian vanha.' + #13#10
        + 'Löysimme: ' + GpuStatus.GpuName + ', ajuri '
        + FormatVersion(GpuStatus.DriverVersion) + #13#10
        + 'Vaatimus: ajuri 550 tai uudempi' + #13#10#13#10
        + 'Päivitä ajuri osoitteessa https://www.nvidia.com/download/index.aspx '
        + 'ja aja asennusohjelma uudelleen.',
        mbCriticalError,
        MB_OK
      );
      Result := False;
      Exit;
    end
    else
    begin
      { VRAM sanity check — Chatterbox needs ~6 GB for comfort.  }
      if (GpuStatus.VramMB > 0) and (GpuStatus.VramMB < 6000) then
      begin
        if MsgBox(
             'Varoitus: VRAM-muisti on vähän.' + #13#10
             + 'Löysimme: ' + GpuStatus.GpuName + ', '
             + IntToStr(GpuStatus.VramMB) + ' MB VRAM' + #13#10
             + 'Suositus: vähintään 6000 MB (6 GB).' + #13#10#13#10
             + 'Chatterbox saattaa kaatua "out of memory" -virheeseen. '
             + 'Jatketaanko silti?',
             mbConfirmation, MB_YESNO) = IDNO then
        begin
          Result := False;
          Exit;
        end;
      end
      else if GpuStatus.VramMB > 0 then
      begin
        MsgBox(
          'Chatterbox käyttää: ' + GpuStatus.GpuName + #13#10
          + IntToStr(GpuStatus.VramMB div 1024) + ' GB VRAM, ajuri '
          + FormatVersion(GpuStatus.DriverVersion),
          mbInformation,
          MB_OK
        );
      end;
    end;
  end;
end;

{ -- Uninstall cleanup prompt for the Chatterbox venv -- }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  VenvDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    VenvDir := 'C:\AudiobookMaker\.venv-chatterbox';
    if DirExists(VenvDir) then
    begin
      if MsgBox(
           'Poistetaanko myös Chatterbox-ympäristö kohteesta '
           + VenvDir + ' (~15 GB)? '
           + #13#10
           + 'HuggingFace-mallivälimuisti säilytetään, '
           + 'koska muut ohjelmat saattavat käyttää sitä.',
           mbConfirmation,
           MB_YESNO
         ) = IDYES then
      begin
        DelTree(VenvDir, True, True, True);
      end;
    end;
  end;
end;
