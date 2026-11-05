# Download + install pending Windows Updates, but HIDE the older Intel graphics/NPU
# drivers so they cannot downgrade the 8826 / 4778 we just installed. Run elevated.
# Does NOT auto-reboot (COM installer only sets RebootRequired).
$log = "$env:USERPROFILE\Downloads\windows_update_log.txt"
Set-Content $log ("Windows Update run {0}" -f (Get-Date)) -Encoding utf8
function L($m){ Add-Content $log ("{0}  {1}" -f ([datetime]::Now.ToString('HH:mm:ss')), $m) -Encoding utf8 }
try {
  $session  = New-Object -ComObject Microsoft.Update.Session
  $searcher = $session.CreateUpdateSearcher()
  $result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
  L ("pending updates found: {0}" -f $result.Updates.Count)
  $coll = New-Object -ComObject Microsoft.Update.UpdateColl
  foreach ($u in $result.Updates) {
    # protect freshly-installed drivers: hide older Intel graphics 8517 + NPU 4724
    if ($u.Title -match '32\.0\.101\.8517' -or $u.Title -match '32\.0\.100\.4724') {
      try { $u.IsHidden = $true; L ("HID (older, keep my newer driver): " + $u.Title) } catch { L ("hide-fail: " + $u.Title) }
      continue
    }
    try { if (-not $u.EulaAccepted) { $u.AcceptEula() } } catch {}
    [void]$coll.Add($u)
    L ("queued: " + $u.Title)
  }
  if ($coll.Count -gt 0) {
    $dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll
    L ("downloading {0} update(s)..." -f $coll.Count)
    $dr = $dl.Download(); L ("download resultCode={0} (2=success) hresult={1}" -f $dr.ResultCode, $dr.HResult)
    $inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
    L "installing..."
    $ir = $inst.Install(); L ("install resultCode={0} (2=success) rebootRequired={1}" -f $ir.ResultCode, $ir.RebootRequired)
  } else { L "nothing to install after filtering" }
  $v = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'Arc' }).DriverVersion
  L ("Arc graphics driver after WU: {0}  (must still be 8826)" -f $v)
  L "DONE"
} catch { L ("ERROR: " + $_.Exception.Message); L "DONE" }
