[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonRoot = Join-Path $ProjectRoot "vendor\python-3.11.7-embed-amd64"
$PythonExe = Join-Path $PythonRoot "python.exe"
$PythonwExe = Join-Path $PythonRoot "pythonw.exe"
$TclLibrary = Join-Path $PythonRoot "Library\lib\tcl8.6"
$TkLibrary = Join-Path $PythonRoot "Library\lib\tk8.6"
$PythonBin = Join-Path $PythonRoot "Library\bin"
$DesktopStdoutLog = Join-Path $ProjectRoot "data\logs\launcher.desktop.stdout.log"
$DesktopStderrLog = Join-Path $ProjectRoot "data\logs\launcher.desktop.stderr.log"

function Write-Step {
    param([string]$Message)
    Write-Host "[private-note] $Message"
}

function Test-ManagedPidFiles {
    $runDir = Join-Path $ProjectRoot "data\run"
    $paths = @(
        (Join-Path $runDir "launcher.pid"),
        (Join-Path $runDir "app.pid"),
        (Join-Path $runDir "ollama.pid")
    )

    foreach ($path in $paths) {
        if (Test-Path $path) {
            return $true
        }
    }
    return $false
}

try {
    $env:PATH = "$PythonRoot;$PythonBin;$env:PATH"
    $env:TCL_LIBRARY = $TclLibrary
    $env:TK_LIBRARY = $TkLibrary
    $env:PYTHONHOME = $PythonRoot

    if ((Test-Path $PythonExe) -and (Test-ManagedPidFiles)) {
        Write-Step "Stopping previous managed processes..."
        & $PythonExe launcher_cli.py shutdown-all *> $null
    }

    if (-not (Test-Path $PythonExe)) {
        throw "Vendored Python runtime was not found at $PythonExe."
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
