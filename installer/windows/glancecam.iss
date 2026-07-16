; GlanceCam Windows installer (Inno Setup 6)
; ==========================================
; Builds GlanceCam-Setup-<ver>.exe: a standard Windows installer that drops the
; app, a private Python runtime, go2rtc, and the tray supervisor under Program
; Files, adds Start Menu shortcuts, and registers itself in Programs and
; Features so a user can find and remove it the normal way.
;
; The payload is a staging tree prepared by CI (.github/workflows/
; windows-installer.yml): app\ (the service checkout), python\ (embeddable
; Python with the app + tray deps preinstalled), go2rtc\ (go2rtc.exe +
; go2rtc.yaml), and tray\ (the supervisor and its deps). Nothing here downloads
; anything at install time; the runner assembled it all beforehand.
;
; CI injects the version and the staging path:
;   iscc /DAppVersion=1.2.3 /DStagingDir=C:\path\to\staging glancecam.iss
; Both have defaults so the script also compiles from a checkout for review.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#ifndef StagingDir
  #define StagingDir "staging"
#endif

#define MyAppName "GlanceCam"
#define MyPublisher "Syracuse3DPrintingOrg"
#define MyAppUrl "https://github.com/Syracuse3DPrintingOrg/GlanceCam"
#define MyAppPort "9292"
#define MyWebUrl "http://localhost:9292"

[Setup]
; A stable AppId keeps upgrades landing on the same install (a newer Setup.exe
; installs over the old one). Do not change it.
AppId={{6F3B9A2E-2C41-4E7A-9C0F-6B1D0E5A7C42}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
; Per-machine install: writes under Program Files and adds firewall rules, both
; of which need elevation.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
OutputBaseFilename=GlanceCam-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\tray\glancecam.ico
; Shown on the final uninstall page so the user knows their cameras survive.
CreateUninstallRegKey=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Dirs]
; Cameras and settings live in ProgramData, never inside Program Files (a
; standard user cannot write there). users-modify lets the tray and app, which
; run as the signed-in user, read and write their data.
Name: "{commonappdata}\{#MyAppName}"; Permissions: users-modify
; The tray and its child processes run as the signed-in user and append their
; logs here, under the install root. Program Files is read-only for a standard
; user by default, so grant modify on just this logs folder.
Name: "{app}\logs"; Permissions: users-modify

[Files]
; The whole staging tree. app\, python\, go2rtc\, tray\ each recurse.
Source: "{#StagingDir}\app\*";    DestDir: "{app}\app";    Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\go2rtc\*"; DestDir: "{app}\go2rtc"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\tray\*";   DestDir: "{app}\tray";   Flags: recursesubdirs createallsubdirs ignoreversion

[INI]
; Tell the tray where the data lives (it reads this at startup). Under {app},
; so it is removed cleanly on uninstall; the data it points at is not.
Filename: "{app}\glancecam.ini"; Section: "glancecam"; Key: "data_dir"; String: "{commonappdata}\{#MyAppName}\data"
; A .url the Start Menu "web page" shortcut opens.
Filename: "{app}\GlanceCam-web.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#MyWebUrl}"

[Icons]
; The app itself: pythonw runs the tray supervisor (no console window).
Name: "{group}\{#MyAppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\tray\glancecam_tray.py"""; WorkingDir: "{app}\tray"; IconFilename: "{app}\tray\glancecam.ico"; Comment: "Start and manage GlanceCam from the system tray"
Name: "{group}\{#MyAppName} web page"; Filename: "{app}\GlanceCam-web.url"; IconFilename: "{app}\tray\glancecam.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Firewall: the app on 9292 (LAN browsers) and go2rtc WebRTC on 8555 TCP+UDP.
; Delete-then-add would need two steps; Inno runs these once at install, and the
; matching deletes live in [UninstallRun], so a plain add is fine here.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GlanceCam App 9292 TCP"" dir=in action=allow protocol=TCP localport=9292"; Flags: runhidden; StatusMsg: "Opening firewall ports..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GlanceCam WebRTC 8555 TCP"" dir=in action=allow protocol=TCP localport=8555"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GlanceCam WebRTC 8555 UDP"" dir=in action=allow protocol=UDP localport=8555"; Flags: runhidden
; Optional post-install launch: the postinstall flag puts a single checked
; checkbox ("Start GlanceCam now") on the finish page. nowait so the wizard
; closes; runasoriginaluser so the tray belongs to the signed-in user, not the
; elevated installer, and its HKCU startup toggle targets the right hive.
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\tray\glancecam_tray.py"""; WorkingDir: "{app}\tray"; Flags: nowait postinstall skipifsilent runasoriginaluser; Description: "Start {#MyAppName} now"

[UninstallRun]
; Stop the tray, its child python, and go2rtc first so their files unlock
; before the uninstaller deletes them. taskkill non-zero exit (nothing running)
; is fine, so ignore the return codes.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM go2rtc.exe"; Flags: runhidden; RunOnceId: "KillGo2rtc"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM pythonw.exe"; Flags: runhidden; RunOnceId: "KillPythonw"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GlanceCam App 9292 TCP"""; Flags: runhidden; RunOnceId: "FwApp"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GlanceCam WebRTC 8555 TCP"""; Flags: runhidden; RunOnceId: "FwTcp"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GlanceCam WebRTC 8555 UDP"""; Flags: runhidden; RunOnceId: "FwUdp"

[UninstallDelete]
; The ini and the generated .url are under {app}; remove them explicitly (they
; were created after install, so they are not tracked by [Files]).
Type: files; Name: "{app}\glancecam.ini"
Type: files; Name: "{app}\GlanceCam-web.url"

[Messages]
; Finish/uninstall wording: make the data-is-kept promise explicit. Note that
; [Messages] values are NOT constant-expanded (no {commonappdata} here), so the
; ProgramData path is written out literally; {#MyAppName} is a compile-time
; ISPP define and does expand.
ConfirmUninstall=Remove {#MyAppName} from this PC?%n%nYour cameras and settings are kept in C:\ProgramData\{#MyAppName}. Delete that folder by hand if you want them gone too.
