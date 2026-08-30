#define MyAppName "HamsterPOS Reports"
#define MyAppVersion "6.7.New-UI"
; VersionInfoVersion (the exe's embedded Win32 file-version resource) must be
; strictly numeric X.X.X.X, unlike AppVersion which accepts any display string.
#define MyAppVersionInfo "6.7.0.0"
#define MyAppPublisher "HamsterPOS Reports"
#define MyAppExeName "report.exe"

[Setup]
AppId={{48E58621-5508-4C40-A670-4C0604AD2817}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\HamsterPOS Reports
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=HamsterPOSReportsSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\app_icon.ico
SetupIconFile=assets\app_icon.ico
VersionInfoVersion={#MyAppVersionInfo}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Wildcard + recursesubdirs so this keeps working unchanged if the PyInstaller
; build ever switches from --onefile to --onedir (a folder full of files
; instead of a single exe) — today dist\ just contains report.exe.
Source: "dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Belt-and-suspenders: forces removal of the whole install directory tree
; (and the dir itself) even if something wrote an extra file into {app} at
; runtime that Inno's automatic per-file uninstall tracking wouldn't know about.
Type: filesandordirs; Name: "{app}"
