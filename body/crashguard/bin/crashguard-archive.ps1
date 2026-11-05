# Archive — compresses old spike-log/ring/forensic data into telemetry/archive/.
# Runs daily (1-hour cycle).

[CmdletBinding()]
param([int]$IntervalSec = 3600, [int]$AgeDaysSpike = 7, [int]$AgeDaysForensic = 3, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$archiveDir = Join-Path $CG_TELEMETRY 'archive'
if (-not (Test-Path $archiveDir)) { New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null }

CG-Log 'archive' 'info' ('Archive starting PID=' + $PID + ' interval=' + $IntervalSec + 's')

while ($true) {
    try {
        $archived = 0

        # 1. Old spike-log .bak files → compress
        Get-ChildItem (Join-Path $CG_TELEMETRY 'spike-log.jsonl.bak.*') -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$AgeDaysSpike) -and $_.Extension -ne '.zip' } | ForEach-Object {
            $zip = Join-Path $archiveDir ($_.Name + '.zip')
            try {
                Compress-Archive -Path $_.FullName -DestinationPath $zip -Force -ErrorAction Stop
                Remove-Item $_.FullName -Force
                $archived++
            } catch {}
        }

        # 2. Old forensic dump dirs → tar+zip
        Get-ChildItem (Join-Path $CG_TELEMETRY 'forensic_*') -Directory -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$AgeDaysForensic) } | ForEach-Object {
            $zip = Join-Path $archiveDir ($_.Name + '.zip')
            try {
                Compress-Archive -Path ($_.FullName + '\*') -DestinationPath $zip -Force -ErrorAction Stop
                Remove-Item $_.FullName -Recurse -Force
                $archived++
            } catch {}
        }

        if ($archived -gt 0) {
            CG-Log 'archive' 'info' ('Archived ' + $archived + ' items')
        }
        CG-Heartbeat 'archive' @{ archived_this_iter = $archived }
    } catch {
        CG-Log 'archive' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
