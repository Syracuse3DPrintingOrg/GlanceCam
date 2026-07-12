# GlanceCam on Windows (native)

Run GlanceCam directly on a Windows PC. No Docker, no Raspberry Pi, no server:
this installer puts a private Python runtime, the app, and the bundled go2rtc
streaming engine on the machine and starts them with Windows.

## Requirements

- Windows 10 or 11, 64-bit.
- An administrator account (the installer registers startup tasks and opens
  firewall ports, both of which need elevation).

## Install

Open **Windows PowerShell as administrator** (right-click it and choose "Run
as administrator"), then run:

```powershell
irm https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/scripts/windows/install.ps1 | iex
```

When it finishes, open http://localhost:9292 in your browser and add your first
camera. Other devices on your network reach the same grid at
`http://YOUR-PC-IP:9292`.

## What gets installed, and where

Everything lives under `C:\GlanceCam` (set the `GLANCECAM_DIR` environment
variable before running to choose a different folder):

- `app\` the GlanceCam app (a checkout or a snapshot of this repo)
- `python\` a private Python 3.12 runtime, used only by GlanceCam
- `go2rtc\` the go2rtc streaming engine and its config
- `data\` your cameras, settings, and secret key
- `logs\` service logs

Two **Scheduled Tasks** run GlanceCam as the SYSTEM account and start it with
the PC:

- **GlanceCam go2rtc** runs the streaming engine.
- **GlanceCam** runs the web app on port 9292.

Both restart automatically if they stop, and both come back after a reboot. The
installer also opens three firewall ports so browsers on your LAN can connect:
TCP 9292 (the app) and TCP + UDP 8555 (go2rtc's WebRTC).

## Update

Run the exact same command again in an administrator PowerShell:

```powershell
irm https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/scripts/windows/install.ps1 | iex
```

It stops the tasks, re-fetches the app, reinstalls dependencies, refreshes
go2rtc, and starts everything again. Your cameras and settings under
`C:\GlanceCam\data` are never touched.

## Logs

Each service appends its output to a file under `C:\GlanceCam\logs`:

- `glancecam.log` the web app
- `go2rtc.log` the streaming engine

Open either in Notepad to see what happened. These files grow over time; delete
them when they get large and they will be recreated on the next start.

## Uninstall

Download the installer and run it with `-Uninstall`:

```powershell
irm https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/scripts/windows/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
```

That removes the two tasks and the firewall rules, then asks whether to delete
`C:\GlanceCam` (which holds your cameras and settings). Add `-Force` to delete
it without asking.
