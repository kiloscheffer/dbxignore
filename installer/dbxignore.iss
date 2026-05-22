; Inno Setup script for the dbxignore Windows installer.
;
; Per-user install (no admin / UAC): lays down the PyInstaller onedir
; bundle under %LOCALAPPDATA%\Programs\dbxignore, adds that directory to
; the per-user PATH, and — when the "register the daemon" task is left
; selected — runs `dbxignore install` to register the Task Scheduler
; entry and the Explorer right-click verbs.
;
; Build:  ISCC.exe /DAppVersion=<version> installer\dbxignore.iss
; Output: dist\dbxignore-setup.exe

#ifndef AppVersion
  #error Compile with: ISCC /DAppVersion=<version> installer\dbxignore.iss
#endif

#define AppName "dbxignore"
#define AppPublisher "Kilo Scheffer"
#define AppUrl "https://github.com/kiloscheffer/dbxignore"

[Setup]
AppId={{35E9C42C-3C83-499F-A9DD-0DF94440D7BC}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
; Per-user install: {autopf} resolves to %LOCALAPPDATA%\Programs under
; PrivilegesRequired=lowest, so no admin rights and no UAC prompt.
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; ChangesEnvironment broadcasts WM_SETTINGCHANGE so new shells see the
; PATH edit without a logout.
ChangesEnvironment=yes
; Restart Manager safety net: close a daemon holding dbxignorew.exe on an
; upgrade. RestartApplications=no — the daemon is not a normal app to relaunch.
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=dbxignore-setup
SetupIconFile=..\pyinstaller\dbxignore-app.ico
UninstallDisplayIcon={app}\dbxignore.exe
UninstallDisplayName={#AppName}

[Files]
; The two executables are named explicitly rather than folded into a
; wildcard: ISCC errors at compile time if a named source file is
; missing, so an incomplete dist\dbxignore\ (e.g. a build interrupted
; by file locks) fails the build instead of silently producing an
; installer with no binaries. _internal\ is the shared dependency tree.
Source: "..\dist\dbxignore\dbxignore.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\dbxignore\dbxignorew.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\dbxignore\_internal\*"; DestDir: "{app}\_internal"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

[Tasks]
; Checked by default. A [Tasks] entry (not a Finished-page [Run]
; postinstall checkbox) is controllable from the command line via
; /MERGETASKS=!registerdaemon, which the CI smoke test uses to skip
; the post-install daemon registration.
Name: "registerdaemon"; \
  Description: "Register the dbxignore background daemon and Explorer right-click menu"

[Run]
; Gated on the task. runhidden suppresses the console flash.
Filename: "{app}\dbxignore.exe"; Parameters: "install"; \
  StatusMsg: "Registering the dbxignore daemon..."; \
  Flags: runhidden; Tasks: registerdaemon

[UninstallRun]
; Runs before [Files] removal, so dbxignore.exe still exists.
; {code:GetPurgeFlag} appends " --purge" iff the uninstall dialog said Yes.
Filename: "{app}\dbxignore.exe"; Parameters: "uninstall{code:GetPurgeFlag}"; \
  RunOnceId: "DbxignoreUninstall"; Flags: runhidden

[Code]
const
  EnvKey = 'Environment';

var
  PurgeOnUninstall: Boolean;

function NeedsAddPath(const Dir: string): Boolean;
var
  ExistingPath: string;
begin
  { Add the dir if the per-user PATH value is missing entirely. }
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', ExistingPath) then
  begin
    Result := True;
    exit;
  end;
  { Case-insensitive segment search. Both sides wrapped in ';' so a
    partial match (C:\dbxignore vs C:\dbxignore-x) cannot register as
    already present. }
  Result := Pos(
    ';' + Uppercase(Dir) + ';',
    ';' + Uppercase(ExistingPath) + ';') = 0;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  { Before file copy, stop a daemon a previous install registered. On an
    upgrade dbxignorew.exe may be running (Task Scheduler launched it at
    logon) and would lock its own image file. `schtasks /End` stops the
    task instance; a non-zero exit (no such task on a first install) is
    expected and ignored. }
  if CurStep = ssInstall then
    Exec('schtasks.exe', '/End /TN dbxignore', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
end;

function InitializeUninstall(): Boolean;
begin
  { Ask once, up front, whether to also clear the ignore markers (the one
    user-data-touching action that the plain uninstall path leaves alone).
    SuppressibleMsgBox — NOT MsgBox: a plain MsgBox is displayed even
    under /VERYSILENT and blocks forever on a headless silent uninstall
    (`winget uninstall` runs the uninstaller silently). SuppressibleMsgBox
    honors /SUPPRESSMSGBOXES and returns the Default argument (IDNO)
    without displaying — so a silent uninstall never purges markers.
    Interactive uninstalls still show the dialog, with No as the default
    button (MB_DEFBUTTON2). }
  PurgeOnUninstall :=
    SuppressibleMsgBox(
      'Also clear all dbxignore ignore markers?' + #13#10#13#10 +
      'This makes Dropbox re-upload every file that was previously ignored.'
        + #13#10 +
      'Choose No to keep your markers.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
  Result := True;
end;

function GetPurgeFlag(Param: string): string;
begin
  if PurgeOnUninstall then
    Result := ' --purge'
  else
    Result := '';
end;

procedure RemovePath(const Dir: string);
var
  ExistingPath: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', ExistingPath) then
    exit;
  { Locate the ';Dir;'-wrapped segment in the ';'-prefixed path. }
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(ExistingPath) + ';');
  if P = 0 then
    exit;
  if P = 1 then
    { Dir is the first segment: "Dir;rest" -> "rest". }
    Delete(ExistingPath, 1, Length(Dir) + 1)
  else
    { Non-first segment: drop the leading ';' + Dir. }
    Delete(ExistingPath, P - 1, Length(Dir) + 1);
  RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', ExistingPath);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemovePath(ExpandConstant('{app}'));
end;

[Registry]
; Append {app} to the per-user PATH (REG_EXPAND_SZ). The Check guard
; skips the entry when {app} is already a PATH segment (re-install /
; upgrade), so the value is never duplicated.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; \
  Check: NeedsAddPath(ExpandConstant('{app}'))
