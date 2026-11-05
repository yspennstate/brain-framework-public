# agentA status: one line per session + overall process counts.
$base = "$env:USERPROFILE\agentA"
$proj = "$env:USERPROFILE\.claude\projects\{{BRAIN_SLUG}}"
$defs = Get-Content "$base\sessions.json" -Raw | ConvertFrom-Json
$now  = Get-Date

$rows = foreach ($def in $defs) {
    $n = $def.n
    $state = 'DOWN'
    $rpid = ''
    $pidF = "$base\state\$n.pid"
    if (Test-Path $pidF) {
        try { $rpid = [int](Get-Content $pidF -TotalCount 1) } catch {}
        if ($rpid) {
            $p = Get-Process -Id $rpid -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -eq 'powershell') { $state = 'RUNNING' } else { $state = 'DEAD-PID' }
        }
    }
    if (Test-Path "$base\state\$n.stalled") { $state = 'STALLED' }
    if (Test-Path "$base\state\$n.error")   { $state = 'ERROR' }
    if (Test-Path "$base\state\$n.blocked") { $state = 'BLOCKED' }
    if (Test-Path "$base\done\$n.done")     { $state = 'DONE' }

    $lastLog = ''
    $logAge = ''
    $logF = "$base\logs\$($n)_$($def.key).log"
    if (Test-Path $logF) {
        $ll = Get-Content $logF -Tail 1
        if ($ll) { $lastLog = $ll.Substring(0, [Math]::Min(70, $ll.Length)) }
        $logAge = [Math]::Round(($now - (Get-Item $logF).LastWriteTime).TotalMinutes, 0)
    }

    # transcript freshness = is the underlying AI session actually producing?
    $trAge = ''
    if ($def.type -eq 'claude') {
        $head = $def.resumeId
        $headF = "$base\state\$n.head"
        if (Test-Path $headF) { $h = (Get-Content $headF -TotalCount 1); if ($h) { $head = $h.Trim() } }
        $tf = Join-Path $proj "$head.jsonl"
        if (Test-Path $tf) { $trAge = [Math]::Round(($now - (Get-Item $tf).LastWriteTime).TotalMinutes, 0) }
    } else {
        $rf = Get-ChildItem "$env:USERPROFILE\.codex\sessions" -Recurse -Filter "*$($def.resumeId)*.jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($rf) { $trAge = [Math]::Round(($now - $rf.LastWriteTime).TotalMinutes, 0) }
    }

    [pscustomobject]@{
        N = $n; Key = $def.key; Type = $def.type; State = $state; RunnerPid = $rpid
        LogAgeMin = $logAge; TranscriptAgeMin = $trAge; LastLog = $lastLog
    }
}
$rows | Format-Table -AutoSize | Out-String -Width 220

$cl = @(Get-CimInstance Win32_Process -Filter "name='claude.exe'")
$cx = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^codex' -or ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'codex') })
$rn = @(Get-CimInstance Win32_Process -Filter "name='powershell.exe'" | Where-Object { $_.CommandLine -match 'agentA\\run_one\.ps1' })
"runners: $($rn.Count)   claude.exe: $($cl.Count)   codex procs: $($cx.Count)"
if (Test-Path "$base\STOP") { 'NOTE: STOP file present - watchdog disarmed until spin_up_agentA.ps1' }
if (Test-Path "$base\state\last_sweep.txt") { "last watchdog sweep: $(Get-Content "$base\state\last_sweep.txt" -TotalCount 1)" }
