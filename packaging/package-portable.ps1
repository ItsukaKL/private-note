[CmdletBinding()]
param(
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildScript = Join-Path $PSScriptRoot "build.ps1"
$PyprojectPath = Join-Path $ProjectRoot "pyproject.toml"
$DistAppPath = Join-Path $ProjectRoot "dist\PrivateNoteDesktop"
$OutputDir = Join-Path $ProjectRoot "packaging\output"
$GuideSourcePath = Join-Path $PSScriptRoot "portable-guide.txt"
$GuideTargetPath = Join-Path $DistAppPath "README.txt"

function Write-Step {
    param([string]$Message)
    Write-Host "[portable] $Message"
}

function Get-ProjectVersion {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return "dev"
    }

    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*version\s*=\s*"([^"]+)"\s*$') {
            return $Matches[1]
        }
    }

    return "dev"
}

try {
    $version = Get-ProjectVersion -Path $PyprojectPath
    $zipName = "PrivateNoteDesktop-portable-v$version-win-x64.zip"
    $zipPath = Join-Path $OutputDir $zipName

    Write-Step "Building one-folder desktop application..."
    & $BuildScript -Clean:$Clean

    if (-not (Test-Path $DistAppPath)) {
        throw "Portable app directory was not created: $DistAppPath"
    }

    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }

    if (Test-Path $GuideSourcePath) {
        Copy-Item -LiteralPath $GuideSourcePath -Destination $GuideTargetPath -Force
    }

    if (Test-Path $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }

    Write-Step "Compressing portable directory..."
    Compress-Archive -Path $DistAppPath -DestinationPath $zipPath -CompressionLevel Optimal

    Write-Step "Portable package created: $zipPath"
    exit 0
} catch {
    Write-Host ""
    Write-Host "Portable packaging failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
