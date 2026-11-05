$ErrorActionPreference = 'Stop'

$computeDir = Split-Path -Parent $PSScriptRoot
$governor = Join-Path $computeDir 'compute_governor.ps1'
. $governor -LibraryMode

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
    if (-not $Condition) { throw "$Name failed." }
    $script:passed++
}

$live = [System.Diagnostics.Process]::GetCurrentProcess()
$racedAway = [System.Diagnostics.Process]::new()
$exitInfo = [System.Diagnostics.ProcessStartInfo]::new()
$exitInfo.FileName = $env:ComSpec
$exitInfo.Arguments = '/d /c exit 0'
$exitInfo.UseShellExecute = $false
$exitInfo.CreateNoWindow = $true
$exited = [System.Diagnostics.Process]::Start($exitInfo)
$exited.WaitForExit()

$filtered = @(
    Get-LiveProcessSample -ProcessSample @(
        $live, $exited, $null, $racedAway
    )
)
Assert-Equal $filtered.Count 1 'only live process objects survive'
Assert-True ($filtered -contains $live) 'live process survives'
Assert-True ($filtered -notcontains $exited) 'exited process is excluded'
Assert-True ($filtered -notcontains $racedAway) 'raced-away process is excluded'
$exited.Dispose()
$racedAway.Dispose()

$raw = @(Get-Process -ErrorAction SilentlyContinue)
$liveSample = @(Get-LiveProcessSample -ProcessSample $raw)
Assert-True ($liveSample.Count -le $raw.Count) 'live sample is bounded by raw sample'

$source = Get-Content -LiteralPath $governor -Raw -Encoding UTF8
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$parseErrors
)
Assert-Equal @($parseErrors).Count 0 'governor source parses'
$commands = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() }
)
Assert-True ($commands -contains 'Get-LiveProcessSample') `
    'live loop invokes exited-process filter'

$mutated = $source.Replace(
    'Get-LiveProcessSample -ProcessSample @(',
    'Get-UnfilteredProcessSample -ProcessSample @('
)
$mutatedTokens = $null
$mutatedErrors = $null
$mutatedAst = [System.Management.Automation.Language.Parser]::ParseInput(
    $mutated, [ref]$mutatedTokens, [ref]$mutatedErrors
)
$mutatedCommands = @(
    $mutatedAst.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() }
)
Assert-True ($mutatedCommands -notcontains 'Get-LiveProcessSample') `
    'negative mutation control detects removal of live-loop filter'

"PASS: $script:passed live-process-filter assertions"
