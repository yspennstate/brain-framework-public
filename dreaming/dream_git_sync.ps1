# dream_git_sync.ps1 - bring the brain repo's local main up to the advertised push main before the nightly
# persist commits, so the exact-SHA push cannot be rejected as a non-fast-forward. Dot-sourced by run_dream.ps1.
# Pure ASCII (ascii_lint). Never force-pushes, never autostashes, never rewrites what is already on the remote.
#
# Why (2026-09-20): every dream from 09-19 19:30 to 09-20 11:30 logged pushed=False and its three consolidation
# commits stayed local while origin/main moved 28 commits ahead through waking sessions' worktree pushes; the
# push's stderr went to Out-Null, so the log never said why, and the brain on GitHub was a day stale.
#
# Sync-LocalMainToAdvertised -Repo <path> -PushUrl <url> -Oid <sha> -MemoryDir <live memory dir> -Log <scriptblock>
#   $true  : local HEAD contains the advertised main (nothing to do, fast-forwarded, or rebased cleanly)
#   $false : could not bring main up; the caller logs and keeps tonight's commit LOCAL ONLY (hook_liveness reports it)
# The rebase runs in a DETACHED TEMPORARY WORKTREE, so the shared checkout is never left mid-rebase. A conflict
# under the memory mirror is resolved with the LIVE memory file (the mirror's truth); any other conflict aborts.
# The checkout then moves with `git reset --keep`, which refuses to touch a locally modified tracked file, so a
# waking session's uncommitted edit is never overwritten. Untracked files that are byte-identical to the incoming
# tracked version (a session pushed from a worktree and left its copy here) are removed first so the move can land.
# Put parked copies back one at a time; one failure never stops the rest (2026-09-24: a top-level file has no parent
# directory, and the unguarded New-Item threw out of the function after the copy had been removed). Returns the paths
# that could not be put back, whose bytes then stay in the park directory.
function Restore-ParkedCopies {
    param([string[]] $Paths, [string] $ParkDir)
    $failed = @()
    foreach ($p in $Paths) {
        try {
            $parent = Split-Path -Parent $p
            if ($parent) { New-Item -ItemType Directory -Force -Path $parent -ErrorAction Stop | Out-Null }
            Copy-Item -LiteralPath (Join-Path $ParkDir $p) -Destination $p -Force -ErrorAction Stop
        } catch { $failed += $p }
    }
    return $failed
}

function Get-SyncFacts {
    # 2026-09-25: one helper call (dream_sync_facts.py, four git calls in all) answers for every incoming path whether it
    # exists here, whether it is tracked, its local blob id and its blob id in the target. The loops below used to start
    # two or three git processes per path: the 03:30 dream of 09-25 spent 34 minutes on 1,141 incoming paths, and live
    # memory files moved on in that window. Returns $null on any failure, and the loops then make their per-file calls
    # exactly as before; a path missing from the answer is handled the same way.
    param([string] $Repo, [string[]] $Paths, [string] $Target)
    $map = New-Object System.Collections.Hashtable ([StringComparer]::Ordinal)
    if ($Paths.Count -eq 0) { return $map }
    $dir = $PSScriptRoot
    if (-not $dir) { $dir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\dreaming' }
    $helper = Join-Path $dir 'dream_sync_facts.py'
    if (-not (Test-Path -LiteralPath $helper)) { return $null }
    $stem = Join-Path ([System.IO.Path]::GetTempPath()) ("dream_sync_facts_" + $PID + "_" + (Get-Date -Format 'HHmmssfff'))
    $inFile = $stem + '.in'
    $outFile = $stem + '.tsv'
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($inFile, [string[]]$Paths, $utf8)
        $null = & python -s $helper $Repo $Target $inFile $outFile 2>$null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outFile)) { return $null }
        foreach ($line in [System.IO.File]::ReadAllLines($outFile, $utf8)) {
            $c = $line.Split("`t")
            if ($c.Count -ne 5) { return $null }
            $map[$c[0]] = [pscustomobject]@{ Exists = ($c[1] -eq '1'); Tracked = ($c[2] -eq '1'); Local = $c[3]; Incoming = $c[4] }
        }
        return $map
    } catch {
        return $null
    } finally {
        Remove-Item -LiteralPath $inFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue
    }
}

function Set-IndexToTarget {
    # 2026-09-25: set the index entry of each listed path to the target's own entry (dream_sync_index.py stage) so that
    # `git reset --keep` keeps the file where it is. The sync used to delete untracked copies of incoming files and park
    # edited ones for the move, and a job reading them in those minutes failed (05:3x 25 Sep, memory
    # the_dreams_git_sync_empties_pushed_campaign_files_for_minutes_2026_09_25). Returns @{ Staged = set of paths staged;
    # Undo = file for Undo-IndexToTarget }, or $null when the helper is missing or fails - then nothing was changed and the
    # caller handles every path the old way. A path the helper leaves out (a deletion, a submodule) gets the old way too.
    param([string] $Repo, [string] $Old, [string] $Target, [string[]] $Paths)
    $staged = New-Object System.Collections.Hashtable ([StringComparer]::Ordinal)
    if ($Paths.Count -eq 0) { return [pscustomobject]@{ Staged = $staged; Undo = '' } }
    $dir = $PSScriptRoot
    if (-not $dir) { $dir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\dreaming' }
    $helper = Join-Path $dir 'dream_sync_index.py'
    if (-not (Test-Path -LiteralPath $helper)) { return $null }
    $stem = Join-Path ([System.IO.Path]::GetTempPath()) ("dream_sync_index_" + $PID + "_" + (Get-Date -Format 'HHmmssfff'))
    $inFile = $stem + '.in'
    $outFile = $stem + '.staged'
    $undoFile = $stem + '.undo'
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($inFile, [string[]]$Paths, $utf8)
        $null = & python -s $helper stage $Repo $Old $Target $inFile $outFile $undoFile 2>$null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outFile)) {
            # a failure after the index was written still leaves an undo file; put the index back before falling back
            if (Test-Path -LiteralPath $undoFile) { $null = & python -s $helper undo $Repo $undoFile 2>$null }
            return $null
        }
        foreach ($line in [System.IO.File]::ReadAllLines($outFile, $utf8)) { if ($line -ne '') { $staged[$line] = $true } }
        return [pscustomobject]@{ Staged = $staged; Undo = $undoFile }
    } catch {
        return $null
    } finally {
        Remove-Item -LiteralPath $inFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue
    }
}

function Undo-IndexToTarget {
    # Puts back the index entries Set-IndexToTarget changed (the move was refused). Returns $true on success.
    param([string] $Repo, $Staging)
    if ($null -eq $Staging -or "$($Staging.Undo)" -eq '' -or $Staging.Staged.Count -eq 0) { return $true }
    $dir = $PSScriptRoot
    if (-not $dir) { $dir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\dreaming' }
    $null = & python -s (Join-Path $dir 'dream_sync_index.py') undo $Repo $Staging.Undo 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Remove-IndexUndoFile {
    param($Staging)
    if ($null -ne $Staging -and "$($Staging.Undo)" -ne '') { Remove-Item -LiteralPath $Staging.Undo -Force -ErrorAction SilentlyContinue }
}

function Push-WithResync {
    # 2026-09-25: push an exact, certified commit to main; when the push is refused as a non-fast-forward (main moved
    # after the certification's last check), bring the local commits onto the newly advertised main with
    # Sync-LocalMainToAdvertised, certify the new exact range with $Certify, and push that commit. Pushing the same commit
    # again cannot succeed: the 09-25 nap certified 9cc3fe9 at 13:5x, main moved within the minute, and all three plain
    # retries were refused, leaving the commit local. Returns @{ Pushed; Commit; Base }. $GetAdvertised returns an object
    # with Oid and PushUrl (or $null); $Certify takes a range 'base..commit' and returns $true only when it scans clean.
    param(
        [Parameter(Mandatory = $true)][string] $Repo,
        [Parameter(Mandatory = $true)][string] $PushUrl,
        [Parameter(Mandatory = $true)][string] $CommitSha,
        [Parameter(Mandatory = $true)][string] $BaseOid,
        [Parameter(Mandatory = $true)][scriptblock] $GetAdvertised,
        [Parameter(Mandatory = $true)][scriptblock] $Certify,
        [string] $MemoryDir = '',
        [int] $Attempts = 3,
        [int] $SleepSeconds = 20,
        [scriptblock] $Logger = { param($m) Write-Host $m }
    )
    # 2026-09-28 (account03/B06 r55, R51-B06-PUSH-WITH-RESYNC-UNLEASED-REWIND-38): every attempt is leased to the base the
    # range was certified against. Unleased, a remote rewound after the last check (main moved from A back to its parent
    # R) took B as a fast-forward and published A, which no scan had covered. A lease refused by a moved main goes through
    # the refresh, re-sync and re-certify below, and the retry is leased to the new base. A matching lease would also let
    # a non-fast-forward through, so the commit must descend from its base before anything is sent.
    # 2026-10-14: a commit that does not descend from its certified base now takes the same re-sync as a refused push.
    # run_dream.ps1 syncs onto the advertised main, then stages, scans and commits (several minutes), then reads the
    # advertised main again and certifies the range from THAT main. When a peer pushed in between, the commit sat on the
    # older main and the check below blocked it outright: eight October dreams (3, 4, 5 twice, 6, 10, 12 and 13 Oct)
    # logged "does not descend from its certified base" and left the night's commit local. The guard is unchanged: nothing
    # is sent until the re-synced commit descends from the base it is leased to and that exact range has certified clean.
    # With no attempt left the commit is still blocked as before (test_push_with_resync_lease.py, STALE arms).
    $sha = $CommitSha
    $baseOid = $BaseOid
    for ($try = 1; $try -le $Attempts; $try++) {
        $stale = $false
        $resync = $false
        & git -C $Repo merge-base --is-ancestor $baseOid $sha 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            if ($try -ge $Attempts) { & $Logger ("PUSH BLOCKED: " + $sha.Substring(0, 7) + " does not descend from its certified base " + $baseOid.Substring(0, 7)); break }
            & $Logger ("persist: " + $sha.Substring(0, 7) + " does not descend from its certified base " + $baseOid.Substring(0, 7) + " (main moved after the sync); re-syncing before any push")
            $stale = $true
            $resync = $true
        } else {
            $global:LASTEXITCODE = 199
            $out = @(& git -C $Repo -c push.followTags=false -c push.recurseSubmodules=no push --no-follow-tags --no-tags --recurse-submodules=no "--force-with-lease=refs/heads/main:$baseOid" -- $PushUrl "${sha}:refs/heads/main" 2>&1)
            $code = $global:LASTEXITCODE
            if ($code -eq 0) {
                $back = [string](@(& git -C $Repo ls-remote -- $PushUrl refs/heads/main 2>$null) | Select-Object -Last 1)
                if (-not $back.StartsWith($sha)) { & $Logger ("persist: pushed " + $sha.Substring(0, 7) + "; the remote main now reads " + $back) }
                return [pscustomobject]@{ Pushed = $true; Commit = $sha; Base = $baseOid }
            }
            $said = (($out | Select-Object -Last 2 | ForEach-Object { "$_" }) -join ' | ')
            & $Logger ("persist: push attempt $try exited $code; git said: " + $said)
            $all = (($out | ForEach-Object { "$_" }) -join ' ')
            $resync = ($try -lt $Attempts -and $all -match 'non-fast-forward|fetch first|stale info|\[rejected\]|failed to push some refs')
        }
        if ($resync) {
            $adv = & $GetAdvertised
            # A refused push re-syncs only onto a main that moved; a stale base re-syncs onto whatever main is advertised
            # now, which may be the certified base itself.
            $advOk = ($null -ne $adv -and "$($adv.Oid)" -match '^[0-9a-f]{40}$' -and "$($adv.PushUrl)" -ceq $PushUrl)
            if ($stale -and -not $advOk) { & $Logger "persist: the advertised main is unreadable or its endpoint changed; not re-syncing" }
            if ($advOk -and ($stale -or "$($adv.Oid)" -cne $baseOid)) {
                $ok = $false
                Push-Location $Repo
                try { $ok = Sync-LocalMainToAdvertised -Repo $Repo -PushUrl $PushUrl -Oid $adv.Oid -MemoryDir $MemoryDir -Logger $Logger } finally { Pop-Location }
                if (-not $ok) { & $Logger "persist: main moved and the re-sync failed; the commit stays local for the next cycle"; break }
                $newSha = [string](@(& git -C $Repo rev-parse HEAD 2>$null) | Select-Object -Last 1)
                if ($newSha -notmatch '^[0-9a-f]{40}$') { & $Logger "persist: HEAD unreadable after the re-sync"; break }
                & git -C $Repo merge-base --is-ancestor $adv.Oid $newSha 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0) { & $Logger "persist: the re-synced HEAD does not contain the advertised main; not pushing"; break }
                if ($newSha -ceq $adv.Oid) { & $Logger "persist: after the re-sync nothing is left to push (the changes are already on main)"; return [pscustomobject]@{ Pushed = $true; Commit = $newSha; Base = $adv.Oid } }
                # From here the local commit IS the re-synced one, pushed or not (the old one is off the branch).
                $sha = $newSha
                $baseOid = [string]$adv.Oid
                $certOut = @(& $Certify "$baseOid..$sha")
                if (-not ($certOut.Count -gt 0 -and $certOut[-1] -is [bool] -and $certOut[-1])) { & $Logger ("PUSH BLOCKED: the re-synced range did not certify clean; " + $sha.Substring(0, 7) + " stays local"); break }
                & $Logger ("persist: main moved to " + $baseOid.Substring(0, 7) + "; re-synced and re-certified " + $sha.Substring(0, 7) + ", pushing again")
                continue
            }
        }
        if ($try -lt $Attempts -and $SleepSeconds -gt 0) { Start-Sleep -Seconds $SleepSeconds }
    }
    return [pscustomobject]@{ Pushed = $false; Commit = $sha; Base = $baseOid }
}

function Sync-LocalMainToAdvertised {
    param(
        [Parameter(Mandatory = $true)][string] $Repo,
        [Parameter(Mandatory = $true)][string] $PushUrl,
        [Parameter(Mandatory = $true)][string] $Oid,
        [string] $MemoryDir = '',
        [string] $MirrorPrefix = '01_ai_consciousness/claude/memory/',
        [hashtable] $LiveMirrors = $null,
        [string] $MachineryPrefix = '12_cognitive_architecture/dreaming/',
        [string] $MachineryDir = '',
        [string] $MovieSentinelDir = '',
        # The paths the persist commit stages (run_dream.ps1 $ownedPaths). An edited file OUTSIDE them is carried across
        # the move at its local bytes; one inside them still blocks the move when it is neither safe nor a kept mirror.
        [string[]] $OwnedPrefixes = @('01_ai_consciousness/brain/', '01_ai_consciousness/claude/memory/'),
        [scriptblock] $Logger = { param($m) Write-Host $m }
    )
    if ($MachineryDir -eq '') { $MachineryDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\dreaming' }
    if ($MovieSentinelDir -eq '') { $MovieSentinelDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\movie_sentinel' }
    # Single-file mirrors of live runtime files (repo path -> live file), as manifests/build_tools/build_master.ps1 maps
    # them. The two instruction files are the mirrors that blocked the 09-23 moves.
    if ($null -eq $LiveMirrors) {
        $homeDir = [Environment]::GetFolderPath('UserProfile')
        $LiveMirrors = @{
            '01_ai_consciousness/claude/CLAUDE.md' = (Join-Path $homeDir '.claude\CLAUDE.md')
            '01_ai_consciousness/codex/AGENTS.md'  = (Join-Path $homeDir '.codex\AGENTS.md')
        }
    }
    # OTHER MIRRORS (2026-09-24): paths outside the memory corpus that copy a live file. A rebase conflict on one of them
    # is resolved only when the live file equals one side (ours or theirs), so no third version is ever written; the same
    # table goes to dream_tree_replay.py through DREAM_SYNC_MIRRORS. From 06:12 to 17:0x on 09-24 a conflict on the
    # movie_sentinel hook mirror held eight commits local (memory the_dreams_sync_refused_edited_copies_... fourth instance).
    $OtherMirrorDirs = @{}
    $OtherMirrorDirs[$MachineryPrefix] = $MachineryDir
    $OtherMirrorDirs['01_ai_consciousness/claude/movie_sentinel/'] = $MovieSentinelDir
    $mirrorJson = @{ prefixes = $OtherMirrorDirs; files = $LiveMirrors } | ConvertTo-Json -Compress -Depth 4
    $savedMirrorsEnv = $env:DREAM_SYNC_MIRRORS
    $env:DREAM_SYNC_MIRRORS = $mirrorJson
    if ($Oid -notmatch '^[0-9a-f]{40}$') { & $Logger "sync: advertised oid is not a sha ($Oid); skipping"; $env:DREAM_SYNC_MIRRORS = $savedMirrorsEnv; return $true }
    Push-Location $Repo
    try {
        & git merge-base --is-ancestor $Oid HEAD 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        & git cat-file -e "$Oid^{commit}" 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $fetchOut = @(& git fetch -q --no-tags -- $PushUrl "+refs/heads/main:refs/remotes/dream-sync/main" 2>&1)
            if ($LASTEXITCODE -ne 0) {
                & $Logger ("sync: fetch of the advertised main failed: " + (($fetchOut | ForEach-Object { "$_" }) -join ' | '))
                return $false
            }
            & git cat-file -e "$Oid^{commit}" 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { & $Logger "sync: advertised oid $Oid still absent after fetch"; return $false }
        }
        $ahead = [string](@(& git rev-list --count "$Oid..HEAD" 2>$null) | Select-Object -Last 1)
        $behind = [string](@(& git rev-list --count "HEAD..$Oid" 2>$null) | Select-Object -Last 1)
        $old = [string](@(& git rev-parse HEAD 2>$null) | Select-Object -Last 1)
        $target = $Oid
        $rounds = 0
        if ($ahead -ne '0') {
            $newHead = ''
            # FAST PATH (2026-09-23): replay the local commits with git merge-tree, from the object database alone - no
            # worktree and no checkout. The worktree below checks out every file of the repo (about 244,000); on this
            # disk-bound machine the 09-23 22:46 sync spent more than 15 minutes creating it to replay one commit.
            # dream_tree_replay.py exits 3 on any conflict, merge commit, signed or re-encoded commit, and the worktree
            # rebase below then runs exactly as before.
            $replayDir = $PSScriptRoot
            if (-not $replayDir) { $replayDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\dreaming' }
            $replay = Join-Path $replayDir 'dream_tree_replay.py'
            if (Test-Path -LiteralPath $replay) {
                # memory-mirror conflicts are resolved from the live memory file, as the worktree rebase below does
                # (2026-09-24). Windows PowerShell drops an empty native argument, so the pair is passed only when set.
                $trArgs = @($replay, $Repo, $Oid)
                if ($MemoryDir -ne '') { $trArgs += @($MemoryDir, $MirrorPrefix) }
                $trOut = @(& python -s @trArgs 2>&1)
                $trRc = $LASTEXITCODE
                $cand = [string]($trOut | Select-Object -Last 1)
                if ($trRc -eq 0 -and $cand -match '^[0-9a-f]{40}$') {
                    $newHead = $cand
                    $trNote = (($trOut | Select-Object -SkipLast 1 | ForEach-Object { "$_" }) -join ' | ')
                    if ($trNote -ne '') { $trNote = "; " + $trNote }
                    & $Logger "sync: replayed $ahead local commit(s) onto the advertised main with merge-tree (no worktree)$trNote"
                } else {
                    & $Logger ("sync: merge-tree replay declined (exit $trRc): " + $cand + "; using the worktree rebase")
                }
            }
          if ($newHead -eq '') {
            $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("dream_sync_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + "_" + $PID)
            & git worktree add --detach -q $tmp HEAD 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { & $Logger "sync: could not create the temporary worktree"; return $false }
            $savedEditor = $env:GIT_EDITOR
            try {
                $env:GIT_EDITOR = 'true'
                Push-Location $tmp
                try {
                    $rbOut = @(& git -c rebase.autoStash=false -c core.editor=true rebase $Oid 2>&1)
                    $rc = $LASTEXITCODE
                    while ($rc -ne 0 -and $rounds -lt 60) {
                        $rounds++
                        $conflicts = @(& git diff --name-only --diff-filter=U 2>$null | Where-Object { "$_" -ne '' })
                        if ($conflicts.Count -eq 0) {
                            # A stop with nothing conflicted may be skipped only when the replayed commit has nothing
                            # staged, i.e. it became empty against the advertised main. Any other stop - a missing
                            # committer identity, a hook, a lock - leaves the commit's changes staged, and --skip would
                            # drop the local commit without a word (2026-09-23: a fixture with no identity lost its only
                            # commit this way and the move then carried main past it). Abort and keep it local instead.
                            & git diff --cached --quiet 2>$null | Out-Null
                            if ($LASTEXITCODE -ne 0) {
                                & git rebase --abort 2>&1 | Out-Null
                                & $Logger ("sync: rebase stopped with no conflict but with changes staged, so it is not an empty commit; aborted rather than skipped, tonight's commit stays LOCAL ONLY: " + (($rbOut | Select-Object -Last 3 | ForEach-Object { "$_" }) -join ' | '))
                                return $false
                            }
                            $rbOut = @(& git -c core.editor=true rebase --skip 2>&1)
                            $rc = $LASTEXITCODE
                            continue
                        }
                        foreach ($p in $conflicts) {
                            $resolved = $false
                            $why = 'not a mirror path'
                            if ($MemoryDir -ne '' -and $p.StartsWith($MirrorPrefix)) {
                                $why = 'a memory mirror with no live file'
                                $live = Join-Path $MemoryDir ($p.Substring($MirrorPrefix.Length).Replace('/', '\'))
                                if (Test-Path -LiteralPath $live) {
                                    Copy-Item -LiteralPath $live -Destination $p -Force
                                    $resolved = $true
                                }
                            }
                            if (-not $resolved -and $p -notmatch '(^|/)\.\.(/|$)') {
                                # another mirror: take the live file only when it is byte-identical to one side
                                $liveOther = $null
                                if ($LiveMirrors.ContainsKey($p)) { $liveOther = $LiveMirrors[$p] }
                                else {
                                    foreach ($pre in $OtherMirrorDirs.Keys) {
                                        if ($p.StartsWith($pre)) { $liveOther = Join-Path $OtherMirrorDirs[$pre] ($p.Substring($pre.Length).Replace('/', '\')); break }
                                    }
                                }
                                if ($liveOther) { $why = 'a mirror with no live file' }
                                if ($liveOther -and (Test-Path -LiteralPath $liveOther)) {
                                    $why = 'a mirror whose live file matches neither side'
                                    $liveSha = [string](@(& git hash-object --path $p -- $liveOther 2>$null) | Select-Object -Last 1)
                                    $oursSha = [string](@(& git rev-parse -q --verify ":2:$p" 2>$null) | Select-Object -Last 1)
                                    $theirsSha = [string](@(& git rev-parse -q --verify ":3:$p" 2>$null) | Select-Object -Last 1)
                                    if ($liveSha -match '^[0-9a-f]{40}$' -and ($liveSha -eq $oursSha -or $liveSha -eq $theirsSha)) {
                                        Copy-Item -LiteralPath $liveOther -Destination $p -Force
                                        & $Logger "sync: resolved the mirror conflict on $p with its live file (equal to one side)"
                                        $resolved = $true
                                    }
                                }
                            }
                            # 2026-10-06, the same two rules as dream_tree_replay.py (fifth instance in memory
                            # the_dreams_sync_refused_edited_copies_and_could_skip_a_commit_2026_09_23): a dream view under
                            # 01_ai_consciousness/brain/ takes the replayed local commit's side (every dream rebuilds them),
                            # and any other path takes the base's side when the shared checkout's own copy already holds
                            # exactly those bytes. A copy equal to the local side, or to neither, still aborts.
                            if (-not $resolved -and $liveOther -eq $null -and $p.StartsWith('01_ai_consciousness/brain/') -and $p -notmatch '(^|/)\.\.(/|$)') {
                                & git checkout --theirs -- $p 2>&1 | Out-Null
                                if ($LASTEXITCODE -eq 0) {
                                    & $Logger "sync: resolved the generated-view conflict on $p with the local commit's regeneration"
                                    $resolved = $true
                                } else { $why = 'a generated view the local commit deleted' }
                            }
                            if (-not $resolved -and $liveOther -eq $null -and $p -notmatch '(^|/)\.\.(/|$)') {
                                $cc = Join-Path $Repo ($p.Replace('/', '\'))
                                if (Test-Path -LiteralPath $cc -PathType Leaf) {
                                    $ccSha = [string](@(& git hash-object --path $p -- $cc 2>$null) | Select-Object -Last 1)
                                    $baseSha = [string](@(& git rev-parse -q --verify ":2:$p" 2>$null) | Select-Object -Last 1)
                                    if ($ccSha -match '^[0-9a-f]{40}$' -and $ccSha -eq $baseSha) {
                                        Copy-Item -LiteralPath $cc -Destination $p -Force
                                        & $Logger "sync: resolved the conflict on $p to the new base, whose bytes the shared checkout already holds"
                                        $resolved = $true
                                    } else {
                                        & $Logger "sync: the shared checkout's copy of $p is not the new base's version, so the checkout rule does not apply"
                                    }
                                }
                            }
                            if (-not $resolved) {
                                & git rebase --abort 2>&1 | Out-Null
                                & $Logger "sync: rebase of $ahead local commit(s) onto the advertised main conflicted on $p ($why); tonight's commit stays LOCAL ONLY until a session rebases by hand"
                                return $false
                            }
                            & git add -- $p 2>&1 | Out-Null
                        }
                        $rbOut = @(& git -c core.editor=true rebase --continue 2>&1)
                        $rc = $LASTEXITCODE
                    }
                    if ($rc -ne 0) {
                        & git rebase --abort 2>&1 | Out-Null
                        & $Logger ("sync: rebase did not finish: " + (($rbOut | Select-Object -Last 3 | ForEach-Object { "$_" }) -join ' | '))
                        return $false
                    }
                    $newHead = [string](@(& git rev-parse HEAD 2>$null) | Select-Object -Last 1)
                } finally { Pop-Location }
            } finally {
                $env:GIT_EDITOR = $savedEditor
                & git worktree remove --force $tmp 2>&1 | Out-Null
            }
          }
            if ($newHead -notmatch '^[0-9a-f]{40}$') { & $Logger "sync: rebased head unreadable"; return $false }
            $target = $newHead
        }
        # untracked local copies that are byte-identical to the incoming tracked file block the move. Since 2026-09-25 they
        # are indexed in place at the incoming entry (Set-IndexToTarget below) and never deleted; one the helper cannot
        # stage is dropped as before, and the move writes it back.
        $incoming = @(& git diff --name-only $old $target 2>$null | Where-Object { "$_" -ne '' })
        $facts = Get-SyncFacts -Repo $Repo -Paths $incoming -Target $target
        $factNote = 'per-file git calls'
        if ($null -ne $facts) { $factNote = 'batched facts' }
        $dropped = 0
        $droppedPaths = @()
        $untrackedDiffer = @()
        foreach ($p in $incoming) {
            $f = $null
            if ($null -ne $facts) { $f = $facts[$p] }
            if ($null -ne $f) {
                if (-not $f.Exists -or $f.Tracked) { continue }
                $localHash = [string]$f.Local
                $incomingHash = [string]$f.Incoming
            } else {
                if (-not (Test-Path -LiteralPath $p)) { continue }
                & git ls-files --error-unmatch -- $p 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { continue }
                $localHash = [string](@(& git hash-object -- $p 2>$null) | Select-Object -Last 1)
                $incomingHash = [string](@(& git rev-parse "${target}:$p" 2>$null) | Select-Object -Last 1)
            }
            if ($localHash -ne '' -and $localHash -eq $incomingHash) {
                $droppedPaths += $p
                $dropped++
            } elseif (Test-Path -LiteralPath $p -PathType Leaf) {
                $untrackedDiffer += $p
            }
        }
        # TRACKED files edited here block `reset --keep` too, even when the edit IS the incoming change: a session that
        # pushes from a private index or a worktree leaves the pushed copy in this checkout, modified against a HEAD that
        # is behind. 2026-09-23: 36 such files refused the 05:11 and 12:35 moves; 34 held the incoming bytes exactly and
        # the other 2 were mirrors whose live source equals the incoming version. Such a file is parked (copied aside)
        # and put back to HEAD, so the move writes the same bytes; a memory mirror whose live memory file equals the
        # incoming version is parked the same way (the rebase above trusts the live file for mirrors), and so is a
        # single-file mirror in $LiveMirrors (the CLAUDE.md and codex AGENTS.md copies were the 2). Any other edited
        # file still blocks the move, as before, and a refused move copies every parked file back exactly.
        # 2026-09-24: two more kinds are carried across the move instead of blocking it, because their local bytes are the
        # ones to keep and nothing is lost. (1) A mirror whose checkout copy equals its live source - a memory, a
        # single-file mirror, a flat machinery mirror under $MachineryPrefix - holds the authoritative bytes (the rebase
        # above resolves mirror conflicts the same way): it is parked, the move writes the incoming version, and the live
        # bytes go back on top for the persist commit. (2) An untracked copy that differs from the incoming file is parked
        # and put back after the move with exactly the bytes the session left, now shown as an edit of the tracked file.
        # Any other edited tracked file still blocks the move, and a refused move now names what git refused. The 09-23
        # 19:30 dream's move (00:19 on 09-24) was refused by ten files: seven memory mirrors the dream had itself copied
        # from the live memories at 22:46 (newer than the incoming versions), one machinery mirror and two campaign files
        # pushed after the sync's target was fetched, 90 minutes earlier.
        $incomingSet = @{}
        foreach ($p in $incoming) { $incomingSet[$p] = $true }
        $editedHere = @(& git diff --name-only HEAD 2>$null | Where-Object { "$_" -ne '' })
        $parkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("dream_sync_park_" + $PID + "_" + (Get-Date -Format 'HHmmss'))
        $parked = @()
        $keepLocal = @()
        $keptOther = @()
        $refreshed = @()
        $untrackedParked = @()
        $trackedCand = @()
        foreach ($p in $editedHere) {
            if (-not $incomingSet.ContainsKey($p)) { continue }
            if (-not (Test-Path -LiteralPath $p)) { continue }
            $f = $null
            if ($null -ne $facts) { $f = $facts[$p] }
            if ($null -ne $f -and [string]$f.Local -ne '') {
                $localHash = [string]$f.Local
                $incomingHash = [string]$f.Incoming
            } else {
                $localHash = [string](@(& git hash-object -- $p 2>$null) | Select-Object -Last 1)
                $incomingHash = [string](@(& git rev-parse "${target}:$p" 2>$null) | Select-Object -Last 1)
            }
            $safe = ($localHash -ne '' -and $localHash -eq $incomingHash)
            $keep = $false
            $live = ''
            if ($MemoryDir -and $p.StartsWith($MirrorPrefix)) { $live = Join-Path $MemoryDir ($p.Substring($MirrorPrefix.Length)) }
            elseif ($LiveMirrors.ContainsKey($p)) { $live = [string]$LiveMirrors[$p] }
            elseif ($p.StartsWith($MachineryPrefix) -and -not $p.Substring($MachineryPrefix.Length).Contains('/')) {
                $live = Join-Path $MachineryDir ($p.Substring($MachineryPrefix.Length))
            }
            if (-not $safe -and $live -ne '' -and (Test-Path -LiteralPath $live -PathType Leaf)) {
                $liveHash = [string](@(& git hash-object -- $live 2>$null) | Select-Object -Last 1)
                if ($liveHash -ne '' -and $liveHash -eq $incomingHash) { $safe = $true }
                elseif ($liveHash -ne '' -and $liveHash -eq $localHash) { $keep = $true }
                elseif ($liveHash -ne '' -and $MemoryDir -and $p.StartsWith($MirrorPrefix)) {
                    # 2026-09-25: a memory mirror whose live file moved on after the dream copied it - a third version,
                    # neither the local copy nor the incoming one. The live file is the mirror's truth (the rebase above
                    # resolves mirror conflicts the same way), so bring the local copy to it and carry it across the move
                    # like a kept mirror; the persist then commits the newest bytes. The 03:30 dream of 09-25 lost its
                    # push this way: its loop ran 34 minutes, sessions filed memories meanwhile, and git refused the move
                    # with "Entry ... MEMORY.md not uptodate".
                    try {
                        Copy-Item -LiteralPath $live -Destination $p -Force -ErrorAction Stop
                        $keep = $true
                        $refreshed += $p
                    } catch { }
                }
            }
            if (-not $safe -and -not $keep) {
                # 2026-09-24, a third kind: an edited file OUTSIDE the paths the persist commit stages is somebody's live
                # work - a campaign ledger kept in this checkout while its snapshots are pushed through plumbing. Its local
                # bytes are carried across the move exactly like an untracked copy, and the persist never stages it. The
                # 09-24 19:30 dream's move (23:50) was refused by two such files (a campaign's LEDGER.md and NEXT.md)
                # among fifteen collisions; the other thirteen already held the incoming bytes. Inside the owned paths the
                # rule is unchanged: a third version there still blocks the move.
                $owned = $false
                foreach ($pre in $OwnedPrefixes) { if ($p.StartsWith($pre)) { $owned = $true; break } }
                if ($owned) { continue }
                $keep = $true
                $keptOther += $p
            }
            $trackedCand += [pscustomobject]@{ Path = $p; Keep = $keep }
        }
        # 2026-09-25: every file above is indexed in place at its incoming entry and left where it is (Set-IndexToTarget),
        # so a job reading it during the move never finds it missing or at HEAD's bytes. Only what the helper cannot stage
        # (a deletion, a submodule, a helper failure) takes the old way: dropped, or parked and put back.
        $staging = Set-IndexToTarget -Repo $Repo -Old $old -Target $target -Paths (@($droppedPaths) + @($untrackedDiffer) + @($trackedCand | ForEach-Object { $_.Path }))
        $inPlace = New-Object System.Collections.Hashtable ([StringComparer]::Ordinal)
        if ($null -ne $staging) { $inPlace = $staging.Staged }
        $removedPaths = @()
        foreach ($p in $droppedPaths) {
            if ($inPlace.ContainsKey($p)) { continue }
            Remove-Item -LiteralPath $p -Force
            $removedPaths += $p
        }
        $keptInPlace = @()
        $untrackedInPlace = @()
        foreach ($c in $trackedCand) {
            if ($inPlace.ContainsKey($c.Path)) {
                if ($c.Keep) { $keptInPlace += $c.Path }
                continue
            }
            $dst = Join-Path $parkDir $c.Path
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -LiteralPath $c.Path -Destination $dst -Force
            $parked += $c.Path
            if ($c.Keep) { $keepLocal += $c.Path }
        }
        foreach ($p in $untrackedDiffer) {
            if ($inPlace.ContainsKey($p)) {
                $keptInPlace += $p
                $untrackedInPlace += $p
                continue
            }
            $dst = Join-Path $parkDir $p
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -LiteralPath $p -Destination $dst -Force
            $parked += $p
            $keepLocal += $p
            $untrackedParked += $p
        }
        $trackedParked = @($parked | Where-Object { $untrackedParked -notcontains $_ })
        if ($trackedParked.Count -gt 0) { & git checkout -q HEAD -- $trackedParked 2>&1 | Out-Null }
        foreach ($p in $untrackedParked) { Remove-Item -LiteralPath $p -Force }
        $resetOut = @(& git reset -q --keep $target 2>&1)
        if ($LASTEXITCODE -ne 0) {
            # the move was refused: the files indexed in place were never touched; put their index entries back
            $undoOk = Undo-IndexToTarget -Repo $Repo -Staging $staging
            Remove-IndexUndoFile -Staging $staging
            if (-not $undoOk) { & $Logger ("sync: could not put back the index entries of " + $inPlace.Count + " files indexed in place; git status shows them as staged changes") }
            $backFail = @(Restore-ParkedCopies -Paths $parked -ParkDir $parkDir)
            if ($backFail.Count -gt 0) {
                & $Logger ("sync: could not put back " + $backFail.Count + " parked copies; their bytes stay in " + $parkDir + ": " + (($backFail | Select-Object -First 5) -join ', '))
            } elseif (Test-Path -LiteralPath $parkDir) { Remove-Item -LiteralPath $parkDir -Recurse -Force }
            # The move was refused, so the checkout stays at $old, which does not track the paths dropped above: put
            # every dropped copy back from the incoming tree, or a waking session's own files vanish from the disk.
            # (2026-09-21 07:45: two FEI notes and thirty scripts under 06_math_research went missing this way and
            # had to be restored from main by hand.)  `git restore --source` writes the worktree only, so the file
            # comes back exactly as it was, still untracked.
            $restored = 0
            foreach ($p in $removedPaths) {
                & git restore --source=$target --worktree -- $p 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $p)) { $restored++ }
            }
            $gitSaid = (($resetOut | Select-Object -First 4 | ForEach-Object { "$_" }) -join ' | ')
            & $Logger "sync: brought main to $($target.Substring(0,7)) but moving the checkout was refused (a locally modified file is among the incoming changes); main left at $($old.Substring(0,7)), tonight's commit stays LOCAL ONLY; $($inPlace.Count) files indexed in place were never touched (index put back: $undoOk), $restored of $($removedPaths.Count) dropped identical untracked copies put back, $($parked.Count) parked copies put back; $($incoming.Count) incoming paths checked by $factNote; git said: $gitSaid"
            return $false
        }
        Remove-IndexUndoFile -Staging $staging
        $keepFail = @(Restore-ParkedCopies -Paths $keepLocal -ParkDir $parkDir)
        if ($keepFail.Count -gt 0) {
            & $Logger ("sync: could not put back " + $keepFail.Count + " kept copies; their bytes stay in " + $parkDir + ": " + (($keepFail | Select-Object -First 5) -join ', '))
        } elseif (Test-Path -LiteralPath $parkDir) { Remove-Item -LiteralPath $parkDir -Recurse -Force }
        $keptNote = "$($keepLocal.Count + $keptInPlace.Count) kept at their local bytes ($($untrackedParked.Count + $untrackedInPlace.Count) untracked copies, $($keptOther.Count) live working files outside the dream's paths, the rest mirrors equal to their live source, $($refreshed.Count) of them refreshed from a live file that had moved on); $($inPlace.Count) files indexed in place, never touched; $($incoming.Count) incoming paths checked by $factNote"
        $untrackedNote = "$dropped identical untracked copies ($($removedPaths.Count) dropped for the move)"
        if ($ahead -eq '0') {
            & $Logger "sync: local main fast-forwarded $($old.Substring(0,7)) -> $($target.Substring(0,7)) ($behind incoming commits, $untrackedNote, $($parked.Count) edited copies parked, $keptNote)"
        } else {
            & $Logger "sync: local main rebased onto the advertised main $($old.Substring(0,7)) -> $($target.Substring(0,7)) ($ahead replayed over $behind incoming, $rounds conflict rounds, $untrackedNote, $($parked.Count) edited copies parked, $keptNote)"
        }
        return $true
    } finally {
        Pop-Location
        $env:DREAM_SYNC_MIRRORS = $savedMirrorsEnv
    }
}
