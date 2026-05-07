# TinyAgentOS worker installer — Windows PowerShell
# Mirror of scripts/install-worker.sh for Windows 10/11 hosts.
#
# Usage:
#     iwr -useb https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-worker.ps1 | iex
#
# or with arguments:
#     $env:TAOS_CONTROLLER_URL = 'http://10.0.0.5:6969'
#     iwr -useb https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-worker.ps1 | iex
#
# Environment / parameter overrides (matches the bash version):
#     TAOS_CONTROLLER_URL     controller URL (required)
#     TAOS_WORKER_NAME        worker display name (default: $env:COMPUTERNAME)
#     TAOS_INSTALL_DIR        install dir (default: %LOCALAPPDATA%\tinyagentos-worker)
#     TAOS_BRANCH             git branch or tag (default: master)
#     TAOS_REPO               git remote
#     TAOS_SKIP_BENCHMARK     if set, skip the on-join benchmark run
#     TAOS_SERVICE            install as service: auto (default), task, skip
#     TAOS_SKIP_INCUS         if set, skip incus install and enrollment (set automatically on Windows)

[CmdletBinding()]
param(
    [string]$ControllerUrl = $env:TAOS_CONTROLLER_URL,
    [string]$WorkerName = $env:TAOS_WORKER_NAME,
    [string]$InstallDir = $env:TAOS_INSTALL_DIR,
    [string]$Branch = $env:TAOS_BRANCH,
    [string]$Repo = $env:TAOS_REPO,
    [switch]$SkipBenchmark,
    [string]$ServiceMode = $env:TAOS_SERVICE
)

$ErrorActionPreference = 'Stop'

if (-not $ControllerUrl) {
    Write-Error "controller URL required. pass -ControllerUrl or set TAOS_CONTROLLER_URL"
    exit 2
}

if (-not $WorkerName) {
    # Default appends "-worker" so the cluster UI distinguishes the worker
    # entry from the underlying machine. Skip if the host already contains
    # "worker" to avoid "rig-worker-worker".
    if ($env:COMPUTERNAME -like '*worker*') {
        $WorkerName = $env:COMPUTERNAME
    } else {
        $WorkerName = "$($env:COMPUTERNAME)-worker"
    }
}
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'tinyagentos-worker' }
if (-not $Branch) { $Branch = 'master' }
if (-not $Repo) { $Repo = 'https://github.com/jaylfc/tinyagentos' }
if (-not $ServiceMode) { $ServiceMode = 'auto' }
if ($env:TAOS_SKIP_BENCHMARK) { $SkipBenchmark = $true }

function Log($m) { Write-Host "[worker-install] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[worker-install] $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "[worker-install] $m" -ForegroundColor Red; exit 1 }

Log "os=Windows arch=$env:PROCESSOR_ARCHITECTURE controller=$ControllerUrl name=$WorkerName"
Log "install_dir=$InstallDir branch=$Branch"

# --- system dependencies --------------------------------------------------

function Ensure-Winget-Package([string]$id, [string]$friendly) {
    $installed = $false
    try {
        $installed = (winget list --id $id -e 2>$null) -match $id
    } catch { }
    if (-not $installed) {
        Log "installing $friendly via winget"
        winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    }
}

# Python 3 check
$pythonCmd = $null
foreach ($candidate in @('python3.12', 'python3', 'python', 'py')) {
    try {
        $v = & $candidate --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match 'Python 3\.\d+') {
            $pythonCmd = $candidate
            break
        }
    } catch { }
}

if (-not $pythonCmd) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Ensure-Winget-Package 'Python.Python.3.12' 'Python 3.12'
        $pythonCmd = 'python'
    } else {
        Die "python 3 not found and winget unavailable. install python 3.12 from https://python.org first"
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Ensure-Winget-Package 'Git.Git' 'Git for Windows'
    } else {
        Die "git not found and winget unavailable. install git first"
    }
}

# --- clone / update the repo ---------------------------------------------

if (-not (Test-Path (Join-Path $InstallDir '.git'))) {
    Log "cloning $Repo into $InstallDir"
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) | Out-Null
    git clone --depth 1 --branch $Branch $Repo $InstallDir
} else {
    Log "updating existing checkout"
    Push-Location $InstallDir
    git fetch --depth 1 origin $Branch
    git reset --hard "origin/$Branch"
    Pop-Location
}

Set-Location $InstallDir

# --- incus install + controller enrollment --------------------------------
# incus is Linux-only. Windows workers can still serve non-LXC workloads.
# Set TAOS_SKIP_INCUS=0 if you are using WSL2 and have incus available via
# a shim, but this is unsupported and you're on your own.

function Install-AndEnroll-Incus {
    # Check if incus is available (e.g. via WSL2 shim — uncommon but possible)
    $incusCmd = Get-Command incus -ErrorAction SilentlyContinue
    if (-not $incusCmd) {
        # Try winget install as a best-effort
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Warn "incus not found — attempting winget install Incus.Incus"
            try {
                winget install --id Incus.Incus -e --silent `
                    --accept-source-agreements --accept-package-agreements | Out-Null
                $incusCmd = Get-Command incus -ErrorAction SilentlyContinue
            } catch { }
        }
    }

    if (-not $incusCmd) {
        Warn "incus is not available on this Windows host — LXC enrollment skipped"
        Warn "  Windows workers can still serve non-LXC workloads"
        Warn "  To enroll manually after installing incus, run:"
        Warn "    `$TOKEN = (incus config trust add controller-enroll 2>&1 | Select-Object -Last 1)"
        Warn "    Invoke-RestMethod -Method POST -Uri `"$ControllerUrl/api/cluster/workers/$WorkerName/incus-enroll`" ``"
        Warn "      -ContentType 'application/json' ``"
        Warn "      -Body (`"{`"incus_url`":`"https://<LAN_IP>:8443`",`"token`":`"`$TOKEN`"`}`")"
        return
    }

    Log "incus found at $($incusCmd.Source)"

    # First-time init
    $listResult = & incus list 2>&1
    if ($LASTEXITCODE -ne 0) {
        Log "running incus admin init --minimal (first-time setup)"
        & incus admin init --minimal
    } else {
        Log "incus daemon already initialised"
    }

    # Enable HTTPS listener
    $currentAddr = (& incus config get core.https_address 2>&1)
    if ($currentAddr -eq ':8443') {
        Log "incus HTTPS listener already set to :8443"
    } else {
        Log "enabling incus HTTPS listener on :8443"
        & incus config set core.https_address :8443
    }

    # Generate trust token
    Log "generating incus trust token for controller enrollment"
    $tokenOutput = (& incus config trust add controller-enroll 2>&1)
    $TOKEN = ($tokenOutput | Where-Object { $_ -match '\S' } | Select-Object -Last 1)
    if (-not $TOKEN) {
        Warn "failed to generate incus trust token — LXC enrollment skipped"
        return
    }

    # Detect LAN IP (first non-loopback IPv4)
    $LAN_IP = $null
    try {
        $LAN_IP = (Get-NetIPAddress -AddressFamily IPv4 `
            | Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike '127.*' } `
            | Select-Object -First 1 -ExpandProperty IPAddress)
    } catch { }
    if (-not $LAN_IP) {
        Warn "could not detect LAN IP — LXC enrollment skipped"
        return
    }
    Log "LAN IP: $LAN_IP"

    # POST to controller
    Log "enrolling incus remote with controller at $ControllerUrl"
    $enrollBody = [ordered]@{ incus_url = "https://${LAN_IP}:8443"; token = $TOKEN } | ConvertTo-Json
    try {
        $resp = Invoke-RestMethod -Method POST `
            -Uri "$ControllerUrl/api/cluster/workers/$WorkerName/incus-enroll" `
            -ContentType 'application/json' `
            -Body $enrollBody
        Log "incus remote enrolled successfully"
    } catch {
        Warn "incus enrollment failed: $_"
        Warn "  To retry manually:"
        Warn "    `$TOKEN = (incus config trust add controller-enroll 2>&1 | Select-Object -Last 1)"
        Warn "    Invoke-RestMethod -Method POST -Uri `"$ControllerUrl/api/cluster/workers/$WorkerName/incus-enroll`" ``"
        Warn "      -ContentType 'application/json' ``"
        Warn "      -Body (`"{`"incus_url`":`"https://$LAN_IP`:8443`",`"token`":`"`$TOKEN`"`}`")"
        Warn "  Set `$env:TAOS_SKIP_INCUS = '1' to skip this block on re-runs"
    }
}

if ($env:TAOS_SKIP_INCUS -eq '1' -or $env:TAOS_SKIP_INCUS -eq 'true') {
    Log "TAOS_SKIP_INCUS=1 — skipping incus install and enrollment"
} else {
    # incus is Linux-native but may be available via WSL2 shim.
    # Install-AndEnroll-Incus handles the best-effort path and prints
    # manual-retry instructions if incus is unavailable on this host.
    Install-AndEnroll-Incus
}

# --- venv + deps ---------------------------------------------------------

$venvDir = Join-Path $InstallDir '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Log "creating venv"
    & $pythonCmd -m venv $venvDir
}

Log "installing worker python deps into .venv"
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet httpx pydantic psutil fastapi uvicorn pyyaml pillow libtorrent

# --- first-boot benchmark -----------------------------------------------

if (-not $SkipBenchmark) {
    Log "running initial worker benchmark (first-join only — subsequent runs are manual)"
    try {
        & $venvPython -m tinyagentos.benchmark.runner --report-to $ControllerUrl --worker-name $WorkerName --first-join
    } catch {
        Warn "benchmark runner not available yet — skipping (worker will run without baseline scores)"
    }
}

# --- install as scheduled task / service --------------------------------

function Install-ScheduledTask {
    $taskName = 'TinyAgentOSWorker'
    $action = New-ScheduledTaskAction `
        -Execute $venvPython `
        -Argument "-m tinyagentos.worker $ControllerUrl --name $WorkerName" `
        -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -RestartCount 999
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description 'TinyAgentOS worker daemon — connects to the controller and serves inference work' | Out-Null

    Start-ScheduledTask -TaskName $taskName
    Log "worker registered as Scheduled Task '$taskName' (starts at logon, auto-restarts)"
    Log "check: Get-ScheduledTask -TaskName $taskName"
    Log "logs:  ~/.local/share/tinyagentos-worker/worker.log (if redirection enabled)"
}

if ($ServiceMode -eq 'skip') {
    Log "TAOS_SERVICE=skip — not installing a service"
    Log "run manually: cd $InstallDir; .\.venv\Scripts\python.exe -m tinyagentos.worker $ControllerUrl --name $WorkerName"
} else {
    Install-ScheduledTask
}

Log "install complete"
Log "worker name: $WorkerName"
Log "controller:  $ControllerUrl"
Log "install dir: $InstallDir"
Log "to upgrade later: cd $InstallDir; git pull; Restart-ScheduledTask -TaskName TinyAgentOSWorker"
