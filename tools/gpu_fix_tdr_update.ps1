$gfxPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
Set-ItemProperty -Path $gfxPath -Name 'TdrDelay' -Value 300
Set-ItemProperty -Path $gfxPath -Name 'TdrDdiDelay' -Value 300
Write-Host "TdrDelay = 300s (5 min)" -ForegroundColor Green
Write-Host "TdrDdiDelay = 300s (5 min)" -ForegroundColor Green
Get-ItemProperty $gfxPath | Select-Object TdrDelay,TdrDdiDelay,TdrLevel
