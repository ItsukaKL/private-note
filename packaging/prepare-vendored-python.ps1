[CmdletBinding()]
param(
    [string]$ArchivePath,
    [string]$SourceDir,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VendorDirName = "python-3.11.7-embed-amd64"
$VendorRoot = Join-Path $ProjectRoot "vendor"
$TargetDir = Join-Path $VendorRoot $VendorDirName

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
    if ($ArchivePath -and $SourceDir) {
        throw "Use either -ArchivePath or -SourceDir, not both."
    }
    if (-not $ArchivePath -and -not $SourceDir) {
        throw "Provide -ArchivePath <zip> or -SourceDir <python directory>."
    }
    if ((Test-Path $TargetDir) -and -not $Force) {
        Write-Step "Vendored Python already exists: $TargetDir"
        Test-VendoredPython -PythonRoot $TargetDir
        exit 0
    }
    if (-not (Test-Path $VendorRoot)) {
        New-Item -ItemType Directory -Path $VendorRoot | Out-Null
    }
    if ((Test-Path $TargetDir) -and $Force) {
        Remove-Item -LiteralPath $TargetDir -Recurse -Force
    }

    if ($ArchivePath) {
        if (-not (Test-Path $ArchivePath)) {
            throw "Archive was not found: $ArchivePath"
        }
        $stagingRoot = Join-Path $ProjectRoot "tmp\vendored-python-prepare"
        if (Test-Path $stagingRoot) {
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $stagingRoot | Out-Null

        Write-Step "Extracting archive: $ArchivePath"
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $stagingRoot -Force

        $extractedDir = Join-Path $stagingRoot $VendorDirName
        if (-not (Test-Path $extractedDir)) {
            $candidate = Get-ChildItem -LiteralPath $stagingRoot -Directory | Select-Object -First 1
            if ($null -eq $candidate) {
                throw "Archive did not contain a Python runtime directory."
            }
            $extractedDir = $candidate.FullName
        }

        Move-Item -LiteralPath $extractedDir -Destination $TargetDir
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        if (-not (Test-Path $SourceDir)) {
            throw "Source directory was not found: $SourceDir"
        }
        Write-Step "Copying source directory: $SourceDir"
        Copy-Item -LiteralPath $SourceDir -Destination $TargetDir -Recurse
    }

    Write-Step "Validating target: $TargetDir"
    Test-VendoredPython -PythonRoot $TargetDir
    Write-Step "Vendored Python is ready."
} catch {
    Write-Host ""
    Write-Host "Vendored Python preparation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
