; C8.2 per-user Inno Setup installer. Build portable bundle first.
#define MyAppName "Wallpaper Orchestrator"
#define MyAppVersion "0.8.0b2"
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
; Register a corrected HKCU Run command only when this user already opted in.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--repair-startup"; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Signal the standalone agent without deleting automation.json or the library.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--stop-agent"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopWallpaperAgent"

[Code]
const
  StartupKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  StartupName = 'WallpaperOrchestratorAgent';

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Executable: String;
  ExitCode: Integer;
begin
  Result := '';
  Executable := ExpandConstant('{app}\{#MyAppExeName}');
  if FileExists(Executable) then
  begin
    // The previous installed agent must release DLLs before replacement.
    if not Exec(Executable, '--stop-agent', '', SW_HIDE,
      ewWaitUntilTerminated, ExitCode) then
    begin
      Result := 'Could not ask the previous wallpaper agent to stop. ' +
        'Close Wallpaper Orchestrator and try again.';
    end;
    // Pre-C8.2 builds may not implement --stop-agent; the installer
    // continues using its built-in CloseApplications safety checks.
  end;
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
