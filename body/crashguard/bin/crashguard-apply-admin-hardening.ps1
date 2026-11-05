<#
CrashGuard admin-only hardening — RUN ELEVATED (Run as Administrator).

CrashGuard's services run as the normal user and cannot write HKLM, so these one-time
machine settings must be applied by hand. None of this touches protected production, fires a
notification card, or restarts any production service. It is safe to run while production is live;
the dump-config and AutoReboot changes only affect what happens *after* a future crash.

What it does:
  1. Crash forensics: CrashDumpEnabled 1 -> 7 (Automatic). Today it is 1 (Complete) which writes
     ONLY C:\Windows\MEMORY.DMP and overwrites it every crash, so C:\Windows\Minidump is empty and
     we keep zero per-crash history. 7 writes a kernel dump to MEMORY.DMP *and* a small minidump
     per crash to Minidump\, retaining MinidumpsCount of them. We bump MinidumpsCount 5 -> 50.
  2. Unattended recovery: AutoReboot 0 -> 1. Today's crash left the rig dead ~18 min until a manual
     reboot. AutoReboot=1 brings it (and the production services) back in ~1 min after a bugcheck.
  3. Lowers TdrDelay/TdrDdiDelay 300 -> 8 s. 300 s is dangerously high: ANY GPU stall (e.g. a
     browser GPU hiccup) freezes the WHOLE desktop for up to 5 minutes before Windows recovers,
     because the compositor shares the GPU. 8 s lets the driver reset and the screen recover in
     seconds instead. (TdrLevel stays 3 = recover.) The idle-loss 0x116 is handled separately by
     the gpu-keepalive; this change is purely to stop multi-minute freezes.

Optional (off by default): -DisableDriverRtd3 also forces the NVIDIA driver to keep the dGPU
powered (registry RMHdcpKeyglobZero/EnableRtd3=0 style). The userspace GPU keepalive already
prevents the idle power-down, so this is only a belt-and-suspenders for if the keepalive is ever
not running. It raises idle GPU power a little. Leave it off unless crashes persist.
#>
[CmdletBinding()]
param(
    [switch]$DisableDriverRtd3,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\CrashGuard\logs\admin-hardening.log"
function Note($m){ $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m); $line; Add-Content -Path $log -Value $line -Encoding utf8 -EA SilentlyContinue }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Note "REFUSING: not elevated. Right-click PowerShell -> Run as Administrator, then re-run this script."
    return
}
Note "=== CrashGuard admin hardening start (WhatIfOnly=$WhatIfOnly DisableDriverRtd3=$DisableDriverRtd3) ==="

function Set-Reg($Path,$Name,$Value){
    $old = $null
    try { $old = (Get-ItemProperty -Path $Path -Name $Name -EA Stop).$Name } catch {}
    if ($old -eq $Value) { Note ("  {0}\{1} already = {2} (skip)" -f $Path,$Name,$Value); return }
    if ($WhatIfOnly) { Note ("  WHATIF {0}\{1} : {2} -> {3}" -f $Path,$Name,$old,$Value); return }
    if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
    New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType DWord -Force | Out-Null
    Note ("  SET {0}\{1} : {2} -> {3}" -f $Path,$Name,$old,$Value)
}

$CC = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
# 1) Forensics: Automatic dump (kernel dump + retained per-crash minidumps)
Set-Reg $CC 'CrashDumpEnabled' 7
Set-Reg $CC 'MinidumpsCount'   50
# 2) Unattended recovery
Set-Reg $CC 'AutoReboot'       1

# 3) Lower TdrDelay so a GPU stall recovers in seconds, not minutes (300 -> 8). TdrLevel stays 3.
$GD = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
try {
    $g = Get-ItemProperty -Path $GD -EA Stop
    Note ("  (before) TdrDelay={0} TdrDdiDelay={1} TdrLevel={2}" -f $g.TdrDelay,$g.TdrDdiDelay,$g.TdrLevel)
} catch {}
Set-Reg $GD 'TdrDelay'    8
Set-Reg $GD 'TdrDdiDelay' 8
Set-Reg $GD 'TdrLevel'    3

if ($DisableDriverRtd3) {
    # Best-effort, driver-version-specific. Forces dGPU to stay powered (no RTD3/GC6).
    $nvKeys = @(Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' -EA SilentlyContinue)
    foreach ($k in $nvKeys) {
        try {
            $desc = (Get-ItemProperty $k.PSPath -EA Stop).DriverDesc
            if ($desc -match 'NVIDIA') {
                Set-Reg $k.PSPath 'EnableRtd3' 0
                Set-Reg $k.PSPath 'RMDeepL1EntryTime' 0
                Note "  (driver RTD3 disable applied to '$desc' -- reboot required to take effect)"
            }
        } catch {}
    }
}

Note "=== done. Dump-config & AutoReboot take effect immediately; driver/TDR changes need a reboot. ==="
Note "Verify: Get-ItemProperty '$CC' | Select CrashDumpEnabled,MinidumpsCount,AutoReboot"
