# Rollback test — verify each mitigation action has a clean rollback path.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$pass = 0
$fail = 0

# 1. Throttle/restore — spawn a sacrificial process, throttle it, restore.
Write-Output 'Test 1: throttle then restore'
$sacrificial = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 60' -WindowStyle Hidden -PassThru
Start-Sleep -Milliseconds 500
try {
    $proc = Get-Process -Id $sacrificial.Id -ErrorAction Stop
    $orig = $proc.PriorityClass
    $proc.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
    Start-Sleep -Milliseconds 200
    $afterSet = (Get-Process -Id $sacrificial.Id).PriorityClass
    $proc.PriorityClass = CG-ResolvePriorityClass $orig
    Start-Sleep -Milliseconds 200
    $afterRestore = (Get-Process -Id $sacrificial.Id).PriorityClass
    if ($afterSet -eq 'BelowNormal' -and $afterRestore -eq $orig) {
        Write-Output ('  PASS throttle: ' + $orig + ' -> BelowNormal -> ' + $afterRestore)
        $pass++
    } else {
        Write-Output ('  FAIL throttle: orig=' + $orig + ' after_set=' + $afterSet + ' after_restore=' + $afterRestore)
        $fail++
    }
} catch {
    Write-Output ('  FAIL throttle: ' + $_)
    $fail++
} finally {
    Stop-Process -Id $sacrificial.Id -Force -ErrorAction SilentlyContinue
}

# 1b. Verify the exact failure mode that previously stranded priorities:
# enum -> JSON -> numeric legacy value/name -> priority class, plus empty-array state.
Write-Output 'Test 1b: priority JSON round-trip and empty state'
$tmpState = Join-Path $env:TEMP ('crashguard-priority-test-' + [Guid]::NewGuid().ToString('N') + '.json')
try {
    $normalByName = CG-ResolvePriorityClass 'Normal'
    $normalByLegacyNumber = CG-ResolvePriorityClass 32
    CG-SaveJson $tmpState @()
    $emptyJson = (Get-Content -LiteralPath $tmpState -Raw).Trim()
    if ($normalByName -eq 'Normal' -and $normalByLegacyNumber -eq 'Normal' -and $emptyJson -eq '[]') {
        Write-Output '  PASS priority names/numbers resolve and empty state is []'
        $pass++
    } else {
        Write-Output ('  FAIL name=' + $normalByName + ' legacy=' + $normalByLegacyNumber + ' empty=' + $emptyJson)
        $fail++
    }
} catch {
    Write-Output ('  FAIL priority JSON round-trip: ' + $_)
    $fail++
} finally {
    Remove-Item -LiteralPath $tmpState -Force -ErrorAction SilentlyContinue
}

Write-Output 'Test 1c: process identity blocks PID-reuse rollback'
try {
    $self = Get-Process -Id $PID
    $same = CG-IsSameProcess $self $self.ProcessName $self.StartTime.ToUniversalTime().ToString('o')
    $wrongName = CG-IsSameProcess $self 'definitely-not-this-process' $self.StartTime.ToUniversalTime().ToString('o')
    $wrongTime = CG-IsSameProcess $self $self.ProcessName $self.StartTime.AddMinutes(-5).ToUniversalTime().ToString('o')
    if ($same -and -not $wrongName -and -not $wrongTime) {
        Write-Output '  PASS matching identity accepted; reused-name/time identities rejected'
        $pass++
    } else {
        Write-Output ('  FAIL same=' + $same + ' wrongName=' + $wrongName + ' wrongTime=' + $wrongTime)
        $fail++
    }
} catch {
    Write-Output ('  FAIL process identity: ' + $_)
    $fail++
}

# 2. Suspend/resume — spawn, suspend, verify CPU usage drops, resume, verify resumes.
Write-Output 'Test 2: suspend then resume'
$sacrificial = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','while ($true) { 1..10000 | ForEach-Object { $_ * 2 } }' -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
try {
    $cpuBefore = (Get-Process -Id $sacrificial.Id).CPU
    $ok = CG-Suspend $sacrificial.Id
    Start-Sleep -Seconds 2
    $cpuMid = (Get-Process -Id $sacrificial.Id).CPU
    $resumeOk = CG-Resume $sacrificial.Id
    Start-Sleep -Seconds 2
    $cpuAfter = (Get-Process -Id $sacrificial.Id).CPU
    $deltaSuspended = [math]::Round($cpuMid - $cpuBefore, 2)
    $deltaResumed = [math]::Round($cpuAfter - $cpuMid, 2)
    if ($ok -and $resumeOk -and $deltaSuspended -lt 0.5 -and $deltaResumed -gt 0.5) {
        Write-Output ('  PASS suspend: cpu_delta_while_suspended=' + $deltaSuspended + 's (low), cpu_delta_after_resume=' + $deltaResumed + 's (high)')
        $pass++
    } else {
        Write-Output ('  FAIL suspend: suspend_ok=' + $ok + ' resume_ok=' + $resumeOk + ' delta_suspended=' + $deltaSuspended + ' delta_resumed=' + $deltaResumed)
        $fail++
    }
} catch {
    Write-Output ('  FAIL suspend: ' + $_)
    $fail++
} finally {
    Stop-Process -Id $sacrificial.Id -Force -ErrorAction SilentlyContinue
}

# 3. Kill — verify a sacrificial process is gone.
Write-Output 'Test 3: kill'
$sacrificial = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 60' -WindowStyle Hidden -PassThru
Start-Sleep -Milliseconds 500
try {
    Stop-Process -Id $sacrificial.Id -Force
    Start-Sleep -Milliseconds 500
    $alive = Get-Process -Id $sacrificial.Id -ErrorAction SilentlyContinue
    if (-not $alive) {
        Write-Output ('  PASS kill: process gone')
        $pass++
    } else {
        Write-Output ('  FAIL kill: still alive')
        $fail++
    }
} catch {
    Write-Output ('  FAIL kill: ' + $_)
    $fail++
}

Write-Output ''
Write-Output ('rollback test: ' + $pass + ' pass / ' + $fail + ' fail')
if ($fail -gt 0) { exit 1 } else { exit 0 }
