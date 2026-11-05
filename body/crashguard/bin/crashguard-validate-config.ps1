# Validate configs — checks policies.json + thresholds.json parse and have required fields.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$errors = New-Object 'System.Collections.Generic.List[string]'

# thresholds.json
$tPath = Join-Path $CG_CONFIGS 'thresholds.json'
try {
    $t = Get-Content $tPath -Raw | ConvertFrom-Json
    $required = @('gpu_temp_c','gpu_util_pct','gpu_mem_used_pct','cpu_util_pct','ram_used_pct')
    foreach ($r in $required) {
        if (-not $t.$r) { $errors.Add("thresholds.json missing $r") }
        else {
            foreach ($tier in 'tier_yellow','tier_orange','tier_red','tier_critical') {
                if (-not $t.$r.$tier) { $errors.Add("thresholds.json $r missing $tier") }
            }
        }
    }
    Write-Output ('[ ok ] thresholds.json parsed; ' + $required.Count + ' metric blocks')
} catch {
    $errors.Add('thresholds.json parse failed: ' + $_)
    Write-Output ('[FAIL] thresholds.json')
}

# policies.json
$pPath = Join-Path $CG_CONFIGS 'policies.json'
try {
    $p = Get-Content $pPath -Raw | ConvertFrom-Json
    if (-not $p.rules -or $p.rules.Count -eq 0) { $errors.Add('policies.json: no rules') }
    if (-not $p.tier_actions) { $errors.Add('policies.json: no tier_actions') }
    foreach ($tier in 'yellow','orange','red','critical') {
        if (-not $p.tier_actions.$tier) { $errors.Add("policies.json: no tier_actions.$tier") }
        elseif (-not $p.tier_actions.$tier.actions) { $errors.Add("policies.json: $tier has no actions list") }
    }
    Write-Output ('[ ok ] policies.json parsed; ' + $p.rules.Count + ' rules, tier_actions present')
} catch {
    $errors.Add('policies.json parse failed: ' + $_)
    Write-Output ('[FAIL] policies.json')
}

Write-Output ''
if ($errors.Count -eq 0) {
    Write-Output 'CONFIG VALID'
    exit 0
} else {
    Write-Output ('CONFIG ERRORS (' + $errors.Count + '):')
    $errors | ForEach-Object { Write-Output ('  ' + $_) }
    exit 1
}
