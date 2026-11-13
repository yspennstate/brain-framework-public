$ErrorActionPreference = 'Stop'
$Governor = Resolve-Path (Join-Path $PSScriptRoot '..\..\compute_governor.ps1')
$AzurePoll = Resolve-Path (Join-Path $PSScriptRoot '..\..\azure_poll.ps1')
$Installer = Resolve-Path (Join-Path $PSScriptRoot '..\..\install_compute_governor.ps1')
. $Governor -Status | Out-Null
. $AzurePoll -LibraryOnly
. $Installer -LibraryOnly
$Hook = Resolve-Path (Join-Path $PSScriptRoot '..\..\..\dreaming\compute_sense.py')

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ("$Actual" -ne "$Expected") {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

$legacyProtectedOnly = Get-AzureSignals ([pscustomobject]@{
    status = 'distress'; mode = 'throttle'
    reasons = @('protected_unit_not_active:example-prod-1,example-feed.service')
})
Assert-Equal $legacyProtectedOnly.PressureDistress $false 'legacy protected-only distress'
Assert-Equal $legacyProtectedOnly.Throttle $false 'legacy protected-only throttle'
Assert-Equal $legacyProtectedOnly.ProtectedHealth 'alert' 'legacy protected health'
Assert-Equal $legacyProtectedOnly.ProtectedAlerts.Count 1 'legacy protected alerts count'

$legacyMeasuredThrottle = Get-AzureSignals ([pscustomobject]@{
    status = 'distress'; mode = 'throttle'; psi_cpu_some_avg60 = 35
    reasons = @('protected_unit_not_active:example-prod-1')
})
Assert-Equal $legacyMeasuredThrottle.PressureDistress $false 'legacy measured distress'
Assert-Equal $legacyMeasuredThrottle.PressureWarning $true 'legacy measured warning'
Assert-Equal $legacyMeasuredThrottle.Throttle $true 'legacy measured CPU throttle'

$mixed = Get-AzureSignals ([pscustomobject]@{
    status = 'distress'; mode = 'throttle'
    reasons = @('cpu_pressure','protected_unit_not_active:example-prod-1')
})
Assert-Equal $mixed.PressureDistress $true 'mixed pressure distress'
Assert-Equal $mixed.Throttle $true 'mixed pressure throttle'

$separated = Get-AzureSignals ([pscustomobject]@{
    status = 'ok'; mode = 'open'; reasons = @()
    protected_health = 'alert'; protected_alerts = @('protected_unit_not_active:example-prod-1')
})
Assert-Equal $separated.PressureDistress $false 'separated pressure distress'
Assert-Equal $separated.Throttle $false 'separated pressure throttle'
Assert-Equal $separated.ProtectedHealth 'alert' 'separated protected health'

$malformedSeparated = Get-AzureSignals ([pscustomobject]@{
    status = 'distress'; mode = 'throttle'; reasons = @()
    protected_health = 'alert'; protected_alerts = @('protected_unit_not_active:example-prod-1')
})
Assert-Equal $malformedSeparated.PressureDistress $true 'separated unknown distress fail-closed'
Assert-Equal $malformedSeparated.Throttle $true 'separated unknown throttle fail-closed'

$fallback = Get-AzureSignals ([pscustomobject]@{
    status = 'warn'; mode = 'throttle'; telemetry_degraded = $true
    reasons = @('fallback_telemetry')
})
Assert-Equal $fallback.PressureDistress $false 'fallback pressure distress'
Assert-Equal $fallback.PressureWarning $true 'fallback pressure warning'
Assert-Equal $fallback.Throttle $true 'fallback throttle'

$combinedFallback = New-FallbackAzureHealth -PsiCpu 10 -Load1 10
Assert-Equal $combinedFallback.status 'distress' 'combined fallback pressure'
Assert-Equal $combinedFallback.mode 'throttle' 'combined fallback throttle'

$staleFallback = New-FallbackAzureHealth -PsiCpu 0 -Load1 5 -SentinelStale $true
$selectedStale = Select-AzureHealth ([pscustomobject]@{
    status = 'ok'; mode = 'open'; reasons = @(); ts = 'stale'
}) $true $staleFallback
Assert-Equal $selectedStale.status 'warn' 'stale sentinel status'
Assert-Equal $selectedStale.mode 'throttle' 'stale sentinel throttle'
Assert-Equal ($selectedStale.reasons -contains 'sentinel_stale') $true 'stale reason'
Assert-Equal $selectedStale.protected_health 'unknown' 'stale protected health'

$staleLiveDistress = Select-AzureHealth ([pscustomobject]@{
    status = 'ok'; mode = 'open'; reasons = @(); ts = 'stale'
}) $true (New-FallbackAzureHealth -PsiCpu 40 -Load1 5 -SentinelStale $true)
Assert-Equal $staleLiveDistress.status 'distress' 'stale live PSI distress'
Assert-Equal ($staleLiveDistress.reasons -contains 'cpu_pressure') $true 'stale live PSI reason'

$clock = [datetime]'2026-07-11T08:30:00'
Assert-Equal (Test-SentinelStale ([pscustomobject]@{ts='2026-07-11 08:25:00'}) $clock) $false 'fresh sentinel timestamp'
Assert-Equal (Test-SentinelStale ([pscustomobject]@{ts='2026-07-11 08:00:00'}) $clock) $true 'old sentinel timestamp'
Assert-Equal (Test-SentinelStale ([pscustomobject]@{ts='not-a-time'}) $clock) $true 'invalid sentinel timestamp'
Assert-Equal (Test-SentinelStale ([pscustomobject]@{}) $clock) $true 'missing sentinel timestamp'
Assert-Equal (Test-SentinelStale $null $clock) $true 'absent sentinel record'

$missingSentinel = Select-AzureHealth $null $true (
    New-FallbackAzureHealth -PsiCpu 0 -Load1 4 -SentinelStale $true
)
Assert-Equal $missingSentinel.protected_health 'unknown' 'missing sentinel protected health'
Assert-Equal ($missingSentinel.protected_alerts -contains 'protected_telemetry_stale') $true 'missing sentinel protected alerts'

$unknownDistress = Get-AzureSignals ([pscustomobject]@{
    status = 'distress'; mode = 'throttle'; reasons = @()
})
Assert-Equal $unknownDistress.PressureDistress $true 'unknown distress fail-closed'
Assert-Equal $unknownDistress.Throttle $true 'unknown throttle fail-closed'

$quietAzure = Get-AzureSignals $null
Assert-Equal (Test-GlobalThrottle 0 3 'warn' $quietAzure 6) $true 'unreachable Azure throttle'
Assert-Equal (Test-GlobalThrottle 0 3 'ok' $quietAzure 0) $false 'quiet global throttle'

Assert-Equal (Select-OldGovernorPid @([pscustomobject]@{ProcessId=123}) $true) 123 'installer exact process'
Assert-Equal (Select-OldGovernorPid @() $false) $null 'installer quiet first launch'
$missingPidFailedClosed = $false
try { Select-OldGovernorPid @() $true | Out-Null } catch { $missingPidFailedClosed = $true }
Assert-Equal $missingPidFailedClosed $true 'installer missing PID fail-closed'
$duplicateFailedClosed = $false
try {
    Select-OldGovernorPid @(
        [pscustomobject]@{ProcessId=123}, [pscustomobject]@{ProcessId=456}
    ) $false | Out-Null
} catch { $duplicateFailedClosed = $true }
Assert-Equal $duplicateFailedClosed $true 'installer duplicate PID fail-closed'

Assert-PythonSyntax $Hook
$invalidPythonFailedClosed = $false
try { Assert-PythonSyntax $Installer } catch { $invalidPythonFailedClosed = $true }
Assert-Equal $invalidPythonFailedClosed $true 'installer Python syntax preflight fail-closed'

Write-Output 'compute contract compatibility tests passed'
