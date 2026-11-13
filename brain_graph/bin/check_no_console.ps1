# check_no_console.ps1 - fire a scheduled task once and prove no process of its run had a console host, so no console
# window could have shown, not even for a frame. (A console started hidden still has a host: this is the strict test.)
#
#   powershell -NoProfile -File check_no_console.ps1 -TaskName BrainGraphCapture1h -Match 'brain_graph\\bin\\run_graph\.vbs'
#
# Starts the task, finds the wscript.exe whose command line matches -Match (the hidden launcher the task runs) and
# watches every process descended from it until the tree is gone or -Seconds pass. A console window needs a console
# host (conhost.exe or OpenConsole.exe), which lives as long as the console process it serves. One listing of all
# processes takes about a second on a busy machine, so a console process that lives longer than that is caught; one
# that starts and exits between two listings can slip through, and a run that does that should be checked again.
# Prints every process seen in the tree, the number of console hosts, and the task's result; exits 1 when a console
# host was attached, the launcher was never seen, or the run was still going when -Seconds ran out, else 0.
param(
    [Parameter(Mandatory = $true)][string]$TaskName,
    [Parameter(Mandatory = $true)][string]$Match,
    [int]$Seconds = 120
)
$ErrorActionPreference = 'Stop'
$seen = @{}
$consoles = 0
$t0 = Get-Date
Start-ScheduledTask -TaskName $TaskName
$root = $null
$done = $false
while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine
    if (-not $root) {
        $w = $all | Where-Object { $_.Name -eq 'wscript.exe' -and $_.CommandLine -match $Match }
        if ($w) { $root = @($w)[0].ProcessId }
    }
    if ($root) {
        $tree = @{ [int]$root = $true }
        $grew = $true
        while ($grew) {
            $grew = $false
            foreach ($p in $all) {
                if ($tree.ContainsKey([int]$p.ParentProcessId) -and -not $tree.ContainsKey([int]$p.ProcessId)) {
                    $tree[[int]$p.ProcessId] = $true; $grew = $true
                }
            }
        }
        foreach ($p in $all) {
            if ($tree.ContainsKey([int]$p.ProcessId) -and -not $seen.ContainsKey([int]$p.ProcessId)) {
                $seen[[int]$p.ProcessId] = $p.Name
                if ($p.Name -in @('conhost.exe', 'OpenConsole.exe')) { $consoles++ }
            }
        }
        $alive = $all | Where-Object { $tree.ContainsKey([int]$_.ProcessId) }
        if (-not $alive) { $done = $true; break }
    }
    Start-Sleep -Milliseconds 100
}
'processes in the run: ' + (($seen.GetEnumerator() | ForEach-Object { '{0}({1})' -f $_.Value, $_.Key }) -join ', ')
'console hosts attached: ' + $consoles
'task result: ' + (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
if (-not $root) { 'the launcher was never seen: check -Match'; exit 1 }
if ($consoles -gt 0) { exit 1 }
if (-not $done) { 'the run was still going after ' + $Seconds + ' s: not fully checked, raise -Seconds'; exit 1 }
exit 0
