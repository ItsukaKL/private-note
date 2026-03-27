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

function Write-Step {
    param([string]$Message)
    Write-Host "[packaging] $Message"
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
    Write-Step "Checking uv..."
    $uvCommand = Ensure-Command -Name "uv" -Hint "Install uv first: https://docs.astral.sh/uv/"

    Write-Step "Syncing project and build dependencies..."
    & $uvCommand.Source sync --group dev
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync --group dev failed."
    }

    if ($Clean) {
        Write-Step "Cleaning previous build artifacts..."
        Remove-Item -LiteralPath $DistPath -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BuildPath -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Step "Building desktop application with PyInstaller..."
    & $uvCommand.Source run pyinstaller --noconfirm --clean $SpecPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    Write-Step "Build completed. Output directory: $DistPath"
    exit 0
} catch {
    Write-Host ""
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
