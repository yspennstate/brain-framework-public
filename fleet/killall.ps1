# killall.ps1 - stop ALL fleet AI agents (claude + codex) at once. For a hard reset or clean-up after a crash.
# SAFE BY DESIGN:
#   * NEVER touches the WindowsTerminal process (killing it kills every WT window incl. your own = the 07-06 suicide).
#   * NEVER kills the controlling session this script runs under (detected via process ancestry).
#   * NEVER kills dream/nap sessions or anything you pass with -KeepId.
#   * Use -DryRun first to see exactly what would die.
# Usage:  powershell -File killall.ps1 -DryRun
#         powershell -File killall.ps1                 # live
#         powershell -File killall.ps1 -KeepId 0c44dd79,019f4b99
param([switch]$DryRun, [string[]]$KeepId = @())
$ErrorActionPreference = 'Continue'

function Get-Ancestors([int]$p) {
    $ids = @(); $cur = $p
    for ($i = 0; $i -lt 12 -and $cur; $i++) {
        $pr = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $pr) { break }
        $ids += [int]$pr.ProcessId; $cur = [int]$pr.ParentProcessId
    }
    return $ids
}
$self = Get-Ancestors $PID

$agents = @(Get-CimInstance Win32_Process -Filter "name='claude.exe' OR name='codex.exe'")
$acted = @(); $kept = @()
foreach ($a in $agents) {
    $cl = "$($a.CommandLine)"
    $why = $null
    if ($self -contains [int]$a.ProcessId) { $why = 'controlling session' }
    elseif ($cl -match 'run_dream|dream_prompt|Hard time budget') { $why = 'dream/nap' }
    else { foreach ($k in $KeepId) { if ($cl -match [regex]::Escape($k)) { $why = "keep ($k)" } } }
    if ($why) { $kept += ("  KEEP {0} pid={1}  [{2}]" -f $a.Name, $a.ProcessId, $why); continue }
    if ($DryRun) { $acted += ("  would kill {0} pid={1}" -f $a.Name, $a.ProcessId) }
    else {
        try { Stop-Process -Id $a.ProcessId -Force -ErrorAction Stop; $acted += ("  killed {0} pid={1}" -f $a.Name, $a.ProcessId) }
        catch { $acted += ("  FAILED {0} pid={1}: {2}" -f $a.Name, $a.ProcessId, $_.Exception.Message.Split([char]10)[0]) }
    }
}
$mode = if ($DryRun) { 'DRY RUN' } else { 'LIVE' }
"=== fleet killall ($mode) ==="
"kept:";  if ($kept) { $kept } else { "  (none)" }
"acted:"; if ($acted) { $acted } else { "  (no fleet agents running)" }
"WindowsTerminal is never touched; dead agents leave a 'session ended' window you can close by hand."
