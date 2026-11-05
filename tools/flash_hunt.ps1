# Comprehensive flasher hunt: logs every NEW visible console window (50ms poll, ~18 min)
# with full process ancestry, excluding the user's own Windows Terminal / OpenConsole.
$log = "$env:USERPROFILE\flash_hunt.log"
$code = @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class FH {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<string> Snap() {
    var o = new List<string>();
    EnumWindows((h,p) => {
      if (IsWindowVisible(h)) {
        var cn = new StringBuilder(256); GetClassName(h, cn, 256); string cls = cn.ToString();
        if (cls=="ConsoleWindowClass" || cls=="PseudoConsoleWindow" || cls.StartsWith("CASCADIA")) {
          uint pid; GetWindowThreadProcessId(h, out pid);
          var tt = new StringBuilder(200); GetWindowText(h, tt, 200);
          o.Add(((long)h)+"|"+cls+"|"+pid+"|"+tt.ToString());
        }
      }
      return true;
    }, IntPtr.Zero);
    return o;
  }
}
'@
Add-Type -TypeDefinition $code
function Ancestry($startPid, $map) {
  $chain = @(); $p = [int]$startPid; $n = 0
  while ($p -gt 0 -and $n -lt 6) {
    $proc = $map[$p]; if (-not $proc) { break }
    $chain += ("{0}:{1}[{2}]" -f $p, $proc.Name, ([string]$proc.CommandLine))
    $p = [int]$proc.ParentProcessId; $n++
  }
  return ($chain -join '  <= ')
}
function HasUserTerminalAncestor($startPid, $map) {
  $p = [int]$startPid; $n = 0
  while ($p -gt 0 -and $n -lt 12) {
    $proc = $map[$p]; if (-not $proc) { break }
    if ($proc.Name -eq 'WindowsTerminal.exe' -or $proc.Name -eq 'OpenConsole.exe') {
      return $true
    }
    $p = [int]$proc.ParentProcessId; $n++
  }
  return $false
}
"=== FLASH HUNT start $(Get-Date -Format o) (50ms poll, ~18min) ===" | Add-Content $log -Encoding utf8
$seen = @{}
foreach ($row in [FH]::Snap()) { $seen[($row -split '\|')[0]] = $true }
$end = (Get-Date).AddMinutes(18)
while ((Get-Date) -lt $end) {
  $cur = @{}
  $newHwnds = @()
  foreach ($row in [FH]::Snap()) {
    $hwnd = ($row -split '\|',4)[0]
    $cur[$hwnd] = $true
    if (-not $seen.ContainsKey($hwnd)) { $newHwnds += $row }
  }
  if ($newHwnds.Count -gt 0) {
    $map = @{}; foreach ($pp in Get-CimInstance Win32_Process) { $map[[int]$pp.ProcessId] = $pp }
    foreach ($row in $newHwnds) {
      $parts = $row -split '\|',4
      $wpid = [int]$parts[2]
      $owner = $map[$wpid]
      $oname = if ($owner) { $owner.Name } else { '?' }
      if (-not (HasUserTerminalAncestor $wpid $map)) {
        $anc = Ancestry $wpid $map
        $line = '{0}  *** VISIBLE CONSOLE *** class={1} pid={2} owner={3} title="{4}"  ANCESTRY: {5}' -f (Get-Date -Format 'HH:mm:ss.fff'), $parts[1], $wpid, $oname, $parts[3], $anc
        Add-Content $log $line -Encoding utf8
      }
    }
  }
  $seen = $cur
  Start-Sleep -Milliseconds 50
}
"=== FLASH HUNT end $(Get-Date -Format o) ===" | Add-Content $log -Encoding utf8
