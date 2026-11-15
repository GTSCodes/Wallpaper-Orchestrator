; C8.2 per-user Inno Setup installer. Build portable bundle first.
#define MyAppName "Wallpaper Orchestrator"
#define MyAppVersion "0.8.0b3"
#define MyAppExeName "WallpaperOrchestrator.exe"

[Setup]
AppId={{EF740115-A75D-44AD-8483-30B3D8E0730A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=GTSCodes
DefaultDirName={localappdata}\Programs\WallpaperOrchestrator
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=WallpaperOrchestrator-Setup
SetupIconFile=..\assets\wallpaper-orchestrator.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\WallpaperOrchestrator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Backup and startup repair run in CurStepChanged, with checked exit codes.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent; Check: CanLaunchApplication

[UninstallRun]
; Signal the standalone agent without deleting automation.json or the library.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--stop-agent"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopWallpaperAgent"

[Code]
const
  StartupKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  StartupName = 'WallpaperOrchestratorAgent';

var
  ReadyToLaunch: Boolean;

function CanLaunchApplication(): Boolean;
begin
  Result := ReadyToLaunch;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Executable: String;
  ExitCode: Integer;
begin
  Result := '';
  Executable := ExpandConstant('{app}\{#MyAppExeName}');
  if FileExists(Executable) then
  begin
    // A previous release must stop the background agent before DLL replacement.
    if not Exec(Executable, '--stop-agent', '', SW_HIDE,
      ewWaitUntilTerminated, ExitCode) then
      Result := 'Could not launch the old wallpaper agent shutdown. ' +
        'Close Wallpaper Orchestrator and retry.'
    else if ExitCode <> 0 then
      Result := 'The old wallpaper agent could not stop safely. ' +
        'Close all Wallpaper Orchestrator copies and retry.';

    if Result = '' then
    begin
      // C8.3.2+ versions support cooperative tray-hidden GUI shutdown.
      // Older versions may reject --quit-gui: Restart Manager remains enabled.
      if Exec(Executable, '--quit-gui', '', SW_HIDE,
        ewWaitUntilTerminated, ExitCode) then
      begin
        if ExitCode <> 0 then
          Log('Older or busy GUI did not acknowledge --quit-gui; ' +
            'Inno Setup CloseApplications will check file locks.');
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Executable: String;
  ExitCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Executable := ExpandConstant('{app}\{#MyAppExeName}');
  ReadyToLaunch := False;
  // The new binary exists now, but it has not opened/migrated the catalogue.
  // A failed backup blocks automatic GUI launch and is clearly reported.
  if not Exec(Executable, '--backup-user-data', '', SW_HIDE,
    ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
  begin
    Log('User-state backup failed; GUI auto-launch disabled.');
    MsgBox('Wallpaper Orchestrator was installed, but could not back up your ' +
      'existing catalogue and settings. Do not launch the new version until ' +
      'the backup problem is resolved. Check the backup-startup-error.txt ' +
      'file under your Local AppData\WallpaperOrchestrator folder.', mbError, MB_OK);
    Exit;
  end;

  // --repair-startup respects opt-out and restoration pause preferences.
  if not Exec(Executable, '--repair-startup', '', SW_HIDE,
    ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
  begin
    Log('Background startup repair failed; GUI auto-launch disabled.');
    MsgBox('Wallpaper Orchestrator was installed, but background startup ' +
      'repair failed. Review background-repair-startup-error.txt under ' +
      'Local AppData\WallpaperOrchestrator before launching.', mbError, MB_OK);
    Exit;
  end;
  ReadyToLaunch := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command, InstalledExe: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    InstalledExe := Lowercase(ExpandConstant('{app}\{#MyAppExeName}'));
    if RegQueryStringValue(HKCU, StartupKey, StartupName, Command) then
      if Pos(InstalledExe, Lowercase(Command)) > 0 then
        RegDeleteValue(HKCU, StartupKey, StartupName);
  end;
end;
