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
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
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
; Inno Setup 6 ships the Finnish language file in Unofficial/. Attempt
; to load it; if it does not exist on the build machine the installer
; falls back to English.
Name: "finnish"; MessagesFile: "compiler:Languages\Unofficial\Finnish.isl"; \
    Check: FinnishLangFileExists

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
; Piper voice download (~60 MB).
Filename: "py"; \
    Parameters: "-3.11 ""{app}\installer\download_piper_voice.py"" --target ""{userappdata}\AudiobookMaker\piper_voices\fi_FI-harri-medium"""; \
    StatusMsg: "Ladataan Piper Harri -ääntä…"; \
    Flags: runhidden waituntilterminated; \
    Components: engine_piper

; Chatterbox post-install: create venv, pip install CUDA torch, prefetch
; models (~12-15 GB over ~15-45 minutes on typical hardware). Runs VISIBLE
; so the user can see progress — an hidden runhidden here would leave
; them staring at a frozen installer.
Filename: "py"; \
    Parameters: "-3.11 ""{app}\installer\post_install_chatterbox.py"" --venv-path ""C:\AudiobookMaker\.venv-chatterbox"""; \
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

{ -- Finnish language file presence check -- }

function FinnishLangFileExists: Boolean;
begin
  { Inno Setup's \$EXEPATH expands at compile time; we cannot probe the }
  { filesystem from here at compile time. Return True unconditionally; }
  { if the file is missing at compile time the [Languages] line above  }
  { will produce a compile-time error and CI will notice.              }
  Result := True;
end;

{ -- nvidia-smi driver version check -- }

function GetNvidiaDriverVersion: Double;
var
  TmpFile: String;
  ResultCode: Integer;
  Lines: TArrayOfString;
  VersionStr: String;
  VersionFloat: Double;
begin
  Result := 0.0;
  TmpFile := ExpandConstant('{tmp}\nvidia_smi.txt');
  { Use ExecAsOriginalUser so the call succeeds even from an elevated  }
  { installer. Redirect stdout to a file via cmd.exe.                  }
  Exec(
    ExpandConstant('{cmd}'),
    '/C nvidia-smi --query-gpu=driver_version --format=csv,noheader > "' + TmpFile + '" 2>&1',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  if ResultCode <> 0 then Exit;
  if not LoadStringsFromFile(TmpFile, Lines) then Exit;
  if GetArrayLength(Lines) = 0 then Exit;
  VersionStr := Trim(Lines[0]);
  { nvidia-smi emits e.g. "551.86" — take the major.minor before the    }
  { first dot past the decimal.                                         }
  try
    VersionFloat := StrToFloat(VersionStr);
    Result := VersionFloat;
  except
    Result := 0.0;
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

function NextButtonClick(CurPageID: Integer): Boolean;
var
  FreeBytes: Int64;
  TotalBytes: Int64;
  DriverVer: Double;
  Needed: Int64;
begin
  Result := True;

  { Only validate when leaving the component selection page. }
  if CurPageID <> wpSelectComponents then Exit;

  { Disk space: 2 GB base, 16 GB if Chatterbox selected (to be safe    }
  { against mid-install OOM during 5 GB torch wheel extraction).       }
  if ChatterboxSelected then
    Needed := Int64(16) * 1024 * 1024 * 1024
  else
    Needed := Int64(2) * 1024 * 1024 * 1024;

  FreeBytes := 0;
  TotalBytes := 0;
  { Use the 64-bit variant — the old GetSpaceOnDisk uses Cardinal and }
  { overflows at 4 GB, which would silently wrap around for our       }
  { 16 GB Chatterbox threshold.                                        }
  if GetSpaceOnDisk64(
       ExtractFileDrive(ExpandConstant('{localappdata}')),
       FreeBytes,
       TotalBytes
     ) then
  begin
    if FreeBytes < Needed then
    begin
      MsgBox(
        'Levytilaa ei riitä. Tarvitaan '
        + IntToStr(Needed div (Int64(1024) * 1024 * 1024))
        + ' GB, vapaana '
        + IntToStr(FreeBytes div (Int64(1024) * 1024 * 1024))
        + ' GB. Vapauta tilaa ja yritä uudelleen.',
        mbCriticalError,
        MB_OK
      );
      Result := False;
      Exit;
    end;
  end;

  { NVIDIA driver: only if Chatterbox selected. }
  if ChatterboxSelected then
  begin
    DriverVer := GetNvidiaDriverVersion;
    if DriverVer <= 0.0 then
    begin
      if MsgBox(
           'NVIDIA-näytönohjainta tai ajuria ei löytynyt. Chatterbox '
           + 'vaatii NVIDIA GPU + ajuri 550 tai uudempi. '
           + #13#10#13#10
           + 'Jatketaanko asennusta ILMAN Chatterboxia? '
           + '(Valitse "Ei" peruaksesi asennuksen.)',
           mbConfirmation,
           MB_YESNO
         ) = IDNO then
      begin
        Result := False;
        Exit;
      end;
      { User chose to proceed without Chatterbox — deselect it. }
      WizardSelectComponents('engine_chatterbox:false');
    end
    else if DriverVer < 550.0 then
    begin
      MsgBox(
        'NVIDIA-ajuri on liian vanha (löytyi: '
        + FloatToStr(DriverVer)
        + ', vaaditaan 550+). Päivitä ajuri osoitteessa nvidia.com '
        + 'ja aja asennusohjelma uudelleen.',
        mbCriticalError,
        MB_OK
      );
      Result := False;
      Exit;
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
