# HTML publisher — regenerates status.html every 30s so the browser auto-refresh sees fresh data.

[CmdletBinding()]
param([int]$IntervalSec = 30, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

CG-Log 'html-publisher' 'info' ('HTML publisher starting PID=' + $PID + ' interval=' + $IntervalSec + 's')

while ($true) {
    try {
        & (Join-Path $CG_BIN 'crashguard-html-status.ps1') | Out-Null
        CG-Heartbeat 'html-publisher' @{ generated = (Get-Date).ToString('o') }
    } catch {
        CG-Log 'html-publisher' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
