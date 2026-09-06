# TinyAgentOS worker uninstaller — Windows PowerShell
[CmdletBinding()]
param(
    [string]$InstallDir = $env:TAOS_INSTALL_DIR
)

$ErrorActionPreference = 'Continue'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'tinyagentos-worker' }

function Log($m) { Write-Host "[worker-uninstall] $m" -ForegroundColor Cyan }

try {
    Stop-ScheduledTask -TaskName TinyAgentOSWorker -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName TinyAgentOSWorker -Confirm:$false -ErrorAction SilentlyContinue
    Log "removed Scheduled Task TinyAgentOSWorker"
} catch { }

if (Test-Path $InstallDir) {
    Log "removing $InstallDir"
    Remove-Item -Recurse -Force $InstallDir
}

Log "uninstall complete"
