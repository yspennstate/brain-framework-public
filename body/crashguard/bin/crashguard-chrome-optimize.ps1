<#
crashguard-chrome-optimize.ps1 — ACTIVE Chrome optimizer (2026-06-06)
NOT a watcher/logger: a one-shot that APPLIES Chrome optimizations, then exits.
Run on demand and once at boot (via crashguard-launch-all.ps1).

User problem: Chrome-only freezes during SCROLL/VIDEO (GPU process stable -> compositing/
video JANK on the Intel Arc, not a crash). Definitive cure = Intel Arc driver update
(needs reboot). No-restart levers this applies:

  1. GpuPreference = Intel Arc (1) for chrome+edge  -> keeps Chrome OFF the marginal NVIDIA
     (regression guard; re-applies if it ever drifts back to 2).
  2. Launch flags on Chrome shortcuts:  --use-angle=gl  (swap the D3D11 compositing path
     that janks on Arc -> ANGLE/OpenGL) + --disable-accelerated-video-decode (decode video
     on the idle CPU cores instead of the Arc). UNAMBIGUOUS command-line flags (no fragile
     Local-State @index edits, no profile-corruption risk). Applies on next Chrome launch.
  3. Priority: Chrome browser + GPU process -> AboveNormal (anti-starvation on this loaded box).

Fully reversible:  -Revert  removes the flags (and leaves GpuPreference/priority, which are
harmless). Safe, non-admin, never kills Chrome, never touches protected production.
#>
param([switch]$Revert)
$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\CrashGuard\logs\chrome-optimize.log"
function Note($m){ $l = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m); $l; Add-Content -Path $log -Value $l -Encoding utf8 -EA SilentlyContinue }
Note "=== chrome-optimize start (Revert=$Revert) ==="

$FLAGS = @('--use-angle=gl','--disable-accelerated-video-decode')

# 1) GpuPreference -> Intel Arc (1) for chrome + edge (regression guard)
$gp = 'HKCU:\SOFTWARE\Microsoft\DirectX\UserGpuPreferences'
foreach ($exe in @('C:\Program Files\Google\Chrome\Application\chrome.exe','C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')) {
  try {
    $cur = (Get-ItemProperty -Path $gp -Name $exe -EA SilentlyContinue).$exe
    if ($cur -ne 'GpuPreference=1;') { Set-ItemProperty -Path $gp -Name $exe -Value 'GpuPreference=1;'; Note "GpuPreference set Intel Arc: $(Split-Path $exe -Leaf) (was '$cur')" }
  } catch {}
}

# 2) Chrome launch flags on user-writable shortcuts (idempotent / revertible)
$sh = New-Object -ComObject WScript.Shell
$lnkDirs = @(
  "$env:APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar",
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
  "$env:USERPROFILE\Desktop"
)
$touched = 0
foreach ($d in $lnkDirs) {
  if (-not (Test-Path $d)) { continue }
  Get-ChildItem $d -Filter *.lnk -EA SilentlyContinue | ForEach-Object {
    try {
      $s = $sh.CreateShortcut($_.FullName)
      if ($s.TargetPath -notmatch 'chrome\.exe$') { return }
      $args = $s.Arguments
      foreach ($f in $FLAGS) { $args = ($args -replace [Regex]::Escape($f), '').Trim() }  # strip existing
      if (-not $Revert) { $args = (($args + ' ' + ($FLAGS -join ' ')).Trim()) }
      $args = ($args -replace '\s+', ' ').Trim()
      if ($s.Arguments -ne $args) { $s.Arguments = $args; $s.Save(); $touched++; Note ("shortcut {0} -> args '{1}'" -f $_.Name, $args) }
    } catch { Note "shortcut $($_.Name): $($_.Exception.Message)" }
  }
}
Note "shortcuts updated: $touched"

# 3) Priority: Chrome browser + GPU process -> AboveNormal
$b = 0
try {
  $meta = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -EA SilentlyContinue
  $crit = $meta | Where-Object { $_.CommandLine -notmatch '--type=' -or $_.CommandLine -match '--type=gpu-process' }
  foreach ($m in $crit) { try { $p = Get-Process -Id $m.ProcessId -EA Stop; if ($p.PriorityClass -eq 'Normal') { $p.PriorityClass = 'AboveNormal'; $b++ } } catch {} }
} catch {}
Note "priority boosted: $b chrome proc(s)"
Note "=== done. NOTE: launch-flag changes apply on the NEXT Chrome launch from a shortcut (taskbar/Start/desktop). ==="
