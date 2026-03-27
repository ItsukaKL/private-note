[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

try {
    if (-not (Test-Path $PythonExe)) {
        throw "Python executable was not found at $PythonExe."
    }

    & $PythonExe launcher_cli.py shutdown-all
    if ($LASTEXITCODE -ne 0) {
        throw "shutdown-all command failed."
    }
    Write-Host "[private-note] All managed processes have been stopped."
    exit 0
} catch {
    Write-Host "Stop failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
