# Unit test: per-process criticality classification.
# Confirms games, Claude, observers, ollama all match expected criticality.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$policies = CG-LoadJson (Join-Path $CG_CONFIGS 'policies.json')

$cases = @(
    @{ name='ExampleGame';                  expected='critical' }
    @{ name='dwm';                          expected='critical' }
    @{ name='csrss';                        expected='critical' }
    @{ name='nvcontainer';                  expected='critical' }
    @{ name='claude';                       expected='high' }
    @{ name='observer_drilldown';           expected='medium' }
    @{ name='inspector_finding';            expected='medium' }
    @{ name='codex';                        expected='medium' }
    @{ name='codex-cli';                    expected='medium' }
    @{ name='chrome';                       expected='low' }
    @{ name='msedge';                       expected='low' }
    @{ name='msedgewebview2';               expected='low' }
    @{ name='Battle.net';                   expected='low' }
    @{ name='Discord';                      expected='low' }
    @{ name='ollama';                       expected='expendable' }
    @{ name='ollama-runner';                expected='expendable' }
    @{ name='lake';                         expected='expendable' }
    @{ name='lean';                         expected='expendable' }
    @{ name='M365Copilot';                  expected='expendable' }
    @{ name='SearchHost';                   expected='expendable' }
    @{ name='somerandomexe';                expected='medium' }
)

$pass = 0
$fail = 0
foreach ($c in $cases) {
    $info = CG-CriticalityOf $policies $c.name 0 0
    $got = $info.criticality
    if ($got -eq $c.expected) { $pass++ } else {
        $fail++
        Write-Output ("FAIL: {0} expected={1} got={2} label={3}" -f $c.name, $c.expected, $got, $info.label)
    }
}
Write-Output ""
Write-Output "Pass: $pass / $($cases.Count)   Fail: $fail"
if ($fail -gt 0) { exit 1 } else { exit 0 }
