# Log rotator — keeps logs/*.log < 50 MB, telemetry/spike-log.jsonl < 5 MB,
# telemetry/ring.jsonl < 5 MB. Renames old to *.bak.yyyyMMddHHmmss.

[CmdletBinding()]
param([int]$IntervalSec = 60, [switch]$Once)

$ErrorActionPreference = 'Continue'
. "$env:USERPROFILE\CrashGuard\bin\crashguard-common.ps1"

$rules = @(
    @{ path="$env:USERPROFILE\CrashGuard\logs";       pattern='*.log';   max_mb=50; keep_last_lines=0 }
    @{ path="$env:USERPROFILE\CrashGuard\logs";       pattern='pulse.log'; max_mb=2; keep_last_lines=10000 }
    @{ path="$env:USERPROFILE\CrashGuard\telemetry";  pattern='spike-log.jsonl'; max_mb=5; keep_last_lines=2000 }
    @{ path="$env:USERPROFILE\CrashGuard\telemetry";  pattern='ring.jsonl'; max_mb=5; keep_last_lines=600 }
)

CG-Log 'log-rotate' 'info' "Log rotator starting PID=$PID interval=${IntervalSec}s"

while ($true) {
    try {
        $rotated = 0
        foreach ($r in $rules) {
            $files = Get-ChildItem -Path $r.path -Filter $r.pattern -ErrorAction SilentlyContinue
            foreach ($f in $files) {
                $mb = $f.Length / 1MB
                if ($mb -gt $r.max_mb) {
                    if ($r.keep_last_lines -gt 0) {
                        $tail = Get-Content $f.FullName -Tail $r.keep_last_lines -ErrorAction SilentlyContinue
                        $bak = $f.FullName + '.bak.' + (Get-Date -Format 'yyyyMMddHHmmss')
                        Move-Item $f.FullName $bak -Force -ErrorAction SilentlyContinue
                        Set-Content $f.FullName -Value $tail -Encoding utf8 -Force
                        CG-Log 'log-rotate' 'info' ('Rotated ' + $f.Name + ' (' + [int]$mb + ' MB -> last ' + $r.keep_last_lines + ' lines kept)')
                    } else {
                        $bak = $f.FullName + '.bak.' + (Get-Date -Format 'yyyyMMddHHmmss')
                        Move-Item $f.FullName $bak -Force -ErrorAction SilentlyContinue
                        New-Item -Path $f.FullName -ItemType File -Force | Out-Null
                        CG-Log 'log-rotate' 'info' ('Rotated ' + $f.Name + ' (' + [int]$mb + ' MB -> bak; new empty file)')
                    }
                    $rotated++
                }
            }
        }
        # Also delete .bak files older than 7 days
        Get-ChildItem -Path "$env:USERPROFILE\CrashGuard\logs","$env:USERPROFILE\CrashGuard\telemetry" -Filter '*.bak.*' -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

        CG-Heartbeat 'log-rotate' @{ rotated_this_iter = $rotated }
    } catch {
        CG-Log 'log-rotate' 'error' ('Iter exception: ' + $_)
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSec
}
