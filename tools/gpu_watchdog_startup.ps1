# Register GPU Watchdog as a scheduled task that runs at logon
$action = New-ScheduledTaskAction `
    -Execute "pythonw.exe" `
    -Argument "$env:USERPROFILE\gpu_watchdog.py" `
    -WorkingDirectory "$env:USERPROFILE"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Remove existing task if present
Unregister-ScheduledTask -TaskName "GPU_Watchdog" -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName "GPU_Watchdog" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Monitors GPU temp/util/VRAM and kills processes before TDR crash" `
    -RunLevel Limited

Write-Host "GPU Watchdog scheduled task created successfully!" -ForegroundColor Green
Write-Host "It will run at every logon as $env:USERNAME"
