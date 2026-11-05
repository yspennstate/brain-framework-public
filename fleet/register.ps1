# register.ps1 - record/update an agent's recovery info so relaunch.ps1 can bring it back after a crash
# WITHOUT guessing from transcripts. This is HOW AGENTS STORE THEIR STATE for recovery.
# Every autonomous agent should run this once at startup, and again whenever its plan/next-step changes.
# Usage (from inside an agent's session):
#   powershell -File $env:USERPROFILE\.claude\fleet\register.ps1 -Id <session-id> -Runtime claude `
#       -Title "brain reflex-firing" -Task "keep working on the graph reflex-firing overhaul" `
#       -Checkpoint "finished firing layer; next: wire conscious/subconscious bridge"
param(
  [Parameter(Mandatory)][string]$Id,
  [ValidateSet('claude','codex')][string]$Runtime = 'claude',
  [string]$Title = '',
  [string]$Task = '',            # the crash-resume prompt: what this agent should keep doing
  [string]$Cwd = "$env:USERPROFILE",   # the agent's session cwd - NOT (Get-Location) which captures the caller's dir and corrupts the registry (07-11 bug)
  [string]$Model = '',
  [string]$Checkpoint = '',      # last-good on-disk state + the very next step
  [ValidateSet('interactive','gentle')][string]$Mode = '',  # HOW to bring it back: 'interactive' = persistent TUI, pre-filled, owner presses Enter (no injection); 'gentle' = headless -p, auto-runs one turn. Default gentle.
  [switch]$Disable,              # turn this agent OFF - relaunch skips it and it STAYS off until -Enable
  [switch]$Enable                # turn a disabled agent back on
)
$ErrorActionPreference = 'Continue'
# test seam (mirrors registry_refresh.py's FLEET_REGISTRY_DIR): point tests at a temp registry; unset = real.
$reg = if ($env:FLEET_REGISTRY_DIR) { $env:FLEET_REGISTRY_DIR } else { "$env:USERPROFILE\.claude\fleet\registry" }
New-Item -ItemType Directory -Force $reg | Out-Null
$f = Join-Path $reg "$Id.json"
# preserve existing fields if only some are passed
$prev = @{}
if (Test-Path $f) { try { (Get-Content $f -Raw | ConvertFrom-Json).psobject.properties | ForEach-Object { $prev[$_.Name] = $_.Value } } catch {} }
function pick($new, $key) { if ($new) { return $new } elseif ($prev.ContainsKey($key)) { return $prev[$key] } else { return '' } }
# mode: explicit -Mode wins; else preserve prior; else default 'gentle' (safe, non-injecting).
$modeVal = if ($Mode) { $Mode } elseif ($prev.ContainsKey('mode') -and $prev['mode']) { [string]$prev['mode'] } else { 'gentle' }
# disabled: -Disable/-Enable win; else PRESERVE the prior flag so a re-register never silently turns an
# off agent back on (07-11: the $obj dropped 'disabled', so any re-register un-disabled it). "Off stays off."
$disabledVal = if ($Disable) { $true } elseif ($Enable) { $false } elseif ($prev.ContainsKey('disabled')) { [bool]$prev['disabled'] } else { $false }
$obj = [ordered]@{
  id         = $Id
  runtime    = if ($Runtime) { $Runtime } else { pick '' 'runtime' }
  title      = pick $Title 'title'
  task       = pick $Task 'task'
  cwd        = pick $Cwd 'cwd'
  model      = pick $Model 'model'
  mode       = $modeVal
  checkpoint = pick $Checkpoint 'checkpoint'
  disabled   = $disabledVal
  updated    = (Get-Date).ToString('s')
}
# BOM-ful UTF-8 (Set-Content -Encoding UTF8 on PS5.1) is INTENTIONAL: PS bare Get-Content mojibakes no-BOM
# non-ASCII into the resume prompt; python readers use utf-8-sig. Do NOT switch to BOM-less. (07-11)
$obj | ConvertTo-Json | Set-Content -LiteralPath $f -Encoding UTF8
"registered $Id [$modeVal$(if($disabledVal){' DISABLED'})] -> $f"
