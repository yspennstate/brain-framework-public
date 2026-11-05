# Export current CrashGuard state to a well-known file so other observers can read it.
# Other tools should check C:\ProgramData\CrashGuard\tier.txt before launching new GPU work.

[CmdletBinding()]
param([int]$IntervalSec = 3)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$exportDir = 'C:\ProgramData\CrashGuard'
if (-not (Test-Path $exportDir)) { New-Item -ItemType Directory -Force -Path $exportDir | Out-Null }

while ($true) {
    try {
        $hb = CG-LoadJson (Join-Path $CG_STATE 'sentinel.heartbeat')
        if ($hb) {
            $tier = $hb.tier
            Set-Content -Path (Join-Path $exportDir 'tier.txt') -Value $tier -Encoding utf8 -Force
            $payload = [PSCustomObject]@{
                tier      = $tier
                tier_index = $hb.tier_index
                updated   = $hb.timestamp
                gpu_temp_c = $hb.last_sample.gpu_temp_c
                gpu_util_pct = $hb.last_sample.gpu_util_pct
                gpu_mem_used_pct = $hb.last_sample.gpu_mem_used_pct
                cpu_util_pct = $hb.last_sample.cpu_util_pct
                ram_used_pct = $hb.last_sample.ram_used_pct
                advice    = switch ($tier) {
                    'green'    { 'normal' }
                    'yellow'   { 'reduce-non-essential' }
                    'orange'   { 'pause-new-gpu-work' }
                    'red'      { 'stop-new-jobs' }
                    'critical' { 'halt' }
                    default { 'unknown' }
                }
            }
            $payload | ConvertTo-Json | Set-Content -Path (Join-Path $exportDir 'state.json') -Encoding utf8 -Force
        }
        CG-Heartbeat 'export' @{ exported_to = $exportDir }
    } catch {
        CG-Log 'export' 'error' "Export iter exception: $_"
    }
    Start-Sleep -Seconds $IntervalSec
}
