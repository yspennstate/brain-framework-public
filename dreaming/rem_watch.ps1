# rem_watch.ps1 - the REM session's watchdog, counted in WORKING time (dot-sourced by run_dream.ps1).
#
# Why (2026-09-25): the watchdog counted 45 minutes from LAUNCH. On a loaded laptop a headless session's start-up
# hooks can take most of that before the model says a word - the 20:21 REM retry spent 27 minutes in its SessionStart
# wake and prompt hooks, reached its first turn at 20:48 and was killed at 21:06 after three turns. The same wake took
# under a minute at 19:34. Start-up time is the machine's, not the dream's; the budget should be the dream's.
#
# Wait-RemWorkingTime returns $true when the session exits by itself and $false when it must be killed:
#   - no first model turn by Launch + BirthMin: dead at birth (the 09-15 case), so the runner can retry. run_dream.ps1
#     passes BirthMin = WorkMin = 45: the hook caps alone (three SessionStart and two prompt hooks, 600 s each) can pass
#     35 minutes, so an earlier cut could kill a live session, and 45 is when the launch clock killed it anyway.
#   - otherwise at FirstTurn + WorkMin, and never after Launch + CapMin (the runner passes 2 x WorkMin).
# The first turn is read from THIS attempt's transcript, found by a mark unique to its prompt (the hard-cap stamp).
# ASCII only (wake-path discipline).

function Find-RemTranscript {
    param([datetime]$Launch, [string]$Mark, [string]$TranscriptDir)
    foreach ($f in @(Get-ChildItem -LiteralPath $TranscriptDir -Filter '*.jsonl' -File -ErrorAction SilentlyContinue |
                     Where-Object { $_.CreationTime -ge $Launch.AddSeconds(-5) })) {
        $head = Get-Content -LiteralPath $f.FullName -TotalCount 40 -Encoding UTF8 -ErrorAction SilentlyContinue
        if (($head -join "`n").Contains($Mark)) { return $f.FullName }
    }
    return $null
}

function Wait-RemWorkingTime {
    param(
        [Parameter(Mandatory = $true)] $Process,
        [Parameter(Mandatory = $true)] [datetime]$Launch,
        [Parameter(Mandatory = $true)] [double]$WorkMin,
        [Parameter(Mandatory = $true)] [string]$Mark,
        [double]$BirthMin = 45,
        [double]$CapMin = 90,
        [string]$TranscriptDir = (Join-Path $env:USERPROFILE '.claude\projects\{{BRAIN_SLUG}}'),
        [double]$PollSec = 15,
        [scriptblock]$OnFirstTurn = $null
    )
    $firstTurn = $null
    $tf = $null
    $cap = $Launch.AddMinutes($CapMin)
    while ($true) {
        if ($Process.WaitForExit([int]($PollSec * 1000))) { return $true }
        $now = Get-Date
        if (-not $firstTurn) {
            try {
                if (-not $tf) { $tf = Find-RemTranscript -Launch $Launch -Mark $Mark -TranscriptDir $TranscriptDir }
                if ($tf -and (Select-String -LiteralPath $tf -Pattern '"type":"assistant"' -SimpleMatch -Quiet -ErrorAction SilentlyContinue)) {
                    $firstTurn = $now
                    if ($OnFirstTurn) { & $OnFirstTurn $firstTurn }
                }
            } catch {}
        }
        $limit = if ($firstTurn) { $firstTurn.AddMinutes($WorkMin) } else { $Launch.AddMinutes($BirthMin) }
        if ($limit -gt $cap) { $limit = $cap }
        if ($now -ge $limit) { return $false }
    }
}
