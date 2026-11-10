; Inno Setup script for the Windows installer. packaging/build.py runs it as:
;   iscc /DAppVersion=0.1.0 /DIconFile=<icon> /DSourceDir=<PyInstaller output> /DOutputDir=<dist> ConstellationCMS.iss
; It installs for the current user only (no administrator password), adds a Start-menu entry,
; and can start ConstellationCMS when Windows starts.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C0F4E52-9B3A-4C7E-9E0B-2F1D6A5C8B71}
AppName=ConstellationCMS
AppVersion={#AppVersion}
AppVerName=ConstellationCMS {#AppVersion}
AppPublisher=ConstellationCMS
AppPublisherURL=https://github.com/FractumSeraph/ConstellationCMS
AppSupportURL=https://github.com/FractumSeraph/ConstellationCMS/issues
AppUpdatesURL=https://github.com/FractumSeraph/ConstellationCMS/releases
DefaultDirName={autopf}\ConstellationCMS
DefaultGroupName=ConstellationCMS
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=ConstellationCMS-{#AppVersion}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\ConstellationCMS.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put an icon on the desktop"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start ConstellationCMS when I sign in to Windows (it waits quietly in the tray)"; GroupDescription: "Starting:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ConstellationCMS"; Filename: "{app}\ConstellationCMS.exe"
Name: "{autodesktop}\ConstellationCMS"; Filename: "{app}\ConstellationCMS.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ConstellationCMS"; ValueData: """{app}\ConstellationCMS.exe"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\ConstellationCMS.exe"; Description: "Open ConstellationCMS now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM ConstellationCMS.exe /F"; Flags: runhidden; RunOnceId: "StopApp"
