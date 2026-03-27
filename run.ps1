[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonwExe = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$DesktopStdoutLog = Join-Path $ProjectRoot "data\logs\launcher.desktop.stdout.log"
$DesktopStderrLog = Join-Path $ProjectRoot "data\logs\launcher.desktop.stderr.log"

function Write-Step {
    param([string]$Message)
    Write-Host "[private-note] $Message"
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

try {
    if (Test-Path $PythonExe) {
        Write-Step "Stopping previous launcher instance if it is running..."
        & $PythonExe launcher_cli.py shutdown-all *> $null
    }

    Write-Step "Checking required commands..."
    $uvCommand = Ensure-Command -Name "uv" -Hint "Install uv first: https://docs.astral.sh/uv/"

    Write-Step "Syncing project dependencies..."
    & $uvCommand.Source sync
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed."
    }

    if (-not (Test-Path $PythonExe)) {
        throw "Python executable was not found at $PythonExe."
    }

    $launcherExe = if (Test-Path $PythonwExe) { $PythonwExe } else { $PythonExe }

    Remove-Item -LiteralPath $DesktopStdoutLog -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $DesktopStderrLog -Force -ErrorAction SilentlyContinue

    Write-Step "Starting desktop launcher..."
    Start-Process -FilePath $launcherExe -ArgumentList @("launcher_desktop.py") -WorkingDirectory $ProjectRoot | Out-Null
    exit 0
} catch {
    Write-Host ""
    Write-Host "Desktop launcher startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
