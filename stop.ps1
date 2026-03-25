[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir = Join-Path $ProjectRoot "data\run"
$AppPidFile = Join-Path $RunDir "app.pid"
$LauncherPidFile = Join-Path $RunDir "launcher.pid"
$AppPort = 8000
$LauncherPort = 8010

function Write-Step {
    param([string]$Message)
    Write-Host "[private-note-launcher] $Message"
}

function Read-PidValue {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return $null
    }
    $raw = (Get-Content -LiteralPath $Path -Raw).Trim()
    if (-not $raw) {
        return $null
    }
    return [int]$raw
}

function Remove-PidValue {
    param([string]$Path)
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Stop-ProcessTree {
    param([int]$Pid)

    & taskkill.exe /PID $Pid /T /F 2>$null | Out-Null
    Start-Sleep -Milliseconds 400
    if (Get-Process -Id $Pid -ErrorAction SilentlyContinue) {
        throw "taskkill failed for PID $Pid"
    }
}

function Get-PortOwnerId {
    param([int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        return [int]$listener[0].OwningProcess
    }
    return $null
}

function Get-ProcessCommandLine {
    param([int]$Pid)

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $Pid" -ErrorAction SilentlyContinue
    if ($processInfo) {
        return [string]$processInfo.CommandLine
    }
    return ""
}

function Stop-LauncherScriptProcesses {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*serve_launcher.py*" -or $_.CommandLine -like "*launcher_app:app*"
    })

    foreach ($processInfo in $processes) {
        try {
            Stop-Process -Id ([int]$processInfo.ProcessId) -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
}

function Stop-AppScriptProcesses {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*serve_app.py*" -or $_.CommandLine -like "*main:app*"
    })

    foreach ($processInfo in $processes) {
        try {
            Stop-Process -Id ([int]$processInfo.ProcessId) -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
}

try {
    $appPid = Read-PidValue -Path $AppPidFile
    $launcherPid = Read-PidValue -Path $LauncherPidFile

    if (-not $appPid) {
        $appPortOwner = Get-PortOwnerId -Port $AppPort
        if ($appPortOwner) {
            $commandLine = Get-ProcessCommandLine -Pid $appPortOwner
            if ($commandLine -like "*private-note*" -or $commandLine -like "*serve_app.py*") {
                $appPid = $appPortOwner
            }
        }
    }

    if (-not $launcherPid) {
        $launcherPortOwner = Get-PortOwnerId -Port $LauncherPort
        if ($launcherPortOwner) {
            $launcherPid = $launcherPortOwner
        }
    }

    if ($appPid) {
        try {
            Stop-ProcessTree -Pid $appPid
            Write-Step "Stopped app process tree rooted at $appPid."
        } catch {
            Write-Step "Direct app PID stop for $appPid did not complete; continuing with script-scan cleanup."
        }
    } else {
        Write-Step "No launcher-managed app process was found."
    }

    if ($launcherPid) {
        try {
            Stop-ProcessTree -Pid $launcherPid
            Write-Step "Stopped launcher process tree rooted at $launcherPid."
        } catch {
            Write-Step "Direct launcher PID stop for $launcherPid did not complete; continuing with script-scan cleanup."
        }
    } else {
        Write-Step "No launcher process was found."
    }

    Stop-AppScriptProcesses
    Stop-LauncherScriptProcesses
    Start-Sleep -Milliseconds 800

    Remove-PidValue -Path $AppPidFile
    Remove-PidValue -Path $LauncherPidFile
    exit 0
} catch {
    Write-Host "Stop failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
