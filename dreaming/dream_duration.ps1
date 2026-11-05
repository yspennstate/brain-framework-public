# One-shot owner-requested dream duration. Called only after the existing dream lease.
function Read-DreamDurationRequest {
    param([string]$Base, [string]$Stamp, [datetime]$NowUtc = [datetime]::UtcNow)
    $path = Join-Path $Base 'DREAM_REQUEST_NEXT.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $request = Get-Content -LiteralPath $path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($request.schema -ne 'dream-duration-v1') { throw 'Unknown dream duration request schema' }
    if ($request.duration_minutes -isnot [int] -or $request.duration_minutes -lt 1 -or $request.duration_minutes -gt 240) {
        throw 'Dream duration must be an integer from 1 to 240 minutes'
    }
    if ($request.no_subagents -isnot [bool] -or -not $request.no_subagents) { throw 'Owner duration requests require no_subagents=true' }
    $expires = [datetimeoffset]::Parse([string]$request.expires_utc).UtcDateTime
    if ($expires -le $NowUtc) { throw 'Dream duration request expired; it was not consumed' }
    if ([string]::IsNullOrWhiteSpace([string]$request.focus)) { throw 'Dream duration request needs a focus' }
    # File.Move does not replace an earlier receipt; malformed/expired requests stay for review.
    $receipt = Join-Path (Join-Path $Base 'logs') ('dream_' + $Stamp + '.request.json')
    [IO.File]::Move([IO.Path]::GetFullPath($path), [IO.Path]::GetFullPath($receipt))
    return $request
}

function Get-DreamDurationPrompt {
    param([object]$Request, [datetime]$WorkDeadline)
    if ($null -eq $Request) { return @() }
    return @(
        '## This one run: explicit owner duration and solo work',
        ('Work continuously on useful dream consolidation until ' + $WorkDeadline.ToString('o') + ' local time.'),
        'Finishing one topic is not the end. Verify the learning, test recall, revisit contrary evidence, then take another in-scope topic.',
        'Write journal checkpoints as you go. In the final twelve minutes, finish the journal, reflection and evidence checks; return only after the work deadline.',
        'No subagents, teams, delegated agents, or nested model/CLI workers. Do the reasoning and writing yourself.',
        'Owner scope: ' + [string]$Request.focus,
        ''
    )
}
