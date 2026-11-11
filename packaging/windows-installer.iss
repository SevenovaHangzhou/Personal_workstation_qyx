#ifndef AppName
#define AppName "星序 ORBIT"
#endif
#ifndef AppSlug
#define AppSlug "orbit"
#endif
#ifndef ProductId
#define ProductId "com.sevenova.Orbit"
#endif
[Setup]
#if AppSlug == "orbit"
AppId={{DBF3764D-4031-4C50-9DB4-778E40B267D3}
#else
AppId={#ProductId}
#endif
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Sevenova
DefaultDirName={localappdata}\Programs\Sevenova\{#AppSlug}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#AppSlug}-{#AppVersion}-windows-x86_64-setup
SetupIconFile=..\assets\{#AppSlug}.ico
UninstallDisplayIcon={app}\{#AppSlug}.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=no
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppSlug}.exe"; IconFilename: "{app}\{#AppSlug}.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppSlug}.exe"; IconFilename: "{app}\{#AppSlug}.ico"; Tasks: desktopicon
