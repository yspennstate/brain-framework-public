$log = "$env:USERPROFILE\win_flash_watch.log"
$code = @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<string> Snap() {
    var outp = new List<string>();
    EnumWindows((h,p) => {
      if (IsWindowVisible(h)) {
        var cn = new StringBuilder(256); GetClassName(h, cn, 256);
        string cls = cn.ToString();
        if (cls=="ConsoleWindowClass" || cls=="PseudoConsoleWindow" || cls.StartsWith("CASCADIA")) {
          uint pid; GetWindowThreadProcessId(h, out pid);
          var tt = new StringBuilder(256); GetWindowText(h, tt, 256);
          outp.Add(((long)h)+"|"+pid+"|"+cls+"|"+tt.ToString());
        }
      }
      return true;
    }, IntPtr.Zero);
    return outp;
  }
}
'@
Add-Type -TypeDefinition $code
"=== window watch start $(Get-Date -Format o) selfPID=$PID ===" | Add-Content $log -Encoding utf8
$seen = @{}
$end = (Get-Date).AddMinutes(6.5)
while ((Get-Date) -lt $end) {
  $cur = @{}
  foreach ($row in [W]::Snap()) {
    $parts = $row -split '\|',4
    $h = $parts[0]; $pid2 = [int]$parts[1]; $cls = $parts[2]; $title = $parts[3]
    $cur[$h] = $true
    if (-not $seen.ContainsKey($h)) {
      $pname=''; $cmd=''; $ppid=''; $pn=''
      try { $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$pid2" -EA Stop; $pname=$pp.Name; $cmd=[string]$pp.CommandLine; $ppid=$pp.ParentProcessId } catch {}
      try { $par = Get-CimInstance Win32_Process -Filter "ProcessId=$ppid" -EA Stop; $pn=$par.Name } catch {}
      $line = '{0}  VISIBLE-CONSOLE pid={1} {2} class={3} title="{4}" parent={5}({6}) cmd={7}' -f (Get-Date -Format 'HH:mm:ss.fff'), $pid2, $pname, $cls, $title, $pn, $ppid, $cmd
      Add-Content $log $line -Encoding utf8
    }
  }
  $seen = $cur
  Start-Sleep -Milliseconds 300
}
"=== window watch end $(Get-Date -Format o) ===" | Add-Content $log -Encoding utf8
