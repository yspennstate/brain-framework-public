$ErrorActionPreference = 'Stop'

$computeDir = Split-Path -Parent $PSScriptRoot
$governor = Join-Path $computeDir 'compute_governor.ps1'
$installer = Join-Path $computeDir 'install_compute_governor.ps1'
. $governor -LibraryMode
. $installer -LibraryOnly

$script:passed = 0

function Assert-Equal {
    param($Actual, $Expected, [string]$Name)
    if ($Actual -ne $Expected) {
        throw "$Name expected '$Expected' but got '$Actual'."
    }
    $script:passed++
}

function Assert-True {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) {
        throw "$Name failed."
    }
    $script:passed++
}

function Assert-Reason {
    param($Signals, [string]$Reason, [string]$Name)
    Assert-True -Condition (@($Signals.Reasons) -contains $Reason) -Name $Name
}

$healthy = Get-LocalMemorySignals -PhysicalAvailableMB 32768 `
    -CommitUsedPct 84.9 -CommitAvailableMB 16385
Assert-Equal $healthy.Severity 'ok' 'healthy severity'
Assert-Equal $healthy.Throttle $false 'healthy throttle'
Assert-Equal @($healthy.Reasons).Count 0 'healthy reasons'

$commitPctWarn = Get-LocalMemorySignals -PhysicalAvailableMB 32768 `
    -CommitUsedPct 85.0 -CommitAvailableMB 20000
Assert-Equal $commitPctWarn.Severity 'warn' 'commit percent warning severity'
Assert-Equal $commitPctWarn.Throttle $true 'commit percent warning throttle'
Assert-Reason $commitPctWarn 'memory_commit_pressure' 'commit percent warning reason'

$commitHeadroomWarn = Get-LocalMemorySignals -PhysicalAvailableMB 32768 `
    -CommitUsedPct 80.0 -CommitAvailableMB 16384
Assert-Equal $commitHeadroomWarn.Severity 'warn' 'commit headroom warning severity'
Assert-Reason $commitHeadroomWarn 'memory_commit_pressure' `
    'commit headroom warning reason'

$commitPctCritical = Get-LocalMemorySignals -PhysicalAvailableMB 32768 `
    -CommitUsedPct 90.0 -CommitAvailableMB 20000
Assert-Equal $commitPctCritical.Severity 'distress' `
    'commit percent critical severity'
Assert-Equal $commitPctCritical.Throttle $true 'commit percent critical throttle'
Assert-Reason $commitPctCritical 'memory_commit_critical' `
    'commit percent critical reason'

$commitHeadroomCritical = Get-LocalMemorySignals -PhysicalAvailableMB 32768 `
    -CommitUsedPct 80.0 -CommitAvailableMB 8192
Assert-Equal $commitHeadroomCritical.Severity 'distress' `
    'commit headroom critical severity'
Assert-Reason $commitHeadroomCritical 'memory_commit_critical' `
    'commit headroom critical reason'

$physicalWarn = Get-LocalMemorySignals -PhysicalAvailableMB 8192 `
    -CommitUsedPct 50.0 -CommitAvailableMB 30000
Assert-Equal $physicalWarn.Severity 'warn' 'physical warning severity'
Assert-Reason $physicalWarn 'memory_physical_pressure' 'physical warning reason'

$physicalCritical = Get-LocalMemorySignals -PhysicalAvailableMB 4096 `
    -CommitUsedPct 50.0 -CommitAvailableMB 30000
Assert-Equal $physicalCritical.Severity 'distress' 'physical critical severity'
Assert-Reason $physicalCritical 'memory_physical_critical' `
    'physical critical reason'

$degraded = Get-LocalMemorySignals -PhysicalAvailableMB 32768 `
    -CommitUsedPct -1 -CommitAvailableMB -1
Assert-Equal $degraded.Severity 'warn' 'degraded telemetry severity'
Assert-Equal $degraded.Throttle $true 'degraded telemetry throttle'
Assert-Reason $degraded 'memory_telemetry_degraded' 'degraded telemetry reason'

$resolvedWarn = Resolve-LocalHealth -BaseReasons @() -MemorySignals $commitPctWarn `
    -HighCycles 0 -RequiredHighCycles 3 -CpuPct 10 -CpuWarnPct 88
Assert-Equal $resolvedWarn.Health 'warn' 'memory warning resolves to warning health'
Assert-Reason $resolvedWarn 'memory_commit_pressure' `
    'memory warning survives resolved state reasons'

$resolvedCritical = Resolve-LocalHealth -BaseReasons @() `
    -MemorySignals $commitPctCritical -HighCycles 0 -RequiredHighCycles 3 `
    -CpuPct 10 -CpuWarnPct 88
Assert-Equal $resolvedCritical.Health 'distress' `
    'memory critical resolves to distress health'
Assert-Reason $resolvedCritical 'memory_commit_critical' `
    'memory critical survives resolved state reasons'

$healthyWithUiFreeze = Resolve-LocalHealth -BaseReasons @('ui_freeze') `
    -MemorySignals $healthy -HighCycles 0 -RequiredHighCycles 3 `
    -CpuPct 10 -CpuWarnPct 88
Assert-Equal $healthyWithUiFreeze.Health 'distress' `
    'non-memory distress authority remains intact'

Assert-Equal (Resolve-LocalMode -MemorySignals $commitPctWarn `
    -GlobalThrottle $false -OwnerAway $true) 'throttle' `
    'memory warning outranks owner-away open mode'
Assert-Equal (Resolve-LocalMode -MemorySignals $healthy `
    -GlobalThrottle $false -OwnerAway $true) 'owner_away_open' `
    'healthy owner-away mode remains open'
Assert-Equal (Resolve-LocalMode -MemorySignals $healthy `
    -GlobalThrottle $true -OwnerAway $true) 'throttle' `
    'global throttle outranks owner-away mode'

$taggedMemory = Get-AiMemorySummary -ProcessSample @(
    [pscustomobject]@{
        ProcessName = 'claude'; PrivateMemorySize64 = 650MB; WorkingSet64 = 300MB
    }
    [pscustomobject]@{
        ProcessName = 'claude'; PrivateMemorySize64 = 850MB; WorkingSet64 = 350MB
    }
    [pscustomobject]@{
        ProcessName = 'codex'; PrivateMemorySize64 = 400MB; WorkingSet64 = 150MB
    }
    [pscustomobject]@{
        ProcessName = 'chrome'; PrivateMemorySize64 = 2000MB; WorkingSet64 = 1000MB
    }
) -AiProcessNames @('claude','codex')
Assert-Equal $taggedMemory.ProcessCount 3 `
    'aggregate AI telemetry counts every tagged process'
Assert-Equal $taggedMemory.PrivateMB 1900 `
    'aggregate AI telemetry sums sub-gigabyte private commit'
Assert-Equal $taggedMemory.WorkingMB 800 `
    'aggregate AI telemetry preserves working-set context'

$emptyTaggedMemory = Get-AiMemorySummary -ProcessSample @(
    [pscustomobject]@{
        ProcessName = 'chrome'; PrivateMemorySize64 = 100MB; WorkingSet64 = 50MB
    }
) -AiProcessNames @('claude','codex')
Assert-Equal $emptyTaggedMemory.ProcessCount 0 `
    'aggregate AI telemetry excludes untagged processes'
Assert-Equal $emptyTaggedMemory.PrivateMB 0 `
    'aggregate AI telemetry returns zero for an empty match'

$canonicalCommand = (
    '"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" ' +
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    $governor + '"'
)
$canonicalProcess = [pscustomobject]@{
    Name = 'powershell.exe'
    CommandLine = $canonicalCommand
}
Assert-True (Test-CanonicalGovernorProcess $canonicalProcess $governor) `
    'installer accepts exact hidden-launcher governor identity'
$diagnosticSubstring = [pscustomobject]@{
    Name = 'powershell.exe'
    CommandLine = (
        '"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" ' +
        '-Command "Get-Content -Raw ''' + $governor + '''"'
    )
}
Assert-True (-not (Test-CanonicalGovernorProcess $diagnosticSubstring $governor)) `
    'installer rejects diagnostic command containing canonical path'
$extraArgument = [pscustomobject]@{
    Name = 'powershell.exe'
    CommandLine = $canonicalCommand + ' -Status'
}
Assert-True (-not (Test-CanonicalGovernorProcess $extraArgument $governor)) `
    'installer rejects extra-argument lookalike'
$wrongExecutable = [pscustomobject]@{
    Name = 'pwsh.exe'
    CommandLine = $canonicalCommand
}
Assert-True (-not (Test-CanonicalGovernorProcess $wrongExecutable $governor)) `
    'installer rejects wrong executable lookalike'

$native = [CG.Native]::MemoryMB()
Assert-Equal @($native).Count 4 'native memory sample field count'
Assert-True ($native[0] -gt 0) 'native physical availability positive'
Assert-True ($native[2] -gt 0) 'native commit limit positive'
Assert-True ($native[3] -le $native[2]) 'native commit headroom bounded'

$flags = [Reflection.BindingFlags]'NonPublic,Static'
$nativeCommitMethod = [CG.Native].GetMethod('GetPerformanceInfo', $flags)
Assert-True ($null -ne $nativeCommitMethod) `
    'native sampler imports system-wide GetPerformanceInfo'
$dll = @($nativeCommitMethod.GetCustomAttributes(
    [Runtime.InteropServices.DllImportAttribute], $false))[0]
Assert-Equal $dll.Value 'psapi.dll' 'system-wide commit API library'

$os = Get-CimInstance Win32_OperatingSystem
$wmiCommitLimitMB = [math]::Round($os.TotalVirtualMemorySize / 1024.0, 0)
$wmiCommitAvailableMB = [math]::Round($os.FreeVirtualMemory / 1024.0, 0)
Assert-True ([math]::Abs($native[2] - $wmiCommitLimitMB) -le 512) `
    'native system commit limit agrees with independent WMI sample'
Assert-True ([math]::Abs($native[3] - $wmiCommitAvailableMB) -le 512) `
    'native system commit headroom agrees with independent WMI sample'

$roundTripSource = [ordered]@{
    status = $resolvedCritical.Health
    mode = Resolve-LocalMode -MemorySignals $commitPctCritical `
        -GlobalThrottle $false -OwnerAway $true
    reasons = @($resolvedCritical.Reasons)
    mem_avail_mb = 32768
    commit_used_mb = 90000
    commit_limit_mb = 100000
    commit_avail_mb = 10000
    commit_used_pct = 90.0
    ai_tagged_process_count = 3
    ai_tagged_private_mb = 1900.0
    ai_tagged_working_mb = 800.0
    top_private = @(
        [ordered]@{
            pid = 23616; name = 'WindowsTerminal'
            private_mb = 37434.0; working_mb = 7091.8
        }
    )
}
$roundTrip = $roundTripSource | ConvertTo-Json -Depth 5 | ConvertFrom-Json
Assert-Equal $roundTrip.status 'distress' 'JSON state preserves critical health'
Assert-Equal $roundTrip.mode 'throttle' 'JSON state preserves memory throttle'
Assert-Reason $roundTrip 'memory_commit_critical' `
    'JSON state preserves critical reason'
Assert-Equal $roundTrip.commit_used_pct 90 `
    'JSON state preserves numeric commit percent'
Assert-Equal $roundTrip.ai_tagged_process_count 3 `
    'JSON state preserves aggregate AI process count'
Assert-Equal $roundTrip.ai_tagged_private_mb 1900 `
    'JSON state preserves aggregate AI private commit'
Assert-Equal $roundTrip.top_private[0].name 'WindowsTerminal' `
    'JSON state preserves largest private-commit offender'

$source = Get-Content -LiteralPath $governor -Raw -Encoding UTF8
$tokens = $null
$parseErrors = $null
$sourceAst = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$parseErrors
)
Assert-Equal @($parseErrors).Count 0 'governor source parses in integration gate'
$commandNames = @(
    $sourceAst.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() }
)
Assert-True ($commandNames -contains 'Resolve-LocalHealth') `
    'live loop invokes behavioral health resolver'
Assert-True ($commandNames -contains 'Resolve-LocalMode') `
    'live loop invokes behavioral mode resolver'
Assert-True ($commandNames -contains 'Get-AiMemorySummary') `
    'live loop publishes aggregate AI memory telemetry'

$mutatedSource = $source.Replace(
    'Resolve-LocalHealth -BaseReasons $baseReasons',
    'Resolve-RemovedHealth -BaseReasons $baseReasons'
)
$mutatedTokens = $null
$mutatedErrors = $null
$mutatedAst = [System.Management.Automation.Language.Parser]::ParseInput(
    $mutatedSource, [ref]$mutatedTokens, [ref]$mutatedErrors
)
$mutatedCommands = @(
    $mutatedAst.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() }
)
Assert-True ($mutatedCommands -notcontains 'Resolve-LocalHealth') `
    'negative mutation control detects removal of live health integration'

"PASS: $script:passed local commit-pressure assertions"
