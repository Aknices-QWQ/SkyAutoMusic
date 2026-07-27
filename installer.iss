#define MyAppName "SkyAutoMusic"
#define MyAppVersion "1.0.1"
#ifndef Edition
  #define Edition "Lite"
#endif
#ifndef SourceDir
  #error SourceDir must point to the prepared standalone directory
#endif
#ifndef OutputDir
  #define OutputDir ".release-installers"
#endif

[Setup]
AppId={{71E16108-8D10-4A93-B2FB-58E54F5C1F42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} ({#Edition})
AppPublisher=Aknices && BA4KQS
AppPublisherURL=https://sky.xxlab.dev
AppSupportURL=https://github.com/Aknices-QWQ/SkyAutoMusic/issues
AppUpdatesURL=https://sky.xxlab.dev
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=SkyAutoMusic-v{#MyAppVersion}-{#Edition}-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallDisplayIcon={app}\SkyAutoMusic.exe
LicenseFile=LICENSE
VersionInfoVersion=1.0.1.0
VersionInfoCompany=Aknices && BA4KQS
VersionInfoDescription=SkyAutoMusic {#Edition} Installer
VersionInfoProductName=SkyAutoMusic

[Languages]
Name: "chinesesimp"; MessagesFile: "installer\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SkyAutoMusic"; Filename: "{app}\SkyAutoMusic.exe"
Name: "{autodesktop}\SkyAutoMusic"; Filename: "{app}\SkyAutoMusic.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SkyAutoMusic.exe"; Description: "启动 SkyAutoMusic"; Flags: nowait postinstall skipifsilent
