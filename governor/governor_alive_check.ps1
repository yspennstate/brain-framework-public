# governor_alive_check.ps1 - watchdog for the watchdog (runs every 30 min, hidden).
# If the governor loop is dead or its heartbeat is stale, relaunch it via the SW_HIDE VBS.
# Deliberately minimal: one read, one liveness check, at most one relaunch. Never kills.

$ErrorActionPreference = 'SilentlyContinue'
$StateDir  = "$env:USERPROFILE\.claude\compute"
$StateFile = Join-Path $StateDir 'compute_state.json'
$PidFile   = Join-Path $StateDir 'governor.pid'
$LogFile   = Join-Path $StateDir 'governor.log'
$Launcher  = "$env:USERPROFILE\brain-repo\12_cognitive_architecture\compute_governor\launch_compute_governor.vbs"

function Log([string]$m) {
    Add-Content -Path $LogFile -Value ("{0}  [alive-check] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8
}

$fresh = $false
if (Test-Path $StateFile) {
    $age = ((Get-Date) - (Get-Item $StateFile).LastWriteTime).TotalSeconds
    if ($age -lt 180) { $fresh = $true }
}

$alive = $false
if (Test-Path $PidFile) {
    $gpid = [int](Get-Content $PidFile | Select-Object -First 1)
    if ($gpid) {
        $p = Get-Process -Id $gpid -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -match 'powershell') { $alive = $true }
    }
}

if ($fresh -and $alive) { return }

Log ("governor unhealthy (fresh={0} alive={1}); relaunching" -f $fresh, $alive)
Start-Process -FilePath 'wscript.exe' -ArgumentList @('//B', $Launcher) -WindowStyle Hidden
