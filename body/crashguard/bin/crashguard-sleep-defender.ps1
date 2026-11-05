# Sleep-defender — uses SetThreadExecutionState to keep system + display awake.

[CmdletBinding()]
param([int]$RefreshSec = 30)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

if (-not ('CGSL.Pwr' -as [type])) {
    try {
        Add-Type -MemberDefinition '[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);' -Name Pwr -Namespace CGSL -ErrorAction Stop
    } catch {
        CG-Log 'sleep-defender' 'error' ('Add-Type failed: ' + $_)
    }
}

$FLAGS = [uint32]2147483715  # 0x80000043 = ES_CONTINUOUS|ES_SYSTEM|ES_DISPLAY|ES_AWAYMODE

CG-Log 'sleep-defender' 'info' ('Sleep-defender starting PID=' + $PID + ' refresh=' + $RefreshSec + 's flags=0x' + $FLAGS.ToString('X'))

while ($true) {
    try {
        $prev = [CGSL.Pwr]::SetThreadExecutionState($FLAGS)
        CG-Heartbeat 'sleep-defender' @{ flags = '0x' + $FLAGS.ToString('X'); prev = '0x' + $prev.ToString('X') }
    } catch {
        CG-Log 'sleep-defender' 'error' ('Iter exception: ' + $_)
    }
    Start-Sleep -Seconds $RefreshSec
}
