# Forensic dump — captures a complete system-state snapshot to telemetry\forensic\.
# Invoked by crisis-correlator on CRISIS, or manually for ad-hoc inspection.

[CmdletBinding()]
param([string]$Reason = 'manual', [switch]$Compress)

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$dir = Join-Path $CG_TELEMETRY ('forensic_' + $ts)
New-Item -ItemType Directory -Path $dir -Force | Out-Null

CG-Log 'forensic-dump' 'info' ('Forensic dump starting reason=' + $Reason + ' dir=' + $dir)

# 1. Process list with handles + memory
Get-Process | Select-Object Id, Name, Path, @{n='MB';e={[int]($_.WorkingSet64/1MB)}}, @{n='CPU';e={[math]::Round($_.CPU,1)}}, Handles, @{n='Threads';e={$_.Threads.Count}}, StartTime | ConvertTo-Json -Depth 3 | Out-File (Join-Path $dir 'processes.json') -Encoding utf8 -Force

# 2. Running drivers
Get-CimInstance Win32_SystemDriver -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Running' } | Select-Object Name, DisplayName, ServiceType, StartMode | ConvertTo-Json | Out-File (Join-Path $dir 'drivers.json') -Encoding utf8 -Force

# 3. Recent event log (last 5 minutes, warn+)
$cutoff = (Get-Date).AddMinutes(-5)
Get-WinEvent -FilterHashtable @{LogName=@('System','Application'); StartTime=$cutoff} -ErrorAction SilentlyContinue | Where-Object { $_.LevelDisplayName -in 'Warning','Error','Critical' } | Select-Object -First 100 TimeCreated, ProviderName, Id, LevelDisplayName, @{n='msg';e={if($_.Message){($_.Message -replace '\r?\n',' ').Substring(0,[Math]::Min(200,$_.Message.Length))}}} | ConvertTo-Json -Depth 3 | Out-File (Join-Path $dir 'recent_events.json') -Encoding utf8 -Force

# 4. GPU state
$nvsmi = CG-FindNvSmi
if ($nvsmi) {
    & $nvsmi --query-gpu=name,driver_version,pstate,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,clocks.gr,clocks.mem,fan.speed,pcie.link.gen.gpucurrent,pcie.link.width.current --format=csv 2>$null | Out-File (Join-Path $dir 'gpu_state.csv') -Encoding utf8 -Force
}

# 5. Last 30 ring samples
$ringPath = Join-Path $CG_TELEMETRY 'ring.jsonl'
if (Test-Path $ringPath) {
    Get-Content $ringPath -Tail 30 | Out-File (Join-Path $dir 'ring_tail.jsonl') -Encoding utf8 -Force
}

# 6. Last 50 spike entries
$spikePath = Join-Path $CG_TELEMETRY 'spike-log.jsonl'
if (Test-Path $spikePath) {
    Get-Content $spikePath -Tail 50 | Out-File (Join-Path $dir 'spike_tail.jsonl') -Encoding utf8 -Force
}

# 7. Active mitigations
$amPath = Join-Path $CG_STATE 'active-mitigations.json'
if (Test-Path $amPath) {
    Copy-Item $amPath (Join-Path $dir 'active_mitigations.json') -Force
}

# 8. Service heartbeats
Get-ChildItem (Join-Path $CG_STATE '*.heartbeat') -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $dir $_.Name) -Force
}

# 9. Power-cfg state
$pinfo = @{
    active = powercfg /getactivescheme
    monitor_ac = powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'AC Power Setting Index'
    monitor_dc = powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'DC Power Setting Index'
    standby_ac = powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String 'AC Power Setting Index'
}
$pinfo | ConvertTo-Json | Out-File (Join-Path $dir 'power_state.json') -Encoding utf8 -Force

# Summary manifest
$manifest = [PSCustomObject]@{
    timestamp = CG-Now
    reason = $Reason
    files = (Get-ChildItem $dir -File | ForEach-Object { @{ name=$_.Name; size_kb=[int]($_.Length/1KB) } })
}
$manifest | ConvertTo-Json -Depth 4 | Out-File (Join-Path $dir 'manifest.json') -Encoding utf8 -Force

CG-Log 'forensic-dump' 'info' ('Forensic dump complete: ' + (Get-ChildItem $dir | Measure-Object).Count + ' files')
Write-Output ('Dumped to ' + $dir)
return $dir
