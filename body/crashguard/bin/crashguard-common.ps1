# Shared helpers for all CrashGuard scripts.

$script:CG_ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script:CG_BIN = Join-Path $CG_ROOT 'bin'
$script:CG_LOGS = Join-Path $CG_ROOT 'logs'
$script:CG_STATE = Join-Path $CG_ROOT 'state'
$script:CG_CONFIGS = Join-Path $CG_ROOT 'configs'
$script:CG_TELEMETRY = Join-Path $CG_ROOT 'telemetry'
$script:CG_MITIG = Join-Path $CG_ROOT 'mitigations'

function CG-Now { (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff') }

# P/Invoke for NtSuspendProcess / NtResumeProcess — shared by mitigate + process-watcher.
# Use top-level class (no nesting) for reliability across PowerShell sessions.
if (-not ('CGNt' -as [type])) {
    try {
        Add-Type -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("ntdll.dll")] public static extern int NtSuspendProcess(System.IntPtr h);
[System.Runtime.InteropServices.DllImport("ntdll.dll")] public static extern int NtResumeProcess(System.IntPtr h);
'@ -Name 'CGNt' -Namespace '' -ErrorAction SilentlyContinue
    } catch {}
}

function CG-Suspend {
    param([int]$ProcessId)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ([CGNt]::NtSuspendProcess($p.Handle) -eq 0)
    } catch { return $false }
}

function CG-Resume {
    param([int]$ProcessId)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ([CGNt]::NtResumeProcess($p.Handle) -eq 0)
    } catch { return $false }
}

function CG-Log {
    param([string]$LogName, [string]$Level, [string]$Message)
    $line = '{0} [{1,-5}] {2}' -f (CG-Now), $Level.ToUpper(), $Message
    $path = Join-Path $CG_LOGS ($LogName + '.log')
    # Rotate at ~50MB
    if ((Test-Path $path) -and ((Get-Item $path).Length -gt 50MB)) {
        $bak = $path + '.' + (Get-Date -Format 'yyyyMMddHHmmss') + '.bak'
        try { Move-Item $path $bak -Force } catch {}
    }
    Add-Content -Path $path -Value $line -Encoding utf8 -ErrorAction SilentlyContinue
}

function CG-LoadJson {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        Get-Content -Path $Path -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        CG-Log 'sentinel' 'error' "Failed to load $Path : $_"
        $null
    }
}

function CG-SaveJson {
    param([string]$Path, $Object)
    $tmp = $null
    try {
        # -InputObject is intentional: piping an empty array emits no objects and used
        # to leave a zero-byte state file. That erased the rollback ledger and left
        # throttled processes at BelowNormal indefinitely.
        $json = ConvertTo-Json -InputObject $Object -Depth 12
        if ($null -eq $json) { $json = 'null' }
        if ($json -match '^\[\s*\]$') { $json = '[]' }
        $tmp = $Path + '.tmp.' + $PID + '.' + [Guid]::NewGuid().ToString('N')
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tmp, ($json + [Environment]::NewLine), $utf8)
        Move-Item -LiteralPath $tmp -Destination $Path -Force
    } catch {
        CG-Log 'sentinel' 'error' "Failed to save $Path : $_"
    } finally {
        if ($tmp -and (Test-Path -LiteralPath $tmp)) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }
}

function CG-ResolvePriorityClass {
    param($Value)
    if ($null -eq $Value) { return $null }
    try {
        if ($Value -is [System.Diagnostics.ProcessPriorityClass]) { return $Value }
        $text = ([string]$Value).Trim()
        if (-not $text) { return $null }
        if ($text -match '^[0-9]+$') {
            return [System.Diagnostics.ProcessPriorityClass][int]$text
        }
        return [System.Enum]::Parse([System.Diagnostics.ProcessPriorityClass], $text, $true)
    } catch {
        return $null
    }
}

function CG-IsSameProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$ExpectedName,
        $ExpectedStartedAt
    )
    if (-not $Process) { return $false }
    if ($ExpectedName -and -not $Process.ProcessName.Equals($ExpectedName, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    if ($ExpectedStartedAt) {
        try {
            $expected = ([datetime]$ExpectedStartedAt).ToUniversalTime()
            $actual = $Process.StartTime.ToUniversalTime()
            if ([math]::Abs(($actual - $expected).TotalSeconds) -gt 2) { return $false }
        } catch {
            return $false
        }
    }
    return $true
}

function CG-Heartbeat {
    param([string]$Component = 'sentinel', [hashtable]$Extra = @{})
    $p = Join-Path $CG_STATE ($Component + '.heartbeat')
    $obj = @{
        component = $Component
        pid       = $PID
        timestamp = CG-Now
        unix      = [int][double]::Parse((Get-Date -UFormat %s))
    }
    foreach ($k in $Extra.Keys) { $obj[$k] = $Extra[$k] }
    CG-SaveJson $p $obj
}

function CG-FindNvSmi {
    $c = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $candidates = @(
        'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe',
        'C:\Windows\System32\nvidia-smi.exe',
        "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

function CG-GpuTelemetry {
    param([string]$NvSmi)
    if (-not $NvSmi) { return $null }
    try {
        $csv = & $NvSmi --query-gpu=temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,clocks.gr,fan.speed --format=csv,noheader,nounits 2>$null
        if (-not $csv) { return $null }
        $line = ($csv -split "`r?`n")[0]
        $vals = $line -split '\s*,\s*'
        if ($vals.Count -lt 7) { return $null }
        $power_pct = if ($vals[6] -match '\d') { [int]([double]$vals[5] / [double]$vals[6] * 100) } else { 0 }
        $mem_pct = if ([double]$vals[4] -gt 0) { [int]([double]$vals[3] / [double]$vals[4] * 100) } else { 0 }
        return [PSCustomObject]@{
            temp_c       = [int]$vals[0]
            util_pct     = [int]$vals[1]
            mem_util_pct = [int]$vals[2]
            mem_used_mb  = [int]$vals[3]
            mem_total_mb = [int]$vals[4]
            mem_used_pct = $mem_pct
            power_w      = [double]$vals[5]
            power_limit_w= if ($vals[6] -match '\d') { [double]$vals[6] } else { 0 }
            power_pct    = $power_pct
            clock_mhz    = [int]$vals[7]
            fan_pct      = if ($vals[8] -match '\d') { [int]$vals[8] } else { 0 }
        }
    } catch { return $null }
}

function CG-CpuRamTelemetry {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $totalKb = $os.TotalVisibleMemorySize
        $freeKb = $os.FreePhysicalMemory
        $ramPct = [int]((1 - $freeKb / $totalKb) * 100)
        $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
        return [PSCustomObject]@{
            cpu_util_pct = [int]$cpu
            ram_used_pct = $ramPct
            ram_total_mb = [int]($totalKb / 1KB)
            ram_free_mb  = [int]($freeKb / 1KB)
        }
    } catch { return $null }
}

function CG-Tier {
    param($Thresholds, $Telemetry)
    # Returns @{ tier='green|yellow|orange|red|critical'; reasons=@() }
    $tiers = @('green','yellow','orange','red','critical')
    $highest = 0
    $reasons = @()
    $mapping = @{
        'gpu_temp_c'     = $Telemetry.gpu_temp_c
        'gpu_util_pct'   = $Telemetry.gpu_util_pct
        'gpu_mem_used_pct' = $Telemetry.gpu_mem_used_pct
        'gpu_power_pct_of_limit' = $Telemetry.gpu_power_pct
        'cpu_util_pct'   = $Telemetry.cpu_util_pct
        'ram_used_pct'   = $Telemetry.ram_used_pct
    }
    foreach ($metric in $mapping.Keys) {
        $val = $mapping[$metric]
        if ($null -eq $val) { continue }
        $cfg = $Thresholds.$metric
        if (-not $cfg) { continue }
        $tierIdx = 0
        if ($val -ge $cfg.tier_critical) { $tierIdx = 4 }
        elseif ($val -ge $cfg.tier_red)    { $tierIdx = 3 }
        elseif ($val -ge $cfg.tier_orange) { $tierIdx = 2 }
        elseif ($val -ge $cfg.tier_yellow) { $tierIdx = 1 }
        if ($tierIdx -gt $highest) { $highest = $tierIdx; $reasons = @("$metric=$val (->$($tiers[$tierIdx]))") }
        elseif ($tierIdx -eq $highest -and $tierIdx -gt 0) { $reasons += "$metric=$val" }
    }
    return [PSCustomObject]@{ tier = $tiers[$highest]; index = $highest; reasons = $reasons }
}

function CG-ForegroundProcessId {
    Add-Type -Namespace CG -Name FG -MemberDefinition @"
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        public static extern System.IntPtr GetForegroundWindow();
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint lpdwProcessId);
"@ -ErrorAction SilentlyContinue
    try {
        $h = [CG.FG]::GetForegroundWindow()
        $pid_out = 0
        [void][CG.FG]::GetWindowThreadProcessId($h, [ref]$pid_out)
        return [int]$pid_out
    } catch { return 0 }
}

function CG-CriticalityOf {
    param($Policies, [string]$ProcName, [int]$ProcessId = 0, [int]$ForegroundPid = 0)
    foreach ($rule in $Policies.rules) {
        if ($ProcName -match $rule.match) {
            $crit = $rule.criticality
            if ($Policies.foreground_window_bonus.enabled -and $ProcessId -gt 0 -and $ProcessId -eq $ForegroundPid) {
                # Bump up one level
                $crit = switch ($crit) {
                    'expendable' { 'low' }
                    'low' { 'medium' }
                    'medium' { 'high' }
                    'high' { 'critical' }
                    default { $crit }
                }
            }
            return @{ criticality = $crit; label = $rule.label; match = $rule.match }
        }
    }
    return @{ criticality = 'medium'; label = 'unmatched'; match = '*' }
}
