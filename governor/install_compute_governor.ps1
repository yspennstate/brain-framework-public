# install_compute_governor.ps1 - register the governor scheduled tasks (idempotent).
#
# Two tasks, both Interactive (logged-on user) and both launched through VBS SW_HIDE so no
# console window ever flashes (no_flashing_terminal_windows_2026_06_07):
#   ComputeGovernor           - at logon, starts the long-running hidden governor loop
#   ComputeGovernorAliveCheck - every 30 min, relaunches the loop if dead/stale
# Then starts the governor immediately and verifies the heartbeat.

[CmdletBinding()]
param([switch]$LibraryOnly)

function Select-OldGovernorPid($Matches, [bool]$FreshHeartbeat) {
    $items = @($Matches)
    if ($items.Count -gt 1) {
        throw 'multiple governor processes match the canonical script; refusing installation'
    }
    if ($items.Count -eq 1) { return [int]$items[0].ProcessId }
    if ($FreshHeartbeat) {
        throw 'fresh governor heartbeat exists but no identity-checked process was found'
    }
    return $null
}

function Test-CanonicalGovernorProcess($Process, [string]$GovScript) {
    if (-not $Process -or $Process.Name -ine 'powershell.exe' -or
        [string]::IsNullOrWhiteSpace($Process.CommandLine)) {
        return $false
    }
    $powerShellExe = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    $pattern = '^\s*(?:"{0}"|{0})\s+-NoProfile\s+-NonInteractive\s+' +
        '-ExecutionPolicy\s+Bypass\s+-File\s+"{1}"\s*$'
    $pattern = $pattern -f [regex]::Escape($powerShellExe), [regex]::Escape($GovScript)
    return [bool]($Process.CommandLine -match $pattern)
}

function Get-CanonicalGovernorProcesses([string]$GovScript) {
    return @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { Test-CanonicalGovernorProcess $_ $GovScript }
    )
}

function Assert-PowerShellSyntax([string]$Path) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $Path, [ref]$tokens, [ref]$errors
    )
    if (@($errors).Count -gt 0) {
        throw ("PowerShell syntax preflight failed for {0}: {1}" -f
            $Path, (($errors | ForEach-Object { $_.Message }) -join '; '))
    }
}

function Assert-PythonSyntax([string]$Path) {
    $env:CG_HOOK_PREFLIGHT = $Path
    try {
        # Feed the checker on stdin. Windows PowerShell's legacy native-command
        # marshalling can strip the quotes from a Python -c expression.
        $pythonOutput = @'
import ast
import os
import pathlib

ast.parse(pathlib.Path(os.environ["CG_HOOK_PREFLIGHT"]).read_text(encoding="utf-8"))
'@ | & python -B - 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw ('compute_sense.py syntax preflight failed: ' + ($pythonOutput -join ' '))
        }
    } finally {
        Remove-Item Env:CG_HOOK_PREFLIGHT -ErrorAction SilentlyContinue
    }
}

if ($LibraryOnly) { return }

$ErrorActionPreference = 'Stop'
$Base = (Join-Path $env:USERPROFILE 'brain-repo\12_cognitive_architecture\compute_governor')
$GovLauncher   = Join-Path $Base 'launch_compute_governor.vbs'
$CheckLauncher = Join-Path $Base 'launch_alive_check.vbs'
$GovScript     = Join-Path $Base 'compute_governor.ps1'
$AliveScript   = Join-Path $Base 'governor_alive_check.ps1'
$AzurePoll     = Join-Path $Base 'azure_poll.ps1'
$HookSource    = Join-Path (Split-Path -Parent $Base) 'dreaming\compute_sense.py'
$HookTarget    = (Join-Path $env:USERPROFILE '.claude\dreaming\compute_sense.py')
$StateFile = (Join-Path $env:USERPROFILE '.claude\compute\compute_state.json')

foreach ($f in @($GovLauncher, $CheckLauncher, $GovScript, $AliveScript, $AzurePoll, $HookSource)) {
    if (-not (Test-Path $f)) { throw "missing launcher: $f" }
}

# Parse every replaced/live executable before stopping the working protector.
foreach ($f in @($GovScript, $AliveScript, $AzurePoll)) { Assert-PowerShellSyntax $f }
Assert-PythonSyntax $HookSource

# Consumer first: the live hook must understand the state schema before either producer
# changes. Keep this explicit because the hook runs from ~/.claude, not from the repo.
New-Item -ItemType Directory -Force (Split-Path -Parent $HookTarget) | Out-Null
Copy-Item -LiteralPath $HookSource -Destination $HookTarget -Force
if ((Get-FileHash $HookSource).Hash -ne (Get-FileHash $HookTarget).Hash) {
    throw 'live compute_sense.py hash does not match the tracked consumer'
}

# A PID file can be absent or stale. Remember whether a current producer claims to exist;
# exact process discovery happens only after both launch paths are shut.
$freshHeartbeat = $false
if (Test-Path $StateFile) {
    $freshHeartbeat = (((Get-Date) - (Get-Item $StateFile).LastWriteTime).TotalSeconds -lt 60)
}
$preStopMatches = @(Get-CanonicalGovernorProcesses $GovScript)
$prevalidatedPid = Select-OldGovernorPid $preStopMatches $freshHeartbeat

# Remove both launch paths before stopping the detached loop, closing the small race where
# the alive check could relaunch the old producer during installation. Re-scan afterward:
# a prevalidated process may legitimately disappear when its task is stopped, while any
# exact canonical process launched in the closing race is safe and necessary to stop.
foreach ($task in @('ComputeGovernor','ComputeGovernorAliveCheck')) {
    try { Stop-ScheduledTask -TaskName $task -ErrorAction Stop } catch {}
    try { Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction Stop } catch {}
}
$oldMatches = @(Get-CanonicalGovernorProcesses $GovScript)
$oldPids = @($oldMatches | ForEach-Object { [int]$_.ProcessId })
foreach ($oldPid in $oldPids) {
    Stop-Process -Id $oldPid -Force -ErrorAction Stop
    try { Wait-Process -Id $oldPid -Timeout 10 -ErrorAction Stop } catch {
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) { throw }
    }
}

$user = "$env:USERDOMAIN\$env:USERNAME"

# --- ComputeGovernor (at logon) ---
$act1  = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B "{0}"' -f $GovLauncher)
$trg1  = New-ScheduledTaskTrigger -AtLogOn -User $user
$set1  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
         -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'ComputeGovernor' -Action $act1 -Trigger $trg1 -Settings $set1 | Out-Null

# --- ComputeGovernorAliveCheck (every 30 min) ---
$act2 = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B "{0}"' -f $CheckLauncher)
$trg2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$set2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'ComputeGovernorAliveCheck' -Action $act2 -Trigger $trg2 -Settings $set2 | Out-Null

# --- start now and verify heartbeat ---
$startedAt = Get-Date
Start-ScheduledTask -TaskName 'ComputeGovernor'
$verifyDeadline = $startedAt.AddSeconds(60)
$verifiedState = $null
$verifiedAge = $null
$lastVerification = 'no state file observed'
do {
    if (Test-Path $StateFile) {
        try {
            $stateItem = Get-Item $StateFile
            $candidateState = Get-Content -Raw $StateFile | ConvertFrom-Json
            $candidatePid = if ($candidateState.pid) { [int]$candidateState.pid } else { 0 }
            $candidateAge = ((Get-Date) - $stateItem.LastWriteTime).TotalSeconds
            $freshLaunch = ($stateItem.LastWriteTime -ge $startedAt.AddSeconds(-1))
            $pidChanged = ($candidatePid -gt 0 -and $candidatePid -notin $oldPids)
            $newProc = if ($candidatePid -gt 0) {
                Get-Process -Id $candidatePid -ErrorAction SilentlyContinue
            } else { $null }
            $newCim = if ($candidatePid -gt 0) {
                Get-CimInstance Win32_Process -Filter "ProcessId=$candidatePid" -ErrorAction SilentlyContinue
            } else { $null }
            $identityOk = Test-CanonicalGovernorProcess $newCim $GovScript
            $lastVerification = "pid=$candidatePid age=$([math]::Round($candidateAge, 1))s fresh=$freshLaunch changed=$pidChanged process=$([bool]$newProc) identity=$identityOk"
            if ($candidateAge -lt 60 -and $freshLaunch -and $pidChanged -and $newProc -and $identityOk) {
                $verifiedState = $candidateState
                $verifiedAge = $candidateAge
                break
            }
        } catch {
            $lastVerification = "state/PID check error: $_"
        }
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $verifyDeadline)

if (-not $verifiedState) {
    throw "new governor heartbeat/PID verification failed after 60s ($lastVerification); check governor.log"
}
Write-Output ("OK: governor pid {0}, state {1:N0}s old, live hook synchronized" -f
    $verifiedState.pid, $verifiedAge)
Get-ScheduledTask -TaskName 'ComputeGovernor','ComputeGovernorAliveCheck' |
    Select-Object TaskName, State | Format-Table -AutoSize
