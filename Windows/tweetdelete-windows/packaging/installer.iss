; TweetDelete Windows installer script.
; Compile with Inno Setup 6 (free): https://jrsoftware.org/isinfo.php
;
; Run from the project root:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; or open this file in the Inno Setup Compiler IDE and click Compile.
;
; Produces: packaging\installer_output\TweetDelete-Setup.exe
;
; Per-user install, no admin/UAC prompt (PrivilegesRequired=lowest):
; installs to %LOCALAPPDATA%\Programs\TweetDelete, matching the same
; pattern used by VS Code's per-user installer. Requires dist\TweetDelete
; to already exist - run `pyinstaller build.spec` from the project root
; first.

#define MyAppName "TweetDelete"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "TweetDelete"
#define MyAppExeName "TweetDelete.exe"

[Setup]
AppId={{6C6F3E6D-6A63-4E33-8B7E-3F1C7A9E7A2A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userpf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer_output
OutputBaseFilename=TweetDelete-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Files]
; Source paths are relative to this .iss file's location (packaging\), so
; step up one level to the project root where `pyinstaller build.spec`
; wrote dist\TweetDelete\.
Source: "..\dist\TweetDelete\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; PyInstaller 6.x onedir builds place all bundled data files (everything
; except the main .exe) inside a _internal\ subfolder, not directly next to
; the exe - this is what server.py's resource_dir()/sys._MEIPASS lookup
; already resolves correctly at runtime, but this hardcoded installer path
; has to match it explicitly. If a future PyInstaller major version changes
; this layout again, update this path to match.
Filename: "{app}\_internal\public\TweetDelete for Windows.pdf"; Description: "View the TweetDelete help guide"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
; Best-effort attempt to close a running instance before its files are
; removed, since Windows can't delete a running .exe. If TweetDelete isn't
; running this is harmless. NOTE: this hasn't been verified on a live
; Windows machine yet - if uninstall ever reports it couldn't delete
; TweetDelete.exe, quit it from the tray icon first and run uninstall again.
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F"; Flags: runhidden; RunOnceId: "KillTweetDeleteBeforeUninstall"
