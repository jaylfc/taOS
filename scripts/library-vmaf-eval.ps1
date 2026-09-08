<#
.SYNOPSIS
Compute VMAF per (source, variant) pair via ffmpeg libvmaf.
Measurement-only harness for the Library P4 research spike.
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$Config
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Config -PathType Leaf)) {
    Write-Error "config not found: $Config"
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-Path([string]$p) {
    if ($p -notmatch '^[\\/]') {
        Join-Path (Join-Path $ScriptDir '..') $p
    } else {
        $p
    }
}

Write-Output 'video,variant,vmaf_mean,bytes_source,bytes_variant,saving_pct'

$pairs = Get-Content -Raw -Path $Config | ConvertFrom-Json
$hadFailure = $false
foreach ($pair in $pairs.pairs) {
    $sourcePath = Resolve-Path $pair.source
    $variantPath = Resolve-Path $pair.variant

    if (-not (Test-Path $sourcePath -PathType Leaf)) {
        [Console]::Error.WriteLine("source not found: $sourcePath")
        $hadFailure = $true
        continue
    }
    if (-not (Test-Path $variantPath -PathType Leaf)) {
        [Console]::Error.WriteLine("variant not found: $variantPath")
        $hadFailure = $true
        continue
    }

    $bytesSource = (Get-Item $sourcePath).Length
    $bytesVariant = (Get-Item $variantPath).Length

    $vmafOutput = & ffmpeg -hide_banner -i "$sourcePath" -i "$variantPath" `
        -lavfi "[0:v][1:v]libvmaf" -f null - 2>&1
    $ffmpegExit = $LASTEXITCODE

    if ($ffmpegExit -ne 0) {
        [Console]::Error.WriteLine("ffmpeg failure: $sourcePath / $variantPath")
        $hadFailure = $true
        continue
    }

    $vmafMean = 0
    $scoreParsed = $false
    foreach ($line in $vmafOutput) {
        if ($line -match 'VMAF score: ([\d.]+)') {
            $vmafMean = [double]$Matches[1]
            $scoreParsed = $true
            break
        }
    }

    if (-not $scoreParsed) {
        $hadFailure = $true
        $vmafMean = "ERROR"
        $savingPct = "ERROR"
    } else {
        $savingPct = [math]::Round((1 - $bytesVariant / $bytesSource) * 100, 2)
    }

    $variantBase = Split-Path -Leaf $variantPath
    Write-Output "$($pair.video),$variantBase,$vmafMean,$bytesSource,$bytesVariant,$savingPct"
}

if ($hadFailure) {
    exit 1
}
