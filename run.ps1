[CmdletBinding()]
param(
    [string]$LauncherHost = "127.0.0.1",
    [int]$LauncherPort = 8010
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherUrl = "http://${LauncherHost}:$LauncherPort/"
$StatusUrl = "http://${LauncherHost}:$LauncherPort/api/status"
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "data\logs"
$RunDir = Join-Path $ProjectRoot "data\run"
$LauncherPidFile = Join-Path $RunDir "launcher.pid"
$LauncherStdoutLog = Join-Path $LogDir "launcher.stdout.log"
$LauncherStderrLog = Join-Path $LogDir "launcher.stderr.log"

function Write-Step {
    param([string]$Message)
    Write-Host "[private-note-launcher] $Message"
}

function Test-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 3
    )

    try {
        $null = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec $TimeoutSeconds
        return $true
    } catch {
        return $false
    }
}

function Test-LauncherPortListening {
    return [bool](Get-NetTCPConnection -LocalPort $LauncherPort -State Listen -ErrorAction SilentlyContinue)
}

function Wait-LauncherPortListening {
    param([int]$TimeoutSeconds = 30)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-LauncherPortListening) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Ensure-Command {
    param(
        [string]$Name,
        [string]$Hint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "$Name was not found. $Hint"
    }
    return $command
}

function Show-RecentLauncherLog {
    param([string]$Path)

    if (Test-Path $Path) {
        Write-Host ""
        Write-Host "Recent launcher log from ${Path}:"
        Get-Content -LiteralPath $Path -Tail 20
    }
}

function Open-LauncherBrowser {
    try {
        Start-Process $LauncherUrl | Out-Null
    } catch {
        Write-Step "Launcher is ready, but the browser could not be opened automatically."
        Write-Step "Open this URL manually: $LauncherUrl"
    }
}

try {
    Write-Step "Checking whether the launcher is already running..."
    if (Test-LauncherPortListening) {
        Write-Step "Launcher is already running. Opening dashboard..."
        Open-LauncherBrowser
        exit 0
    }

    Write-Step "Checking required commands..."
    $uvCommand = Ensure-Command -Name "uv" -Hint "Install uv first: https://docs.astral.sh/uv/"

    if (-not (Test-Path $PythonExe)) {
        throw "Python executable was not found at $PythonExe."
    }

    Write-Step "Ensuring Python dependencies are synced..."
    & $uvCommand.Source sync
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed."
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

    if (Test-Path $LauncherStdoutLog) {
        Remove-Item -LiteralPath $LauncherStdoutLog -Force
    }
    if (Test-Path $LauncherStderrLog) {
        Remove-Item -LiteralPath $LauncherStderrLog -Force
    }

    $env:LAUNCHER_HOST = $LauncherHost
    $env:LAUNCHER_PORT = "$LauncherPort"

    Write-Step "Starting launcher service..."
    $process = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList @("serve_launcher.py") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LauncherStdoutLog `
        -RedirectStandardError $LauncherStderrLog `
        -PassThru

    Set-Content -LiteralPath $LauncherPidFile -Value $process.Id -Encoding ascii

    Write-Step "Waiting for launcher port to become ready..."
    if (-not (Wait-LauncherPortListening -TimeoutSeconds 30)) {
        throw "Launcher did not open port $LauncherPort within 30 seconds."
    }

    if (-not (Test-HttpOk -Url $StatusUrl -TimeoutSeconds 3)) {
        Write-Step "Launcher port is ready, but /api/status did not respond to the startup probe. Continuing anyway..."
    }

    Write-Step "Launcher is ready. Opening dashboard..."
    Open-LauncherBrowser
    exit 0
} catch {
    Write-Host ""
    Write-Host "Launcher startup failed: $($_.Exception.Message)" -ForegroundColor Red
    Show-RecentLauncherLog -Path $LauncherStderrLog
    exit 1
}
