# flash_poll_test.ps1 - STOP-armed proof that BrainDream creates no visible console.
# A kernel dream lease and an undeletable STOP handle prevent an actual dream.
# A dedicated poller starts before the hidden trigger and stays alive through
# causally verified task completion plus a 500 ms grace interval.
$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$stop = Join-Path $base 'STOP'
$lock = Join-Path $base 'dream.lock'
$helper = Join-Path $base 'dream_atomic_io.ps1'
$logDir = Join-Path $base 'logs'
$marker = 'STOP file present - not dreaming'
$failureCode = 0
$failureMessage = $null
$stopStream = $null
$probeLease = $null
$pollTask = $null
$pollResult = $null
$triggerDispatched = $false
$terminalReady = $false
$beforeLogs = @{}

function Fail-Probe([int] $Code, [string] $Message) {
    $script:failureCode = $Code
    $script:failureMessage = $Message
    throw [InvalidOperationException]::new($Message)
}

function Count-Marker([string] $Text) {
    return [regex]::Matches($Text, [regex]::Escape($marker)).Count
}

function Get-NewStopMarkerCount {
    $total = 0
    try {
        foreach ($item in Get-ChildItem -LiteralPath $logDir -Filter 'dream_*.log' -File) {
            $text = [IO.File]::ReadAllText($item.FullName)
            $afterCount = Count-Marker $text
            $beforeCount = if ($beforeLogs.ContainsKey($item.FullName)) {
                [int] $beforeLogs[$item.FullName].Markers
            } else {
                0
            }
            if ($afterCount -gt $beforeCount) { $total += $afterCount - $beforeCount }
        }
    } catch {
        return -1
    }
    return $total
}

try {
    if (-not (Test-Path -LiteralPath $stop -PathType Leaf)) {
        Fail-Probe 2 'STOP must be armed before the no-flash probe'
    }
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
        Fail-Probe 3 'dream lease helper is unavailable'
    }
    . $helper
    if (-not (Get-Command Open-DreamLease -CommandType Function -ErrorAction SilentlyContinue)) {
        Fail-Probe 4 'Open-DreamLease did not load'
    }

    # Deny delete/rename/write of STOP for the whole trigger. The matching
    # kernel lease independently blocks a runner that somehow misses STOP.
    $stopStream = [IO.FileStream]::new(
        $stop, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::None)
    $probeLease = Open-DreamLease -Path $lock
    if ($null -eq $probeLease) {
        Fail-Probe 5 'a dream lease is already active'
    }

    $taskBefore = Get-ScheduledTask -TaskName 'BrainDream'
    if ("$($taskBefore.State)" -cne 'Ready') {
        Fail-Probe 6 "BrainDream initial state is $($taskBefore.State), not Ready"
    }
    if ("$($taskBefore.Settings.MultipleInstances)" -cne 'IgnoreNew') {
        Fail-Probe 7 'BrainDream no-overlap policy is not IgnoreNew'
    }
    $actions = @($taskBefore.Actions)
    $expectedLauncher = Join-Path $base 'launch_dream.vbs'
    $expectedArguments = '//B "' + $expectedLauncher + '"'
    if ($actions.Count -ne 1 -or
        "$($actions[0].Execute)" -cne 'wscript.exe' -or
        "$($actions[0].Arguments)" -cne $expectedArguments) {
        Fail-Probe 8 'BrainDream action is not the exact hidden live launcher'
    }
    $infoBefore = Get-ScheduledTaskInfo -TaskName 'BrainDream'

    if (Test-Path -LiteralPath $logDir -PathType Container) {
        foreach ($item in Get-ChildItem -LiteralPath $logDir -Filter 'dream_*.log' -File) {
            $text = [IO.File]::ReadAllText($item.FullName)
            $beforeLogs[$item.FullName] = [pscustomobject]@{
                Markers = Count-Marker $text
                Length = $item.Length
                Mtime = $item.LastWriteTimeUtc.Ticks
            }
        }
    }

    Add-Type @"
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

public sealed class BrainDreamPollResult {
  public int BaselineCount;
  public int Samples;
  public double MaxGapMs;
  public long FirstTicks;
  public long LastTicks;
  public string[] Flashes;
}

public static class BrainDreamFlashPoll {
  static readonly ManualResetEventSlim PollReady = new ManualResetEventSlim(false);
  static readonly ManualResetEventSlim StopRequested = new ManualResetEventSlim(false);
  static long triggerTicks;
  [DllImport("user32.dll", SetLastError=true)] static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern int GetClassName(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll", SetLastError=true)]
  static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);

  static List<string> VisibleConsoles() {
    var found = new List<string>();
    bool callbackFailed = false;
    bool enumerated = EnumWindows((h, lp) => {
      if (IsWindowVisible(h)) {
        var sb = new StringBuilder(64);
        if (GetClassName(h, sb, 64) == 0) { callbackFailed = true; return true; }
        var cls = sb.ToString();
        if (cls == "ConsoleWindowClass" || cls == "PseudoConsoleWindow" ||
            cls.StartsWith("CASCADIA", StringComparison.Ordinal)) {
          uint pid;
          if (GetWindowThreadProcessId(h, out pid) == 0) { callbackFailed = true; return true; }
          found.Add(h.ToInt64().ToString() + "|" + cls + "|" + pid.ToString());
        }
      }
      return true;
    }, IntPtr.Zero);
    if (!enumerated || callbackFailed) throw new Win32Exception(Marshal.GetLastWin32Error());
    return found;
  }

  static void Sample(List<long> ticks, HashSet<string> flashes, ref List<string> previous) {
    ticks.Add(Stopwatch.GetTimestamp());
    var current = VisibleConsoles();
    var prior = new HashSet<string>(previous, StringComparer.Ordinal);
    foreach (var window in current) if (!prior.Contains(window)) flashes.Add(window);
    previous = current;
  }

  public static Task<BrainDreamPollResult> StartPolling(int intervalMs) {
    return Task.Run(() => {
      var ticks = new List<long>();
      var flashes = new HashSet<string>(StringComparer.Ordinal);
      var previous = VisibleConsoles();
      int baselineCount = previous.Count;
      ticks.Add(Stopwatch.GetTimestamp());
      PollReady.Set();
      while (!StopRequested.Wait(intervalMs)) Sample(ticks, flashes, ref previous);
      Sample(ticks, flashes, ref previous);
      double maxGapMs = 0.0;
      for (int i = 1; i < ticks.Count; i++) {
        double gap = 1000.0 * (ticks[i] - ticks[i - 1]) / Stopwatch.Frequency;
        if (gap > maxGapMs) maxGapMs = gap;
      }
      return new BrainDreamPollResult {
        BaselineCount = baselineCount,
        Samples = ticks.Count,
        MaxGapMs = maxGapMs,
        FirstTicks = ticks[0],
        LastTicks = ticks[ticks.Count - 1],
        Flashes = new List<string>(flashes).ToArray()
      };
    });
  }

  public static void StopPolling() { StopRequested.Set(); }
  public static long GetTriggerTicks() { return Interlocked.Read(ref triggerTicks); }

  public static Task<int> TriggerTaskAsync(string taskName, int delayMs, long notAfterTicks) {
    return Task.Run(() => {
      if (!PollReady.Wait(TimeSpan.FromSeconds(5))) return 121;
      Thread.Sleep(delayMs);
      long now = Stopwatch.GetTimestamp();
      if (now >= notAfterTicks) return 122;
      Interlocked.Exchange(ref triggerTicks, now);
      var psi = new ProcessStartInfo();
      psi.FileName = Environment.ExpandEnvironmentVariables(@"%SystemRoot%\System32\schtasks.exe");
      psi.Arguments = "/Run /TN \"" + taskName + "\"";
      psi.UseShellExecute = false;
      psi.CreateNoWindow = true;
      psi.WindowStyle = ProcessWindowStyle.Hidden;
      psi.RedirectStandardOutput = true;
      psi.RedirectStandardError = true;
      using (var process = Process.Start(psi)) {
        if (process == null) return 123;
        process.StandardOutput.ReadToEnd();
        process.StandardError.ReadToEnd();
        process.WaitForExit();
        return process.ExitCode;
      }
    });
  }
}
"@

    $frequency = [Diagnostics.Stopwatch]::Frequency
    $pollTask = [BrainDreamFlashPoll]::StartPolling(20)
    $notAfterTicks = [Diagnostics.Stopwatch]::GetTimestamp() + (5 * $frequency)
    $triggerTask = [BrainDreamFlashPoll]::TriggerTaskAsync(
        'BrainDream', 100, $notAfterTicks)
    $triggerCode = $triggerTask.GetAwaiter().GetResult()
    $triggerTicks = [BrainDreamFlashPoll]::GetTriggerTicks()
    $triggerDispatched = $triggerTicks -gt 0
    if ($triggerCode -ne 0) {
        Fail-Probe 9 "hidden schtasks trigger failed (exit=$triggerCode)"
    }

    $completionDeadline = (Get-Date).AddSeconds(14)
    $infoAfter = $null
    $markerDelta = -1
    while ((Get-Date) -lt $completionDeadline) {
        $taskAfter = Get-ScheduledTask -TaskName 'BrainDream'
        $infoAfter = Get-ScheduledTaskInfo -TaskName 'BrainDream'
        $markerDelta = Get-NewStopMarkerCount
        if ("$($taskAfter.State)" -ceq 'Ready' -and
            $infoAfter.LastRunTime -gt $infoBefore.LastRunTime -and
            $infoAfter.LastTaskResult -eq 0 -and $markerDelta -eq 1) {
            $terminalReady = $true
            break
        }
        if ($markerDelta -gt 1) { Fail-Probe 10 'more than one STOP exit was recorded' }
        Start-Sleep -Milliseconds 100
    }
    if (-not $terminalReady) {
        Fail-Probe 11 'the triggered task did not reach one proven STOP exit while Ready'
    }

    $completionTicks = [Diagnostics.Stopwatch]::GetTimestamp()
    Start-Sleep -Milliseconds 500
    [BrainDreamFlashPoll]::StopPolling()
    $pollResult = $pollTask.GetAwaiter().GetResult()
    $pollTask = $null

    if ($pollResult.Samples -lt 2 -or
        $triggerTicks -le $pollResult.FirstTicks -or
        $triggerTicks -ge $pollResult.LastTicks) {
        Fail-Probe 12 'the task trigger was not bracketed by live polling samples'
    }
    if ($completionTicks -ge $pollResult.LastTicks -or
        (1000.0 * ($pollResult.LastTicks - $completionTicks) / $frequency) -lt 450.0) {
        Fail-Probe 13 'task completion lacked a polled 500 ms grace interval'
    }
    if ($pollResult.MaxGapMs -gt 50.0) {
        Fail-Probe 14 ("poll cadence was inconclusive (max gap {0:N1} ms)" -f $pollResult.MaxGapMs)
    }
    if ($pollResult.Flashes.Count -ne 0) {
        Fail-Probe 15 "visible console windows appeared: $($pollResult.Flashes.Count)"
    }

    Write-Output "baseline consoles: $($pollResult.BaselineCount)"
    Write-Output "samples: $($pollResult.Samples)"
    Write-Output ("max sample gap ms: {0:N1}" -f $pollResult.MaxGapMs)
    Write-Output "new visible consoles: $($pollResult.Flashes.Count)"
    Write-Output "new STOP markers: $markerDelta"
    Write-Output "task result: $($infoAfter.LastTaskResult)"
} catch {
    if ($failureCode -eq 0) {
        $failureCode = 1
        $failureMessage = $_.Exception.Message
    }
} finally {
    if ($null -ne $pollTask) {
        if ($triggerDispatched -and -not $terminalReady) {
            $safetyDeadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $safetyDeadline) {
                try {
                    $state = "$(Get-ScheduledTask -TaskName 'BrainDream' -ErrorAction Stop).State"
                    $safetyInfo = Get-ScheduledTaskInfo -TaskName 'BrainDream' -ErrorAction Stop
                    if ($state -ceq 'Ready' -and
                        $safetyInfo.LastRunTime -gt $infoBefore.LastRunTime) {
                        $terminalReady = $true
                        break
                    }
                } catch {}
                Start-Sleep -Milliseconds 100
            }
            if ($terminalReady) { Start-Sleep -Milliseconds 500 }
        }
        try {
            [BrainDreamFlashPoll]::StopPolling()
            $pollResult = $pollTask.GetAwaiter().GetResult()
        } catch {}
    }
    if ($null -ne $probeLease) { $probeLease.Dispose() }
    if ($null -ne $stopStream) { $stopStream.Dispose() }
}

if ($failureCode -ne 0) {
    if ($triggerDispatched -and -not $terminalReady) {
        $failureMessage += '; STOP MUST REMAIN because the task is not proven Ready'
    }
    [Console]::Error.WriteLine($failureMessage)
    exit $failureCode
}
exit 0
