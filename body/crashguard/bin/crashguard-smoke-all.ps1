# Smoke test — runs every one-shot tool once and reports pass/fail.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$tests = @(
    @{ name='preflight';        cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-preflight.ps1" }
    @{ name='summary';          cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-summary.ps1" }
    @{ name='stats';            cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-stats.ps1" }
    @{ name='coverage';         cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-coverage.ps1" }
    @{ name='status';           cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-status.ps1" }
    @{ name='html-status';      cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-html-status.ps1" }
    @{ name='session-summary';  cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-session-summary.ps1" }
    @{ name='pulse-graph';      cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-pulse-graph.ps1" }
    @{ name='health-check';     cmd="$env:USERPROFILE\CrashGuard\bin\crashguard-health-check.ps1" }
)

$pass = 0
$fail = 0
foreach ($t in $tests) {
    try {
        & $t.cmd 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
            Write-Output ('[ ok ] ' + $t.name)
            $pass++
        } else {
            Write-Output ('[FAIL] ' + $t.name + ' exit=' + $LASTEXITCODE)
            $fail++
        }
    } catch {
        Write-Output ('[FAIL] ' + $t.name + ' exception=' + $_)
        $fail++
    }
}
Write-Output ''
Write-Output ('smoke: ' + $pass + ' pass / ' + $fail + ' fail')
if ($fail -gt 0) { exit 1 } else { exit 0 }
