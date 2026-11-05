# selftest.ps1 - PROVE the crash-recovery invariants fire, against a throwaway temp registry (never the real
# one). These are the owner's "never" specs turned into guards + tests: an off agent STAYS off, the recorded
# mode round-trips, files carry a BOM (so PS readers don't mojibake the resume prompt), relaunch SKIPS
# disabled agents, and relaunch reproduces each agent's mode. Since a prior incident (duplicate-resume scar) also:
# launch_one.ps1 refuses live (3) / cooldown-locked (4) / disabled (5) launches, the 6 side scripts stay
# routed through launch_one with no bare resume, and relaunch's pass-lock refuses a rapid second pass.
# Opens no windows, launches nothing. Run: powershell -File selftest.ps1
$ErrorActionPreference = 'Continue'
$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$register = Join-Path $here 'register.ps1'
$relaunch = Join-Path $here 'relaunch.ps1'
$tmp      = Join-Path $env:TEMP 'fleet_selftest_reg'
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Force $tmp | Out-Null
$env:FLEET_REGISTRY_DIR = $tmp          # child register.ps1/relaunch.ps1 inherit this -> temp registry

$fail = 0
function Check($name, $cond) { if ($cond) { "  PASS  $name" } else { "  FAIL  $name"; $script:fail++ } }
function Reg($id) { Get-Content (Join-Path $tmp "$id.json") -Raw -Encoding UTF8 | ConvertFrom-Json }
function RunRe([string[]]$a) { $script:reOut = & powershell -NoProfile -ExecutionPolicy Bypass -File $relaunch @a 2>&1; $script:reExit = $LASTEXITCODE; $script:reOut }
function RegPS([string[]]$a) { & powershell -NoProfile -ExecutionPolicy Bypass -File $register @a 2>&1 | Out-Null }

"=== fleet recovery selftest (temp registry: $tmp) ==="

# --- Invariant 1: an agent turned OFF stays off through a re-register (the $obj used to drop 'disabled') ---
RegPS @('-Id','selftest-off','-Runtime','claude','-Task','t1','-Mode','gentle')
RegPS @('-Id','selftest-off','-Disable')
RegPS @('-Id','selftest-off','-Task','t2-changed')     # re-register with a NEW task, NO -Enable
$o = Reg 'selftest-off'
Check 'disabled STAYS true through a re-register' ($o.disabled -eq $true)
Check 'other fields still update while disabled sticks' ($o.task -eq 't2-changed')
RegPS @('-Id','selftest-off','-Enable')
Check '-Enable clears disabled' ((Reg 'selftest-off').disabled -eq $false)

# --- Invariant 2: recorded mode round-trips; explicit -Mode wins, else preserved ---
RegPS @('-Id','selftest-mode','-Mode','interactive','-Task','x')
RegPS @('-Id','selftest-mode','-Checkpoint','cp-only')  # no -Mode -> must preserve interactive
Check 'mode preserved when not re-specified' ((Reg 'selftest-mode').mode -eq 'interactive')
RegPS @('-Id','selftest-mode','-Mode','gentle')
Check 'explicit -Mode overrides' ((Reg 'selftest-mode').mode -eq 'gentle')

# --- Invariant 3: files carry a UTF-8 BOM (PS Get-Content else mojibakes non-ASCII into the resume prompt) ---
$bytes = [System.IO.File]::ReadAllBytes((Join-Path $tmp 'selftest-mode.json'))
Check 'registry file written WITH a UTF-8 BOM' ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)

# --- Invariant 4 + 5: relaunch SKIPS disabled and reproduces each agent's MODE (window-free, via -DryRun) ---
RegPS @('-Id','selftest-int','-Mode','interactive','-Task','i')
RegPS @('-Id','selftest-gen','-Mode','gentle','-Task','g')
RegPS @('-Id','selftest-dis','-Mode','interactive','-Task','d','-Disable')

$plain = RunRe @('-DryRun')
$faith = RunRe @('-DryRun','-Faithful')
$inter = RunRe @('-DryRun','-Interactive')
function Has($out, $pat) { @($out | Where-Object { $_ -match $pat }).Count -gt 0 }

Check 'disabled agent NEVER appears (plain)'     (-not (Has $plain 'selftest-dis'))
Check 'disabled agent NEVER appears (-Faithful)' (-not (Has $faith 'selftest-dis'))
Check 'disabled agent NEVER appears (-Interactive)' (-not (Has $inter 'selftest-dis'))
Check 'enabled agents DO appear (plain)' ((Has $plain 'selftest-int') -and (Has $plain 'selftest-gen'))

# plain default: everyone gentle -p (safe, no dead windows)
Check 'plain: interactive agent launches gentle -p'  (Has $plain 'claude/gentle -p\]\s+selftest-int')
Check 'plain: gentle agent launches gentle -p'        (Has $plain 'claude/gentle -p\]\s+selftest-gen')
# -Faithful: reproduce recorded mode (interactive stays interactive, gentle stays -p)
Check '-Faithful: interactive agent -> interactive'  (Has $faith 'claude/interactive\]\s+selftest-int')
Check '-Faithful: gentle agent -> gentle -p'          (Has $faith 'claude/gentle -p\]\s+selftest-gen')
# -Interactive: force ALL interactive
Check '-Interactive: gentle agent forced interactive' (Has $inter 'claude/interactive\]\s+selftest-gen')

# --- Invariant 6: launch_one.ps1 guards (a prior incident duplicate-resume scar). All window-free: -DryRun +
# the LAUNCHONE_TEST_CMDLINES seam (fake "pid|commandline" lines stand in for Win32_Process) + the
# FLEET_LOCKS_DIR seam (temp locks dir - also keeps test locks out of fleet\locks). Frozen exit codes:
# 0 launched/preview, 3 alive-refusal, 5 disabled (-Force overrides 3 and 5), 4 lock cooldown (no bypass).
$launchone = Join-Path $here 'launch_one.ps1'
$tmpLocks  = Join-Path $env:TEMP 'fleet_selftest_locks'
if (Test-Path $tmpLocks) { Remove-Item $tmpLocks -Recurse -Force }
New-Item -ItemType Directory -Force $tmpLocks | Out-Null
$env:FLEET_LOCKS_DIR = $tmpLocks
$cmdFile = Join-Path $tmpLocks 'fake_cmdlines.txt'
$env:LAUNCHONE_TEST_CMDLINES = $cmdFile
function LOne([string[]]$a) { $script:loOut = & powershell -NoProfile -ExecutionPolicy Bypass -File $launchone @a 2>&1; $script:loExit = $LASTEXITCODE; $script:loOut }

$aliveId = 'aaaaaaaa-1111-4111-8111-111111111111'
Set-Content -LiteralPath $cmdFile -Value "31337|C:\node\claude.exe --resume $aliveId --dangerously-skip-permissions" -Encoding UTF8
$o1 = LOne @('-Id',$aliveId,'-DryRun')
Check 'launch_one ALIVE-guard refuses a live id (exit 3)' ($loExit -eq 3)
Check 'alive refusal names the pid(s) and the zombie caveat' (Has $o1 'ALREADY LIVE pid\(s\) 31337')
$o2 = LOne @('-Id',$aliveId,'-DryRun','-Force')
Check 'alive-guard -Force bypass previews the launch' ($loExit -eq 0 -and (Has $o2 'would launch'))

$lockId = 'bbbbbbbb-2222-4222-8222-222222222222'
Set-Content -LiteralPath $cmdFile -Value '4242|C:\node\claude.exe --resume ffffffff-9999-4999-8999-999999999999' -Encoding UTF8
$lockF = Join-Path $tmpLocks "$lockId.launch.lock"
@{ pid = 12345; ts = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $lockF -Encoding UTF8
LOne @('-Id',$lockId,'-DryRun') | Out-Null
Check 'fresh launch lock -> exit 4' ($loExit -eq 4)
LOne @('-Id',$lockId,'-DryRun','-Force') | Out-Null
Check 'the lock has NO -Force bypass (a cooldown is a cooldown)' ($loExit -eq 4)
@{ pid = 12345; ts = (Get-Date).AddMinutes(-11).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $lockF -Encoding UTF8
$o3 = LOne @('-Id',$lockId,'-DryRun')
Check 'stale (>10 min) lock -> -DryRun proceeds' ($loExit -eq 0 -and (Has $o3 'would launch'))
$tsAfter = $null
try { $tsAfter = [datetime]::Parse([string]((Get-Content $lockF -Raw -Encoding UTF8 | ConvertFrom-Json).ts)) } catch {}
Check '-DryRun never writes the lock (a preview starts no cooldown)' ($tsAfter -and $tsAfter -lt (Get-Date).AddMinutes(-10))

# transcript-freshness fallback (a prior incident reviewer catch): a session with NO sid in any command
# line but a HOT transcript is alive (plain-started / picker-resumed) - launching would duplicate it.
$tfId = 'dddddddd-4444-4444-8444-444444444444'
$tmpProj = Join-Path $tmpLocks 'proj'
New-Item -ItemType Directory -Force (Join-Path $tmpProj 'C--fake') | Out-Null
Set-Content -LiteralPath (Join-Path $tmpProj "C--fake\$tfId.jsonl") -Value '{"type":"assistant"}' -Encoding UTF8
$env:LAUNCHONE_PROJROOT = $tmpProj
$o5 = LOne @('-Id',$tfId,'-DryRun')
Check 'plain-alive (fresh transcript, no cmdline match) -> exit 3' ($loExit -eq 3 -and (Has $o5 'ALREADY LIVE by transcript'))
Remove-Item Env:\LAUNCHONE_PROJROOT -ErrorAction SilentlyContinue

$disId = 'cccccccc-3333-4333-8333-333333333333'
RegPS @('-Id',$disId,'-Runtime','claude','-Title','selftest-dis-title','-Task','selftest-dis-task','-Mode','interactive','-Disable')
LOne @('-Id',$disId,'-DryRun') | Out-Null
Check 'disabled registry entry -> exit 5' ($loExit -eq 5)
$o4 = LOne @('-Id',$disId,'-DryRun','-Force')
Check 'disabled -Force override previews' ($loExit -eq 0)
Check 'default prompt is built from the registry task (relaunch recipe)' (Has $o4 'selftest-dis-task')
Check 'registry mode drives the launch form (interactive)' (Has $o4 'claude/interactive')

# --- Invariant 7: the 6 side scripts route through launch_one - parse clean, no bare resume left.
# (A bare resume in a side script is exactly the unguarded path that duplicated the fleet on 07-12.
# NOTE: relaunch.ps1 regenerates fleet-root run_<first8>.ps1 bodies on a real launch; this check going
# RED after a real relaunch means those runners need re-wrapping.) ---
$wrapped = @(Get-ChildItem $fleet -Filter 'run_*.ps1' -ErrorAction SilentlyContinue | ForEach-Object Name) + @(Get-ChildItem $fleet -Filter 'restore_*.ps1' -ErrorAction SilentlyContinue | ForEach-Object Name)  # discovered, vacuous-safe
$badParse = @(); $badBare = @(); $badRoute = @()
foreach ($w in $wrapped) {
    $wp = Join-Path $here $w
    $tk = $null; $er = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($wp, [ref]$tk, [ref]$er)
    if ($er -and $er.Count) { $badParse += $w }
    $wc = Get-Content $wp -Raw -Encoding UTF8
    if ($wc -match 'claude\s+--resume') { $badBare += $w }
    if ($wc -notmatch 'launch_one\.ps1') { $badRoute += $w }
}
Check "all 6 side scripts parse clean$(if($badParse.Count){' ['+($badParse -join ',')+']'})" (-not $badParse.Count)
Check "no side script carries a bare resume$(if($badBare.Count){' ['+($badBare -join ',')+']'})" (-not $badBare.Count)
Check "all 6 side scripts route through launch_one$(if($badRoute.Count){' ['+($badRoute -join ',')+']'})" (-not $badRoute.Count)
$tk = $null; $er = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($launchone, [ref]$tk, [ref]$er)
Check 'launch_one.ps1 parses clean' (-not ($er -and $er.Count))
$tk = $null; $er = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($relaunch, [ref]$tk, [ref]$er)
Check 'relaunch.ps1 parses clean' (-not ($er -and $er.Count))

# --- Invariant 8: relaunch pass-lock - a second non-DryRun pass inside 10 min refuses; -DryRun exempt.
# The registry is swapped to an EMPTY temp dir so a non-DryRun pass exits at "no enabled registered
# agents" and can never launch anything; the lock lands in the temp FLEET_LOCKS_DIR. ---
$tmp2 = Join-Path $env:TEMP 'fleet_selftest_reg2'
if (Test-Path $tmp2) { Remove-Item $tmp2 -Recurse -Force }
New-Item -ItemType Directory -Force $tmp2 | Out-Null
$env:FLEET_REGISTRY_DIR = $tmp2
$passF = Join-Path $tmpLocks 'relaunch.pass.lock'
$p1 = RunRe @()
Check 'non-DryRun relaunch pass takes the pass-lock' (Test-Path $passF)
Check 'first pass ran normally (empty registry, nothing launched)' (Has $p1 'no enabled registered agents')
$p2 = RunRe @()
Check 'second non-DryRun pass inside 10 min REFUSES (exit 4)' ($reExit -eq 4)
Check 'refusal names the pass-lock' (Has $p2 'pass-lock')
$p3 = RunRe @('-DryRun')
Check '-DryRun is exempt from the pass-lock' ($reExit -eq 0 -and -not (Has $p3 'REFUSED'))
$env:FLEET_REGISTRY_DIR = $tmp

""
if ($fail -eq 0) { "ALL PASS" } else { "FAILURES: $fail" }
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $tmp2 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $tmpLocks -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Env:\FLEET_REGISTRY_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\FLEET_LOCKS_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\LAUNCHONE_TEST_CMDLINES -ErrorAction SilentlyContinue
exit $fail
