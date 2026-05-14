[CmdletBinding()]
param(
    [string]$SourceDir,
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VendorDirName = "python-3.11.7-embed-amd64"

if (-not $SourceDir) {
    $SourceDir = Join-Path $ProjectRoot "vendor\$VendorDirName"
}
if (-not $OutputPath) {
    $OutputDir = Join-Path $PSScriptRoot "output"
    $OutputPath = Join-Path $OutputDir "PrivateNote-vendored-python-3.11.7-win-x64.zip"
} else {
    $OutputDir = Split-Path -Parent $OutputPath
}

function Write-Step {
    param([string]$Message)
    Write-Host "[vendored-python] $Message"
}

function Test-VendoredPython {
    param([string]$PythonRoot)

    $pythonExe = Join-Path $PythonRoot "python.exe"
    if (-not (Test-Path $pythonExe)) {
        throw "python.exe was not found at $pythonExe"
    }

    $env:PATH = "$PythonRoot;$(Join-Path $PythonRoot 'Library\bin');$env:PATH"
    $env:TCL_LIBRARY = Join-Path $PythonRoot "Library\lib\tcl8.6"
    $env:TK_LIBRARY = Join-Path $PythonRoot "Library\lib\tk8.6"
    $env:PYTHONHOME = $PythonRoot

    & $pythonExe -c "import chromadb, psutil, PyInstaller, tkinter; print('vendored python ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "Vendored Python is missing required runtime/build dependencies."
    }
}

try {
    if (-not (Test-Path $SourceDir)) {
        throw "Vendored Python source directory was not found: $SourceDir"
    }
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }

    Write-Step "Validating source: $SourceDir"
    Test-VendoredPython -PythonRoot $SourceDir

    if (Test-Path $OutputPath) {
        Remove-Item -LiteralPath $OutputPath -Force
    }

    Write-Step "Creating archive: $OutputPath"
    Compress-Archive -Path $SourceDir -DestinationPath $OutputPath -CompressionLevel Optimal

    Write-Step "Archive created."
} catch {
    Write-Host ""
    Write-Host "Vendored Python packaging failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
