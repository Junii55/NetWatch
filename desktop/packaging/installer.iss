; NetWatch Desktop - Windows installer (Inno Setup 6)
;
;   iscc /DEdition=retail   packaging\installer.iss
;   iscc /DEdition=personal packaging\installer.iss
;
; Produces dist\NetWatch-Setup-<version>.exe - the single file a customer
; double-clicks. Installs per-user so it needs NO admin rights, which removes
; a UAC prompt that would otherwise lose people at the first step.

#ifndef Edition
  #define Edition "retail"
#endif

#if Edition == "retail"
  #define AppName    "NetWatch"
  #define ExeBase    "NetWatch"
  #define AppId      "{{B4E3C2A1-7D5F-4E8B-9A6C-1F2E3D4C5B6A}"
#else
  #define AppName    "NetWatch Personal"
  #define ExeBase    "NetWatchPersonal"
  #define AppId      "{{C5F4D3B2-8E6A-4F9C-A07D-2A3B4C5D6E7F}"
#endif

#define AppVersion   "2.4.1"
#define AppPublisher "junii55"
#define AppCopyright "Copyright (C) 2026 junii55 - AGPL v3"
#define AppUrl       "https://github.com/junii55/netwatch"
#define AppExe       ExeBase + ".exe"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright={#AppCopyright}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={localappdata}\Programs\{#ExeBase}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename={#ExeBase}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user: no UAC prompt, no admin account needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SetupIconFile=netwatch.ico
; Repository root, two levels up from packaging/ - the licence lives there, not
; in desktop/, so the installer shows the AGPL the project actually ships under.
LicenseFile=..\..\LICENSE
; Sign both the app and this installer before shipping, or Windows will warn
; every customer. Smart App Control blocks unsigned binaries outright.
;SignTool=mysigner $f

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\{#ExeBase}\*"; DestDir: "{app}"; \
    Excludes: "install.ps1,Install *.bat"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nothing here on purpose. %LOCALAPPDATA%\NetWatch holds the tag private keys,
; and losing them makes every flashed board permanently unlocatable, so user
; data survives an uninstall and a reinstall picks it straight back up.

[Code]
// WebView2 backs the app window. Windows 11 ships it; check anyway so a missing
// runtime produces a clear message instead of a blank window.
function WebView2Installed: Boolean;
var
  V: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V);
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not WebView2Installed then
  begin
    if MsgBox('{#AppName} needs the Microsoft Edge WebView2 runtime, which was not found.'
              + #13#10#13#10 +
              'Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and run '
              + 'this installer again.' + #13#10#13#10 + 'Continue anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;
