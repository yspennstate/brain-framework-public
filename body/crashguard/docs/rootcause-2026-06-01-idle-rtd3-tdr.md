# Root-cause analysis & fix — recurring 0x116 VIDEO_TDR_FAILURE (2026-06-01)

## The crash
- Today's crash: **2026-06-01 07:41:18**, bugcheck `0x116`
  `(…, …, 0xC000009A, 0x4)` — VIDEO_TDR_FAILURE, param-3 = `STATUS_INSUFFICIENT_RESOURCES`.
- This is the **dominant signature**: 0x116 + 0xC000009A recurs (5/27 ×4, 5/5, 4/13, …).
  Two other codes appear less often (0x19C on 5/31 & 5/3, 0x13A on 5/10).

## Why the old model missed it
CrashGuard was built to fight a **load/thermal** GPU crash: throttle / suspend / kill
workloads + drop GPU clocks when GPU temp/util/mem are high. But the telemetry shows
every 0x116 fires while the GPU is **idle**:
- At/around crash time: GPU util **0%**, **~1.0 W average power**, ~50–60 °C, mem 0 MiB.
- `gpu-watch` only escalates on temp/power/mem/HW-throttle, so at idle it is always GREEN.
- The sentinel was instead firing on **CPU=100%** spikes — which the config itself notes
  are *not* the crash signal.

Two standard stopgaps were already in place and **did not help**, which is the key tell:
- `TdrDelay`/`TdrDdiDelay` = **300 s** (extreme). A 5-minute GPU-busy timeout cannot help
  when the GPU is not "busy" — it has **fallen off the PCIe bus**.
- PCIe **ASPM Link State Power Management = Off** (High Performance plan) already set.

## Actual root cause
**Idle dGPU power-down (NVIDIA RTD3 / GC6) with a failing D3cold→D0 restore** on driver
**595.97** (RTX PRO 2000 Blackwell Laptop GPU). When the dGPU is idle it powers off; on this
driver/silicon the wake-up transition intermittently fails → the device drops off the bus →
immediate `0x116` device-loss (TdrDelay irrelevant). Corroborating evidence:
- **34 corrected PCIe AER errors / 72 h** from `PCI\VEN_10DE&DEV_2D39` (the GPU) —
  `Microsoft-Windows-WHEA-Logger` Id 17, "PCI Express Legacy Endpoint".
- PCIe link trains to **x8 of a max x16** — a marginal/under-trained link.
- `pstate` flips to deep idle; **average power ~1.00 W** = GC6 deep-idle signature.

> Note: persistent corrected AER + under-trained link can also indicate **marginal hardware**
> (GPU/PCIe connection). Software mitigations reduce frequency; they cannot fix failing silicon.
> If crashes persist with the keepalive running, escalate to driver update → BIOS → hardware service.

## Fix shipped (Iteration 1) — anti-RTD3 GPU keepalive
`bin/crashguard-gpu-keepalive.py` (Python 3.14 + torch CUDA), launched hidden via `pythonw`.
- Holds **one resident CUDA context** (tiny elementwise op every 8 s; no cuBLAS/cuDNN load).
- A resident allocation prevents the driver from entering D3cold/GC6, so the **failing
  power-down→restore transition never happens**. Clocks are *not* pinned → idle power stays modest.
- Footprint: ~114 MiB device memory, ~2 MB torch-reserved. Fully reversible (stop the process).
- **Non-admin, process-local. Does not touch protected production, fires no approval
  cards, opens no visible window.**

### Validation
- Idle GPU memory went **0 → 114 MiB and held steady** across samples (device stays on the bus).
- Supervised kill-test: killed keepalive → `self-heal` respawned it automatically (new PID),
  heartbeat resumed with correct `timestamp` key.

### Supervision / persistence wiring
- `crashguard-launch-all.ps1` and `crashguard-self-heal.ps1` taught to handle `pythonw`
  services (new optional `exe='pythonw'` field) — previously only `powershell.exe` was
  detected/launched. Keepalive now auto-starts at boot and respawns if it dies.
- **Bonus bug fix:** `self-heal` was throwing `Invalid JSON primitive: .` on a corrupt/empty
  heartbeat since 07:59, which aborted the *entire* heal iteration (services after the bad one
  went unsupervised). Added `CG-HeartbeatAgeSec` (tolerates missing `timestamp`, alt `ts` key,
  or unparseable JSON → falls back to file mtime). JSON errors stopped after the fix.

## Backlog (next iterations)
1. **Fix the PCIe-AER reaction** (`pcie-watch.ps1`): today a *corrected* (already-recovered)
   AER triggers a destructive RED mitigation (kill ollama, throttle all codex/observers, drop
   GPU clocks) — useless against idle device-loss and disruptive (~11×/day). Replace with:
   ensure keepalive alive + rate-based alerting; stop nuking the user's work on benign errors.
2. **Forensics (needs admin):** `CrashDumpEnabled` is `1` (full-dump-only) → `Minidump\` empty,
   each crash overwrites the single 68 GB `MEMORY.DMP`. Set to `7` (Automatic) for retained
   per-crash minidumps. Set `AutoReboot=1` (currently 0 → the unattended rig sat dead ~18 min).
3. **Driver:** update off **595.97** via the vendor updater at the weekend maintenance pause.
4. **BIOS / hardware:** check for an RTD3 / PCIe-ASPM firmware toggle; consider GPU reseat/service
   if AER rate keeps climbing.
5. **Lighter keepalive:** optional CUDA-driver-API (nvcuda.dll) context to cut VRAM vs torch.

## Validation log
- **2026-06-01 ~08:11** keepalive went live. **PCIe AER errors (WHEA id 17): 31 before 08:11, 0
  after** (the 31 are the boot-replay burst at 07:59). 0 new corrected link errors in the first
  ~30 min with the GPU pinned in D0 — the predicted result (no idle power transitions = no link
  errors). Needs longer observation (hours/days) to confirm the rate stays ~0.
- GPU memory holds steady at 114 MiB (device stays on the bus); keepalive supervised respawn OK.
- Fleet verified 1x (a brief "duplicate storm" scare was a *measurement artifact* — counting
  process instances by substring against CommandLine also matched my own shell's command text and
  conflated `watchdog`/`watchdog2`. Correct count = exact `.ps1` name, excluding own PID.)
- **09:19 (~1 h in):** `aer_last_60min` 31 -> **0** (boot burst aged out, no new link errors while
  the GPU is pinned). **No bugcheck since 08:00.** Clean confirmation the fix is holding. A
  `keepalive-health.ps1` snapshot tool now logs this time-series to `logs/keepalive-health.jsonl`
  and doubles as a fast keepalive supervisor (relaunches it if missing).
- **09:22:** keepalive hardened with a dependency-free **fallback engine**. Primary stays `torch`;
  if torch/python is ever broken it falls back to `cuDevicePrimaryCtxRetain` via `nvcuda.dll`
  (ships with the driver, validated working). Removes torch/python as a single point of failure.
  Heartbeat now reports which `engine` is active. New code restarted live & verified (engine=torch,
  GPU resident, 1x).
- **10:09 (~2.5 h in):** still crash-free since 08:00; AER 60-min = 0; keepalive uptime ~48 min
  (engine=torch), zero relaunch churn; GPU resident 114 MiB. Added an **"Idle-TDR (0x116)
  Protection: ACTIVE"** panel to the CrashGuard status dashboard (`crashguard-html-status.ps1`)
  showing keepalive engine/uptime, dGPU-held-in-D0, and PCIe AER rate; html-publisher regenerates
  it every 30 s. `gpu-keepalive` also added to the services heartbeat table.
- **11:12 (~3 h in):** stable. Crash-free since 08:00; AER 60-min = 0; keepalive uptime ~110 min
  (engine=torch), no relaunch churn; GPU resident 114 MiB; dashboard panel = ACTIVE. No changes made.
- **12:13 (~4 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~171 min;
  GPU resident. Admin hardening not yet applied (CrashDumpEnabled=1, AutoReboot=0 — pending user,
  fine). Driver update deferred to the weekend maintenance pause. No changes made.
- **13:14 (~5 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~232 min
  (engine=torch). Admin hardening still pending; driver update still deferred to weekend. No changes.
- **14:15 (~6 h in, Mon):** stable — 6th consecutive clean hourly check. Crash-free since 08:00;
  AER=0; keepalive uptime ~293 min. Protection is durably autonomous (boot autostart + self-heal
  respawn + dashboard panel); the agent watch is now just a safety net and can wind down. No changes.
- **15:16 (~7 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~354 min. No changes.
- **16:17 (~8 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~415 min. No changes.
- **17:18 (~9 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~476 min. No changes.
- **18:19 (~10 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~537 min. No changes.
- **19:20 (~11 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~597 min. No changes.
- **20:21 (~12 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~658 min. No changes.
- **21:22 (~13 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~719 min. No changes.
- **22:23 (~14 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~781 min. GPU now
  P2/284 MB (a real workload running alongside the keepalive — coexists fine, no errors). No changes.
- **23:24 (~15 h in, Mon):** stable. Crash-free since 08:00; AER=0; keepalive uptime ~842 min; GPU 614 MB
  resident. No changes.

## 2026-06-01 ~23:30 — fixed: CrashGuard was FREEZING browsers on launch (user-reported)
User reported Chrome/Edge "kind of freeze" on open, fearing a crash. Root cause was **CrashGuard
itself**, not hardware: the sentinel escalated to **orange tier on CPU spikes** (`cpu_util_pct=99/100`)
and the orange mitigation ran `suspend:low` + `drop-hw-accel:browsers`, which **suspended all browser
processes** (logs showed 20+ `suspend_browser_gpu` then `ROLLBACK ...resume` ~15 s later). Opening a
browser pegs the CPU -> orange -> browser suspended -> ~15 s freeze until CPU drops and CrashGuard
resumes it. (The config even noted "CPU is NOT the crash signal" yet still suspended on it.) The real
crash mode is the idle-GPU TDR — load-based suspensions never prevented it and were pure collateral
damage (also hit Discord/Spotify/ollama/Lean on every CPU spike).

Fix (config only, reversible):
- `thresholds.json`: `cpu_util_pct.tier_orange` 95 -> **101** — CPU now caps at "yellow" (throttle
  only, never suspend). Routine CPU spikes (browser launch, codex, compiles) no longer escalate.
- `policies.json`: browsers/webview `low` -> **`medium`** (never suspended); removed
  `drop-hw-accel:browsers` from orange & red. Browsers can now only be *throttled* (priority), never
  frozen. Orange/red now reachable only via real GPU-thermal/RAM emergencies (gpu-watch), not CPU.
- Sentinel restarted to load new config; 0 processes were stranded suspended.

No actual browser/system crashes found: only ONE `chrome.exe` hang (Event 1002) on 5/29 (consistent
with a suspension), and ZERO GPU display-reset / live-dump events (Id 4101/141) in 4 days — i.e.
opening a browser was NOT risking a real crash; it was CrashGuard suspending it. Net: browsers should
open without freezing now.
- **23:53 verify:** fix confirmed effective. Sentinel=1 (dup reaped); NO new browser suspensions
  after the fix; CPU spikes to 100% now escalate only to **yellow** (throttle), never orange/suspend
  (e.g. `cpu_util_pct=100 (->yellow)`). Keepalive healthy, AER=0, crash-free since 08:00 (~16 h).

## 2026-06-02 — whole-desktop freeze when opening Edge (deeper than the CrashGuard suspend)
User reported the WHOLE computer froze because Edge froze — the config fix above only stopped
CrashGuard suspending the browser; this is a separate GPU-layer issue. Findings: 1 Edge window
"not responding", Edge HW-acceleration ON, NO 4101/141 TDR-recovery event and NO bugcheck. Mechanism:
Edge's GPU process stalls on the marginal dGPU/595.97, and because the desktop compositor (DWM)
shares that GPU the whole screen freezes — and **TdrDelay=300** means Windows waits up to 5 MINUTES
before resetting the GPU (so no 4101 is logged and the freeze is long).
Fixes:
- **Immediate (non-admin, done):** force-closed the hung Edge; disabled **hardware acceleration** in
  Edge AND Chrome via each browser's `Local State` (`hardware_acceleration_mode.enabled=false`;
  HKCU policy route was ACL-blocked). Browsers now render on CPU -> can't stall the shared GPU.
  Backups at `Local State.cgbak`. Reversible (set back to true / re-enable in browser settings).
- **Needs admin+reboot (added to crashguard-apply-admin-hardening.ps1):** lower **TdrDelay/TdrDdiDelay
  300 -> 8** so any future GPU stall recovers in seconds, not minutes. (TdrLevel stays 3.)
- Root fix remains the 595.97 driver update (weekend pause).
Post-fix: GPU idle/responsive, keepalive OK, no crash.
- **6/2 01:23 verify:** Edge HW-accel still OFF; GPU shows only the keepalive (114 MB, no Edge GPU
  process); no 4101/141 TDR-recovery or bugcheck since 6/1 23:00; keepalive OK, AER=0; no new browser
  suspends. (Edge not reopened yet at this hour.) Awaiting user confirmation that reopening Edge is smooth.
- **6/2 02:24:** stable. No bugcheck/TDR since 6/1 23:00; keepalive OK (AER=0); Edge HW-accel still False. No changes.
- **6/2 03:25:** stable. No bugcheck/TDR; keepalive OK (AER=0); Edge HW-accel still False. No changes.
- **6/2 04:26:** stable. No bugcheck/TDR; keepalive OK (AER=0); Edge HW-accel still False. No changes.
- **6/2 05:29:** stable. No bugcheck/TDR; keepalive OK (AER=0); Edge HW-accel still False. No changes.
- **6/2 06:30:** stable. No bugcheck/TDR; keepalive OK (AER=0); Edge HW-accel still False. No changes.
- **6/2 07:31:** stable. A transient 3-event corrected-AER cluster at 07:01:56 (2 s burst, then none;
  aer_rate_5min back to 0, no storm) — keepalive held the GPU in D0 through it, NO device-loss/TDR/crash.
  This is expected on the marginal link: corrected errors stay corrected. Keepalive OK; Edge HW-accel
  False. Watching in case AER trends up (would point to hardware degradation -> driver update/reseat). No changes.
- **6/2 08:33:** stable. AER back to 0 (07:01 cluster was a one-off, not a trend); no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 09:34:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 10:35:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 11:36:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 12:37:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 13:38:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 14:39:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 15:40:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 16:41:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.
- **6/2 17:42:** stable. AER=0; no bugcheck/TDR; keepalive OK; Edge HW-accel False. No changes.

## Secondary code 0x19C (WIN32K_POWER_WATCHDOG_TIMEOUT) — same family
0x19C (5/31 22:30, 5/3) = "Win32k did not turn the monitor on in time" — a display/graphics driver
**wake/power-transition** failure, i.e. the same 595.97 power-transition bug at the display layer.
Already largely covered here: keepalive keeps the GPU in D0 (helps display-off->on, which does not
suspend the keepalive), and `sleep-defender` holds system+display awake via SetThreadExecutionState;
AC monitor-timeout & standby are already "Never". Residual gaps: DC (battery) standby = 4 min, and
the underlying driver bug. Root fix = driver update. Full system-sleep->wake would suspend the
keepalive, so the rig should stay on AC / never fully sleep (it already does while CrashGuard runs).
