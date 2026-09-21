; Inno Setup script for WispWasp.
;
; Build with:
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; This packages the PyInstaller output only. ComfyUI, PyTorch and the
; image model are not included - together they are several gigabytes, and
; bundling them would make a 7 GB installer that most people would
; abandon. The app fetches them on first run instead.
;
; The build is unsigned, so Windows will show "Windows protected your PC"
; on first run. That is expected; there is a note about it in the readme.

#define AppName "WispWasp"
; Passed in by build.ps1, which reads avcore/version.py - the one
; place the version is written down. The default is only a fallback for
; compiling this by hand.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "Unlea"
#define AppExe "WispWasp.exe"

[Setup]
; A new AppId, because this is a different product to the AudioVision
; builds that came before. Reusing the old one would make Windows treat
; this as an upgrade and replace that install rather than sitting
; alongside it.
AppId={{2D9E4F17-C8A3-4B62-A5D1-7E30F94C6B28}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=WispWasp-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install by default, so no admin prompt is needed just to try it.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=installer\LICENSE-NOTICE.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Shortcuts:"

[Files]
Source: "dist\WispWasp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Human-readable project/legal material belongs beside the executable, not
; only under PyInstaller's internal runtime folder.
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer\READ-ME-FIRST.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only the unpacked program files are removed. Settings and generated
; images under LocalAppData are deliberately left alone - uninstalling
; should not delete someone's images.
Type: filesandordirs; Name: "{app}\_internal"
