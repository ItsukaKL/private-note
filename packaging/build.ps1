[CmdletBinding()]
param(
    [switch]$Clean,
    [ValidateSet("auto", "uv", "vendor")]
    [string]$Environment = "auto"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SpecPath = Join-Path $PSScriptRoot "private-note.spec"
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build"
$PythonRoot = Join-Path $ProjectRoot "vendor\python-3.11.7-embed-amd64"
$VendorPythonExe = Join-Path $PythonRoot "python.exe"
$TclLibrary = Join-Path $PythonRoot "Library\lib\tcl8.6"
$TkLibrary = Join-Path $PythonRoot "Library\lib\tk8.6"
$PythonBin = Join-Path $PythonRoot "Library\bin"
$VenvPythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BuildToolsDir = Join-Path $ProjectRoot ".build-tools"
$LocalUvVenvDir = Join-Path $BuildToolsDir "uv"
$LocalUvPythonExe = Join-Path $LocalUvVenvDir "Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[packaging] $Message"
}

function Get-UvCommand {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $uv) {
        return @($uv.Source)
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        & $python.Source -m uv --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($python.Source, "-m", "uv")
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        & $py.Source -m uv --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($py.Source, "-m", "uv")
        }
    }

    return @()
}

function Get-BootstrapPython {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        return $py.Source
    }

    throw "Python was not found. Install Python 3.10+ first, then rerun packaging/build.ps1."
}

function Ensure-LocalUv {
    if (Test-Path $LocalUvPythonExe) {
        & $LocalUvPythonExe -m uv --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($LocalUvPythonExe, "-m", "uv")
        }
    }

    $bootstrapPython = Get-BootstrapPython
    if (-not (Test-Path $BuildToolsDir)) {
        New-Item -ItemType Directory -Path $BuildToolsDir | Out-Null
    }

    Write-Step "Bootstrapping project-local uv into .build-tools..."
    & $bootstrapPython -m venv $LocalUvVenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the local uv bootstrap environment."
    }

    & $LocalUvPythonExe -m pip install --upgrade pip uv | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install uv into the local bootstrap environment."
    }

    return @($LocalUvPythonExe, "-m", "uv")
}

function Invoke-Uv {
    param(
        [string[]]$UvCommand,
        [string[]]$Arguments
    )

    & $UvCommand[0] @($UvCommand | Select-Object -Skip 1) @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv command failed: $($Arguments -join ' ')"
    }
}

function Use-VendoredPython {
    $script:BuildPythonExe = $VendorPythonExe
    $env:PATH = "$PythonRoot;$PythonBin;$env:PATH"
    $env:TCL_LIBRARY = $TclLibrary
    $env:TK_LIBRARY = $TkLibrary
    $env:PYTHONHOME = $PythonRoot
    Write-Step "Using vendored Python: $BuildPythonExe"
}

function Use-UvEnvironment {
    $uvCommand = @(Get-UvCommand)
    if ($uvCommand.Count -eq 0) {
        $uvCommand = @(Ensure-LocalUv)
    }

    Write-Step "Synchronizing locked Python environment with uv..."
    Invoke-Uv -UvCommand $uvCommand -Arguments @("sync", "--locked", "--group", "dev")

    if (-not (Test-Path $VenvPythonExe)) {
        throw "uv sync completed but .venv Python was not found at $VenvPythonExe"
    }

    $script:BuildPythonExe = $VenvPythonExe
    Remove-Item Env:\PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:\TCL_LIBRARY -ErrorAction SilentlyContinue
    Remove-Item Env:\TK_LIBRARY -ErrorAction SilentlyContinue
    Write-Step "Using uv managed Python: $BuildPythonExe"
}

try {
    $BuildPythonExe = ""
    if ($Environment -eq "vendor") {
        if (-not (Test-Path $VendorPythonExe)) {
            throw "Vendored Python runtime was not found at $VendorPythonExe."
        }
        Use-VendoredPython
    } elseif ($Environment -eq "uv") {
        Use-UvEnvironment
    } else {
        try {
            Use-UvEnvironment
        } catch {
            if (Test-Path $VendorPythonExe) {
                Write-Step "uv environment was not available; falling back to vendored Python."
                Use-VendoredPython
            } else {
                throw
            }
        }
    }

    if ($Clean) {
        Write-Step "Cleaning previous build artifacts..."
        Remove-Item -LiteralPath $DistPath -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BuildPath -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Step "Validating local build environment..."
    & $BuildPythonExe -c "import chromadb, psutil, PyInstaller, tkinter"
    if ($LASTEXITCODE -ne 0) {
        throw "Build Python environment is missing required dependencies."
    }

    Write-Step "Building desktop application..."
    & $BuildPythonExe -m PyInstaller --noconfirm --clean $SpecPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    Write-Step "Build completed. Output directory: $DistPath"
} catch {
    Write-Host ""
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
    throw
}
