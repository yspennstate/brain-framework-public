$ErrorActionPreference = 'Stop'
$root = "$env:USERPROFILE\brain-repo\12_cognitive_architecture\resilience"
$state = "$env:USERPROFILE\.brain-resilience"
$computeState = "$env:USERPROFILE\.claude\compute\compute_state.json"
$runtimePath = Join-Path $state 'runtime.json'
$logs = Join-Path $state 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs ("backup_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

try {
    try { [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = 'BelowNormal' } catch {}
    if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
        throw "Missing pinned scheduled-backup runtime: $runtimePath; rerun install_task.ps1"
    }
    $runtime = Get-Content -Raw -LiteralPath $runtimePath | ConvertFrom-Json
    $python = [string]$runtime.python_path
    if (-not $python -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Pinned Python runtime is unavailable: $python"
    }

    function Test-OpenComputeWindow {
        try {
            if (-not (Test-Path -LiteralPath $computeState -PathType Leaf)) { return $false }
            $value = Get-Content -Raw -LiteralPath $computeState | ConvertFrom-Json
            if ([string]$value.status -eq 'distress' -or [bool]$value.owner_active) { return $false }
            return ([string]$value.mode -in @('open', 'owner_away_open'))
        } catch {
            return $false
        }
    }

    $deadline = (Get-Date).AddHours(5)
    while (-not (Test-OpenComputeWindow)) {
        if ((Get-Date) -ge $deadline) {
            ("{0:o} DEFERRED no owner-away/open compute window before deadline" -f (Get-Date)) |
                Add-Content -LiteralPath $log -Encoding utf8
            exit 3
        }
        Start-Sleep -Seconds 300
    }

    & $python (Join-Path $root 'backup_brain.py') run *>> $log
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "backup_brain.py exited $code" }
    exit 0
} catch {
    ("{0:o} ERROR {1}" -f (Get-Date), $_.Exception.Message) | Add-Content -LiteralPath $log -Encoding utf8
    exit 1
}
