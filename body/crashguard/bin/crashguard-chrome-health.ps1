<#
crashguard-chrome-health.ps1 — silent Chrome health watcher + optimizer (2026-06-06)

Goal: make Chrome run smoothly on this heavily-loaded, marginal-NVIDIA box. It:
  1. Logs Chrome health every tick (proc count, total RAM, # top-level windows,
     # NOT-responding windows = freeze episodes) to logs/chrome-health.jsonl so we can
     MEASURE whether freezes actually stop.
  2. Keeps the UI-critical Chrome processes (browser + GPU process) at AboveNormal
     priority so production / observers / agents can't starve Chrome's UI thread.
     (New renderers spawn at Normal; we re-apply each tick. Renderers are left alone.)
  3. Regression guard: detects if Chrome's GPU process drifts back onto the marginal
     NVIDIA dGPU (it should stay on the Intel Arc). Logs a WARN if so.
  4. 100% read-mostly + reversible. NEVER kills Chrome (user may be working). No admin,
     no visible window, no notification card, does not touch protected production.

Launched hidden by CrashGuard (crashguard-launch-all.ps1). Self-heal respawns it.
#>
param([int]$IntervalSec = 45, [switch]$Once)

$ErrorActionPreference = 'Continue'
$root   = "$env:USERPROFILE\CrashGuard"
$log    = Join-Path $root 'logs\chrome-health.jsonl'
$events = Join-Path $root 'logs\chrome-health-events.log'
$hb     = Join-Path $root 'state\chrome-health.heartbeat'

function Get-NvidiaChromePids {
  # best-effort: which PIDs are using the NVIDIA dGPU (regression check). Empty if no nvidia-smi.
  try {
    $smi = (Get-Command nvidia-smi -EA SilentlyContinue).Source
    if (-not $smi) { return @() }
    $out = & $smi --query-compute-apps=pid,process_name --format=csv,noheader 2>$null
    return @($out | Where-Object { $_ -match 'chrome' } | ForEach-Object { ($_ -split ',')[0].Trim() })
  } catch { return @() }
}

function Tick {
  $ch = Get-Process chrome -ErrorAction SilentlyContinue
  if (-not $ch) { return }  # Chrome not running; nothing to do
  $wins = @($ch | Where-Object { $_.MainWindowHandle -ne 0 })
  $hung = @($wins | Where-Object { -not $_.Responding })

  # priority maintenance on UI-critical procs only (browser = no --type=, gpu-process)
  $boost = 0
  try {
    $meta = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -EA SilentlyContinue
    $crit = $meta | Where-Object { $_.CommandLine -notmatch '--type=' -or $_.CommandLine -match '--type=gpu-process' }
    foreach ($m in $crit) {
      try { $p = Get-Process -Id $m.ProcessId -EA Stop; if ($p.PriorityClass -eq 'Normal') { $p.PriorityClass = 'AboveNormal'; $boost++ } } catch {}
    }
  } catch {}

  $nvPids = Get-NvidiaChromePids
  $rec = [ordered]@{
    ts = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    procs = $ch.Count
    mem_gb = [math]::Round(($ch | Measure-Object WS -Sum).Sum / 1GB, 2)
    windows = $wins.Count
    hung = $hung.Count
    reprioritized = $boost
    chrome_on_nvidia = $nvPids.Count
  }
  ($rec | ConvertTo-Json -Compress) | Add-Content -Path $log -Encoding utf8 -EA SilentlyContinue
  ($rec | ConvertTo-Json -Compress) | Set-Content -Path $hb -Encoding utf8 -EA SilentlyContinue
  if ($hung.Count -gt 0) {
    ('{0} FREEZE chrome not-responding windows={1} (procs={2}, {3}GB)' -f (Get-Date -Format 's'), $hung.Count, $ch.Count, $rec.mem_gb) | Add-Content -Path $events -Encoding utf8 -EA SilentlyContinue
  }
  if ($nvPids.Count -gt 0) {
    ('{0} REGRESSION chrome using NVIDIA dGPU pids={1} (should be Intel Arc)' -f (Get-Date -Format 's'), ($nvPids -join ',')) | Add-Content -Path $events -Encoding utf8 -EA SilentlyContinue
  }
}

if ($Once) { Tick; return }
while ($true) { try { Tick } catch {}; Start-Sleep -Seconds $IntervalSec }
