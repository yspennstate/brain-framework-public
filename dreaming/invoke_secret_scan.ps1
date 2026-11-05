# Identity-safe native invocation for the nightly secret gate.
# A PowerShell command-start failure can leave $LASTEXITCODE at its prior value;
# seed it with a value the scanner never returns and accept only 0, 1, or 2.
function Invoke-SecretScan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [string] $ScannerPath,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [string] $PythonCommand = 'python'
    )

    $sentinel = 197
    $global:LASTEXITCODE = $sentinel
    $output = @('SECRET SCAN ERROR: scanner process did not return a trusted exit')
    try {
        $candidateOutput = @(& $PythonCommand $ScannerPath @Arguments 2>&1)
        $candidateCode = $global:LASTEXITCODE
        if ($candidateCode -eq 0) {
            $lines = @($candidateOutput | ForEach-Object { "$_".Trim() })
            if ($lines.Count -eq 1 -and $lines[0] -ceq 'secret scan clean') {
                $output = $candidateOutput
                $code = 0
            } else {
                # Say which untrusted shape it was, never what it printed.
                $output = @("SECRET SCAN ERROR: scanner exited 0 but printed $($lines.Count) line(s) instead of the one clean line")
                $code = 2
            }
        } elseif ($candidateCode -in @(1, 2)) {
            $output = $candidateOutput
            $code = [int] $candidateCode
        } else {
            $output = @("SECRET SCAN ERROR: scanner exited $candidateCode, which is not 0, 1 or 2")
            $code = 2
        }
    } catch {
        # Do not copy exception text into logs: command and argument diagnostics
        # can contain the credential-shaped value the gate is meant to contain.
        $output = @('SECRET SCAN ERROR: the scanner process could not be started')
        $code = 2
    }
    [pscustomobject]@{
        Code = $code
        Output = @($output)
    }
}

# 2026-09-25: exit 2 says the scan did not finish, not why, and the dream logged only the code, so four refusals in a
# row (09-23 19:30 to 09-24 19:30) read the same. The scanner's refusal lines are fixed text plus at most an exception
# type, an errno or a short object id - never scanned content - so they are safe in an unattended log. A finding is
# printed without this prefix and is never returned here.
function Get-SecretScanRefusalLines {
    [CmdletBinding()]
    param([object[]] $Output)
    $lines = @()
    foreach ($item in @($Output)) {
        $text = "$item".Trim()
        if (-not $text.StartsWith('SECRET SCAN ERROR: ')) { continue }
        $clean = $text -replace '[^\x20-\x7E]', '?'
        if ($clean.Length -gt 160) { $clean = $clean.Substring(0, 160) }
        if ($lines -notcontains $clean) { $lines += $clean }
        if ($lines.Count -ge 4) { break }
    }
    return $lines
}

# True only when every line the scanner printed is a refusal caused by another git process in the same checkout. The
# scanner fingerprints the index and HEAD (size, mtime, sha256) and re-checks them before each git call and at the
# end, so a peer's status or diff refresh, add or commit in the shared checkout during a four-minute scan refuses it.
# Reproduced 2026-09-25 on an isolated clone: an identical-bytes rewrite of the index 23 s into the scan ended it with
# exit 2 and "Git repository identity changed during the scan". Any other refusal, and any finding, returns false.
function Test-SecretScanRaceRefusal {
    [CmdletBinding()]
    param([object[]] $Output)
    $exact = @(
        'SECRET SCAN ERROR: Git repository identity changed during the scan',
        'SECRET SCAN ERROR: could not bind the staged comparison commit',
        'SECRET SCAN ERROR: staged comparison commit changed during the scan',
        'SECRET SCAN ERROR: could not complete Git operation (TimeoutExpired)',
        'SECRET SCAN ERROR: private index snapshot identity changed during the scan',
        'SECRET SCAN ERROR: split-index control identity changed during the scan'
    )
    # 2026-10-14: the 13 Oct 19:30 dream's persist died on "could not establish a private index snapshot (OSError)"
    # after one attempt. The scanner copies the shared 57 MB index under identity checks, and a peer's commit in the
    # middle of that copy raises OSError; three direct --staged runs at 07:0x on 14 Oct were clean in 6-20 s. It is
    # the same race as the lines above, so it gets the same retry. A finding is exit 1 and is never retried.
    $prefixes = @(
        'SECRET SCAN ERROR: could not capture Git repository identity (',
        'SECRET SCAN ERROR: could not recheck Git repository identity (',
        'SECRET SCAN ERROR: could not establish a private index snapshot ('
    )
    $refusals = 0
    foreach ($item in @($Output)) {
        $text = "$item".Trim()
        if (-not $text) { continue }
        $known = $exact -ccontains $text
        if (-not $known) {
            foreach ($p in $prefixes) { if ($text.StartsWith($p, [System.StringComparison]::Ordinal)) { $known = $true; break } }
        }
        if (-not $known) { return $false }
        $refusals++
    }
    return ($refusals -gt 0)
}

# Invoke-SecretScan, retried when (and only when) the refusal was a concurrent git write. Exit 0 and exit 1 return at
# once; so does any other refusal. $BeforeRetry re-stages for a staged scan (a peer's reset may have unstaged the
# paths); a range scan reads immutable objects and needs none. The last attempt's result is the verdict.
function Invoke-SecretScanRetryingRaces {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [string] $ScannerPath,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [int] $Attempts = 3,
        [int] $PauseSeconds = 30,
        [scriptblock] $BeforeRetry = $null,
        [scriptblock] $Logger = $null,
        [string] $Label = 'secret scan',
        [string] $PythonCommand = 'python'
    )
    $attempt = 0
    while ($true) {
        $attempt++
        $code = 2
        $out = @('SECRET SCAN ERROR: scanner process did not return a trusted exit')
        try {
            $r = Invoke-SecretScan -ScannerPath $ScannerPath -Arguments $Arguments -PythonCommand $PythonCommand
            if ($null -ne $r -and $r.PSObject.Properties['Code'] -and $r.Code -in @(0, 1, 2)) {
                $code = [int] $r.Code
                $out = @($r.Output)
            }
        } catch {}
        $race = $false
        if ($code -eq 2) { try { $race = Test-SecretScanRaceRefusal -Output $out } catch { $race = $false } }
        if ($code -ne 2 -or -not $race -or $attempt -ge $Attempts) {
            return [pscustomobject]@{ Code = $code; Output = @($out); Attempts = $attempt; LastWasRace = $race }
        }
        if ($null -ne $Logger) {
            try { & $Logger ("$Label attempt $attempt refused by a concurrent git write in the checkout (" + ((Get-SecretScanRefusalLines -Output $out) -join ' | ') + "); retrying in $PauseSeconds s") } catch {}
        }
        if ($PauseSeconds -gt 0) { Start-Sleep -Seconds $PauseSeconds }
        if ($null -ne $BeforeRetry) { try { & $BeforeRetry } catch {} }
    }
}

# Read the server-advertised main OID rather than trusting a mutable/stale local
# origin/main tracking ref. Null is a hard block, including command-start failure.
function Get-AdvertisedPushMain {
    [CmdletBinding()]
    param(
        [string] $Remote = 'origin',
        [string] $GitCommand = 'git'
    )

    $sentinel = 198
    $global:LASTEXITCODE = $sentinel
    try {
        $pushUrls = @(& $GitCommand remote get-url --push --all $Remote 2>$null)
        $candidateCode = $global:LASTEXITCODE
        if ($candidateCode -ne 0 -or $pushUrls.Count -ne 1) { return $null }
        $pushUrl = "$($pushUrls[0])".Trim()
        if (-not $pushUrl) { return $null }

        $global:LASTEXITCODE = $sentinel
        $lines = @(& $GitCommand ls-remote --exit-code --refs -- $pushUrl refs/heads/main 2>$null)
        $candidateCode = $global:LASTEXITCODE
        if ($candidateCode -ne 0) { return $null }
        $matches = @($lines | ForEach-Object { "$_".Trim() } | Where-Object {
            $_ -match '^(?:[0-9a-f]{40}|[0-9a-f]{64})\s+refs/heads/main$'
        })
        if ($matches.Count -ne 1) { return $null }
        [pscustomobject]@{
            Oid = ($matches[0] -split '\s+', 2)[0]
            PushUrl = $pushUrl
        }
    } catch {
        return $null
    }
}
