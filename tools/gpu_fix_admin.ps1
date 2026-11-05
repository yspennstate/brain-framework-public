# GPU Crash Prevention - Admin Script
# Run as Administrator

Write-Host "=== GPU Crash Prevention Setup ===" -ForegroundColor Cyan

# 1. Increase TDR timeout (default is 2 seconds, setting to 60)
Write-Host "`n[1/3] Setting TDR timeout to 60 seconds..." -ForegroundColor Yellow
$gfxPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
New-ItemProperty -Path $gfxPath -Name 'TdrDelay' -Value 60 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $gfxPath -Name 'TdrDdiDelay' -Value 60 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $gfxPath -Name 'TdrLevel' -Value 3 -PropertyType DWord -Force | Out-Null
Write-Host "  TdrDelay = 60s (was 2s default)" -ForegroundColor Green
Write-Host "  TdrDdiDelay = 60s (was 5s default)" -ForegroundColor Green
Write-Host "  TdrLevel = 3 (recover without crash)" -ForegroundColor Green

# 2. Disable auto-reboot on BSOD
Write-Host "`n[2/3] Disabling auto-restart on crash..." -ForegroundColor Yellow
$crashPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
Set-ItemProperty -Path $crashPath -Name 'AutoReboot' -Value 0
Set-ItemProperty -Path $crashPath -Name 'CrashDumpEnabled' -Value 1
Write-Host "  AutoReboot = 0 (BSOD will stay on screen)" -ForegroundColor Green
Write-Host "  CrashDumpEnabled = 1 (full dumps for diagnostics)" -ForegroundColor Green

# 3. Set power management to prevent GPU power-saving issues
Write-Host "`n[3/3] Setting GPU power management..." -ForegroundColor Yellow
# Set active power scheme to High Performance for PCI Express Link State
powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0
powercfg /setactive SCHEME_CURRENT
Write-Host "  PCI Express Link State Power Management = Off" -ForegroundColor Green

Write-Host "`n=== All registry changes applied ===" -ForegroundColor Cyan
Write-Host "A REBOOT is required for TDR changes to take effect." -ForegroundColor Red
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
