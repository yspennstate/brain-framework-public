# relaunch.ps1 - bring the crashed fleet back into visible windows, resumed and auto-started, with ONE command.
#
# STABLE BY DEFAULT (no keystroke injection, no focus-stealing): each agent opens in its own visible WT window
# running `claude --resume <id> -p '<prompt>'`. The `-p` form AUTO-SUBMITS and streams its work into the
# window, so nothing has to press Enter - which means no SetForegroundWindow, no SendKeys, nothing that grabs
# your mouse/keyboard. `codex resume` auto-submits too.
#
# -Faithful reproduces EXACTLY how each agent is running now: agents recorded mode="interactive" come back as
# a persistent TUI (pre-filled, with a "PRESS ENTER" banner - still no injection, so you press it), and
# mode="gentle" agents come back headless -p. Use this to restore the current setup after a crash.
#
# -Interactive forces the full interactive Claude TUI for ALL agents AND presses Enter for you. It is the ONLY
# path that injects (claude --resume only pre-fills the box), so it briefly steals focus (Alt-unlock + SendWait
# '{ENTER}', only into a window we created). Hands-off, but expect a few seconds of focus-stealing at launch.
#
# Reads registry\*.json. SKIPS any agent marked "disabled": true - so anything you turn off STAYS off (until
# you re-enable it with register.ps1 -Enable). Never kills or touches the WindowsTerminal process.
param([switch]$DryRun, [switch]$Discover, [switch]$Interactive, [switch]$Faithful, [int]$MinutesBack = 180, [string[]]$Only = @(), [string]$Model = '')
$ErrorActionPreference = 'Continue'
# -File passes "-Only a,b" as ONE string; normalize to a clean list (also accept arrays / space lists).
$Only = @($Only | ForEach-Object { $_ -split '[,\s]+' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$fleet = "$env:USERPROFILE\.claude\fleet"
# test seam (mirrors registry_refresh.py's FLEET_REGISTRY_DIR): point tests at a temp registry; unset = real.
$reg   = if ($env:FLEET_REGISTRY_DIR) { $env:FLEET_REGISTRY_DIR } else { Join-Path $fleet 'registry' }
$proj  = "$env:USERPROFILE\.claude\projects\{{BRAIN_SLUG}}"
$codex = Join-Path $env:USERPROFILE '.codex\sessions'
$log   = Join-Path $fleet 'relaunch.log'
New-Item -ItemType Directory -Force $reg | Out-Null
function L($m){ $t = (Get-Date).ToString('HH:mm:ss'); "$t  $m" | Tee-Object -FilePath $log -Append }

# The interactive TUI path (and ONLY it) needs desktop-input rights to inject Enter, so it must run inside a
# real WT console. The gentle default does no injection and runs fine anywhere - so bootstrap only for -Interactive.
if ($Interactive -and -not $DryRun) {
    function Test-Injectable {
        $underWT = $false; $cur = $PID
        for ($i = 0; $i -lt 12 -and $cur; $i++) {
            $pr = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
            if (-not $pr) { break }
            if ($pr.Name -eq 'WindowsTerminal.exe') { $underWT = $true; break }
            $cur = [int]$pr.ParentProcessId
        }
        $hasConsole = $true; try { [void][System.Console]::WindowHeight } catch { $hasConsole = $false }
        return ($underWT -and $hasConsole)
    }
    if (-not (Test-Injectable)) {
        $a = "-ExecutionPolicy Bypass -File `"$PSCommandPath`" -Interactive"
        if ($Discover) { $a += " -Discover -MinutesBack $MinutesBack" }
        if ($Only.Count) { $a += ' -Only ' + ($Only -join ',') }
        if ($Model) { $a += " -Model $Model" }
        # NOTE: no -NoExit - the orchestrator window closes itself when done, so no debris lingers.
        Start-Process wt -ArgumentList "-w new nt --title FLEET-RELAUNCH powershell $a"
        "not under Windows Terminal -> re-launched -Interactive in a WT window (needed to inject Enter). This window can close."
        return
    }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class WF {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    public static List<IntPtr> TW() { var r = new List<IntPtr>();
        EnumWindows(delegate(IntPtr h, IntPtr l){ if(!IsWindowVisible(h)) return true;
            var sb = new StringBuilder(64); GetClassName(h, sb, 64);
            if (sb.ToString() == "CASCADIA_HOSTING_WINDOW_CLASS") r.Add(h); return true; }, IntPtr.Zero);
        return r; }
}
"@
}

# PASS-LOCK (2026-07-12 duplicate scar): a second recovery pass re-ran launches against an already-recovered
# fleet and duplicated 6 sessions. A non-DryRun pass takes locks\relaunch.pass.lock; a second non-DryRun pass
# within 10 minutes REFUSES (delete the lock to override). -DryRun neither takes nor honors it. A lock older
# than the last boot never blocks (a pre-crash pass must not refuse the post-crash recovery). Seam:
# FLEET_LOCKS_DIR points selftest at a temp locks dir, mirroring FLEET_REGISTRY_DIR.
if (-not $DryRun) {
    $locksDir = if ($env:FLEET_LOCKS_DIR) { $env:FLEET_LOCKS_DIR } else { Join-Path $fleet 'locks' }
    New-Item -ItemType Directory -Force $locksDir | Out-Null
    $passLock = Join-Path $locksDir 'relaunch.pass.lock'
    if (Test-Path $passLock) {
        $pl = $null; $plTime = $null
        try { $pl = Get-Content $passLock -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
        if ($pl -and $pl.ts) { try { $plTime = [datetime]::Parse([string]$pl.ts) } catch {} }
        if ($plTime) {
            $plAge = ((Get-Date) - $plTime).TotalMinutes
            $plBoot = $null; try { $plBoot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime } catch {}
            if (($plAge -lt 10) -and -not ($plBoot -and $plTime -lt $plBoot)) {
                L "REFUSED: relaunch pass-lock is fresh (pid $($pl.pid), $([math]::Round($plAge,1)) min ago) - a second pass inside 10 min re-launches an already-recovered fleet. Wait, or delete $passLock"
                exit 4
            }
        }
    }
    @{ pid = $PID; ts = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $passLock -Encoding UTF8
}

if ($Discover) {
    Get-ChildItem "$proj\*.jsonl" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-$MinutesBack) } | ForEach-Object {
        $id = $_.BaseName; $regf = Join-Path $reg "$id.json"
        if (Test-Path $regf) { return }
        $head = (Get-Content $_.FullName -TotalCount 60) -join "`n"
        if ($head -match 'run_dream|Hard time budget') { return }
        @{ id=$id; runtime='claude'; title=$id.Substring(0,8); task=''; cwd="$env:USERPROFILE"; model=''; mode='gentle'; checkpoint=''; disabled=$false; updated=(Get-Date).ToString('s') } | ConvertTo-Json | Set-Content -LiteralPath $regf -Encoding UTF8
        L "discovered claude $id"
    }
    Get-ChildItem $codex -Recurse -Filter 'rollout-*.jsonl' -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-$MinutesBack) } | ForEach-Object {
        try { $m = (Get-Content $_.FullName -TotalCount 1 | ConvertFrom-Json).payload } catch { return }
        if ($m.originator -ne 'codex-tui' -or $m.thread_source -eq 'subagent' -or $m.forked_from_id) { return }
        $regf = Join-Path $reg "$($m.id).json"; if (Test-Path $regf) { return }
        @{ id=$m.id; runtime='codex'; title="codex $($m.id.Substring(0,8))"; task=''; cwd="$env:USERPROFILE"; model=''; mode='gentle'; checkpoint=''; disabled=$false; updated=(Get-Date).ToString('s') } | ConvertTo-Json | Set-Content -LiteralPath $regf -Encoding UTF8
        L "discovered codex $($m.id)"
    }
}

$agents = @(Get-ChildItem "$reg\*.json" -ErrorAction SilentlyContinue | ForEach-Object { try { Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} } | Where-Object { $_ })
$agents = @($agents | Where-Object { -not $_.disabled })   # anything you turned off stays off
# AUTO-registered entries (registry_refresh writes one for every session's first Stop) resurrect
# ONLY if recently active: the registry had grown to 45 all-enabled entries by 2026-07-12, most of
# them finished one-shots and other-cwd sessions, so a bare relaunch would have opened ~2 dozen
# ghost windows. A crash recovery runs minutes after death - an auto entry quiet for 3+ hours was
# not part of the running fleet. EXPLICIT registrations (register.ps1, no auto flag) are the
# owner's list and are always honored. ASCII-only literals (SessionStart-hook em-dash scar).
$projRoot = Join-Path $env:USERPROFILE '.claude\projects'
function NewestActivityH($id) {
    $ages = @()
    foreach ($f in (Get-ChildItem "$projRoot\*\$id*.jsonl" -ErrorAction SilentlyContinue)) { $ages += ((Get-Date) - $f.LastWriteTime).TotalHours }
    foreach ($f in (Get-ChildItem $codex -Recurse -Filter "*$id*.jsonl" -ErrorAction SilentlyContinue)) { $ages += ((Get-Date) - $f.LastWriteTime).TotalHours }
    if ($ages.Count) { return ($ages | Measure-Object -Minimum).Minimum } else { return 1e9 }
}
$skippedStale = @($agents | Where-Object { $_.auto -and ((NewestActivityH $_.id) -ge 3) })
if ($skippedStale.Count) { L ("  skipping {0} stale auto-registered entr(ies) (quiet 3h+; not the running fleet): {1}" -f $skippedStale.Count, (($skippedStale | ForEach-Object { $_.id.Substring(0,8) }) -join ' ')) }
$agents = @($agents | Where-Object { (-not $_.auto) -or ((NewestActivityH $_.id) -lt 3) })
if ($Only.Count) { $agents = @($agents | Where-Object { $a = $_; @($Only | Where-Object { $a.id -like "$_*" }).Count -gt 0 }) }
if (-not $agents.Count) { "no enabled registered agents. (Discover, register, or re-enable with register.ps1 -Enable.)"; return }

$running = @(Get-CimInstance Win32_Process -Filter "name='claude.exe' OR name='codex.exe'" | ForEach-Object { "$($_.CommandLine)" })
function Test-Alive($id) {
    foreach ($c in $running) { if ($c -match [regex]::Escape($id)) { return $true } }
    $tf = Join-Path $proj "$id.jsonl"
    if ((Test-Path $tf) -and (((Get-Date) - (Get-Item $tf).LastWriteTime).TotalSeconds -lt 180)) { return $true }
    # codex transcripts live under ~/.codex/sessions (rollout-<ts>-<id>.jsonl), NOT $proj - check them too, or
    # a live codex reads as DEAD and a real relaunch duplicates it (verified 2026-07-11: C1/C3 019f4b99 /
    # 019f4d1e were alive yet mis-flagged dead). Full id only - the 8-char prefix collides across codex ids.
    if (Get-ChildItem $codex -Recurse -Filter "*$id*.jsonl" -ErrorAction SilentlyContinue | Where-Object { ((Get-Date) - $_.LastWriteTime).TotalSeconds -lt 180 } | Select-Object -First 1) { return $true }
    return $false
}

$todo = @($agents | Where-Object { -not (Test-Alive $_.id) })
$mode = if ($Interactive) { 'interactive TUI (injects Enter)' } elseif ($Faithful) { 'faithful (per-agent recorded mode, no injection)' } else { 'gentle -p (no injection)' }
L "=== relaunch [$mode]: $($agents.Count) enabled, $($todo.Count) dead ==="
# the effective launch mode for one agent, given the switches - the SAME rule the launch loop uses below.
function Eff($a) { if ($a.runtime -ne 'claude') { 'codex' } elseif ($Interactive -or ($Faithful -and $a.mode -eq 'interactive')) { 'interactive' } else { 'gentle -p' } }
if ($DryRun) { $todo | ForEach-Object { "  would relaunch [$($_.runtime)/$(Eff $_)] $($_.title)  $($_.id)" }; if(-not $todo.Count){"  (all enabled agents are already running)"}; return }

$pre = "CRASH RECOVERY - visible relaunch. The computer crashed and rebooted, killing you mid-task. You are resumed in a VISIBLE terminal the owner is watching. Continue from where you left off; end each turn with a concise status. FIRST verify your on-disk state (a job or write may have died mid-run). Keep heavy compute OFF this laptop (crashes are GPU/PCIe load-correlated); use the Azure box. Never spawn a visible or flashing background window. "
$modelArg = if ($Model) { "--model $Model " } else { '' }

function New-Win([string]$title, [string]$runner) {
    $before = if ($Interactive) { @([WF]::TW()) } else { @() }
    Start-Process wt -ArgumentList ('-w new nt --title "' + $title + '" powershell -NoExit -ExecutionPolicy Bypass -File "' + $runner + '"')
    if (-not $Interactive) { return [IntPtr]::Zero }   # gentle mode needs no HWND
    for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 500; $n = @([WF]::TW()) | Where-Object { $before -notcontains $_ }; if ($n) { return ($n | Select-Object -First 1) } }
    return [IntPtr]::Zero
}

$plan = @()
foreach ($d in $todo) {
    $task = if ($d.task) { $d.task } else { 'Resume your task now.' }
    # include the fresh registry checkpoint (registry_refresh keeps it current every Stop) - the last state
    # plus the very next step, so the resume picks up where it was instead of re-deriving from the task
    # alone. It was WRITTEN to the registry but never READ into the prompt until 2026-07-11, so a relaunched
    # agent never actually saw its checkpoint. ASCII-only literal (SessionStart-hook em-dash scar).
    $ckpt = if ($d.checkpoint) { ' WHERE YOU WERE (your last checkpoint - verify on disk before trusting it): ' + $d.checkpoint } else { '' }
    $q = ($pre + $task + $ckpt).Replace("'", "''")
    # resume must run from the session's real cwd; a cwd under .claude (a tool dir) is the 07-11 corruption -> fall back
    $cwd = if ($d.cwd -and (Test-Path $d.cwd) -and $d.cwd -notmatch '\\\.claude\\') { $d.cwd } else { "$env:USERPROFILE" }
    # generated runners live under runners\ since 2026-07-12: the fleet-root run_<id8>.ps1 files are
    # now hand-kept WRAPPERS routing through launch_one.ps1 - regenerating over them would strip the
    # duplicate guards exactly when a recovery pass runs (the incident class this whole day was about).
    $runnerDir = Join-Path $fleet 'runners'
    if (-not (Test-Path $runnerDir)) { New-Item -ItemType Directory -Path $runnerDir -Force | Out-Null }
    $runner = Join-Path $runnerDir ("relaunch_{0}.ps1" -f $d.id.Substring(0, 8))
    # -Faithful HONORS the agent's recorded mode: 'interactive' comes back as a persistent pre-filled TUI (the
    # way it is running now); 'gentle' comes back headless -p. -Interactive forces ALL agents interactive. The
    # plain default stays gentle -p for everyone (auto-runs, visible, no injection, no dead-looking windows).
    $useInteractive = $Interactive -or ($Faithful -and $d.mode -eq 'interactive')
    if ($d.runtime -eq 'claude') {
        if ($useInteractive) {
            $line = "claude --resume $($d.id) $modelArg--dangerously-skip-permissions '$q'"
        } else {
            $line = "claude --resume $($d.id) $modelArg-p '$q' --dangerously-skip-permissions --verbose"
        }
        $amode = if ($useInteractive) { 'interactive' } else { 'gentle -p' }
        # Default relaunch of an interactive agent PRE-FILLS the prompt and does NOT inject Enter (no
        # focus-stealing) - so tell the owner to press it. Under -Interactive we auto-press, so no banner.
        $banner = if ($useInteractive -and -not $Interactive) { "`nWrite-Host '>>> PRESS ENTER to resume (prompt is pre-filled) <<<' -ForegroundColor Yellow" } else { '' }
        $body = "Set-Location '$cwd'`n`$host.UI.RawUI.WindowTitle = '$($d.title)'`nWrite-Host '=== resume CLAUDE $($d.id) [$amode] ===' -ForegroundColor Cyan$banner`n$line"
    } else {
        $body = "Set-Location '$cwd'`n`$host.UI.RawUI.WindowTitle = '$($d.title)'`nWrite-Host '=== resume CODEX $($d.id) ===' -ForegroundColor Green`ncodex resume $($d.id) --dangerously-bypass-approvals-and-sandbox '$q'"
    }
    Set-Content -LiteralPath $runner -Value $body -Encoding UTF8
    $file = Join-Path $proj "$($d.id).jsonl"
    $base = if (Test-Path $file) { (Get-Item $file).LastWriteTime } else { Get-Date '2000-01-01' }
    $hwnd = New-Win $d.title $runner
    $plan += [pscustomobject]@{ d = $d; file = $file; base = $base; hwnd = $hwnd; status = 'launched' }
    L "opened [$($d.runtime)] $($d.title)$(if($Interactive){" hwnd=$hwnd"})"
    Start-Sleep -Seconds 2
}

# No injection here (plain default OR -Faithful): -p agents auto-submit; -Faithful interactive agents are
# pre-filled and wait for the owner's Enter. Nothing steals focus.
if (-not $Interactive) {
    $waitEnter = @($plan | Where-Object { $_.d.runtime -eq 'claude' -and $_.d.mode -eq 'interactive' -and $Faithful })
    $auto = @($plan | Where-Object { -not ($_.d.runtime -eq 'claude' -and $_.d.mode -eq 'interactive' -and $Faithful) })
    L "=== done: $($plan.Count) opened, no keystroke injection ==="
    if ($auto.Count) { L "  $($auto.Count) auto-submitting (gentle -p / codex) - nothing to press" }
    if ($waitEnter.Count) {
        $names = ($waitEnter | ForEach-Object { $_.d.title }) -join ', '
        L "  $($waitEnter.Count) INTERACTIVE pre-filled - PRESS ENTER in each to resume: $names"
        L "  (or re-run with -Interactive to auto-press Enter for you)"
    }
    return
}

# -Interactive only: press Enter into each window we created (the focus-stealing path).
L "waiting 30s for the claude TUIs to load before pressing Enter..."
Start-Sleep -Seconds 30
$originals = @($plan | ForEach-Object { "$($_.d.id).jsonl" }); $attr = @{}
foreach ($p in $plan) {
    if ($p.d.runtime -ne 'claude') { $p.status = 'codex auto-submit'; L "  $($p.d.title): codex auto-submits"; continue }
    if ($p.hwnd -eq [IntPtr]::Zero) { $p.status = 'NO WINDOW'; L "  $($p.d.title): no window"; continue }
    $ps = Get-Date; $dl = $ps.AddSeconds(60); $p.status = 'timeout'
    while ((Get-Date) -lt $dl) {
        if ((Get-Item $p.file).LastWriteTime -gt $p.base) { $p.status = 'submitted'; break }
        $fork = Get-ChildItem $proj -Filter *.jsonl | Where-Object { $_.LastWriteTime -gt $ps -and $originals -notcontains $_.Name -and -not $attr[$_.Name] } | Sort-Object LastWriteTime | Select-Object -First 1
        if ($fork) { $attr[$fork.Name] = $true; $p.status = 'submitted (fork)'; break }
        [WF]::ShowWindow($p.hwnd, 9) | Out-Null
        [WF]::SetForegroundWindow($p.hwnd) | Out-Null
        Start-Sleep -Milliseconds 500
        if ([WF]::GetForegroundWindow() -ne $p.hwnd) {
            [System.Windows.Forms.SendKeys]::SendWait('%')
            [WF]::SetForegroundWindow($p.hwnd) | Out-Null
            Start-Sleep -Milliseconds 300
        }
        if ([WF]::GetForegroundWindow() -eq $p.hwnd) { [System.Windows.Forms.SendKeys]::SendWait('{ENTER}') }
        Start-Sleep -Seconds 7
    }
    L "  $($p.d.title): $($p.status)"
}
$ok = @($plan | Where-Object { $_.status -match 'submit' }).Count
L "=== done: $ok/$($plan.Count) submitted (interactive) ==="
