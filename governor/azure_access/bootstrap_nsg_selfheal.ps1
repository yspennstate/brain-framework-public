<#
=====================================================================
 bootstrap_nsg_selfheal.ps1  -  RUN ONCE, by the owner, after `az login`.
=====================================================================
 The problem this ends: the home IP rotates (residential DHCP) and the
 NSG allow-list goes stale, so SSH/RDP to <box-host> die - and the
 only fix was an interactive `az login` whose token expires in days, so
 the fleet could never self-heal it. (Lived 2026-07-10: box unreachable
 for hours, two agents blocked, waiting on a human.)

 This creates a least-privilege service principal scoped to the SINGLE
 NSG resource (role: Network Contributor on that NSG only - it cannot
 touch the VM, the stack, or anything else). Its secret is stored
 DPAPI-encrypted to THIS Windows user, and a hidden scheduled task runs
 refresh_box_access.ps1 -ServicePrincipal every 10 minutes. After this
 one run, IP rotation heals itself with no human and no expiring token.

 Security note (owner's call): a stored Azure credential lives on this
 PC. Blast radius if leaked = edit that one NSG's rules. Rotate with
 `az ad sp credential reset` and re-run this script. Remove entirely
 with the UNINSTALL block at the bottom (commented).
=====================================================================
#>
$ErrorActionPreference = 'Stop'
$Rg     = '<resource-group>'
$Nsg    = '<nsg-name>'
$SpName = '<selfheal-sp-name>'
$SpFile = "$env:USERPROFILE\.azure\nsg_selfheal_sp.xml"
$azHint = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
$az = if (Test-Path $azHint) { $azHint } else { 'az' }

# 0. require a working interactive login FIRST
& $az account get-access-token -o none 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Run 'az login' first, then re-run this script."; exit 1 }

# 1. resolve the NSG resource id (the ONLY scope the SP will get)
$nsgId = (& $az network nsg show -g $Rg -n $Nsg --query id -o tsv).Trim()
if (-not $nsgId) { Write-Host "Could not resolve NSG '$Nsg' in '$Rg'."; exit 1 }
Write-Host "Scoping SP to: $nsgId"

# 2. create (or reset) the scoped SP - Network Contributor on that NSG only
$spJson = & $az ad sp create-for-rbac --name $SpName --role "Network Contributor" --scopes $nsgId -o json
if ($LASTEXITCODE -ne 0) { Write-Host "SP creation failed (need Owner/UAA rights on the NSG?)."; exit 1 }
$sp = $spJson | ConvertFrom-Json

# 3. store DPAPI-encrypted to this user (SecureString via Export-Clixml)
$sec  = ConvertTo-SecureString $sp.password -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($sp.appId, $sec)
[pscustomobject]@{ Cred = $cred; Tenant = $sp.tenant } | Export-Clixml $SpFile
Write-Host "SP credential stored (DPAPI, this user only): $SpFile"

# 4. hidden self-heal task every 10 min via VBS SW_HIDE (no window ever)
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs  = Join-Path $base 'launch_box_selfheal.vbs'
@"
Dim sh : Set sh = CreateObject("WScript.Shell")
sh.Run "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""$base\refresh_box_access.ps1"" -ServicePrincipal -Quiet", 0, True
"@ | Set-Content -Path $vbs -Encoding ASCII

$user = "$env:USERDOMAIN\$env:USERNAME"
try { Unregister-ScheduledTask -TaskName 'BoxAccessSelfHeal' -Confirm:$false -ErrorAction Stop } catch {}
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B "{0}"' -f $vbs)
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
       -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
       -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'BoxAccessSelfHeal' -Action $act -Trigger $trg -Settings $set -User $user | Out-Null

# 5. prove it works right now (also fixes the current stale IP)
Write-Host "Running the first self-heal refresh..."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $base 'refresh_box_access.ps1') -ServicePrincipal
Write-Host "Done. IP rotation now self-heals every 10 min. Verify: ssh into the box should work."

# ---- UNINSTALL (uncomment to remove everything) ----
# Unregister-ScheduledTask -TaskName 'BoxAccessSelfHeal' -Confirm:$false
# & $az ad sp delete --id (Import-Clixml $SpFile).Cred.UserName
# Remove-Item $SpFile, $vbs -ErrorAction SilentlyContinue
