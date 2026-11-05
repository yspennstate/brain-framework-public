<#
=====================================================================
 refresh_box_access.ps1  -  keep SSH/RDP to <box-host> reachable
 as the home IP rotates. Supersedes update_home_ip_azure.ps1, which
 printed "done" even when the az calls silently failed (the
 misleading-green bug hit live 2026-07-10).
=====================================================================
 Two modes:
   (default) interactive user token - needs a fresh `az login`.
   -ServicePrincipal - non-interactive, reads the scoped SP created by
     bootstrap_nsg_selfheal.ps1. This is the autonomous path: the SP
     token always refreshes from its stored secret, so IP rotation
     self-heals with NO human and NO expiring interactive login.
 Every az result is checked. On an auth failure it says exactly what
 to do instead of lying that it succeeded.
=====================================================================
#>
param(
    [switch]$ServicePrincipal,
    [string]$SpFile = "$env:USERPROFILE\.azure\nsg_selfheal_sp.xml",
    [switch]$Quiet
)
$ErrorActionPreference = 'Stop'
$Rg  = '<resource-group>'
$Nsg = '<nsg-name>'
$Log = "$env:USERPROFILE\.azure\box_access_refresh.log"

function Say([string]$m) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    try { Add-Content -Path $Log -Value $line -Encoding UTF8 } catch {}
    if (-not $Quiet) { Write-Host $m }
}

# Locate az.cmd without depending on PATH (scheduled tasks have a thin PATH)
$azExe = 'az'
$azHint = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
if (Test-Path $azHint) { $azExe = $azHint }

function Invoke-Az {
    param([string[]]$AzArgs)   # NOT $Args - that is an automatic var; the collision made az
                               # receive no command, print help, exit 0, and pass gates falsely.
    # PS 5.1: a native exe's stderr under ErrorActionPreference=Stop becomes a terminating
    # NativeCommandError even on exit 0 (az prints upgrade warnings to stderr). Neutralize it
    # and trust the exit code, which is the real success signal.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $azExe @AzArgs 2>&1 | ForEach-Object { "$_" }
        return @{ ok = ($LASTEXITCODE -eq 0); out = (($out | Where-Object { $_ -notmatch '^WARNING:' }) -join "`n") }
    } finally { $ErrorActionPreference = $old }
}

# ---- authenticate ----
if ($ServicePrincipal) {
    if (-not (Test-Path $SpFile)) { Say "FATAL: no SP file at $SpFile - run bootstrap_nsg_selfheal.ps1 once (after az login)."; exit 2 }
    try {
        $sp = Import-Clixml $SpFile   # DPAPI-decrypts SecureString to this user only
        $cred = $sp.Cred
        $appId  = $cred.UserName
        $secret = $cred.GetNetworkCredential().Password
        $tenant = $sp.Tenant
    } catch { Say "FATAL: cannot read SP file (wrong user profile?): $($_.Exception.Message)"; exit 2 }
    $r = Invoke-Az @('login','--service-principal','-u',$appId,'-p',$secret,'--tenant',$tenant,'-o','none')
    if (-not $r.ok) { Say "FATAL: SP login failed - secret rotated/expired? re-run bootstrap. az said: $($r.out)"; exit 3 }
} else {
    $r = Invoke-Az @('account','get-access-token','--query','expiresOn','-o','tsv')
    if (-not $r.ok) {
        Say "BLOCKED: the interactive az token is expired ('Can't find token from MSAL cache')."
        Say "  Fix now:  az login   (then re-run this script)"
        Say "  Fix forever:  bootstrap_nsg_selfheal.ps1  (creates a scoped SP so this never needs a human again)"
        exit 3
    }
}

# ---- current egress IP ----
$new = $null
foreach ($svc in @('https://checkip.amazonaws.com','https://api.ipify.org','https://ifconfig.me/ip')) {
    try { $new = (Invoke-RestMethod $svc -TimeoutSec 8).ToString().Trim(); if ($new) { break } } catch {}
}
if (-not $new) { Say 'FATAL: could not determine current public IP (all 3 services failed).'; exit 4 }

# ---- read + update each rule, checking every call ----
$changed = $false
foreach ($rule in @('home-ssh','home-rdp')) {
    $show = Invoke-Az @('network','nsg','rule','show','-g',$Rg,'--nsg-name',$Nsg,'-n',$rule,'--query','sourceAddressPrefix','-o','tsv')
    if (-not $show.ok) { Say "FATAL: cannot read rule $rule (auth or NSG issue): $($show.out)"; exit 5 }
    $cur = $show.out.Trim()
    if ($cur -eq "$new/32") { Say "$rule already allows $new/32."; continue }
    $upd = Invoke-Az @('network','nsg','rule','update','-g',$Rg,'--nsg-name',$Nsg,'-n',$rule,'--source-address-prefixes',"$new/32",'-o','none')
    if (-not $upd.ok) { Say "FATAL: update of $rule FAILED (rule NOT changed): $($upd.out)"; exit 6 }
    # verify the change actually took (green-not-trusted)
    $ver = Invoke-Az @('network','nsg','rule','show','-g',$Rg,'--nsg-name',$Nsg,'-n',$rule,'--query','sourceAddressPrefix','-o','tsv')
    if (-not $ver.ok -or $ver.out.Trim() -ne "$new/32") { Say "FATAL: $rule update did not verify (still '$($ver.out.Trim())')."; exit 7 }
    Say "${rule}: $cur -> $new/32 (verified)."
    $changed = $true
}
if ($ServicePrincipal) { Invoke-Az @('logout') | Out-Null }
Say ($(if ($changed) { "OK: NSG now allows $new. SSH/RDP reachable." } else { "OK: IP unchanged ($new), nothing to do." }))
exit 0
