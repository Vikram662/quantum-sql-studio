; ============================================================
;  Quantum SQL - Inno Setup script
;  Built by build.ps1 (it passes /DAppVersion=... from quantumsql/__init__.py).
;  Manual build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\QuantumSQL.iss
; ============================================================

#ifndef AppVersion
  #define AppVersion "2.0.1"
#endif
#define AppName       "Quantum SQL"
#define AppPublisher  "Quantum SQL"
#define AppExeName    "QuantumSQL.exe"
#define SourceDir     "..\dist\QuantumSQL"

[Setup]
; Keep this AppId forever: it lets new versions upgrade the installed one in place.
AppId={{A3F7E2D1-9B4C-4E8A-B2D5-C6F1E0A93827}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Quantum SQL
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\Output
OutputBaseFilename=QuantumSQL_Setup_{#AppVersion}
SetupIconFile=..\app_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Install for the current user without admin rights by default; the user may pick "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nQuantum SQL is a desktop client for MySQL and MariaDB: browse and edit data, design tables, run queries, and import or export SQL dumps.%n%nClose the application before continuing.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "sqlassoc"; Description: "Open .sql files with Quantum SQL"; GroupDescription: "File association:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.sql\OpenWithProgids"; ValueType: string; ValueName: "QuantumSQL.sql"; ValueData: ""; Flags: uninsdeletevalue; Tasks: sqlassoc
Root: HKA; Subkey: "Software\Classes\QuantumSQL.sql"; ValueType: string; ValueName: ""; ValueData: "SQL script"; Flags: uninsdeletekey; Tasks: sqlassoc
Root: HKA; Subkey: "Software\Classes\QuantumSQL.sql\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"; Tasks: sqlassoc
Root: HKA; Subkey: "Software\Classes\QuantumSQL.sql\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: sqlassoc

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Saved sessions, settings and history live in %APPDATA%\QuantumSQL.
// They survive upgrades; on uninstall the user decides whether to remove them.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\QuantumSQL');
    if DirExists(DataDir) and not UninstallSilent() then
      if MsgBox('Also delete your saved sessions, settings and query history?' + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
