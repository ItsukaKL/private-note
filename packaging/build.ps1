[CmdletBinding()]
param(
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SpecPath = Join-Path $PSScriptRoot "private-note.spec"
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build"
$PythonRoot = Join-Path $ProjectRoot "vendor\python-3.11.7-embed-amd64"
$PythonExe = Join-Path $PythonRoot "python.exe"
$TclLibrary = Join-Path $PythonRoot "Library\lib\tcl8.6"
$TkLibrary = Join-Path $PythonRoot "Library\lib\tk8.6"
$PythonBin = Join-Path $PythonRoot "Library\bin"

function Write-Step {
    param([string]$Message)
    Write-Host "[packaging] $Message"
}

try {
    $env:PATH = "$PythonRoot;$PythonBin;$env:PATH"
    $env:TCL_LIBRARY = $TclLibrary
    $env:TK_LIBRARY = $TkLibrary
    $env:PYTHONHOME = $PythonRoot

    if (-not (Test-Path $PythonExe)) {
        throw "Vendored Python runtime was not found at $PythonExe. Packaging is offline-only and depends on the repository-local Python runtime."
    }

    if ($Clean) {
        Write-Step "Cleaning previous build artifacts..."
        Remove-Item -LiteralPath $DistPath -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BuildPath -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Step "Validating local build environment..."
    & $PythonExe -c "import chromadb, psutil, PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        throw "Vendored Python runtime is missing required build dependencies."
    }

    Write-Step "Building desktop application with local pinned environment..."
    & $PythonExe -m PyInstaller --noconfirm --clean $SpecPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    Write-Step "Build completed. Output directory: $DistPath"
} catch {
    Write-Host ""
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
    throw
}
