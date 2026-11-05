[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'BrainResilienceBackup',
    [string]$At = '04:35',
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$root = "$env:USERPROFILE\brain-repo\12_cognitive_architecture\resilience"
$launcher = Join-Path $root 'launch_backup.vbs'
if (-not (Test-Path -LiteralPath $launcher)) { throw "Missing launcher: $launcher" }
if (-not $PythonPath) {
    $PythonPath = (Get-Command python.exe -ErrorAction Stop).Source
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
if (-not $PSCmdlet.ShouldProcess($TaskName, 'Register daily windowless brain backup')) { return }

$state = "$env:USERPROFILE\.brain-resilience"
New-Item -ItemType Directory -Force -Path $state | Out-Null
$runtimePath = Join-Path $state 'runtime.json'
$runtimeJson = @{ schema_version = 1; python_path = $PythonPath } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($runtimePath, $runtimeJson + "`n", [System.Text.UTF8Encoding]::new($false))

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$action = New-ScheduledTaskAction -Execute $wscript -Argument ("//B `"{0}`"" -f $launcher) -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Independent private GitHub + locked/versioned S3 backup of the shared AI brain.'
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

$registered = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $registered.TaskName
    State = $registered.State
    NextRunTime = $info.NextRunTime
    Execute = $registered.Actions[0].Execute
    Arguments = $registered.Actions[0].Arguments
    PythonPath = $PythonPath
}
