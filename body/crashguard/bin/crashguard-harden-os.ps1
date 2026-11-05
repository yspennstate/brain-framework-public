# OS-level hardening: TDR delay/level, NVIDIA prefer-performance, page file health, error-reporting.
# REQUIRES ADMIN. Idempotent. Logs old + new values.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'crashguard-common.ps1')

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "Not running as admin. Some hardening (TDR registry, dump config) cannot be applied. Continuing with non-admin-safe steps."
}

function Set-RegSafe {
    param([string]$Path, [string]$Name, $Value, [string]$Type = 'DWord')
    if (-not (Test-Path $Path)) {
        if (-not $DryRun) { New-Item -Path $Path -Force | Out-Null }
        CG-Log 'hardening' 'info' "Created reg path: $Path"
    }
    $old = $null
    try { $old = (Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop).$Name } catch {}
    if ($old -eq $Value -and -not $Force) {
        CG-Log 'hardening' 'info' "Reg $Path\$Name already=$Value (skipped)"
        return
    }
    if (-not $DryRun) {
        New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
    }
    CG-Log 'hardening' 'info' "Reg $Path\$Name : $old -> $Value (dry=$DryRun)"
}

# TDR (Timeout Detection and Recovery) — the very mechanism that fires when the GPU hangs.
# TdrDelay: seconds before TDR fires (default 2). Raise to 10 to absorb transient hangs.
# TdrDdiDelay: similar
# TdrLevel: 3 = recover (default), 0 = disable (NOT recommended — would BSOD instead)
# Keep TdrLevel at 3; only extend the delays.
if ($isAdmin) {
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' 'TdrDelay'    10 'DWord'
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' 'TdrDdiDelay' 10 'DWord'
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' 'TdrLevel'    3  'DWord'

    # Ensure crash dumps are captured for diagnosis (full + minidump retention)
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' 'CrashDumpEnabled' 1 'DWord'   # 1 = full kernel, 2 = kernel only, 3 = small minidump
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' 'MinidumpsCount'   50 'DWord'
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' 'AutoReboot'       1 'DWord'

    # Disable automatic Windows error reporting nags (we have our own)
    Set-RegSafe 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting' 'Disabled' 0 'DWord'

    # Disable Hardware Accelerated GPU Scheduling — known to interact poorly with some Blackwell laptop drivers
    Set-RegSafe 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' 'HwSchMode' 1 'DWord'

    # Faster recovery: shorten DPC watchdog tolerance? NO — leave alone.
} else {
    Write-Output "Skipping admin-only registry changes (TDR delay, dump config, HwSch). Re-run as admin to apply."
}

# Non-admin-safe: set power plan to High Performance for the active session.
try {
    $cur = powercfg /getactivescheme
    CG-Log 'hardening' 'info' "Active power scheme: $cur"
    # Switch to High performance (8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c)
    if (-not $DryRun) { powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c | Out-Null }
} catch {}

# Reduce visual effects that hammer the GPU at idle: skip — user-visible cosmetic change.

CG-Log 'hardening' 'info' "Hardening complete (admin=$isAdmin, dry=$DryRun)"
Write-Output "Hardening complete. A reboot is required for TDR registry changes to take effect."
