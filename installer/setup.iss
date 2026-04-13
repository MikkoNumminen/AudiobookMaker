; =============================================================================
; AudiobookMaker - Inno Setup 6 Installer Script
; =============================================================================
;
; Build requirements:
;   - Inno Setup 6.x  (https://jrsoftware.org/isinfo.php)
;   - The application must be built first so that dist\AudiobookMaker\ exists
;     relative to the root of the repository (one level above this file).
;
; To compile:
;   Open this file in the Inno Setup Compiler (ISCC) or run:
;     ISCC.exe installer\setup.iss
;   from the repository root, or adjust SourceDir below accordingly.
;
; Output:
;   installer\Output\AudiobookMaker-Setup-1.0.0.exe
; =============================================================================


; -----------------------------------------------------------------------------
; [Setup] — Global installer metadata and behaviour
; -----------------------------------------------------------------------------
[Setup]

; Unique application identifier. Changing this GUID causes Windows to treat
; the new installer as a completely different application. Keep it stable
; across version updates so that upgrades work correctly.
AppId={{A3F2C1D4-8B7E-4F6A-9C2D-1E5B3A7F0D8C}

; Human-readable application name shown throughout the installer UI and in
; "Add or Remove Programs".
AppName=AudiobookMaker

; Version string shown in "Add or Remove Programs" and the title bar.
AppVersion=1.0.0

; Publisher name shown in "Add or Remove Programs".
AppPublisher=AudiobookMaker

; Publisher URL shown in "Add or Remove Programs". Leave blank if not set.
AppPublisherURL=

; Support URL shown in "Add or Remove Programs". Leave blank if not set.
AppSupportURL=

; Updates URL shown in "Add or Remove Programs". Leave blank if not set.
AppUpdatesURL=

; Copyright string embedded in the uninstaller executable.
AppCopyright=Copyright (C) 2026 AudiobookMaker Contributors

; Default installation directory. {autopf} resolves to "Program Files" on
; 64-bit Windows (the native 64-bit Program Files folder).
DefaultDirName={autopf}\AudiobookMaker

; Name of the Start Menu folder created for the app shortcuts.
DefaultGroupName=AudiobookMaker

; Allow the user to choose a different Start Menu folder name.
AllowNoIcons=yes

; Path to the MIT license file shown on the License page.
; The path is relative to the location of this .iss file.
LicenseFile=..\LICENSE.txt

; Directory where the compiled installer executable is placed.
OutputDir=Output

; Final installer filename (without the .exe extension).
OutputBaseFilename=AudiobookMaker-Setup-1.0.0

; Installer icon — shown in the Windows taskbar and on the installer window.
; Path is relative to this .iss file.
SetupIconFile=..\assets\icon.ico

; Compression algorithm and level used when packing application files.
; lzma2/ultra64 gives the best compression ratio for large bundles.
Compression=lzma2/ultra64
SolidCompression=yes

; Require Windows 10 (build 10.0) or later. The installer will refuse to run
; on older Windows versions.
MinVersion=10.0

; Restrict installation to 64-bit compatible systems only.
ArchitecturesAllowed=x64compatible

; Install into the native 64-bit directories (i.e. avoid WOW64 redirection).
ArchitecturesInstallIn64BitMode=x64compatible

; Display a "Welcome" wizard page before any other pages.
DisableWelcomePage=no

; Show a "Ready to Install" summary page before copying files.
DisableReadyPage=no

; Show an "Installation finished" page at the end.
DisableFinishedPage=no

; Prompt the user before cancelling if they click the X button mid-install.
; This prevents accidental aborts.
CloseApplications=yes

; Named mutex used by the app's single-instance guard (src/single_instance.py).
; When running with /VERYSILENT, Inno Setup uses this to wait for the app
; to exit before overwriting files — prevents "file in use" failures.
AppMutex=AudiobookMaker_SingleInstance

; Restart the application after installation if it was running beforehand
; and had to be closed.
RestartApplications=yes

; Require administrator privileges so that the app can be installed into
; Program Files and registry keys can be written to HKLM.
PrivilegesRequired=admin

; Show the "Run AudiobookMaker after setup finishes" checkbox on the last page.
; The checkbox is checked by default.
UninstallDisplayIcon={app}\AudiobookMaker.exe

; Wizard style — "modern" gives the clean two-panel layout introduced in
; Inno Setup 6. Use "classic" for the older single-panel look.
WizardStyle=modern


; -----------------------------------------------------------------------------
; [Languages] — Localisation
; -----------------------------------------------------------------------------
[Languages]

; Include the built-in English translation that ships with Inno Setup.
Name: "english"; MessagesFile: "compiler:Default.isl"


; -----------------------------------------------------------------------------
; [Tasks] — Optional installation tasks shown as checkboxes in the wizard
; -----------------------------------------------------------------------------
[Tasks]

; Desktop shortcut — shown as a checkbox on the "Select Additional Tasks" page.
; Checked by default (Flags: checkedonce means it defaults to checked only on
; the first install; on upgrades the previous user choice is remembered).
Name: "desktopicon";       \
  Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; \
  Flags: checkedonce



; -----------------------------------------------------------------------------
; [Files] — Files to copy to the installation directory
; -----------------------------------------------------------------------------
[Files]

; Copy the entire PyInstaller output folder (dist\AudiobookMaker\) into the
; installation directory. The {app} constant resolves to the directory the
; user chose on the "Select Destination Location" wizard page.
;
; Flags:
;   ignoreversion     — always overwrite regardless of version information
;                       (useful for Python bundles that embed no version data)
;   recursesubdirs    — copy all sub-folders recursively
;   createallsubdirs  — recreate the original sub-folder structure
Source: "..\dist\AudiobookMaker\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs


; -----------------------------------------------------------------------------
; [Icons] — Shortcuts created by the installer
; -----------------------------------------------------------------------------
[Icons]

; Start Menu shortcut inside the folder named by DefaultGroupName.
Name: "{group}\AudiobookMaker"; \
  Filename: "{app}\AudiobookMaker.exe"; \
  IconFilename: "{app}\AudiobookMaker.exe"; \
  Comment: "Launch AudiobookMaker"

; Start Menu shortcut to the uninstaller so users can remove the app from
; the Start Menu without opening "Add or Remove Programs".
Name: "{group}\Uninstall AudiobookMaker"; \
  Filename: "{uninstallexe}"; \
  Comment: "Uninstall AudiobookMaker"

; Desktop shortcut — only created when the "desktopicon" task is selected.
Name: "{autodesktop}\AudiobookMaker"; \
  Filename: "{app}\AudiobookMaker.exe"; \
  IconFilename: "{app}\AudiobookMaker.exe"; \
  Comment: "Launch AudiobookMaker"; \
  Tasks: desktopicon


; -----------------------------------------------------------------------------
; [Run] — Commands executed after files have been copied
; -----------------------------------------------------------------------------
[Run]

; Offer to launch AudiobookMaker immediately after installation completes.
; The "postinstall" flag adds a checkbox on the final wizard page.
; "skipifsilent" skips this step during silent (/SILENT or /VERYSILENT) installs.
Filename: "{app}\AudiobookMaker.exe"; \
  Description: "{cm:LaunchProgram,AudiobookMaker}"; \
  Flags: nowait postinstall skipifsilent


; -----------------------------------------------------------------------------
; [Registry] — Registry entries written by the installer
; -----------------------------------------------------------------------------
[Registry]

; --- "Add or Remove Programs" / "Programs and Features" registration ---
; These keys cause Windows to display AudiobookMaker in the installed-programs
; list with publisher, version, and uninstall information.

Root: HKLM; \
  Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1"; \
  ValueType: string; ValueName: "DisplayName"; \
  ValueData: "AudiobookMaker"; \
  Flags: uninsdeletekey

Root: HKLM; \
  Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1"; \
  ValueType: string; ValueName: "DisplayVersion"; \
  ValueData: "1.0.0"

Root: HKLM; \
  Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1"; \
  ValueType: string; ValueName: "Publisher"; \
  ValueData: "AudiobookMaker"



; -----------------------------------------------------------------------------
; [UninstallDelete] — Extra items removed during uninstallation
; -----------------------------------------------------------------------------
[UninstallDelete]

; Remove any files or directories that the application may have created at
; runtime inside the installation folder (e.g. logs, temp files) that would
; otherwise be left behind after the standard file removal step.
Type: filesandordirs; Name: "{app}"


; -----------------------------------------------------------------------------
; [Code] — Pascal script for custom installer logic
; -----------------------------------------------------------------------------
[Code]

// ---------------------------------------------------------------------------
// SHChangeNotify — Win32 API call used to refresh the Windows shell after
// file-association registry keys have been written.
// ---------------------------------------------------------------------------
// Inno Setup's Pascal Script does not support the `Pointer` type in external
// function declarations, so we declare the two pointer parameters as
// Cardinal (the native pointer width on 32-bit; Inno Setup installers are
// 32-bit) and always pass 0 (= NULL) from the call site.
procedure SHChangeNotify(wEventId: Integer; uFlags: Cardinal;
  dwItem1: Cardinal; dwItem2: Cardinal);
  external 'SHChangeNotify@shell32.dll stdcall';

const
  // Shell change-notification constants used to signal that file-type
  // associations have been updated.
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST       = $0000;

// ---------------------------------------------------------------------------
// CurStepChanged — Called by Inno Setup at each major step transition.
// We use the ssPostInstall step to notify the shell of association changes
// after all registry keys have been written.
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Only refresh associations if the user opted in to the pdfassoc task.
    if IsTaskSelected('pdfassoc') then
    begin
      // Tell the shell that file associations have changed. This causes
      // Explorer to refresh icons and default-program information.
      SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
    end;
  end;
end;

// ---------------------------------------------------------------------------
// InitializeSetup — Called before the installer wizard is shown.
// Use this function to enforce the Windows 10 minimum-version requirement
// with a friendly error message in addition to the MinVersion directive.
// ---------------------------------------------------------------------------
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);

  // Windows 10 has MajorVersion = 10. Reject anything older.
  if Version.Major < 10 then
  begin
    MsgBox(
      'AudiobookMaker requires Windows 10 or later.' + #13#10 +
      'Your operating system is not supported.' + #13#10#13#10 +
      'Please upgrade to Windows 10 or later and run this installer again.',
      mbCriticalError,
      MB_OK
    );
    Result := False;
  end
  else
    Result := True;
end;

// ---------------------------------------------------------------------------
// CurUninstallStepChanged — Called at each step of the uninstall process.
// We use usPostUninstall to refresh the shell after association keys have
// been removed so that .pdf icons revert to their previous state immediately.
// ---------------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Refresh the shell regardless of whether the pdfassoc task was active,
    // since the user might be uninstalling after a re-run that added it.
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
  end;
end;
