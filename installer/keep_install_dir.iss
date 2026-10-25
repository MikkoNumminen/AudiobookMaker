// keep_install_dir.iss — included from the [Code] section of setup.iss, and
// from installer/tests/keep_install_dir_test.iss, which compiles this exact
// file into a throwaway installer to exercise it.
//
// Why: the auto-updater in 3.24.0 and older wrote the install folder into its
// relaunch .bat as UTF-8, and cmd.exe decoded it in the OEM codepage. So an
// update of an app under "E:\Äänikirjat\AudiobookMaker" ran this installer
// with /DIR="E:\├ä├ñnikirjat\AudiobookMaker", and the app moved into that new
// folder. Those old versions keep doing it on their way to a fixed one: the
// .bat is written by the version being replaced, and only this installer is
// new code. So it recognises the garbled /DIR and installs into the folder
// the app was really in.
//
// A /DIR counts as a garbled copy of the previous install folder only when
// the install is silent (the updater's /VERYSILENT), that folder does not
// exist, and it has the shape the garbling leaves: the same number of path
// parts, every ASCII part identical, and each part that held non-ASCII
// letters longer but with the same ASCII letters in the same order. Anything
// else is a real choice and is left alone.

#ifndef AppExeName
  #define AppExeName "AudiobookMaker.exe"
#endif

var
  KeepDirPrevious: String;
  KeepDirReroute: Boolean;

// Folder of the installed version, as Inno recorded it. Read before
// UninstallPreviousVersion runs, because the uninstall deletes this key.
function KeepDirReadPrevious(): String;
var
  SubKey: String;
begin
  // AppId is written "{{GUID}" in [Setup]: the doubled brace is an escape
  // that the other sections resolve but a Pascal string literal does not,
  // so it is collapsed here, at compile time. The key is "{GUID}_is1".
  SubKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
            '{#StringChange(SetupSetting("AppId"), "{{", "{")}_is1';
  if not RegQueryStringValue(HKCU, SubKey, 'Inno Setup: App Path', Result) then
    if not RegQueryStringValue(HKLM, SubKey, 'Inno Setup: App Path', Result) then
      Result := '';
end;

function KeepDirIsAscii(const S: String): Boolean;
var
  I: Integer;
begin
  Result := True;
  for I := 1 to Length(S) do
    if Ord(S[I]) > 127 then
    begin
      Result := False;
      exit;
    end;
end;

// S with every non-ASCII character removed.
function KeepDirAsciiOnly(const S: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
    if Ord(S[I]) <= 127 then
      Result := Result + S[I];
end;

// Cuts the first path part off Path and returns it. Path keeps the rest.
function KeepDirNextPart(var Path: String): String;
var
  P: Integer;
begin
  P := Pos('\', Path);
  if P = 0 then
  begin
    Result := Path;
    Path := '';
  end
  else
  begin
    Result := Copy(Path, 1, P - 1);
    Path := Copy(Path, P + 1, Length(Path));
  end;
end;

function KeepDirLooksGarbled(Previous, Requested: String): Boolean;
var
  PrevPart, ReqPart: String;
  ChangedNonAscii: Boolean;
begin
  Result := False;
  ChangedNonAscii := False;
  while (Previous <> '') or (Requested <> '') do
  begin
    if (Previous = '') or (Requested = '') then
      exit; // different number of parts
    PrevPart := KeepDirNextPart(Previous);
    ReqPart := KeepDirNextPart(Requested);
    if KeepDirIsAscii(PrevPart) then
    begin
      if CompareText(PrevPart, ReqPart) <> 0 then
        exit; // garbling never touches an ASCII part
    end
    else if PrevPart <> ReqPart then
    begin
      // Garbling turns each non-ASCII letter into two or more characters
      // and leaves the ASCII ones where they were: "Äänikirjat" becomes
      // "├ä├ñnikirjat", longer, with the same "nikirjat". A part that fails
      // either test was renamed by a person.
      if (Length(ReqPart) <= Length(PrevPart)) or
         (KeepDirAsciiOnly(ReqPart) <> KeepDirAsciiOnly(PrevPart)) then
        exit;
      ChangedNonAscii := True;
    end;
  end;
  Result := ChangedNonAscii;
end;

// Call from InitializeSetup, BEFORE UninstallPreviousVersion.
procedure KeepDirDecide();
var
  Requested: String;
begin
  KeepDirReroute := False;
  KeepDirPrevious := RemoveBackslashUnlessRoot(KeepDirReadPrevious());
  Requested := RemoveBackslashUnlessRoot(ExpandConstant('{param:DIR|}'));
  if WizardSilent() and (Requested <> '') and (KeepDirPrevious <> '')
     and DirExists(KeepDirPrevious) and not DirExists(Requested)
     and KeepDirLooksGarbled(KeepDirPrevious, Requested) then
  begin
    KeepDirReroute := True;
    Log('Garbled /DIR from an older updater: ' + Requested +
        '; installing into the previous folder instead: ' + KeepDirPrevious);
  end;
end;

// Call from InitializeWizard, where the directory /DIR set can be replaced.
procedure KeepDirApply();
begin
  if KeepDirReroute then
    WizardForm.DirEdit.Text := KeepDirPrevious;
end;

// Call from CurStepChanged(ssPostInstall). The old .bat relaunches the app
// from the garbled path, which now does not exist (cmd.exe only prints an
// error to its hidden console), so start it from where it really is.
procedure KeepDirStartApp();
var
  ResultCode: Integer;
begin
  if KeepDirReroute then
    Exec(ExpandConstant('{app}\{#AppExeName}'), '', ExpandConstant('{app}'),
         SW_SHOWNORMAL, ewNoWait, ResultCode);
end;
