; Inno Setup script for the Voltage Lab Windows installer.
; Built automatically by .github/workflows/windows.yml; to build by hand on Windows:
;   pyinstaller ... (see the workflow), then
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\windows\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0C2B5A-3F7D-4C8E-9A51-2D8B7F4E1C93}
AppName=Voltage Lab
AppVersion={#AppVersion}
AppPublisher=Glenn Turner
AppPublisherURL=https://github.com/turnerglenn-cmyk/voltage-lab
AppSupportURL=https://github.com/turnerglenn-cmyk/voltage-lab/issues
DefaultDirName={autopf}\Voltage Lab
DefaultGroupName=Voltage Lab
DisableProgramGroupPage=yes
; Lets people without admin rights install just for themselves.
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\..\dist
OutputBaseFilename=VoltageLab-{#AppVersion}-Setup
SetupIconFile=..\voltage-lab.ico
UninstallDisplayIcon={app}\Voltage Lab.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\Voltage Lab\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Voltage Lab"; Filename: "{app}\Voltage Lab.exe"
Name: "{autodesktop}\Voltage Lab"; Filename: "{app}\Voltage Lab.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Voltage Lab.exe"; Description: "{cm:LaunchProgram,Voltage Lab}"; Flags: nowait postinstall skipifsilent
