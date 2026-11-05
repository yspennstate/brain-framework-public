# Generate a static HTML status page at $env:USERPROFILE\CrashGuard\status.html.
# Open via file:///<HOME>/CrashGuard/status.html in any browser.

. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$sessionStart = Get-Date '2026-05-27 15:00:00'
$now = Get-Date

# Collect data
$services = @('sentinel','watchdog','process-watcher','export','driver-health','pcie-watch','ring-recorder','dedup-watcher','power-watch','crisis-correlator','power-guard','wudfrd-watch','hid-watch','update-watcher','sleep-defender','self-heal','log-rotate','trend-analyzer','recovery','pulse','watchdog2','gpu-keepalive')
$rows = foreach ($svc in $services) {
    $hb = Get-Content "$env:USERPROFILE\CrashGuard\state\$svc.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
    if ($hb) {
        $age = [int](($now - (Get-Date $hb.timestamp)).TotalSeconds)
        $color = if ($age -lt 60) { '#0a0' } elseif ($age -lt 360) { '#aa0' } else { '#a00' }
        '<tr><td>' + $svc + '</td><td>' + $hb.pid + '</td><td style="color:' + $color + '">' + $age + 's</td></tr>'
    } else {
        '<tr><td>' + $svc + '</td><td>-</td><td style="color:#f00">MISSING</td></tr>'
    }
}

$snt = Get-Content "$env:USERPROFILE\CrashGuard\state\sentinel.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
$tier = if ($snt) { $snt.tier } else { '?' }
$tierColor = switch ($tier) { 'green' {'#0a0'}; 'yellow' {'#cc0'}; 'orange' {'#f80'}; 'red' {'#f00'}; 'critical' {'#900'}; default {'#888'} }

$bsods = @(Get-WinEvent -FilterHashtable @{LogName='System';ID=1001;StartTime=$sessionStart} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'bugcheck was:' })
$whea = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue)
$lastWhea = $whea | Sort-Object TimeCreated -Descending | Select-Object -First 1
$minSinceWhea = if ($lastWhea) { [int](($now - $lastWhea.TimeCreated).TotalMinutes) } else { 99999 }

$tests = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\test-counter.txt" -ErrorAction SilentlyContinue)
$ticks = [int](Get-Content "$env:USERPROFILE\CrashGuard\state\tick-counter.txt" -ErrorAction SilentlyContinue)

# Idle-TDR (RTD3) protection status — the 2026-06-01 fix (gpu-keepalive holds the dGPU in D0).
$ka = Get-Content "$env:USERPROFILE\CrashGuard\state\gpu-keepalive.heartbeat" -ErrorAction SilentlyContinue | ConvertFrom-Json
$kaAge = if ($ka -and $ka.timestamp) { [int](($now - (Get-Date $ka.timestamp)).TotalSeconds) } else { 99999 }
$kaAlive = ($ka -and $kaAge -lt 60)
$kaEngine = if ($ka -and $ka.engine) { $ka.engine } else { '-' }
$kaUptimeMin = if ($ka -and $ka.ticks -and $ka.interval_sec) { [int]($ka.ticks * $ka.interval_sec / 60) } else { 0 }
$gpuMem = -1
try { $nvp = (Get-Command nvidia-smi -EA Stop).Source; $rawm = (& $nvp --query-gpu=memory.used --format=csv,noheader,nounits | Select-Object -First 1); $gpuMem = [int]($rawm.Trim()) } catch {}
$resident = ($gpuMem -gt 0)
$aer60 = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; Id=17; StartTime=$now.AddMinutes(-60)} -ErrorAction SilentlyContinue).Count
$storm = $false; try { $sj = Get-Content "$env:USERPROFILE\CrashGuard\state\pcie-aer-storm.json" -EA Stop | ConvertFrom-Json; $storm = [bool]$sj.active } catch {}
$protActive = ($kaAlive -and $resident -and -not $storm)
$protColor = if ($protActive) { '#0a0' } else { '#f00' }
$protText  = if ($protActive) { 'ACTIVE' } else { 'CHECK' }

$spikeTail = (Get-Content "$env:USERPROFILE\CrashGuard\telemetry\spike-log.jsonl" -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object {
    try { $j = $_ | ConvertFrom-Json; '<li>' + $j.ts + ' <code>' + $j.kind + '</code></li>' } catch {}
}) -join "`n"

$html = @"
<!doctype html>
<html><head>
<title>CrashGuard Status</title>
<meta http-equiv="refresh" content="10">
<style>
body { font-family: -apple-system, sans-serif; background:#111; color:#ddd; padding: 20px; }
h1, h2 { color: #fff; }
.tier { font-size: 2em; font-weight: bold; }
table { border-collapse: collapse; margin: 10px 0; }
td { padding: 4px 12px; border-bottom: 1px solid #333; }
code { background:#222; padding: 2px 6px; border-radius: 3px; }
.grid { display: grid; grid-template-columns: 200px auto; gap: 8px; }
.metric { font-weight: bold; color: #0af; }
</style>
</head><body>
<h1>CrashGuard Status</h1>
<p>Generated: $now  (auto-refresh 10s)</p>

<h2>Idle-TDR (0x116) Protection</h2>
<div class="tier" style="color: $protColor">$protText</div>
<div class="grid">
<span>GPU keepalive</span><span class="metric">$(if($kaAlive){'alive'}else{'DOWN'}) &mdash; engine=$kaEngine, ${kaUptimeMin}min up, hb ${kaAge}s</span>
<span>dGPU held in D0</span><span class="metric">$(if($resident){'yes'}else{'NO'}) &mdash; ${gpuMem}MB resident</span>
<span>PCIe AER (60min)</span><span class="metric">$aer60$(if($storm){' &mdash; STORM!'}else{''})</span>
</div>
<p style="color:#888;font-size:0.85em">Root cause: idle dGPU RTD3/GC6 D3cold&rarr;D0 wake failure on driver 595.97. Fix: hold a CUDA context so the dGPU never powers down. See docs/rootcause-2026-06-01-idle-rtd3-tdr.md</p>

<h2>Sentinel</h2>
<div class="tier" style="color: $tierColor">$tier</div>
<div class="grid">
<span>iters</span><span class="metric">$($snt.iterations)</span>
<span>CPU</span><span class="metric">$($snt.last_sample.cpu_util_pct)%</span>
<span>RAM</span><span class="metric">$($snt.last_sample.ram_used_pct)%</span>
<span>GPU temp</span><span class="metric">$($snt.last_sample.gpu_temp_c)C</span>
<span>GPU util</span><span class="metric">$($snt.last_sample.gpu_util_pct)%</span>
</div>

<h2>Crash counters since 2026-05-27 15:00</h2>
<div class="grid">
<span>BSODs</span><span class="metric">$($bsods.Count)</span>
<span>WHEA today</span><span class="metric">$($whea.Count)</span>
<span>Min since last WHEA</span><span class="metric">$minSinceWhea</span>
<span>Watchdog ticks</span><span class="metric">$ticks</span>
<span>Cumulative tests</span><span class="metric">$tests</span>
</div>

<h2>Services ($($services.Count))</h2>
<table>
<tr><th>Service</th><th>PID</th><th>Heartbeat age</th></tr>
$($rows -join "`n")
</table>

<h2>Last 10 spike-log entries</h2>
<ul>
$spikeTail
</ul>
</body></html>
"@

$out = "$env:USERPROFILE\CrashGuard\status.html"
$html | Out-File -FilePath $out -Encoding utf8 -Force
Write-Output ('Status page: file:///' + ($out -replace '\\','/'))
