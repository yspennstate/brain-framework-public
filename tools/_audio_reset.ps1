$ErrorActionPreference = "SilentlyContinue"
# Re-detect audio hardware and restart the Windows audio stack (needs admin)
pnputil /scan-devices | Out-Null
Restart-Service AudioEndpointBuilder -Force
Start-Sleep -Seconds 3
