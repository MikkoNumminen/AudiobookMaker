; Throwaway installer that compiles ..\keep_install_dir.iss, the exact code
; setup.iss ships, so tests\test_installer_keep_dir.py can run the old
; updater's garbled-/DIR scenario for real. Own AppId, so it never touches
; an installed AudiobookMaker.

#define AppExeName "probe.bat"

[Setup]
AppId={{6F1D2E3C-9A4B-4C5D-8E7F-0A1B2C3D4E5F}
AppName=KeepDirTest
AppVersion=1.0
DefaultDirName={localappdata}\Programs\KeepDirTest
PrivilegesRequired=lowest
OutputBaseFilename=keep_dir_test_setup
DisableProgramGroupPage=yes
Compression=none

[Files]
Source: "probe.bat"; DestDir: "{app}"

[Code]
#include "..\keep_install_dir.iss"

function InitializeSetup(): Boolean;
begin
  KeepDirDecide();
  Result := True;
end;

procedure InitializeWizard();
begin
  KeepDirApply();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    KeepDirStartApp();
end;
