# Installs CrashGuard scheduled tasks: sentinel + watchdog. Runs at user logon, hidden, restart-on-failure.

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'crashguard-common.ps1')

$sentinelName = 'CrashGuard-Sentinel'
$watchdogName = 'CrashGuard-Watchdog'
$watcherName  = 'CrashGuard-ProcessWatcher'
$exportName   = 'CrashGuard-StateExporter'

function Remove-CGTask {
    param([string]$Name)
    try {
        $existing = schtasks /Query /TN $Name 2>$null
        if ($existing) { schtasks /Delete /TN $Name /F | Out-Null; Write-Output "Removed task: $Name" }
    } catch {}
}

if ($Uninstall) {
    Remove-CGTask $sentinelName
    Remove-CGTask $watchdogName
    Remove-CGTask $watcherName
    Remove-CGTask $exportName
    Write-Output "Uninstalled."
    return
}

# Remove existing to refresh
if ($Force) {
    Remove-CGTask $sentinelName
    Remove-CGTask $watchdogName
    Remove-CGTask $watcherName
    Remove-CGTask $exportName
}

$psExe = (Get-Command powershell.exe).Source
$sentinelScript = Join-Path $CG_BIN 'crashguard-sentinel.ps1'
$watchdogScript = Join-Path $CG_BIN 'crashguard-watchdog.ps1'
$watcherScript  = Join-Path $CG_BIN 'crashguard-process-watcher.ps1'
$exportScript   = Join-Path $CG_BIN 'crashguard-export-state.ps1'

$sentinelAction = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$sentinelScript`""
$watchdogAction = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdogScript`""
$watcherAction  = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watcherScript`""
$exportAction   = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$exportScript`""

# Build via XML for fine control of hidden + restart-on-failure + at logon
function New-CGTaskXml {
    param([string]$Cmd, [string]$Args, [string]$Description)
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$Description</Description>
    <Author>CrashGuard</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$env:USERDOMAIN\$env:USERNAME</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$env:USERDOMAIN\$env:USERNAME</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>false</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>9999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$Cmd</Command>
      <Arguments>$Args</Arguments>
    </Exec>
  </Actions>
</Task>
"@
}

$xmlSentinel = New-CGTaskXml -Cmd $psExe -Args $sentinelAction -Description "CrashGuard sentinel (GPU/CPU/RAM crash-prevention monitor)"
$xmlWatchdog = New-CGTaskXml -Cmd $psExe -Args $watchdogAction -Description "CrashGuard watchdog (restarts sentinel if it dies)"
$xmlWatcher  = New-CGTaskXml -Cmd $psExe -Args $watcherAction  -Description "CrashGuard process watcher (preemptive risk-combo detection)"
$xmlExport   = New-CGTaskXml -Cmd $psExe -Args $exportAction   -Description "CrashGuard state exporter (for other observers)"

$sxml = Join-Path $env:TEMP 'crashguard-sentinel.xml'
$wxml = Join-Path $env:TEMP 'crashguard-watchdog.xml'
$pxml = Join-Path $env:TEMP 'crashguard-watcher.xml'
$exml = Join-Path $env:TEMP 'crashguard-export.xml'
$xmlSentinel | Out-File -FilePath $sxml -Encoding unicode -Force
$xmlWatchdog | Out-File -FilePath $wxml -Encoding unicode -Force
$xmlWatcher  | Out-File -FilePath $pxml -Encoding unicode -Force
$xmlExport   | Out-File -FilePath $exml -Encoding unicode -Force

$r1 = schtasks /Create /TN $sentinelName /XML $sxml /F 2>&1
$r2 = schtasks /Create /TN $watchdogName /XML $wxml /F 2>&1
$r3 = schtasks /Create /TN $watcherName  /XML $pxml /F 2>&1
$r4 = schtasks /Create /TN $exportName   /XML $exml /F 2>&1

Write-Output "Sentinel task:    $r1"
Write-Output "Watchdog task:    $r2"
Write-Output "Watcher task:     $r3"
Write-Output "Export task:      $r4"

# Start now
$r5 = schtasks /Run /TN $sentinelName 2>&1
$r6 = schtasks /Run /TN $watchdogName 2>&1
$r7 = schtasks /Run /TN $watcherName  2>&1
$r8 = schtasks /Run /TN $exportName   2>&1
Write-Output "Run sentinel: $r5"
Write-Output "Run watchdog: $r6"
Write-Output "Run watcher:  $r7"
Write-Output "Run export:   $r8"

Remove-Item $sxml,$wxml,$pxml,$exml -Force -ErrorAction SilentlyContinue

Write-Output "Install complete. Check status: powershell -File `"$CG_BIN\crashguard-status.ps1`""
