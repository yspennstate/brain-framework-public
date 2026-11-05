# Observer Integration Guide

CrashGuard exposes its current tier to *all* other observers via two well-known paths:

| Path                                   | Format     | Refresh |
|----------------------------------------|------------|---------|
| `C:\ProgramData\CrashGuard\tier.txt`   | plain text | ~3s     |
| `C:\ProgramData\CrashGuard\state.json` | JSON       | ~3s     |

## Tier values and recommended observer behavior

| Tier      | Recommended observer behavior                                |
|-----------|--------------------------------------------------------------|
| `green`   | Run normally.                                                 |
| `yellow`  | Reduce non-essential work; defer heavy GPU spins.            |
| `orange`  | Pause new GPU work; finish current job and idle.             |
| `red`     | Stop all new jobs; current GPU job should be killed.         |
| `critical`| Halt entirely; do not relaunch until tier returns to ≤ yellow. |

## Drop-in PowerShell snippet for any observer

```powershell
function Get-CrashGuardTier {
    $p = 'C:\ProgramData\CrashGuard\tier.txt'
    if (Test-Path $p) {
        (Get-Content $p -Raw).Trim()
    } else {
        'unknown'
    }
}

# At the top of each iteration:
$tier = Get-CrashGuardTier
if ($tier -in @('red','critical')) {
    Write-Output "CrashGuard tier=$tier — skipping iteration"
    Start-Sleep -Seconds 30
    continue
}
if ($tier -in @('orange')) {
    Start-Sleep -Seconds 10  # gentler cadence
}
```

## Touch points for an example compute stack

- `restart_all_observers.ps1` should set `WindowStyle Hidden` and read the tier file before launching each observer. If tier ≥ orange, defer until back to green.
- Lean compilation jobs (lake, lean) should check tier and back off — they hit GPU via NVIDIA Optimus.
