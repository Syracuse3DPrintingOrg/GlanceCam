<#
GlanceCam Windows installer
===========================
Runs GlanceCam natively on a Windows 10/11 PC: no Docker, no Raspberry Pi, no
server. It installs a private Python runtime, the app, and the bundled go2rtc
streaming engine, then registers two Scheduled Tasks so GlanceCam starts with
the PC and serves the camera grid on http://localhost:9292.

The one-line install (run it in an elevated "Windows PowerShell" prompt,
Run as administrator):

  irm https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/scripts/windows/install.ps1 | iex

Re-running that exact command later is the update path: it re-fetches the app,
reinstalls dependencies, refreshes go2rtc, and restarts the tasks in place.
Your cameras and settings (under C:\GlanceCam\data) are never touched.

Uninstall (download the file first, then run):

  powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall

Add -Force to also delete C:\GlanceCam without prompting.

This script targets stock Windows PowerShell 5.1 (the version every Windows 10
and 11 ships). It does not need pwsh, NSSM, or any other extra tool.
#>

param(
    [switch]$Uninstall,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# TLS 1.2 for every download. Windows PowerShell 5.1 defaults to older
# protocols that github.com and python.org no longer accept, so set this
# before the first Invoke-WebRequest or every download fails.
[Net.ServicePointManager]::SecurityProtocol = `
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# ----------------------------------------------------------------------------
# Settings (all overridable through the environment before running)
# ----------------------------------------------------------------------------
$InstallDir = $env:GLANCECAM_DIR
if ([string]::IsNullOrWhiteSpace($InstallDir)) { $InstallDir = 'C:\GlanceCam' }

# Pinned to match install.sh. go2rtc renames its release assets between major
# versions, so bump this on purpose (and re-check the asset name below) rather
# than tracking latest.
$Go2rtcVersion = $env:GLANCECAM_GO2RTC_VERSION
if ([string]::IsNullOrWhiteSpace($Go2rtcVersion)) { $Go2rtcVersion = 'v1.9.14' }

# The official Windows embeddable Python. 3.12.x amd64. Any 3.12 release works;
# bump freely.
$PythonVersion = $env:GLANCECAM_PYTHON_VERSION
if ([string]::IsNullOrWhiteSpace($PythonVersion)) { $PythonVersion = '3.12.10' }

$RepoOwner  = 'Syracuse3DPrintingOrg'
$RepoName   = 'GlanceCam'
$RepoBranch = 'main'
$RepoUrl    = "https://github.com/$RepoOwner/$RepoName.git"
$RepoZipUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$RepoBranch.zip"

# Derived paths. All quoted at use so paths with spaces (a non-default
# GLANCECAM_DIR under "C:\Program Files") keep working.
$AppDir     = Join-Path $InstallDir 'app'
$ServiceDir = Join-Path $AppDir 'service'
$PythonDir  = Join-Path $InstallDir 'python'
$PythonExe  = Join-Path $PythonDir 'python.exe'
$Go2rtcDir  = Join-Path $InstallDir 'go2rtc'
$Go2rtcExe  = Join-Path $Go2rtcDir 'go2rtc.exe'
$Go2rtcYaml = Join-Path $Go2rtcDir 'go2rtc.yaml'
$DataDir    = Join-Path $InstallDir 'data'
$LogsDir    = Join-Path $InstallDir 'logs'

$AppTaskName    = 'GlanceCam'
$Go2rtcTaskName = 'GlanceCam go2rtc'

$AppLog    = Join-Path $LogsDir 'glancecam.log'
$Go2rtcLog = Join-Path $LogsDir 'go2rtc.log'

$AppCmd    = Join-Path $AppDir 'run-glancecam.cmd'
$Go2rtcCmd = Join-Path $Go2rtcDir 'run-go2rtc.cmd'

# Firewall rule names (used to add and to remove, so keep them stable).
$FwApp       = 'GlanceCam App 9292 TCP'
$FwWebrtcTcp = 'GlanceCam WebRTC 8555 TCP'
$FwWebrtcUdp = 'GlanceCam WebRTC 8555 UDP'

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Die($msg) {
    Write-Host "Error: $msg" -ForegroundColor Red
    exit 1
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Download($url, $dest) {
    Write-Step "Downloading $url"
    try {
        # -UseBasicParsing keeps this working on a machine that never launched
        # Internet Explorer (its DOM engine is otherwise required and absent on
        # Server Core and some hardened builds).
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    } catch {
        Die "Download failed: $url`n$($_.Exception.Message)"
    }
}

# ----------------------------------------------------------------------------
# Scheduled task lifecycle
# ----------------------------------------------------------------------------
function Stop-GlanceCamTasks {
    # Best effort: used on update before we touch locked files, and on
    # uninstall. Missing tasks are fine.
    foreach ($name in @($AppTaskName, $Go2rtcTaskName)) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        }
    }
    # go2rtc.exe is uniquely ours, so force-kill any straggler that the task
    # engine did not reap; the app's python is left to Stop-ScheduledTask so we
    # never touch an unrelated python.exe on the machine.
    taskkill /IM go2rtc.exe /F 2>$null | Out-Null
    # Give Windows a moment to release file handles before we overwrite the
    # binaries and site-packages.
    Start-Sleep -Seconds 3
}

function Register-GlanceCamTask($name, $cmdPath) {
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" `
        -Argument "/c `"$cmdPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
        -LogonType ServiceAccount -RunLevel Highest
    # Long-running server: no time limit, keep running on battery, and restart
    # if the process ever exits. RestartInterval must be at least one minute.
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    # -Force makes this both the install and the update path: it replaces an
    # existing definition in place.
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
}

# ----------------------------------------------------------------------------
# Uninstall
# ----------------------------------------------------------------------------
function Invoke-Uninstall {
    Write-Step 'Removing GlanceCam'
    Stop-GlanceCamTasks
    foreach ($name in @($AppTaskName, $Go2rtcTaskName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        }
    }
    foreach ($rule in @($FwApp, $FwWebrtcTcp, $FwWebrtcUdp)) {
        netsh advfirewall firewall delete rule name="$rule" 2>$null | Out-Null
    }
    Write-Ok 'Tasks and firewall rules removed'

    if (Test-Path $InstallDir) {
        $remove = $Force
        if (-not $remove) {
            $answer = Read-Host "Delete $InstallDir and all cameras/settings? (y/N)"
            if ($answer -match '^(y|yes)$') { $remove = $true }
        }
        if ($remove) {
            Remove-Item -Recurse -Force $InstallDir
            Write-Ok "Deleted $InstallDir"
        } else {
            Write-Warn2 "Left $InstallDir in place (cameras and settings kept)"
        }
    }
    Write-Ok 'GlanceCam uninstalled'
    exit 0
}

# ----------------------------------------------------------------------------
# Fetch the app tree
# ----------------------------------------------------------------------------
function Get-Repo {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        if (Test-Path (Join-Path $AppDir '.git')) {
            Write-Step "Updating existing checkout at $AppDir"
            git -C "$AppDir" fetch --depth 1 origin $RepoBranch
            git -C "$AppDir" reset --hard "origin/$RepoBranch"
        } else {
            Write-Step "Cloning GlanceCam to $AppDir"
            if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
            git clone --depth 1 --branch $RepoBranch $RepoUrl "$AppDir"
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Ok 'Repo ready (git)'
            return
        }
        Write-Warn2 'git fetch/clone failed; falling back to a zip download'
    }

    # No git, or git failed: download and expand the branch zip. GitHub wraps
    # the tree in a single "GlanceCam-main" folder, so expand to a temp dir and
    # copy that inner folder's contents into app\.
    $tmpZip = Join-Path $env:TEMP ("glancecam-" + [guid]::NewGuid().ToString('N') + '.zip')
    $tmpDir = Join-Path $env:TEMP ("glancecam-" + [guid]::NewGuid().ToString('N'))
    Get-Download $RepoZipUrl $tmpZip
    Write-Step 'Expanding the app archive'
    if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    $inner = Get-ChildItem -Path $tmpDir -Directory | Select-Object -First 1
    if (-not $inner) { Die 'The downloaded archive was empty.' }

    # Replace app\ contents wholesale. Cameras and settings live under
    # data\ (outside app\), so this never touches user data.
    if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $AppDir -Recurse -Force

    Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    Write-Ok 'Repo ready (zip)'
}

# ----------------------------------------------------------------------------
# Python runtime (embeddable) + pip
# ----------------------------------------------------------------------------
function Install-Python {
    if (-not (Test-Path $PythonExe)) {
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
        $tmpZip = Join-Path $env:TEMP ("python-" + [guid]::NewGuid().ToString('N') + '.zip')
        Get-Download $url $tmpZip
        Write-Step "Installing Python $PythonVersion into $PythonDir"
        if (Test-Path $PythonDir) { Remove-Item -Recurse -Force $PythonDir }
        New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
        Expand-Archive -Path $tmpZip -DestinationPath $PythonDir -Force
        Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
    } else {
        Write-Step 'Python runtime already present'
    }

    # The embeddable build ships with site imports disabled and no
    # site-packages on its path, which blocks pip and installed packages.
    # Enable both by editing the ._pth file (idempotent).
    $pth = Get-ChildItem -Path $PythonDir -Filter '*._pth' | Select-Object -First 1
    if (-not $pth) { Die "Could not find the Python ._pth file in $PythonDir" }
    $lines = Get-Content $pth.FullName
    $new = @()
    foreach ($line in $lines) {
        if ($line -match '^\s*#\s*import\s+site\s*$') { $new += 'import site' }
        else { $new += $line }
    }
    if ($new -notcontains 'import site')       { $new += 'import site' }
    if ($new -notcontains 'Lib\site-packages') { $new += 'Lib\site-packages' }
    Set-Content -Path $pth.FullName -Value $new -Encoding ASCII

    # Bootstrap pip only when it is missing; the embeddable build has none.
    & $PythonExe -m pip --version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Step 'Bootstrapping pip'
        $getPip = Join-Path $PythonDir 'get-pip.py'
        Get-Download 'https://bootstrap.pypa.io/get-pip.py' $getPip
        & $PythonExe $getPip --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { Die 'pip bootstrap failed' }
    }

    # Always (re)install requirements: on an update this pulls new deps.
    Write-Step 'Installing Python dependencies (this can take a minute)'
    & $PythonExe -m pip install --upgrade pip --no-warn-script-location
    & $PythonExe -m pip install --no-warn-script-location -r (Join-Path $ServiceDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Die 'pip install of requirements failed' }
    Write-Ok 'Python dependencies ready'
}

# ----------------------------------------------------------------------------
# go2rtc engine
# ----------------------------------------------------------------------------
function Install-Go2rtc {
    New-Item -ItemType Directory -Force -Path $Go2rtcDir | Out-Null
    # Windows amd64 asset for the pinned version, verified against the GitHub
    # releases API: go2rtc_win64.zip, which contains a single go2rtc.exe.
    $url = "https://github.com/AlexxIT/go2rtc/releases/download/$Go2rtcVersion/go2rtc_win64.zip"
    $tmpZip = Join-Path $env:TEMP ("go2rtc-" + [guid]::NewGuid().ToString('N') + '.zip')
    $tmpDir = Join-Path $env:TEMP ("go2rtc-" + [guid]::NewGuid().ToString('N'))
    Get-Download $url $tmpZip
    Write-Step "Installing go2rtc $Go2rtcVersion"
    if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    $exe = Get-ChildItem -Path $tmpDir -Filter 'go2rtc.exe' -Recurse | Select-Object -First 1
    if (-not $exe) { Die 'go2rtc.exe was not found in the downloaded archive.' }
    Copy-Item -Path $exe.FullName -Destination $Go2rtcExe -Force
    Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue

    # Config adapted from docker/go2rtc/go2rtc.yaml. GlanceCam owns the stream
    # table at runtime through go2rtc's REST API, so no streams are listed. The
    # REST API stays on loopback (only the app talks to it); WebRTC on 8555
    # stays open so LAN browsers can play the streams. Written each run: it is
    # static, deterministic config with nothing user-editable in it.
    $yaml = @"
# Managed by the GlanceCam Windows installer. GlanceCam adds and removes
# streams through go2rtc's REST API at runtime, so none are listed here.

api:
  listen: "127.0.0.1:1984"

# WebRTC must be reachable from LAN browsers. TCP plus UDP on 8555.
webrtc:
  listen: ":8555"

rtsp:
  listen: ":8554"

log:
  level: info
"@
    Set-Content -Path $Go2rtcYaml -Value $yaml -Encoding ASCII
    Write-Ok 'go2rtc installed'
}

# ----------------------------------------------------------------------------
# Launcher .cmd wrappers (they set env, working dir, and log redirection)
# ----------------------------------------------------------------------------
function Write-Launchers {
    # cmd's `set "VAR=value"` quoting keeps values with spaces intact. Output is
    # appended to a per-service log under logs\.
    $appScript = @"
@echo off
cd /d "$ServiceDir"
set "GLANCECAM_GO2RTC_URL=http://127.0.0.1:1984"
set "GLANCECAM_DATA_DIR=$DataDir"
"$PythonExe" -m uvicorn app.main:app --host 0.0.0.0 --port 9292 --no-proxy-headers >> "$AppLog" 2>&1
"@
    Set-Content -Path $AppCmd -Value $appScript -Encoding ASCII

    $go2rtcScript = @"
@echo off
cd /d "$Go2rtcDir"
"$Go2rtcExe" -config "$Go2rtcYaml" >> "$Go2rtcLog" 2>&1
"@
    Set-Content -Path $Go2rtcCmd -Value $go2rtcScript -Encoding ASCII
}

# ----------------------------------------------------------------------------
# Firewall
# ----------------------------------------------------------------------------
function Set-Firewall {
    Write-Step 'Opening firewall ports (9292 TCP, 8555 TCP+UDP)'
    # Delete-then-add is idempotent: a missing rule makes delete a harmless
    # no-op, and we never stack duplicate rules on re-runs.
    netsh advfirewall firewall delete rule name="$FwApp" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="$FwApp" dir=in action=allow protocol=TCP localport=9292 | Out-Null

    netsh advfirewall firewall delete rule name="$FwWebrtcTcp" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="$FwWebrtcTcp" dir=in action=allow protocol=TCP localport=8555 | Out-Null

    netsh advfirewall firewall delete rule name="$FwWebrtcUdp" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="$FwWebrtcUdp" dir=in action=allow protocol=UDP localport=8555 | Out-Null
    Write-Ok 'Firewall rules in place'
}

# ----------------------------------------------------------------------------
# Main install / update flow
# ----------------------------------------------------------------------------
function Invoke-Install {
    Write-Host ''
    Write-Host '  GlanceCam Windows installer' -ForegroundColor Green
    Write-Host "  Install dir: $InstallDir"
    Write-Host ''

    # Create the directory skeleton up front. Data and logs survive updates.
    foreach ($dir in @($InstallDir, $DataDir, $LogsDir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    # On an update, stop the running tasks first so we can overwrite the
    # binaries and site-packages without hitting file locks. Harmless on a
    # first install (the tasks do not exist yet).
    Stop-GlanceCamTasks

    Get-Repo
    Install-Python
    Install-Go2rtc
    Write-Launchers
    Set-Firewall

    Write-Step 'Registering startup tasks'
    Register-GlanceCamTask $Go2rtcTaskName $Go2rtcCmd
    Register-GlanceCamTask $AppTaskName $AppCmd

    Write-Step 'Starting GlanceCam'
    Start-ScheduledTask -TaskName $Go2rtcTaskName
    # Give go2rtc a couple of seconds to bind its REST API before the app talks
    # to it (not required, the app retries, but it makes the first log cleaner).
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $AppTaskName

    Write-Host ''
    Write-Ok 'GlanceCam is running.'
    Write-Host '  Open  http://localhost:9292  in your browser to add your first camera.'
    Write-Host '  Other PCs on your network reach it at  http://THIS-PC-IP:9292'
    Write-Host "  Logs: $LogsDir"
    Write-Host '  Run this same command again any time to update.'
    Write-Host ''
}

# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
if (-not (Test-Admin)) {
    Die "This installer needs an elevated prompt. Right-click 'Windows PowerShell' and choose 'Run as administrator', then run the command again."
}

if ($Uninstall) {
    Invoke-Uninstall
} else {
    Invoke-Install
}
