<# Install or remove the windowless continuity task.  The action is wscript/SW_HIDE,
   never powershell.exe directly, because interactive scheduled consoles flash before
   -WindowStyle Hidden takes effect. #>
param([switch]$Uninstall, [switch]$RunNow)
$ErrorActionPreference = 'Stop'
$name = 'FleetContinuitySupervisor'
$vbs = "$env:USERPROFILE\.claude\fleet\codex_continuity\launch_supervisor.vbs"
$supervisor = "$env:USERPROFILE\.claude\fleet\codex_continuity\supervisor.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed $name"
    exit 0
}

if (-not (Test-Path -LiteralPath $vbs)) { throw "missing $vbs" }
& 'C:\Python314\python.exe' $supervisor --validate
if ($LASTEXITCODE -ne 0) { throw "continuity preflight failed: $LASTEXITCODE" }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B "{0}"' -f $vbs)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$minute = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 2)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger @($logon, $minute) -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $name -ErrorAction Stop
$actualAction = @($registered.Actions)[0]
if ([IO.Path]::GetFileName([string]$actualAction.Execute) -ine 'wscript.exe') {
    throw "unsafe task action: $($actualAction.Execute)"
}
$expectedArgs = '//B "{0}"' -f $vbs
if ([string]$actualAction.Arguments -ne $expectedArgs) {
    throw "unexpected task arguments: $($actualAction.Arguments)"
}
if ([string]$registered.Principal.LogonType -ne 'Interactive' -or
    [string]$registered.Principal.RunLevel -ne 'Limited') {
    throw "unexpected task principal"
}
if ([string]$registered.Settings.MultipleInstances -ne 'IgnoreNew') {
    throw "unexpected task instance policy"
}
Write-Output "installed $name -> wscript.exe //B $vbs"
if ($RunNow) { Start-ScheduledTask -TaskName $name }
