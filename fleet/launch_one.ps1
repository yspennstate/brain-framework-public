# launch_one.ps1 - the ONE guarded way to launch/resume a single fleet session (2026-07-12).
#
# WHY: the side entry points (run_*.ps1 / restore_*.ps1) used to resume their session BLINDLY. On
# 2026-07-12 a second recovery pass at 12:13-12:18 re-ran them against an already-recovered fleet and
# spawned duplicate processes for 6 session ids (restore_<user> even fired twice 15 seconds apart).
# Two live processes resuming one session risk transcript corruption; the day before, a peer nearly
# killed a LIVE session while hunting the zombie. So: every launch path now funnels through here.
#
# Guards, in order (exit codes are a FROZEN CONTRACT - nudge.py and the wrappers consume them):
#   a. DISABLED  registry disabled=true              -> exit 5   (-Force overrides)
#   b. ALIVE     a live claude/codex process already carries the id -> exit 3   (-Force overrides,
#                but ONLY after you tree-verified the pids are zombies - see the 07-12 near-miss)
#   c. COOLDOWN  locks\<id>.launch.lock fresher than 10 min       -> exit 4   (NO -Force bypass)
#   0 = launched (or -DryRun previewed). The lock is deliberately LEFT after a launch - it IS the
#   10-minute cooldown against rapid double-fire. A lock older than the last boot never blocks
#   (post-crash recovery must not be refused by a pre-crash lock). -DryRun honors every guard but
#   writes nothing (a preview must not start a cooldown).
#
# Launch mechanics use a small generated runner script. Gentle mode starts
# PowerShell hidden at process creation; interactive mode alone opens WT for an
# owner-controlled TUI with a pre-filled prompt and yellow PRESS
# ENTER banner, NO keystroke injection; codex = `codex resume` (auto-submits). Runners are written
# to runners\ (NOT the fleet root: relaunch.ps1 owns/regenerates run_<first8>.ps1 there, and those
# are now the guarded wrappers we must not clobber).
#
# Usage:
#   powershell -File launch_one.ps1 -Id <36-char session id> [-Prompt <text>] [-Mode interactive|gentle]
#              [-Title <window title>] [-Banner <press-enter text>] [-Force] [-DryRun]
# Prompt/mode/title default from the registry entry (task + checkpoint, same recipe as relaunch.ps1).
param(
  [Parameter(Mandatory)]
  [ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')]
  [string]$Id,
  [string]$Prompt = '',
  [ValidateSet('','interactive','gentle')][string]$Mode = '',
  [string]$Title = '',
  [string]$Banner = '>>> PRESS ENTER to resume (prompt is pre-filled) <<<',
  [switch]$Force,
  [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$fleet = "$env:USERPROFILE\.claude\fleet"
# test seams (mirror registry_refresh.py's FLEET_REGISTRY_DIR): point tests at temp dirs; unset = real.
$reg   = if ($env:FLEET_REGISTRY_DIR) { $env:FLEET_REGISTRY_DIR } else { Join-Path $fleet 'registry' }
$locks = if ($env:FLEET_LOCKS_DIR)    { $env:FLEET_LOCKS_DIR }    else { Join-Path $fleet 'locks' }
$log   = Join-Path $fleet 'launch_one.log'
function L($m){ $t = (Get-Date).ToString('HH:mm:ss'); "$t  $m" | Tee-Object -FilePath $log -Append }

# registry entry is optional - an unregistered id still launches, with generic defaults.
$entry = $null
$rf = Join-Path $reg "$Id.json"
if (Test-Path $rf) { try { $entry = Get-Content $rf -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }

# --- a. DISABLED-GUARD: off stays off (register.ps1 -Enable turns it back on) ---
if ($entry -and $entry.disabled) {
    if ($Force) {
        L "-Force: launching $Id despite disabled=true in the registry"
    } else {
        L "DISABLED: registry marks $Id disabled (off stays off) - refusing. register.ps1 -Enable to turn it back on, or -Force."
        exit 5
    }
}

# --- b. ALIVE-GUARD: never resume a session a live process already serves (relaunch.ps1's approach) ---
# seam: LAUNCHONE_TEST_CMDLINES=<file> of "pid|commandline" lines replaces the Win32_Process snapshot,
# so selftest can PROVE this guard fires without any real claude/codex process.
if ($env:LAUNCHONE_TEST_CMDLINES -and (Test-Path $env:LAUNCHONE_TEST_CMDLINES)) {
    $live = @(Get-Content $env:LAUNCHONE_TEST_CMDLINES -Encoding UTF8 | Where-Object { $_ } | ForEach-Object {
        $p = $_ -split '\|', 2
        if ($p.Count -eq 2) { [pscustomobject]@{ ProcessId = $p[0].Trim(); CommandLine = $p[1] } }
        else                { [pscustomobject]@{ ProcessId = '?';          CommandLine = $_ } }
    })
} else {
    $live = @(Get-CimInstance Win32_Process -Filter "name='claude.exe' OR name='codex.exe'" |
        ForEach-Object { [pscustomobject]@{ ProcessId = $_.ProcessId; CommandLine = "$($_.CommandLine)" } })
}
$mine = @($live | Where-Object { $_.CommandLine -match [regex]::Escape($Id) })
if ($mine.Count) {
    $pids = @($mine | ForEach-Object { $_.ProcessId }) -join ', '
    if ($Force) {
        L "-Force: $Id is already live in pid(s) $pids - launching anyway (you said you tree-verified those are zombies)"
    } else {
        L "ALREADY LIVE pid(s) $pids - refusing (use -Force only if you have tree-verified those are zombies)"
        exit 3
    }
}
# cmdline missed: a PLAIN-started or picker-resumed session carries no session id in any command
# line, yet is very much alive - its transcript is hot. Same fallback relaunch's Test-Alive has;
# without it a launch here would DUPLICATE a live session (independent reviewer catch, 2026-07-12).
# seam: LAUNCHONE_PROJROOT redirects the projects root for tests.
if (-not $mine.Count -and -not $Force) {
    $projRoot = if ($env:LAUNCHONE_PROJROOT) { $env:LAUNCHONE_PROJROOT } else { Join-Path $env:USERPROFILE '.claude\projects' }
    $codexRoot = Join-Path $env:USERPROFILE '.codex\sessions'
    $freshTf = @(@(Get-ChildItem "$projRoot\*\$Id*.jsonl" -ErrorAction SilentlyContinue) +
                 @(Get-ChildItem $codexRoot -Recurse -Filter "*$Id*.jsonl" -ErrorAction SilentlyContinue) |
        Where-Object { ((Get-Date) - $_.LastWriteTime).TotalSeconds -lt 180 } | Select-Object -First 1)
    if ($freshTf.Count) {
        L "ALREADY LIVE by transcript ($($freshTf[0].Name) written <180s ago; the session runs without the id in any command line) - refusing. Use -Force only if you have tree-verified it is dead."
        exit 3
    }
}

# --- c. LOCK/COOLDOWN: one launch per id per 10 minutes. A cooldown is a cooldown - NO -Force bypass. ---
$lock = Join-Path $locks "$Id.launch.lock"
if (Test-Path $lock) {
    $holder = $null; $lockTime = $null
    try { $holder = Get-Content $lock -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
    if ($holder -and $holder.ts) { try { $lockTime = [datetime]::Parse([string]$holder.ts) } catch {} }
    if ($lockTime) {
        $ageMin = ((Get-Date) - $lockTime).TotalMinutes
        # a lock written before the last boot belongs to a previous OS session - it must never refuse
        # the post-crash recovery this apparatus exists for.
        $boot = $null; try { $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime } catch {}
        if (($ageMin -lt 10) -and -not ($boot -and $lockTime -lt $boot)) {
            L "COOLDOWN: launch lock for $Id is fresh (pid $($holder.pid), $([math]::Round($ageMin,1)) min ago; 10-min window) - refusing. No -Force bypass; wait or delete $lock"
            exit 4
        }
    }
}

# --- resolve what to launch (same rules as relaunch.ps1) ---
$runtime = 'claude'
if ($entry -and $entry.runtime) { $runtime = [string]$entry.runtime }
$effMode = 'gentle'
if ($Mode) { $effMode = $Mode } elseif ($entry -and $entry.mode) { $effMode = [string]$entry.mode }
$effTitle = $Id.Substring(0, 8)
if ($Title) { $effTitle = $Title } elseif ($entry -and $entry.title) { $effTitle = [string]$entry.title }
$amode = if ($runtime -ne 'claude') { 'codex' } elseif ($effMode -eq 'interactive') { 'interactive' } else { 'gentle -p' }

# neutral preamble: launch_one serves nudges and manual resumes on a LIVE machine, so it must not
# assert a crash that never happened (the 2026-07-12 waves resumed agents with a stale crash
# narrative and each burned its first turn verifying a phantom reboot). The crash story belongs to
# relaunch.ps1/autorecover.ps1, which have boot-time evidence for it; callers can also pass -Prompt.
$pre = "RESUMED by the fleet launcher. Your process had stopped; the machine did NOT necessarily crash - FIRST verify your on-disk state briefly (a job or write may have died mid-run), then continue from where you left off; end each turn with a concise status. Keep heavy compute OFF this laptop (crashes are GPU/PCIe load-correlated); use the Azure box. Never spawn a visible or flashing background window. "
if ($Prompt) {
    $ptext = $Prompt
} else {
    # no -Prompt: build it from the registry the way relaunch.ps1 does (task + fresh checkpoint).
    $task = 'Resume your task now.'
    if ($entry -and $entry.task) { $task = [string]$entry.task }
    $ckpt = ''
    if ($entry -and $entry.checkpoint) { $ckpt = ' WHERE YOU WERE (your last checkpoint - verify on disk before trusting it): ' + [string]$entry.checkpoint }
    $ptext = $pre + $task + $ckpt
}
$q = $ptext.Replace("'", "''")

# resume must run from the session's real cwd; a cwd under .claude (a tool dir) is the 07-11 corruption -> fall back
$cwd = "$env:USERPROFILE"
if ($entry -and $entry.cwd -and (Test-Path $entry.cwd) -and $entry.cwd -notmatch '\\\.claude\\') { $cwd = [string]$entry.cwd }

$tq = $effTitle.Replace("'", "''")
if ($runtime -eq 'claude') {
    if ($effMode -eq 'interactive') {
        $line = "claude --resume $Id --dangerously-skip-permissions '$q'"
    } else {
        $line = "claude --resume $Id -p '$q' --dangerously-skip-permissions --verbose"
    }
    # interactive pre-fills and does NOT inject Enter (no focus-stealing) - tell the owner to press it.
    $banner = ''
    if ($effMode -eq 'interactive' -and $Banner) { $banner = "`nWrite-Host '$($Banner.Replace("'", "''"))' -ForegroundColor Yellow" }
    $body = "Set-Location '$cwd'`n`$host.UI.RawUI.WindowTitle = '$tq'`nWrite-Host '=== resume CLAUDE $Id [$amode] ===' -ForegroundColor Cyan$banner`n$line"
} else {
    $line = "codex resume $Id --dangerously-bypass-approvals-and-sandbox '$q'"
    $body = "Set-Location '$cwd'`n`$host.UI.RawUI.WindowTitle = '$tq'`nWrite-Host '=== resume CODEX $Id ===' -ForegroundColor Green`n$line"
}

if ($DryRun) {
    L "DRYRUN would launch [$runtime/$amode] $effTitle  $Id (cwd $cwd)"
    L "DRYRUN   $line"
    exit 0
}

# take the cooldown lock ATOMICALLY (CreateNew), then launch. The lock stays behind on purpose
# (see header). Test-Path-then-write had a TOCTOU window where two concurrent callers both passed
# (independent reviewer catch, 2026-07-12): now the filesystem arbitrates - exactly one CreateNew
# wins; the loser re-reads freshness and refuses, or replaces a stale lock and retries once.
New-Item -ItemType Directory -Force $locks | Out-Null
$lockJson = (@{ pid = $PID; ts = (Get-Date).ToString('o') } | ConvertTo-Json)
$wrote = $false
for ($attempt = 0; $attempt -lt 2 -and -not $wrote; $attempt++) {
    try {
        $fs = [System.IO.File]::Open($lock, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $sw = New-Object System.IO.StreamWriter($fs, (New-Object System.Text.UTF8Encoding($true)))
        $sw.Write($lockJson)
        $sw.Dispose()
        $wrote = $true
    } catch [System.IO.IOException] {
        $holder2 = $null; $lt2 = $null
        try { $holder2 = Get-Content $lock -Raw -Encoding UTF8 | ConvertFrom-Json; $lt2 = [datetime]::Parse([string]$holder2.ts) } catch {}
        if ($lt2 -and (((Get-Date) - $lt2).TotalMinutes -lt 10)) {
            L "COOLDOWN (lost the creation race): launch lock for $Id was just taken by pid $($holder2.pid) - refusing"
            exit 4
        }
        try { Remove-Item -LiteralPath $lock -Force } catch {}
    }
}
if (-not $wrote) {
    L "COOLDOWN: could not atomically take the launch lock for $Id - refusing"
    exit 4
}

# runner lives in runners\ - relaunch.ps1 regenerates fleet-root run_<first8>.ps1, which are now wrappers.
$runnerDir = Join-Path $fleet 'runners'
New-Item -ItemType Directory -Force $runnerDir | Out-Null
$runner = Join-Path $runnerDir ("run_{0}.ps1" -f $Id.Substring(0, 8))
Set-Content -LiteralPath $runner -Value $body -Encoding UTF8
if ($runtime -eq 'claude' -and $effMode -ne 'interactive') {
    $hiddenPowerShell = Join-Path $PSHOME 'powershell.exe'
    $hiddenArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $runner
    Start-Process -FilePath $hiddenPowerShell -ArgumentList $hiddenArgs -WindowStyle Hidden
} else {
    Start-Process wt -ArgumentList ('-w new nt --title "' + $effTitle + '" powershell -NoExit -ExecutionPolicy Bypass -File "' + $runner + '"')
}
L "launched [$runtime/$amode] $effTitle  $Id (10-min cooldown lock: $lock)"
exit 0
