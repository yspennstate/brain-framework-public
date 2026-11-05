# status.ps1 - fleet health at a glance: which registered agents are working, idle, or dead.
# Read-only. Pair with relaunch.ps1 (bring dead ones back) and killall.ps1 (stop them all).
$ErrorActionPreference = 'Continue'
$reg  = "$env:USERPROFILE\.claude\fleet\registry"
$proj = "$env:USERPROFILE\.claude\projects\{{BRAIN_SLUG}}"
$codex = Join-Path $env:USERPROFILE '.codex\sessions'
$running = @(Get-CimInstance Win32_Process -Filter "name='claude.exe' OR name='codex.exe'" | ForEach-Object { "$($_.CommandLine)" })
function Test-Alive($id) {
    foreach ($c in $running) { if ($c -match [regex]::Escape($id)) { return $true } }
    $tf = Join-Path $proj "$id.jsonl"
    if ((Test-Path $tf) -and (((Get-Date) - (Get-Item $tf).LastWriteTime).TotalSeconds -lt 180)) { return $true }
    # codex transcripts live under ~/.codex/sessions, not $proj - a live codex reads as DEAD otherwise.
    if (Get-ChildItem $codex -Recurse -Filter "*$id*.jsonl" -ErrorAction SilentlyContinue | Where-Object { ((Get-Date) - $_.LastWriteTime).TotalSeconds -lt 180 } | Select-Object -First 1) { return $true }
    return $false
}
$rows = @()
Get-ChildItem "$reg\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
    try { $a = Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return }
    $tf = Join-Path $proj "$($a.id).jsonl"
    $lw = if (Test-Path $tf) { (Get-Item $tf).LastWriteTime } else { $null }
    $ageS = if ($lw) { [int]((Get-Date) - $lw).TotalSeconds } else { -1 }
    $alive = Test-Alive $a.id
    $state = if (-not $alive) { 'DEAD' } elseif ($ageS -ge 0 -and $ageS -lt 180) { 'working' } else { 'idle' }
    $rows += [pscustomobject]@{
        agent = $a.title; runtime = $a.runtime; state = $state
        lastActivity = if ($lw) { $lw.ToString('HH:mm:ss') } else { '-' }; id = $a.id.Substring(0, 8)
    }
}
"=== fleet status ($(Get-Date -Format HH:mm:ss)) ==="
if ($rows.Count) { $rows | Sort-Object @{e={@('DEAD','working','idle').IndexOf($_.state)}}, agent | Format-Table -AutoSize }
else { "(no registered agents - seed with register.ps1 or relaunch.ps1 -Discover)" }
$dead = @($rows | Where-Object { $_.state -eq 'DEAD' }).Count
$work = @($rows | Where-Object { $_.state -eq 'working' }).Count
$idle = @($rows | Where-Object { $_.state -eq 'idle' }).Count
"summary: $($rows.Count) registered | $work working | $idle idle | $dead DEAD"
if ($dead) { "  -> run  relaunch.ps1  to bring the $dead dead one(s) back" }
try { $h = Get-Content "$env:USERPROFILE\.claude\compute\compute_state.json" -Raw | ConvertFrom-Json
    "machine: status=$($h.status) cpu=$($h.cpu_pct)% memAvailMB=$($h.mem_avail_mb)" } catch {}
