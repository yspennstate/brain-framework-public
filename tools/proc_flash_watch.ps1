$log = "$env:USERPROFILE\proc_flash_watch.log"
$interesting = @('cmd.exe','powershell.exe','pwsh.exe','python.exe','python3.exe','py.exe','wscript.exe','cscript.exe','conhost.exe','node.exe','wt.exe')
"=== watch start (poll 0.5s, 12min) $(Get-Date -Format o) selfPID=$PID ===" | Add-Content -Path $log -Encoding utf8
$known = @{}
foreach ($p in Get-CimInstance Win32_Process) { $known[[int]$p.ProcessId] = $true }
$end = (Get-Date).AddMinutes(12)
while ((Get-Date) -lt $end) {
  $procs = Get-CimInstance Win32_Process
  $map = @{}
  foreach ($p in $procs) { $map[[int]$p.ProcessId] = $p }
  foreach ($p in $procs) {
    $id = [int]$p.ProcessId
    if (-not $known.ContainsKey($id)) {
      $known[$id] = $true
      if ($interesting -contains ([string]$p.Name).ToLower()) {
        $par = $map[[int]$p.ParentProcessId]
        $pname = '?'
        if ($par) { $pname = $par.Name }
        $line = '{0}  NEW {1} pid={2} parent={3}({4})  cmd={5}' -f (Get-Date -Format 'HH:mm:ss.fff'), $p.Name, $id, $pname, $p.ParentProcessId, $p.CommandLine
        Add-Content -Path $log -Value $line -Encoding utf8
      }
    }
  }
  Start-Sleep -Milliseconds 500
}
"=== watch end $(Get-Date -Format o) ===" | Add-Content -Path $log -Encoding utf8
