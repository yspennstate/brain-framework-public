# compute_governor.ps1 - owner-first compute governor for <hostname>.
#
# One hidden long-running loop (launched at logon via launch_compute_governor.vbs, SW_HIDE).
# Every cycle (~20s) it samples the machine, keeps background AI compute at BelowNormal
# priority (Idle during owner contention), detects distress (owner contention, starvation,
# UI freeze, memory pressure, Azure-box pressure), and publishes everything to
# %USERPROFILE%\.claude\compute\compute_state.json for the compute_sense.py hook and for
# codex sessions to read.
#
# Restraint by design (the healer scar, theta_probe_churn_five_days_2026_07_08):
#   - priority changes ONLY; never kill, never restart, never suspend
#   - one demotion per process (BelowNormal is the permanent home of AI compute)
#   - hard rate limit; beyond it the governor suspends its own medicine and says so
#   - distress fires on edges (new episodes), sustained thresholds, not single spikes
#   - full intervention ledger (interventions.tsv) so the healer can be audited
#
# Doctrine: COMPUTE_DOCTRINE.md next to this file (canonical copy in the brain repo).

[CmdletBinding()]
param(
    [switch]$Once,          # run one cycle and exit (testing / smoke)
    [switch]$DryRun,        # log what would change, change nothing
    [switch]$Status,        # pretty-print the current state file and exit
    [switch]$LibraryMode,   # load pure policy helpers without starting the governor
    [int]$IntervalSec = 20
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------- paths ----------------
$StateDir   = (Join-Path $env:USERPROFILE '.claude\compute')
$StateFile  = Join-Path $StateDir 'compute_state.json'
$LedgerFile = Join-Path $StateDir 'interventions.tsv'
$LogFile    = Join-Path $StateDir 'governor.log'
$PidFile    = Join-Path $StateDir 'governor.pid'
$MeshPy     = (Join-Path $env:USERPROFILE 'brain-repo\12_cognitive_architecture\agent_mesh\agent_mesh.py')

# ---------------- policy ----------------
$AiNames          = @('python','pythonw','node','codex','claude','uv','julia','Rscript','deno',
                      'rg','grep')  # 2026-07-14: agent Grep bursts (4x rg at Normal) froze the owner's UI on 07-13;
                             # the HeavyProcPct gate means quick greps are untouched, only sustained heavy ones demote.
                             # 2026-08-09: 'grep' added - the 07-14 fix covered rg but not Git-bash grep.exe, which is
                             # the same tool by another name. Two orphaned recursive greps (parents already exited) sat
                             # at Normal and burned 313s and 78s of CPU during an owner ui_freeze with 2 hung windows;
                             # the ledger had 1,173 rows and not one mentioning grep, because it was never managed.
$NeverTouchNames  = @('wezterm-gui','wezterm','WindowsTerminal','OpenConsole','conhost','explorer',
                      'dwm','powershell','pwsh','cmd','ssh','git','wscript','cscript','svchost',
                      'chrome','msedge','firefox','Code','devenv')
$NeverTouchCmd    = 'your_protected_service|your_critical_process|crashguard|compute_governor'
$CpuHighPct       = 88      # owner-contention threshold (total CPU)
$CpuElevatedPct   = 70      # above this, heavy AI procs get their one-time demotion (owner present)
$CpuElevatedAway  = 90      # owner away: sims may run hot; only demote near saturation
$SustainCycles    = 3       # cycles a condition must hold before distress
$OwnerActiveSec   = 60      # input fresher than this = owner is at the keyboard
$OwnerAwaySec     = 600     # no input for this long = owner away, open the throttle
$CpuSatPct        = 95      # saturation: throttle a notch even with no owner around
$GpuSatPct        = 97
$PhysMemWarnMB    = 8192    # physical headroom: throttle before paging stalls the UI
$PhysMemCriticalMB = 4096   # immediate distress
$CommitWarnPct    = 85      # system commit can exhaust while physical RAM still looks healthy
$CommitCriticalPct = 90     # immediate distress; priority changes cannot release commit
$CommitWarnAvailMB = 16384
$CommitCriticalAvailMB = 8192
$HeavyProcPct     = 4.0     # per-process share of ALL cores that counts as heavy
$MaxChangesPerHr  = 30      # healer self-limit
$MeshWarnGapMin   = 10
$AzureEveryCycles = 15      # ~5 min at 20s cycles
$AzureSsh         = '<box-user>@<host-ip>'
$AzureKey         = (Join-Path $env:USERPROFILE '.ssh\<box-key>.pem')
$Cores            = [Environment]::ProcessorCount

# ---------------- native helpers ----------------
Add-Type -Namespace CG -Name Native -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)]
public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
[DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
[DllImport("user32.dll")] public static extern bool IsHungAppWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
public static System.Collections.Generic.List<uint> HungPids() {
    var outp = new System.Collections.Generic.List<uint>();
    EnumWindows(delegate(IntPtr h, IntPtr l) {
        if (IsWindowVisible(h) && IsHungAppWindow(h)) { uint p; GetWindowThreadProcessId(h, out p); if (p != 0) outp.Add(p); }
        return true;
    }, IntPtr.Zero);
    return outp;
}
public static uint IdleMs() {
    LASTINPUTINFO li = new LASTINPUTINFO(); li.cbSize = (uint)Marshal.SizeOf(li);
    if (!GetLastInputInfo(ref li)) return 0;
    return (uint)Environment.TickCount - li.dwTime;
}
[DllImport("kernel32.dll")] public static extern bool GetSystemTimes(out long idle, out long kernel, out long user);
[StructLayout(LayoutKind.Sequential)]
public struct MEMSTATEX { public uint dwLength; public uint dwMemoryLoad; public ulong ullTotalPhys; public ulong ullAvailPhys; public ulong ullTotalPageFile; public ulong ullAvailPageFile; public ulong ullTotalVirtual; public ulong ullAvailVirtual; public ulong ullAvailExtendedVirtual; }
[DllImport("kernel32.dll", CharSet=CharSet.Auto)] public static extern bool GlobalMemoryStatusEx(ref MEMSTATEX buf);
[StructLayout(LayoutKind.Sequential)]
public struct PERFINFO {
    public uint cb;
    public UIntPtr CommitTotal;
    public UIntPtr CommitLimit;
    public UIntPtr CommitPeak;
    public UIntPtr PhysicalTotal;
    public UIntPtr PhysicalAvailable;
    public UIntPtr SystemCache;
    public UIntPtr KernelTotal;
    public UIntPtr KernelPaged;
    public UIntPtr KernelNonpaged;
    public UIntPtr PageSize;
    public uint HandleCount;
    public uint ProcessCount;
    public uint ThreadCount;
}
[DllImport("psapi.dll", SetLastError=true)]
static extern bool GetPerformanceInfo(out PERFINFO info, uint size);
public static ulong AvailMB() {
    MEMSTATEX m = new MEMSTATEX(); m.dwLength = (uint)Marshal.SizeOf(typeof(MEMSTATEX));
    if (!GlobalMemoryStatusEx(ref m)) return 0;
    return m.ullAvailPhys / (1024UL * 1024UL);
}
public static ulong[] MemoryMB() {
    MEMSTATEX m = new MEMSTATEX(); m.dwLength = (uint)Marshal.SizeOf(typeof(MEMSTATEX));
    PERFINFO p;
    if (!GlobalMemoryStatusEx(ref m) ||
        !GetPerformanceInfo(out p, (uint)Marshal.SizeOf(typeof(PERFINFO)))) return null;
    const ulong MB = 1024UL * 1024UL;
    ulong pageBytes = p.PageSize.ToUInt64();
    ulong commitTotalBytes = p.CommitTotal.ToUInt64() * pageBytes;
    ulong commitLimitBytes = p.CommitLimit.ToUInt64() * pageBytes;
    ulong commitAvailableBytes = commitLimitBytes > commitTotalBytes
        ? commitLimitBytes - commitTotalBytes : 0UL;
    return new ulong[] {
        m.ullAvailPhys / MB,
        (ulong)m.dwMemoryLoad,
        commitLimitBytes / MB,
        commitAvailableBytes / MB
    };
}
[DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
static extern bool GetDiskFreeSpaceEx(string path, out ulong freeAvailable, out ulong totalBytes, out ulong totalFreeBytes);
public static bool TryDiskFreeBytes(string path, out ulong freeAvailable, out int error) {
    ulong totalBytes, totalFreeBytes;
    if (GetDiskFreeSpaceEx(path, out freeAvailable, out totalBytes, out totalFreeBytes)) {
        error = 0;
        return true;
    }
    error = Marshal.GetLastWin32Error();
    return false;
}
'@

# ---------------- small utils ----------------
function Write-Log([string]$msg) {
    try {
        if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 2MB)) {
            Move-Item -Force $LogFile "$LogFile.1"
        }
        Add-Content -Path $LogFile -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding UTF8
    } catch {}
}

function Write-Ledger([string]$action, $procInfo, [string]$from, [string]$to, [double]$cpuPct, [string]$reason) {
    try {
        if (-not (Test-Path $LedgerFile)) {
            Set-Content -Path $LedgerFile -Value "ts`taction`tpid`tname`tfrom`tto`tcpu_pct`treason" -Encoding UTF8
        }
        $row = "{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6:N1}`t{7}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),
            $action, $procInfo.Id, $procInfo.ProcessName, $from, $to, $cpuPct, $reason
        Add-Content -Path $LedgerFile -Value $row -Encoding UTF8
    } catch {}
}

function Get-ChangesLastHour {
    try {
        if (-not (Test-Path $LedgerFile)) { return 0 }
        $cut = (Get-Date).AddHours(-1)
        $n = 0
        foreach ($ln in (Get-Content $LedgerFile -Encoding UTF8 | Select-Object -Last 400)) {
            $p = $ln.Split("`t")
            if ($p.Count -ge 2 -and $p[1] -match '^(demote|escalate|deescalate)$') {
                try { if ([datetime]$p[0] -gt $cut) { $n++ } } catch {}
            }
        }
        return $n
    } catch { return 0 }
}

function Write-StateAtomic($obj) {
    try {
        $tmp = "$StateFile.tmp.$PID"
        # UTF-8 WITHOUT BOM: PS 5.1 'Set-Content -Encoding UTF8' prepends a BOM that breaks a naive
        # json.load in any reader that isn't BOM-tolerant. compute_sense.py strips it, but the doctrine
        # has Codex read this file too, and a plain json.load fails on the BOM (throttle-blind). WriteAllText
        # with UTF8Encoding($false) writes no BOM, so every reader parses it. (Effective on governor restart.)
        [System.IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -Force $tmp $StateFile
    } catch { Write-Log "state write failed: $_" }
}

function Get-DiskFreeGB([string]$Path = 'C:\') {
    # Do not use Get-PSDrive here. Its FileSystem provider can block indefinitely when
    # Windows storage-management providers wedge, which freezes the safety heartbeat.
    # This local Win32 call has no shell/console child and exposes failure as -1.
    try {
        [uint64]$freeBytes = 0
        [int]$win32Error = 0
        if (-not [CG.Native]::TryDiskFreeBytes($Path, [ref]$freeBytes, [ref]$win32Error)) {
            return -1.0
        }
        return [math]::Round($freeBytes / 1GB, 1)
    } catch {
        return -1.0
    }
}

function Get-AzureSignals($Azure) {
    # Rolling compatibility: old sentinels put protected-unit observations in pressure
    # reasons and marked the whole box distress. Separate that one legacy shape without
    # weakening unknown/empty distress records or real pressure reasons.
    if (-not $Azure) {
        return [pscustomobject]@{
            PressureDistress = $false; PressureWarning = $false; Throttle = $false
            ProtectedHealth = $null; ProtectedAlerts = @()
        }
    }
    $rawReasons = @($Azure.reasons | Where-Object { $null -ne $_ })
    $legacyProtectedAlerts = @(
        $rawReasons | Where-Object { "$_" -like 'protected_unit_not_active:*' }
    )
    $pressureReasons = @(
        $rawReasons | Where-Object { "$_" -notlike 'protected_unit_not_active:*' }
    )
    $hasExplicitProtectedAlerts = $null -ne $Azure.PSObject.Properties['protected_alerts']
    $protectedAlerts = @($Azure.protected_alerts | Where-Object { $null -ne $_ })
    if ($protectedAlerts.Count -eq 0) { $protectedAlerts = $legacyProtectedAlerts }
    $protectedHealth = $Azure.protected_health
    if (-not $protectedHealth -and $protectedAlerts.Count -gt 0) { $protectedHealth = 'alert' }

    # Suppress only the exact legacy producer shape. An explicit separated-schema alert
    # can coexist with pressure and must never negate an unknown or malformed distress.
    $legacyProtectedOnly = (
        -not $hasExplicitProtectedAlerts -and
        $legacyProtectedAlerts.Count -gt 0 -and
        $pressureReasons.Count -eq 0
    )
    $pressureDistress = ($Azure.status -eq 'distress' -and -not $legacyProtectedOnly)
    $psiCpu = 0.0
    $hasPsiCpu = [double]::TryParse("$($Azure.psi_cpu_some_avg60)", [ref]$psiCpu)
    $measuredCpuThrottle = ($hasPsiCpu -and $psiCpu -ge 35)
    $pressureWarning = (
        ($Azure.status -eq 'warn' -and -not $legacyProtectedOnly) -or
        ($measuredCpuThrottle -and -not $pressureDistress)
    )
    $throttle = (
        $measuredCpuThrottle -or
        ($Azure.mode -eq 'throttle' -and -not $legacyProtectedOnly)
    )
    return [pscustomobject]@{
        PressureDistress = $pressureDistress; PressureWarning = $pressureWarning
        Throttle = $throttle
        ProtectedHealth = $protectedHealth; ProtectedAlerts = $protectedAlerts
    }
}

function Test-GlobalThrottle($SustainSat, $SustainCycles, $Health, $AzureSignals, $AzureFails) {
    return (
        $SustainSat -ge $SustainCycles -or
        $Health -eq 'distress' -or
        $AzureSignals.Throttle -or
        $AzureFails -ge 6
    )
}

function Get-LocalMemorySignals {
    param(
        [long]$PhysicalAvailableMB,
        [double]$CommitUsedPct,
        [long]$CommitAvailableMB
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    $telemetryOk = (
        $PhysicalAvailableMB -ge 0 -and
        $CommitUsedPct -ge 0 -and $CommitUsedPct -le 100 -and
        $CommitAvailableMB -ge 0
    )
    if (-not $telemetryOk) {
        $reasons.Add('memory_telemetry_degraded')
        return [pscustomobject]@{
            Severity = 'warn'; Throttle = $true; Reasons = $reasons.ToArray()
        }
    }

    $critical = $false
    if ($PhysicalAvailableMB -le $PhysMemCriticalMB) {
        $reasons.Add('memory_physical_critical')
        $critical = $true
    }
    if ($CommitUsedPct -ge $CommitCriticalPct -or
        $CommitAvailableMB -le $CommitCriticalAvailMB) {
        $reasons.Add('memory_commit_critical')
        $critical = $true
    }
    if ($critical) {
        return [pscustomobject]@{
            Severity = 'distress'; Throttle = $true; Reasons = $reasons.ToArray()
        }
    }

    if ($PhysicalAvailableMB -le $PhysMemWarnMB) {
        $reasons.Add('memory_physical_pressure')
    }
    if ($CommitUsedPct -ge $CommitWarnPct -or
        $CommitAvailableMB -le $CommitWarnAvailMB) {
        $reasons.Add('memory_commit_pressure')
    }
    $warn = ($reasons.Count -gt 0)
    return [pscustomobject]@{
        Severity = if ($warn) { 'warn' } else { 'ok' }
        Throttle = $warn
        Reasons  = $reasons.ToArray()
    }
}

function Resolve-LocalHealth {
    param(
        [string[]]$BaseReasons,
        $MemorySignals,
        [int]$HighCycles,
        [int]$RequiredHighCycles,
        [double]$CpuPct,
        [double]$CpuWarnPct
    )

    $reasons = @(
        @($MemorySignals.Reasons) + @($BaseReasons) |
            Where-Object { -not [string]::IsNullOrWhiteSpace("$_") }
    )
    $distressAuthority = @(
        'owner_contention','ui_freeze','starvation','azure_pressure',
        'disk_critical','process_critical'
    )
    if ($MemorySignals.Severity -eq 'distress' -or
        ($reasons | Where-Object { $_ -in $distressAuthority })) {
        $health = 'distress'
    } elseif ($reasons.Count -gt 0) {
        $health = 'warn'
    } elseif ($HighCycles -ge $RequiredHighCycles -or $CpuPct -ge $CpuWarnPct) {
        $health = 'warn'
        $reasons += 'cpu_high_no_owner'
    } else {
        $health = 'ok'
    }
    return [pscustomobject]@{ Health = $health; Reasons = @($reasons) }
}

function Resolve-LocalMode {
    param(
        $MemorySignals,
        [bool]$GlobalThrottle,
        [bool]$OwnerAway
    )
    if ($MemorySignals.Throttle -or $GlobalThrottle) { return 'throttle' }
    if ($OwnerAway) { return 'owner_away_open' }
    return 'owner_present_polite'
}

function Get-LiveProcessSample {
    param([System.Diagnostics.Process[]]$ProcessSample)

    # Get-Process can retain signaled Process objects while a long-lived parent still
    # owns their handles.  Counting those exited objects caused false process-pressure
    # alarms during the 2026-08-01 hook-shell leak.  A process that exits between the
    # enumeration and this check is also safely excluded.
    return @(
        foreach ($process in @($ProcessSample)) {
            if ($null -eq $process) { continue }
            try {
                $hasExited = $process.HasExited
                if ($null -ne $hasExited -and -not [bool]$hasExited) { $process }
            } catch {
                # Treat an inaccessible/raced-away process as no longer live.
            }
        }
    )
}

function Get-AiMemorySummary {
    param(
        [object[]]$ProcessSample,
        [string[]]$AiProcessNames
    )

    $tagged = @(
        foreach ($process in @($ProcessSample)) {
            if ($null -eq $process) { continue }
            try {
                if ($AiProcessNames -contains "$($process.ProcessName)") {
                    $process
                }
            } catch {}
        }
    )
    $privateBytes = 0.0
    $workingBytes = 0.0
    foreach ($process in $tagged) {
        try { $privateBytes += [double]$process.PrivateMemorySize64 } catch {}
        try { $workingBytes += [double]$process.WorkingSet64 } catch {}
    }
    return [pscustomobject]@{
        ProcessCount = $tagged.Count
        PrivateMB = [math]::Round($privateBytes / 1MB, 1)
        WorkingMB = [math]::Round($workingBytes / 1MB, 1)
    }
}

function Get-GpuSample {
    # nvidia-smi with a hard kill-timeout (a wedged driver must never block the loop);
    # CreateNoWindow so no console can ever flash. Returns $null when no GPU/driver.
    try {
        $si = New-Object System.Diagnostics.ProcessStartInfo
        $si.FileName = 'nvidia-smi'
        $si.Arguments = '--query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits'
        $si.UseShellExecute = $false
        $si.RedirectStandardOutput = $true
        $si.RedirectStandardError = $true
        $si.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($si)
        if ($p.WaitForExit(3000)) {
            $line = $p.StandardOutput.ReadLine()
            if ($line -and $line -match '^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)') {
                return @{ util_pct = [int]$matches[1]; mem_used_mb = [int]$matches[2]; mem_total_mb = [int]$matches[3] }
            }
        } else {
            try { $p.Kill() } catch {}
        }
    } catch {}
    return $null
}

if ($LibraryMode) { return }

# ---------------- status mode ----------------
if ($Status) {
    if (Test-Path $StateFile) { Get-Content $StateFile -Encoding UTF8 } else { 'no state file yet' }
    return
}

# ---------------- single instance ----------------
if (-not $Once) {
    try {
        if (Test-Path $PidFile) {
            $old = [int](Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($old -and $old -ne $PID) {
                $p = Get-Process -Id $old -ErrorAction SilentlyContinue
                if ($p -and $p.ProcessName -match 'powershell') { Write-Log "already running as pid $old; exiting"; return }
            }
        }
        New-Item -ItemType Directory -Force $StateDir | Out-Null
        Set-Content -Path $PidFile -Value $PID -Encoding ASCII
    } catch {}
}

Write-Log ("governor start pid={0} dryrun={1} interval={2}s cores={3}" -f $PID, [bool]$DryRun, $IntervalSec, $Cores)

# ---------------- orphan recovery ----------------
# If a previous governor died while processes were Idle-escalated, nothing would ever lift
# them (the managed map dies with the process - found by the 07-10 contention drill). Per
# doctrine, an AI-tagged process at Idle with no live escalation is an orphan: lift it to
# BelowNormal, its rightful home. Runs once at startup, logged per process.
try {
    foreach ($p in (Get-Process | Where-Object { $AiNames -contains $_.ProcessName })) {
        try {
            if ("$($p.PriorityClass)" -eq 'Idle') {
                if (-not $DryRun) { $p.PriorityClass = 'BelowNormal' }
                Write-Ledger 'orphan_lift' $p 'Idle' 'BelowNormal' 0 'startup orphan recovery'
            }
        } catch {}
    }
} catch {}

# ---------------- cycle state ----------------
$prevTicks     = @{}   # pid -> TotalProcessorTime ticks
$managed       = @{}   # pid -> current level we set ('BelowNormal'|'Idle')
$denied        = @{}   # pid -> access denied once, stop retrying
$sustainHigh   = 0
$sustainFreeze = 0
$cycleN        = 0
$azure         = $null
$azureFails    = 0
$lastMeshWarn  = [datetime]::MinValue
$lastDistress  = @()
$selfSuspended = $false

while ($true) {
    $cycleStart = Get-Date
    $cycleN++
    try {
        # ---- sample cpu/mem via P/Invoke (never blocks, unlike Get-Counter) ----
        $ti = 0L; $tk = 0L; $tu = 0L
        [CG.Native]::GetSystemTimes([ref]$ti, [ref]$tk, [ref]$tu) | Out-Null
        $cpuPct = 0.0
        if ($script:prevIdle) {
            $dIdle  = $ti - $script:prevIdle
            $dTotal = ($tk - $script:prevKernel) + ($tu - $script:prevUser)   # kernel includes idle
            if ($dTotal -gt 0) { $cpuPct = [math]::Round(100.0 * (1.0 - ($dIdle / [double]$dTotal)), 1) }
            if ($cpuPct -lt 0) { $cpuPct = 0.0 }
        }
        $script:prevIdle = $ti; $script:prevKernel = $tk; $script:prevUser = $tu
        $memorySample = [CG.Native]::MemoryMB()
        if ($null -eq $memorySample -or $memorySample.Count -ne 4) {
            $availMB = -1
            $memoryLoadPct = -1
            $commitLimitMB = -1
            $commitAvailMB = -1
            $commitUsedMB = -1
            $commitUsedPct = -1.0
        } else {
            $availMB = [long]$memorySample[0]
            $memoryLoadPct = [int]$memorySample[1]
            $commitLimitMB = [long]$memorySample[2]
            $commitAvailMB = [long]$memorySample[3]
            $commitUsedMB = [math]::Max(0, $commitLimitMB - $commitAvailMB)
            $commitUsedPct = if ($commitLimitMB -gt 0) {
                [math]::Round(100.0 * $commitUsedMB / [double]$commitLimitMB, 1)
            } else {
                -1.0
            }
        }
        $memorySignals = Get-LocalMemorySignals -PhysicalAvailableMB $availMB `
            -CommitUsedPct $commitUsedPct -CommitAvailableMB $commitAvailMB
        $diskQ   = 0

        # ---- owner activity + hung windows ----
        $idleSec = [math]::Round([CG.Native]::IdleMs() / 1000.0, 0)
        $ownerActive = ($idleSec -lt $OwnerActiveSec)
        $ownerAway   = ($idleSec -ge $OwnerAwaySec)
        $hungPids = @()
        try { $hungPids = @([CG.Native]::HungPids() | Select-Object -Unique) } catch {}

        # ---- gpu sample (every 3rd cycle; cached between) ----
        if (($cycleN % 3) -eq 1) { $script:gpu = Get-GpuSample }
        $gpu = $script:gpu

        # ---- per-process CPU deltas ----
        $now = Get-Date
        $procs = Get-Process | Where-Object { $_.Id -ne 0 -and $_.Id -ne 4 }
        $newTicks = @{}
        $sampleGapSec = $IntervalSec
        if ($script:prevSampleAt) { $sampleGapSec = [math]::Max(1.0, ($now - $script:prevSampleAt).TotalSeconds) }
        $script:prevSampleAt = $now
        $gapTicks = [double]($sampleGapSec * 10000000)
        $procCpu = @{}   # pid -> pct of all cores over last sample gap
        foreach ($p in $procs) {
            try {
                $t = $p.TotalProcessorTime.Ticks
                $newTicks[$p.Id] = $t
                if ($prevTicks.ContainsKey($p.Id)) {
                    $d = $t - $prevTicks[$p.Id]
                    if ($d -gt 0) { $procCpu[$p.Id] = [math]::Round(100.0 * $d / ($gapTicks * $Cores), 1) }
                }
            } catch {}
        }
        $prevTicks = $newTicks

        $top = @($procs | Where-Object { $procCpu.ContainsKey($_.Id) -and $procCpu[$_.Id] -ge 1.0 } |
            Sort-Object { $procCpu[$_.Id] } -Descending | Select-Object -First 6 |
            ForEach-Object { @{ pid = $_.Id; name = $_.ProcessName; cpu_pct = $procCpu[$_.Id]; prio = "$($_.PriorityClass)" } })
        $topPrivate = @($procs | Sort-Object PrivateMemorySize64 -Descending |
            Select-Object -First 8 | ForEach-Object {
                $priority = try { "$($_.PriorityClass)" } catch { '' }
                @{
                    pid = $_.Id
                    name = $_.ProcessName
                    private_mb = [math]::Round($_.PrivateMemorySize64 / 1MB, 1)
                    working_mb = [math]::Round($_.WorkingSet64 / 1MB, 1)
                    prio = $priority
                }
            })
        # SWARM VIEW - added 2026-08-07 after a blind spot cost us a diagnosis.
        # top_private ranks INDIVIDUAL processes, so a swarm is invisible to it: on
        # 2026-08-07 commit hit 98% and the governor named SearchIndexer (14.8GB),
        # while 343 powershell.exe held 14.7GB BETWEEN them - more than the #1 entry
        # - and never appeared in the report at all. Any per-process ranking has this
        # hole. Group by name and report totals so a swarm outranks a single hog.
        $topPrivateGroups = @($procs | Group-Object ProcessName | ForEach-Object {
                $sum = 0.0
                foreach ($p in $_.Group) { try { $sum += [double]$p.PrivateMemorySize64 } catch {} }
                @{
                    name = $_.Name
                    count = $_.Count
                    private_mb = [math]::Round($sum / 1MB, 1)
                }
            } | Sort-Object { $_.private_mb } -Descending | Select-Object -First 6)

        # EXITED-BUT-PINNED - the other half of that diagnosis. A process can be
        # HasExited with zero threads and STILL hold its commit, because something
        # holds an open handle and the kernel cannot free the object. Stop-Process
        # reports success on these and does nothing. Only a reboot (or closing the
        # leaking handle holder) releases it, so it must be reported as owner-action,
        # never as something an agent can shed.
        $exitedCount = 0
        $exitedMb = 0.0
        foreach ($p in $procs) {
            try {
                if ($p.HasExited) {
                    $exitedCount++
                    $exitedMb += [double]$p.PrivateMemorySize64
                }
            } catch {}
        }
        $exitedPinned = @{
            count = $exitedCount
            private_mb = [math]::Round($exitedMb / 1MB, 1)
            note = 'already-exited process objects pinned by open handles; unkillable, needs reboot'
        }

        $aiMemorySummary = Get-AiMemorySummary -ProcessSample $procs `
            -AiProcessNames $AiNames
        $largeAiProcesses = @($procs | Where-Object {
            $AiNames -contains $_.ProcessName -and $_.PrivateMemorySize64 -ge 1GB
        })
        $largeAiPrivateMB = [math]::Round(
            (($largeAiProcesses | Measure-Object PrivateMemorySize64 -Sum).Sum) / 1MB,
            1
        )

        # ---- policy: demote-once / escalate / de-escalate ----
        $changesLastHour = Get-ChangesLastHour
        $selfSuspended = ($changesLastHour -ge $MaxChangesPerHr)
        # medicine needs SUSTAINED contention (2 cycles ~40s), and calm needs 2 quiet cycles
        # before de-escalation - hysteresis so an oscillating load cannot flap Idle/BelowNormal
        $rawContention = ($cpuPct -ge $CpuHighPct -and $ownerActive)
        if ($rawContention) { $script:contCycles++; $script:calmCycles = 0 }
        else { $script:calmCycles++; $script:contCycles = 0 }
        $contention = ($script:contCycles -ge 2)
        $calmEnough = ($script:calmCycles -ge 2)
        if ($denied.Count -gt 300) { $denied = @{} }   # pids recycle; never skip forever
        # owner away = open throttle: sims may run hot, demotion waits until near saturation.
        # owner present/around = doctrine literal: heavy AI compute lives at BelowNormal ALWAYS,
        # not only when the machine is already loaded (one-time, ledgered, still gentle).
        $demoteAt = if ($ownerAway) { $CpuElevatedAway } else { 0 }
        $actions = 0
        if (-not $selfSuspended) {
            foreach ($p in $procs) {
                if ($p.Id -eq $PID) { continue }
                if ($denied.ContainsKey($p.Id)) { continue }
                if ($AiNames -notcontains $p.ProcessName) { continue }
                if ($NeverTouchNames -contains $p.ProcessName) { continue }
                $cpu = 0.0; if ($procCpu.ContainsKey($p.Id)) { $cpu = $procCpu[$p.Id] }
                try {
                    $cmd = ''
                    try { $cmd = ($p.StartInfo.Arguments + ' ' + $p.Path) } catch {}
                    if ($cmd -match $NeverTouchCmd) { continue }
                    $prio = "$($p.PriorityClass)"
                    if ($contention -and $cpu -ge $HeavyProcPct -and $prio -ne 'Idle') {
                        if (-not $DryRun) { $p.PriorityClass = 'Idle' }
                        $managed[$p.Id] = 'Idle'; $actions++
                        Write-Ledger 'escalate' $p $prio 'Idle' $cpu 'owner contention'
                    } elseif ($calmEnough -and $managed.ContainsKey($p.Id) -and $managed[$p.Id] -eq 'Idle') {
                        if (-not $DryRun) { $p.PriorityClass = 'BelowNormal' }
                        $managed[$p.Id] = 'BelowNormal'; $actions++
                        Write-Ledger 'deescalate' $p 'Idle' 'BelowNormal' $cpu 'contention cleared'
                    } elseif ($cpuPct -ge $demoteAt -and $cpu -ge $HeavyProcPct -and
                              ($prio -eq 'Normal' -or $prio -eq 'AboveNormal' -or $prio -eq 'High')) {
                        if (-not $DryRun) { $p.PriorityClass = 'BelowNormal' }
                        $managed[$p.Id] = 'BelowNormal'; $actions++
                        Write-Ledger 'demote' $p $prio 'BelowNormal' $cpu 'ai compute runs BelowNormal'
                    }
                } catch {
                    $denied[$p.Id] = $true
                    Write-Log ("no access to pid {0} {1} (elevated?); skipping henceforth" -f $p.Id, $p.ProcessName)
                }
            }
        }

        # ---- azure health: fire-and-forget poller writes azure_last.json; we only READ ----
        if (($cycleN % $AzureEveryCycles) -eq 1) {
            try {
                $poller = Join-Path $ScriptDir 'azure_poll.ps1'
                Start-Process -WindowStyle Hidden -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
                    -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File', $poller) -ErrorAction SilentlyContinue
            } catch {}
        }
        $azure = $null
        try {
            $azFile = Join-Path $StateDir 'azure_last.json'
            if (Test-Path $azFile) {
                $azAge = ((Get-Date) - (Get-Item $azFile).LastWriteTime).TotalMinutes
                $azRec = Get-Content $azFile -Encoding UTF8 | ConvertFrom-Json
                if ($azAge -lt 15 -and $azRec.ok) { $azure = $azRec.azure; $azureFails = 0 }
                elseif ($azAge -lt 15 -and -not $azRec.ok) {
                    # poller alive but ssh failing: count each NEW failed fetch (found live
                    # 12:45 during the NSG block - a fresh file of failures must still alarm)
                    if ($script:lastAzFetch -ne "$($azRec.fetched_at)") {
                        $script:lastAzFetch = "$($azRec.fetched_at)"; $azureFails++
                    }
                }
                elseif ($azAge -ge 30) { $azureFails = 6 }   # stale cache = poller itself dead
            }
        } catch {}

        # ---- distress evaluation (sustained, edge-fired) ----
        # disk free-space sense (added 2026-07-16 after C: hit 0 bytes with no alarm;
        # an earlier data-loss incident showed: <4% free has corrupted data here)
        $diskFreeGB = Get-DiskFreeGB
        # process-pressure sense (added 2026-07-19 after fork failures at ~474 procs /
        # 62 orphaned powershells wedged the machine while cpu/mem/disk all read ok;
        # native Get-Process only - WMI enumeration under this pressure wedges too)
        $procSample = @(
            Get-LiveProcessSample -ProcessSample @(
                Get-Process -ErrorAction SilentlyContinue
            )
        )
        $procCount = $procSample.Count
        $psShellCount = @($procSample | Where-Object { $_.ProcessName -eq 'powershell' }).Count
        if ($procCount -ge 550 -or $psShellCount -ge 55) { $script:sustainProc++ } else { $script:sustainProc = 0 }
        if ($cpuPct -ge $CpuHighPct) { $sustainHigh++ } else { $sustainHigh = 0 }
        $satNow = ($cpuPct -ge $CpuSatPct) -or ($gpu -and $gpu.util_pct -ge $GpuSatPct)
        if ($satNow) { $script:sustainSat++ } else { $script:sustainSat = 0 }
        $ownerHung = ($hungPids.Count -gt 0 -and $ownerActive)
        if ($ownerHung) { $sustainFreeze++ } else { $sustainFreeze = 0 }
        $elapsed = ((Get-Date) - $cycleStart).TotalSeconds
        $azureSignals = Get-AzureSignals $azure
        $baseReasons = @()
        if ($sustainHigh -ge $SustainCycles -and $ownerActive) { $baseReasons += 'owner_contention' }
        if ($sustainFreeze -ge 2) { $baseReasons += 'ui_freeze' }
        # overshoot 2.5-30x = the box is starved; beyond 30x = we slept/hibernated (a resumed
        # laptop must not wake every session with a false starvation alarm)
        if ($script:lastOvershoot -gt 30) {
            Write-Log ("resume detected (overshoot {0:N1}x); counters reset" -f $script:lastOvershoot)
            $sustainHigh = 0; $sustainFreeze = 0; $script:lastOvershoot = 0
        } elseif ($script:lastOvershoot -gt 2.5) { $baseReasons += 'starvation' }
        if ($azureSignals.PressureDistress) { $baseReasons += 'azure_pressure' }
        if ($azureSignals.PressureWarning) { $baseReasons += 'azure_pressure_warn' }
        if ($azure -and $azure.sentinel_stale) { $baseReasons += 'azure_sentinel_stale' }
        if ($azureFails -ge 6) { $baseReasons += 'azure_unreachable' }
        if ($selfSuspended) { $baseReasons += 'governor_selfcheck' }
        if ($script:sustainSat -ge $SustainCycles) { $baseReasons += 'saturation_throttle' }
        if ($diskFreeGB -lt 0) { $baseReasons += 'disk_probe_failed' }
        if ($diskFreeGB -ge 0 -and $diskFreeGB -lt 30) { $baseReasons += 'disk_critical' }
        elseif ($diskFreeGB -ge 0 -and $diskFreeGB -lt 100) { $baseReasons += 'disk_low' }
        if ($script:sustainProc -ge 2 -and ($procCount -ge 600 -or $psShellCount -ge 80)) { $baseReasons += 'process_critical' }
        elseif ($script:sustainProc -ge 2) { $baseReasons += 'process_pressure' }

        $resolvedHealth = Resolve-LocalHealth -BaseReasons $baseReasons `
            -MemorySignals $memorySignals -HighCycles $sustainHigh `
            -RequiredHighCycles $SustainCycles -CpuPct $cpuPct `
            -CpuWarnPct $CpuHighPct
        $health = $resolvedHealth.Health
        $reasons = @($resolvedHealth.Reasons)

        # the mode agents read BEFORE sizing work: away = open the throttle, sims may grow;
        # throttle = saturation or owner just returned - take parallelism down a notch
        $globalThrottle = Test-GlobalThrottle $script:sustainSat $SustainCycles `
            $health $azureSignals $azureFails
        $mode = Resolve-LocalMode -MemorySignals $memorySignals `
            -GlobalThrottle $globalThrottle -OwnerAway $ownerAway

        # ---- publish state ----
        $state = [ordered]@{
            ts            = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
            host          = $env:COMPUTERNAME
            status        = $health
            mode          = $mode
            reasons       = $reasons
            cpu_pct       = $cpuPct
            gpu           = $gpu
            mem_avail_mb  = $availMB
            mem_load_pct  = $memoryLoadPct
            commit_used_mb = $commitUsedMB
            commit_limit_mb = $commitLimitMB
            commit_avail_mb = $commitAvailMB
            commit_used_pct = $commitUsedPct
            disk_queue    = $diskQ
            disk_free_gb  = $diskFreeGB
            proc_count    = $procCount
            ps_shell_count = $psShellCount
            owner_active  = $ownerActive
            owner_away    = $ownerAway
            owner_idle_s  = $idleSec
            hung_windows  = $hungPids.Count
            top           = $top
            top_private   = $topPrivate
            top_private_groups = $topPrivateGroups
            exited_pinned = $exitedPinned
            ai_tagged_process_count = $aiMemorySummary.ProcessCount
            ai_tagged_private_mb = $aiMemorySummary.PrivateMB
            ai_tagged_working_mb = $aiMemorySummary.WorkingMB
            large_ai_process_count = $largeAiProcesses.Count
            large_ai_private_mb = $largeAiPrivateMB
            interventions_last_hour = $changesLastHour + $actions
            governor_suspended      = $selfSuspended
            cycle_ms      = [int](((Get-Date) - $cycleStart).TotalMilliseconds)
            azure         = $azure
            azure_protected_health = $azureSignals.ProtectedHealth
            azure_protected_alerts = @($azureSignals.ProtectedAlerts)
            azure_ssh_fails = $azureFails
            doctrine      = 'brain-repo/12_cognitive_architecture/compute_governor/COMPUTE_DOCTRINE.md'
            contract      = 'if status is distress: pause your task, shed/fix the load (yours first), improve the governor+doctrine if systemic, write the lesson to the brain, then resume'
            pid           = $PID
        }
        Write-StateAtomic $state

        # ---- mesh warning on distress edge ----
        $newReasons = @($reasons | Where-Object { $_ -notin $lastDistress -and $_ -ne 'cpu_high_no_owner' })
        if ($health -eq 'distress' -and $newReasons.Count -gt 0 -and
            ((Get-Date) - $lastMeshWarn).TotalMinutes -ge $MeshWarnGapMin) {
            $lastMeshWarn = Get-Date
            $body = "compute distress on $($env:COMPUTERNAME): $($reasons -join ', '). cpu=$cpuPct% physical_available=$availMB MB commit=$commitUsedPct% commit_available=$commitAvailMB MB. Contract: pause heavy work, check $(Join-Path $StateDir 'compute_state.json'), fix first."
            $bcast = Join-Path (Split-Path -Parent $MeshPy) '..\compute_governor\mesh_broadcast.py'
            try {
                Start-Process -WindowStyle Hidden -FilePath python -ArgumentList @('-s', $bcast, '--topic','compute-distress','--body', $body) -ErrorAction SilentlyContinue
            } catch {}
            Write-Log ("distress: {0} (cpu={1} mem={2} commit={3}% commit_avail={4} hung={5})" -f
                ($reasons -join ','), $cpuPct, $availMB, $commitUsedPct,
                $commitAvailMB, $hungPids.Count)
        }
        $lastDistress = $reasons
    } catch {
        Write-Log "cycle error: $_"
    }

    if ($Once) { break }

    # ---- sleep + starvation probe (sliced: when the owner was away, wake within ~5s of
    # his return so demotions land in seconds, not at the next 20s cycle) ----
    $sleepStart = Get-Date
    $slept = 0
    while ($slept -lt $IntervalSec) {
        $s = [math]::Min(5, $IntervalSec - $slept)
        Start-Sleep -Seconds $s
        $slept += $s
        if ($ownerAway -and ([CG.Native]::IdleMs() -lt 5000)) { break }
    }
    $script:lastOvershoot = ((Get-Date) - $sleepStart).TotalSeconds / [double]$IntervalSec
}
