[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonRoot = Join-Path $ProjectRoot "vendor\python-3.11.7-embed-amd64"
$PythonExe = Join-Path $PythonRoot "python.exe"
$TclLibrary = Join-Path $PythonRoot "Library\lib\tcl8.6"
$TkLibrary = Join-Path $PythonRoot "Library\lib\tk8.6"
$PythonBin = Join-Path $PythonRoot "Library\bin"

try {
    Push-Location $ProjectRoot
    $env:PATH = "$PythonRoot;$PythonBin;$env:PATH"
    $env:TCL_LIBRARY = $TclLibrary
    $env:TK_LIBRARY = $TkLibrary
    $env:PYTHONHOME = $PythonRoot

    if (-not (Test-Path $PythonExe)) {
        throw "Vendored Python runtime was not found at $PythonExe."
    }

    & $PythonExe -c "import launcher_core; launcher_core.force_shutdown_all()"
    if ($LASTEXITCODE -ne 0) {
        throw "managed process shutdown failed."
    }
    Write-Host "[private-note] All managed processes have been stopped."
    exit 0
} catch {
    Write-Host "Stop failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
