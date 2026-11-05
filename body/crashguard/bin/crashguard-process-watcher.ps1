# Proactive process watcher — runs in tandem with the sentinel.
# Detects KNOWN-RISKY process combinations before metric pressure builds:
#   - ollama running while another GPU app is foreground
#   - Many concurrent Claude/Codex sessions
#   - Heavy Lean compilation during browser-heavy session
# Lowers risk by suspending the lower-priority workload preemptively.

[CmdletBinding()]
param(
    [int]$IntervalSec = 10,
    [switch]$Once,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

function Suspend-By-Name {
    param([string]$Name)
    Get-Process $Name -ErrorAction SilentlyContinue | ForEach-Object {
        [void](CG-Suspend $_.Id)
    }
}

function Risky-Combos {
    $procs = Get-Process
    $names = ($procs | ForEach-Object Name) | Sort-Object -Unique
    $combos = New-Object 'System.Collections.Generic.List[string]'

    if ($names -contains 'ollama' -or $names -contains 'ollama-runner') {
        if ($names -match '^(ExampleGame|ExampleGameClient)$') { $combos.Add('ollama-vs-game') }
        $claudeCount = ($procs | Where-Object { $_.Name -match '^(claude|codex)$' }).Count
        if ($claudeCount -ge 3) { $combos.Add("ollama-with-$claudeCount-Claude-Codex") }
    }
    $leanCount = ($procs | Where-Object { $_.Name -match '^(lake|lean|leanchecker)$' }).Count
    if ($leanCount -ge 4) { $combos.Add("$leanCount-Lean-processes") }
    if ($names -match '^ExampleLauncher' -and $names -match '^ExampleGame$') { $combos.Add('launcher-while-game') }
    $wv = ($procs | Where-Object { $_.Name -eq 'msedgewebview2' }).Count
    if ($wv -gt 25) { $combos.Add("$wv-msedgewebview2-tabs") }
    $os = Get-CimInstance Win32_OperatingSystem
    $ramPct = [int]((1 - $os.FreePhysicalMemory / $os.TotalVisibleMemorySize) * 100)
    if (($names -contains 'ollama') -and $ramPct -gt 60) { $combos.Add("ollama-and-ram-$ramPct") }

    # 2026-05-27: Detect DUPLICATE worker-process instances (waste of RAM, can trip BSOD).
    # READ-ONLY: do not kill — just log and notify the operator session via spike-log.
    $pythons = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    $byScript = @{}
    foreach ($p in $pythons) {
        if ($p.CommandLine -match 'your_worker\\(\w+\.py)') {
            $script = $matches[1]
            if (-not $byScript.ContainsKey($script)) { $byScript[$script] = 0 }
            $byScript[$script]++
        } elseif ($p.CommandLine -match '\\(\w+_observer\.py|\w+_recorder\.py|\w+_streamer\.py|\w+_bot\.py)') {
            $script = $matches[1]
            if (-not $byScript.ContainsKey($script)) { $byScript[$script] = 0 }
            $byScript[$script]++
        }
    }
    $dupes = @($byScript.GetEnumerator() | Where-Object { $_.Value -ge 2 })
    foreach ($d in $dupes) {
        $combos.Add("DUPLICATE-observer:$($d.Key)=$($d.Value)x")
    }

    return ,$combos
}

function React {
    param($Combos)
    if (-not $Combos -or $Combos.Count -eq 0) { return }
    foreach ($c in $Combos) {
        if ($c -match '^ollama-vs-ExampleGame') {
            CG-Log 'process-watcher' 'warn' "Risk: ollama vs a game - suspending ollama"
            if (-not $DryRun) { Suspend-By-Name 'ollama' }
        }
        elseif ($c -match '^BattleNet-while-ExampleGame') {
            CG-Log 'process-watcher' 'warn' "Battle.net while a game runs - suspending Battle.net"
            if (-not $DryRun) { Suspend-By-Name 'Battle.net' }
        }
        else {
            CG-Log 'process-watcher' 'info' "Risk pattern noted (no auto-action): $c"
        }
    }
}

CG-Log 'process-watcher' 'info' "Process watcher starting PID=$PID interval=$IntervalSec dryRun=$DryRun"

while ($true) {
    try {
        $combos = Risky-Combos
        CG-Heartbeat 'process-watcher' @{ combos = $combos; combo_count = $combos.Count }
        if ($combos.Count -gt 0) {
            CG-Log 'process-watcher' 'warn' ("Risky combos: " + ($combos -join ', '))
            React $combos
        }
    } catch {
        CG-Log 'process-watcher' 'error' ("Iter exception: " + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
