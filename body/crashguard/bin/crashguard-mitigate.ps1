# CrashGuard Mitigation Engine.
# Invoked by the sentinel when tier changes. Applies tier-mapped actions from policies.json.

[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('green','yellow','orange','red','critical')][string]$Tier,
    [string]$Reasons = '',
    [switch]$Rollback
)

$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'crashguard-common.ps1')

$policies = CG-LoadJson (Join-Path $CG_CONFIGS 'policies.json')
$activePath = Join-Path $CG_STATE 'active-mitigations.json'
$active = if (Test-Path $activePath) { CG-LoadJson $activePath } else { @() }
if ($null -eq $active) { $active = @() }
$active = [System.Collections.ArrayList]@($active)

# Suspend/Resume helpers live in common.ps1 as CG-Suspend / CG-Resume (using CGShared.NtProc).

function CG-SuspendProcess { param([int]$ProcessId) CG-Suspend $ProcessId }
function CG-ResumeProcess  { param([int]$ProcessId) CG-Resume  $ProcessId }

function CG-SetPriority {
    param([int]$ProcessId, $PriorityName)
    try {
        $resolved = CG-ResolvePriorityClass $PriorityName
        if ($null -eq $resolved) { return $false }
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        $p.PriorityClass = $resolved
        return $true
    } catch { return $false }
}

function CG-RecordMitigation {
    param([string]$Action, [int]$ProcessId, [string]$ProcessName, $Before, $After, $ProcessStartedAt = $null)
    $rec = @{
        action     = $Action
        pid        = $ProcessId
        name       = $ProcessName
        # Enum values serialize to integers in Windows PowerShell 5.1. Persist
        # names so rollback can restore them reliably; the resolver also accepts
        # legacy numeric records already on disk.
        before     = if ($null -eq $Before) { $null } else { [string]$Before }
        after      = if ($null -eq $After) { $null } else { [string]$After }
        process_started_at = if ($ProcessStartedAt) { ([datetime]$ProcessStartedAt).ToUniversalTime().ToString('o') } else { $null }
        applied_at = CG-Now
        tier       = $Tier
    }
    [void]$active.Add($rec)
}

function CG-EnumWorkloads {
    $fgPid = CG-ForegroundProcessId
    $byCrit = @{}
    Get-Process | ForEach-Object {
        $info = CG-CriticalityOf $policies $_.Name $_.Id $fgPid
        $crit = $info.criticality
        if (-not $byCrit.ContainsKey($crit)) { $byCrit[$crit] = New-Object 'System.Collections.ArrayList' }
        [void]$byCrit[$crit].Add([PSCustomObject]@{
            Process = $_
            Name = $_.Name
            Id = $_.Id
            MemMB = [int]($_.WorkingSet64 / 1MB)
            CpuSec = $_.CPU
            Criticality = $crit
            Label = $info.label
        })
    }
    return $byCrit
}

function CG-Action-Throttle {
    param([string]$Class)
    $by = CG-EnumWorkloads
    if (-not $by.ContainsKey($Class)) { return 0 }
    $n = 0
    foreach ($w in $by[$Class]) {
        # Skip if foreground
        try {
            $before = $w.Process.PriorityClass
            if ($before -in @('BelowNormal','Idle')) { continue }
            $ok = CG-SetPriority -ProcessId $w.Id -PriorityName 'BelowNormal'
            if ($ok) {
                CG-Log 'mitigations' 'info' "THROTTLE pid=$($w.Id) name=$($w.Name) crit=$Class $before->BelowNormal"
                CG-RecordMitigation 'throttle' $w.Id $w.Name $before 'BelowNormal' $w.Process.StartTime
                $n++
            }
        } catch {}
    }
    return $n
}

function CG-Action-Suspend {
    param([string]$Class)
    $by = CG-EnumWorkloads
    if (-not $by.ContainsKey($Class)) { return 0 }
    $n = 0
    foreach ($w in $by[$Class]) {
        # Skip if already suspended
        $alreadySuspended = $active | Where-Object { $_.action -eq 'suspend' -and $_.pid -eq $w.Id }
        if ($alreadySuspended) { continue }
        $ok = CG-SuspendProcess -ProcessId $w.Id
        if ($ok) {
            CG-Log 'mitigations' 'warn' "SUSPEND pid=$($w.Id) name=$($w.Name) crit=$Class mem=$($w.MemMB)MB"
            CG-RecordMitigation 'suspend' $w.Id $w.Name 'running' 'suspended' $w.Process.StartTime
            $n++
        }
    }
    return $n
}

function CG-Action-Kill {
    param([string]$Class)
    $by = CG-EnumWorkloads
    if (-not $by.ContainsKey($Class)) { return 0 }
    $n = 0
    foreach ($w in $by[$Class]) {
        try {
            Stop-Process -Id $w.Id -Force -ErrorAction Stop
            CG-Log 'mitigations' 'warn' "KILL pid=$($w.Id) name=$($w.Name) crit=$Class"
            CG-RecordMitigation 'kill' $w.Id $w.Name 'running' 'killed' $w.Process.StartTime
            $n++
        } catch {}
    }
    return $n
}

function CG-Action-LowerGpuPower {
    $nvsmi = CG-FindNvSmi
    if (-not $nvsmi) { return $false }
    try {
        $out = & $nvsmi --query-gpu=power.limit,power.min_limit --format=csv,noheader,nounits 2>$null
        if (-not $out) { return $false }
        $line = ($out -split "`r?`n")[0] -split '\s*,\s*'
        if ($line[0] -notmatch '^[\d.]+$') {
            CG-Log 'mitigations' 'info' "lower-gpu-power skipped: power.limit unreadable ('$($line[0])')"
            return $false
        }
        $cur = [double]$line[0]
        $minL = if ($line[1] -match '^[\d.]+$') { [double]$line[1] } else { $cur * 0.7 }
        $target = [Math]::Max($minL, $cur * 0.8)
        $r = & $nvsmi -i 0 -pl ([int]$target) 2>&1
        CG-Log 'mitigations' 'info' "GPU power limit set: $cur->$target (out: $r)"
        CG-RecordMitigation 'gpu_power_limit' 0 'GPU' $cur $target
        return $true
    } catch { CG-Log 'mitigations' 'error' "lower-gpu-power failed: $_"; return $false }
}

function CG-Action-DropGpuClocks {
    param([switch]$Min)
    $nvsmi = CG-FindNvSmi
    if (-not $nvsmi) { return $false }
    try {
        $maxOut = & $nvsmi --query-gpu=clocks.max.gr,clocks.min.gr --format=csv,noheader,nounits 2>$null
        if (-not $maxOut) { return $false }
        $line = ($maxOut -split "`r?`n")[0] -split '\s*,\s*'
        $maxC = [int]$line[0]
        $minC = [int]$line[1]
        $target = if ($Min) { $minC + [int](($maxC - $minC) * 0.1) } else { $minC + [int](($maxC - $minC) * 0.5) }
        $r = & $nvsmi -i 0 -lgc $minC,$target 2>&1
        CG-Log 'mitigations' 'info' "GPU clocks locked: min=$minC max=$target (out: $r)"
        CG-RecordMitigation 'gpu_clock_lock' 0 'GPU' "$minC-$maxC" "$minC-$target"
        return $true
    } catch { CG-Log 'mitigations' 'error' "drop-gpu-clocks failed: $_"; return $false }
}

function CG-Action-ResetGpu {
    $nvsmi = CG-FindNvSmi
    if (-not $nvsmi) { return $false }
    try {
        & $nvsmi -i 0 -rgc 2>&1 | Out-Null
        & $nvsmi -i 0 -rmc 2>&1 | Out-Null
        & $nvsmi -i 0 -pm 0 2>&1 | Out-Null
        CG-Log 'mitigations' 'info' "GPU clocks/power reset to defaults"
        return $true
    } catch { return $false }
}

function CG-Action-DropHwAccelBrowsers {
    # Sticky setting; takes effect on browser restart. Schedules a soft-reload notify.
    # For real-time relief, we instead suspend the GPU-using webview2 processes.
    $by = CG-EnumWorkloads
    $browserNames = @('chrome','msedge','msedgewebview2','brave','firefox','opera','vivaldi')
    $n = 0
    foreach ($crit in @('low','medium','expendable')) {
        if (-not $by.ContainsKey($crit)) { continue }
        foreach ($w in $by[$crit]) {
            if ($browserNames -contains $w.Name) {
                $ok = CG-SuspendProcess -ProcessId $w.Id
                if ($ok) {
                    CG-RecordMitigation 'suspend_browser_gpu' $w.Id $w.Name 'running' 'suspended' $w.Process.StartTime
                    $n++
                }
            }
        }
    }
    return $n
}

function CG-Action-Notify {
    param([string]$Title, [string]$Msg)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $ni = New-Object System.Windows.Forms.NotifyIcon
        $ni.Icon = [System.Drawing.SystemIcons]::Warning
        $ni.BalloonTipTitle = $Title
        $ni.BalloonTipText = $Msg
        $ni.Visible = $true
        $ni.ShowBalloonTip(8000)
        Start-Sleep -Milliseconds 200
        $ni.Dispose()
    } catch {}
    CG-Log 'mitigations' 'warn' "NOTIFY: $Title - $Msg"
}

# --- ROLLBACK PATH ---
if ($Rollback) {
    CG-Log 'mitigations' 'info' "Beginning rollback (target tier=$Tier)"
    $tierIdx = @('green','yellow','orange','red','critical').IndexOf($Tier)
    $remaining = New-Object 'System.Collections.ArrayList'
    foreach ($m in $active) {
        $mTierIdx = @('green','yellow','orange','red','critical').IndexOf($m.tier)
        if ($mTierIdx -le $tierIdx) {
            [void]$remaining.Add($m)
            continue
        }
        # Roll back
        $rollbackOk = $true
        switch ($m.action) {
            'throttle' {
                $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                $same = if ($proc) { CG-IsSameProcess $proc $m.name $m.process_started_at } else { $false }
                $ok = if (-not $proc -or -not $same) { $true } else { CG-SetPriority -ProcessId $m.pid -PriorityName $m.before }
                $rollbackOk = $ok
                CG-Log 'mitigations' 'info' "ROLLBACK throttle pid=$($m.pid) same_process=$same -> $($m.before) ok=$ok"
            }
            'suspend' {
                $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                $same = if ($proc) { CG-IsSameProcess $proc $m.name $m.process_started_at } else { $false }
                $ok = if (-not $proc -or -not $same) { $true } else { CG-ResumeProcess -ProcessId $m.pid }
                $rollbackOk = $ok
                CG-Log 'mitigations' 'info' "ROLLBACK suspend pid=$($m.pid) same_process=$same ok=$ok"
            }
            'suspend_browser_gpu' {
                $proc = Get-Process -Id $m.pid -ErrorAction SilentlyContinue
                $same = if ($proc) { CG-IsSameProcess $proc $m.name $m.process_started_at } else { $false }
                $ok = if (-not $proc -or -not $same) { $true } else { CG-ResumeProcess -ProcessId $m.pid }
                $rollbackOk = $ok
                CG-Log 'mitigations' 'info' "ROLLBACK suspend_browser_gpu pid=$($m.pid) same_process=$same ok=$ok"
            }
            'kill' {
                # cannot revive — log only
                CG-Log 'mitigations' 'info' "ROLLBACK note: killed $($m.name) cannot be restored"
            }
            'gpu_power_limit' { CG-Action-ResetGpu | Out-Null }
            'gpu_clock_lock' { CG-Action-ResetGpu | Out-Null }
        }
        if (-not $rollbackOk) {
            [void]$remaining.Add($m)
            CG-Log 'mitigations' 'warn' "ROLLBACK retained failed action=$($m.action) pid=$($m.pid)"
        }
    }
    CG-SaveJson $activePath @($remaining)
    return
}

# --- FORWARD PATH ---
$tierActions = $policies.tier_actions.$Tier
if (-not $tierActions) { return }

CG-Log 'mitigations' 'warn' "TIER=$Tier reasons=$Reasons actions=$($tierActions.actions -join ', ')"

foreach ($act in $tierActions.actions) {
    $parts = $act -split ':'
    $verb = $parts[0]
    $arg  = if ($parts.Count -gt 1) { $parts[1] } else { '' }
    try {
        switch ($verb) {
            'throttle'                 { $n = CG-Action-Throttle $arg;  CG-Log 'mitigations' 'info' "throttle($arg)=$n procs" }
            'suspend'                  { $n = CG-Action-Suspend $arg;   CG-Log 'mitigations' 'info' "suspend($arg)=$n procs" }
            'kill'                     { $n = CG-Action-Kill $arg;      CG-Log 'mitigations' 'info' "kill($arg)=$n procs" }
            'drop-hw-accel'            { $n = CG-Action-DropHwAccelBrowsers; CG-Log 'mitigations' 'info' "drop-hw-accel browsers=$n" }
            'lower-gpu-power'          { CG-Action-LowerGpuPower | Out-Null }
            'drop-gpu-clocks'          { CG-Action-DropGpuClocks | Out-Null }
            'drop-gpu-clocks-min'      { CG-Action-DropGpuClocks -Min | Out-Null }
            'disable-cuda-temporarily' { CG-Log 'mitigations' 'info' "disable-cuda: documented, requires per-proc env" }
            default                    { CG-Log 'mitigations' 'warn' "Unknown action: $act" }
        }
    } catch {
        CG-Log 'mitigations' 'error' "Action $act failed: $_"
    }
}

if ($tierActions.notify) {
    CG-Action-Notify "CrashGuard $Tier" $Reasons
}

CG-SaveJson $activePath @($active)
