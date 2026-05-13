[CmdletBinding()]
param(
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VendorDir = Join-Path $ProjectRoot "vendor"
$OutputDir = Join-Path $ProjectRoot "packaging\output\runtime-assets"
$RuntimeVersion = "0.20.2"
$CpuSource = Join-Path $VendorDir "ollama-windows-amd64-cpu-$RuntimeVersion"
$GpuSource = Join-Path $VendorDir "ollama-windows-amd64-gpu-nvidia-$RuntimeVersion"

function Write-Step {
    param([string]$Message)
    Write-Host "[runtime-assets] $Message"
}

function Compress-Asset {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Missing runtime asset source: $SourcePath"
    }
    if (Test-Path $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Force
    }
    Compress-Archive -Path $SourcePath -DestinationPath $DestinationPath -CompressionLevel Optimal
}

function Compress-GpuComponent {
    param(
        [string]$ComponentName,
        [string]$AssetName
    )

    $componentSource = Join-Path $GpuSource "lib\ollama\$ComponentName"
    $staging = Join-Path $OutputDir "_stage-$ComponentName"
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Join-Path $staging "lib\ollama") -Force | Out-Null
    Copy-Item -LiteralPath $componentSource -Destination (Join-Path $staging "lib\ollama") -Recurse -Force
    Compress-Asset -SourcePath (Join-Path $staging "*") -DestinationPath (Join-Path $OutputDir $AssetName)
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

try {
    if ($Clean -and (Test-Path $OutputDir)) {
        Remove-Item -LiteralPath $OutputDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

    Write-Step "Packaging CPU runtime."
    Compress-Asset `
        -SourcePath (Join-Path $CpuSource "*") `
        -DestinationPath (Join-Path $OutputDir "PrivateNote-ollama-windows-amd64-cpu-$RuntimeVersion.zip")

    $cudaV12Root = Join-Path $GpuSource "lib\ollama\cuda_v12"
    $cudaV12CoreStaging = Join-Path $OutputDir "_stage-cuda-v12-core"
    $cudaV12DepsStaging = Join-Path $OutputDir "_stage-cuda-v12-deps"
    Remove-Item -LiteralPath $cudaV12CoreStaging, $cudaV12DepsStaging -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Join-Path $cudaV12CoreStaging "lib\ollama\cuda_v12") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $cudaV12DepsStaging "lib\ollama\cuda_v12") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $cudaV12Root "ggml-cuda.dll") -Destination (Join-Path $cudaV12CoreStaging "lib\ollama\cuda_v12") -Force
    Get-ChildItem -LiteralPath $cudaV12Root -File |
        Where-Object { $_.Name -ne "ggml-cuda.dll" } |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $cudaV12DepsStaging "lib\ollama\cuda_v12") -Force
        }

    Write-Step "Packaging GPU CUDA 12 core runtime."
    Compress-Asset `
        -SourcePath (Join-Path $cudaV12CoreStaging "*") `
        -DestinationPath (Join-Path $OutputDir "PrivateNote-ollama-gpu-cuda-v12-core-$RuntimeVersion.zip")

    Write-Step "Packaging GPU CUDA 12 dependency runtime."
    Compress-Asset `
        -SourcePath (Join-Path $cudaV12DepsStaging "*") `
        -DestinationPath (Join-Path $OutputDir "PrivateNote-ollama-gpu-cuda-v12-deps-$RuntimeVersion.zip")

    Write-Step "Packaging GPU CUDA 13 runtime."
    Compress-GpuComponent `
        -ComponentName "cuda_v13" `
        -AssetName "PrivateNote-ollama-gpu-cuda-v13-$RuntimeVersion.zip"

    Write-Step "Packaging GPU MLX CUDA 13 runtime."
    Compress-GpuComponent `
        -ComponentName "mlx_cuda_v13" `
        -AssetName "PrivateNote-ollama-gpu-mlx-cuda-v13-$RuntimeVersion.zip"

    Write-Step "Packaging GPU Vulkan runtime."
    Compress-GpuComponent `
        -ComponentName "vulkan" `
        -AssetName "PrivateNote-ollama-gpu-vulkan-$RuntimeVersion.zip"

    Remove-Item -LiteralPath $cudaV12CoreStaging, $cudaV12DepsStaging -Recurse -Force -ErrorAction SilentlyContinue
    Write-Step "Runtime assets created in $OutputDir"
} catch {
    Write-Host ""
    Write-Host "Runtime asset packaging failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
