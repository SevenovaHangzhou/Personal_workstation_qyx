#define AppName "星序 ORBIT"
[Setup]
AppId={{DBF3764D-4031-4C50-9DB4-778E40B267D3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Sevenova
DefaultDirName={localappdata}\Programs\Sevenova\Orbit
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=orbit-{#AppVersion}-windows-x86_64-setup
SetupIconFile=..\assets\orbit.ico
UninstallDisplayIcon={app}\orbit.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=no
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\orbit.exe"; IconFilename: "{app}\orbit.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\orbit.exe"; IconFilename: "{app}\orbit.ico"; Tasks: desktopicon
