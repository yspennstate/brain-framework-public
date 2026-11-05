# CrashGuard

**Goal:** Prevent the recurring 0x116 VIDEO_TDR_FAILURE BSODs on the reference machine (a mobile NVIDIA RTX workstation, driver 595.97) and other crash classes (0x13a heap, 0x19c DPC watchdog) without sacrificing performance for the workloads that matter (the human's interactive programs, the active agent session, and any protected production workload).

## Crash baseline (as of 2026-05-27)

- 12 BSODs in ~3 months
- 11 are 0x116 VIDEO_TDR_FAILURE with identical status code 0xc000009a (GPU INSUFFICIENT_RESOURCES)
- 3 today alone (01:02, 06:42, 07:34 — most recent reboot 10:36 after the 10:32 crash)
- Driver 595.97 is the common factor

## Design

Three cooperating components:

1. **Sentinel** (`bin\crashguard-sentinel.ps1`) — 1–2 s polling loop. Reads GPU temp/util/mem, CPU util, RAM pressure, NVIDIA process list, ETW driver-stall warnings. Computes a `Pressure` score and a `Tier` (GREEN → YELLOW → ORANGE → RED → CRITICAL).
2. **Mitigation engine** (`bin\crashguard-mitigate.ps1` + `mitigations\*.ps1`) — Tier-driven actions. Lowest-impact first. Always reversible. Logs every action.
3. **Policies** (`configs\policies.json`) — Per-app rules. Critical apps (the human's interactive programs, the foreground agent, protected production) are preserved; background workloads (extra Claude/Codex, observers, ollama, browser tabs) are throttled or suspended first.

## Auto-start

Scheduled task at user logon, hidden window, restart-on-failure. See `bin\crashguard-install.ps1`.

## Watchdog of the sentinel

A separate `crashguard-watchdog.ps1` runs as its own scheduled task and confirms the sentinel is alive every 30 s. If the sentinel dies, the watchdog restarts it.

## Testing

- `tests\replay\` — replays historical pre-crash signatures and verifies a tier escalation would have been emitted in time
- `tests\stress\` — live failure injection (GPU/thermal/RAM stress)
- `tests\unit\` — pure-function detection/policy tests
- `audits\audit-runner.ps1` — randomized scenarios, hundreds of rounds

## Reversibility

Every mitigation is reversible. State changes are recorded in `state\active-mitigations.json` so the sentinel can roll them back when pressure subsides.
