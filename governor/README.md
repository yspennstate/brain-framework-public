# compute_governor - the fleet's compute-priority apparatus

Built 2026-07-10 from the owner's directive: compute a lot, but his PC must never slow down
and the protected production stack must never be affected. `COMPUTE_DOCTRINE.md` is the canonical
statement of the laws; this README is the operator's manual. Memories:
`compute_doctrine_owner_first_2026_07_10`, `compute_governor_apparatus_2026_07_10`.

## Pieces

| piece | where it runs | what it does |
|---|---|---|
| `compute_governor.ps1` | <hostname>, hidden loop (task `ComputeGovernor`, at logon) | samples cpu/mem/input/hung-windows every ~20s; keeps AI compute at BelowNormal (Idle during owner contention); publishes `<HOME>\.claude\compute\compute_state.json`; ledgered demote-only medicine, self-suspends past 30 changes/hr |
| `governor_alive_check.ps1` | task `ComputeGovernorAliveCheck`, every 30 min | relaunches the loop if pid dead or heartbeat stale (>3 min) |
| `azure_poll.ps1` | detached child of the governor, every ~5 min | fetches box health over ssh with a hard 15s kill; writes `azure_last.json`; degraded fallback can prove distress only from CPU PSI, never bare load; the loop never blocks on the network |
| `azure/compute_sentinel.py` | <box-host>, `compute-sentinel.timer` (60s, root) | publishes `/var/local/compute_health.json`: pressure `status/reasons/mode`, separate `protected_health/protected_alerts`, PSI, load, 8 protected-units observed (never touched), and renice-only user.slice medicine (owner's user-1001.slice exempt), 20/hr ledger cap |
| `azure/research.slice` | <box-host> | CPUWeight=25 home for research: `systemd-run --slice=research.slice -- <cmd>` |
| `compute_sense.py` | `~/.claude/dreaming/`, hook on UserPromptSubmit + PreToolUse | injects pressure distress/warn/stale-governor plus a distinct non-authoritative protected-health observation; pressure always has priority |
| `mesh_broadcast.py` | called by the governor on distress edges | warns every live mesh agent (codex included), 10-min rate limit |

## Check it is alive

```powershell
# heartbeat (should be < 60s old) + current status
powershell -File ...\compute_governor.ps1 -Status
Get-Item <HOME>\.claude\compute\compute_state.json | Select LastWriteTime
# the healer audit - a high count means suspect the GOVERNOR, not the machine
Get-Content <HOME>\.claude\compute\interventions.tsv | Select -Last 20
# box side
ssh -i ~\.ssh\<box-ssh-key>.pem <box-user>@<host-ip> "cat /var/local/compute_health.json"
```

## Repair

- Governor dead: `wscript //B launch_compute_governor.vbs` (or wait for the 30-min alive check).
- Full reinstall: `install_compute_governor.ps1` (idempotent). It synchronizes the tracked
  hook to `~/.claude/dreaming/compute_sense.py`, identity-checks and stops the detached old
  governor, registers both tasks via VBS SW_HIDE, starts a new PID, and verifies a new
  heartbeat. If you change launchers, re-verify with a <=50ms flash poll - a 300ms poll lies.
- **Schema rollout is consumer-first:** merge/pull the whole change on this PC, run the full
  reinstall above, and verify the new PID plus `azure_protected_health` in local state. Only
  then install a changed Azure sentinel. This prevents a producer from publishing a new
  alert channel before the live hook/governor can preserve and interpret it.
- Box sentinel: rerun the install block (scp the 4 files in `azure/`, then
  `sudo install ...` + `systemctl daemon-reload` + `enable --now compute-sentinel.timer`).
  Snapshot protected-unit states before and after and require them to remain unchanged. Never
  start, restart, or otherwise touch a protected-unit to make this deployment check pass.
- **Box unreachable** (`azure_unreachable` / `azure_sentinel_stale`, SSH times out): the home
  IP rotated and the NSG allow-list is stale. `azure_access/refresh_box_access.ps1` fixes it
  (needs a fresh `az login`); `azure_access/bootstrap_nsg_selfheal.ps1` makes it permanent -
  one owner `az login`, then a scoped service principal self-heals IP rotation every 10 min
  with no human. Full story: memory `box_access_selfheal_2026_07_10`.

## Design constraints (do not regress these)

1. The loop must stay unblockable: no inline network/Get-Counter calls - anything slow runs
   detached and talks through files (the 07-10 lesson: ConnectTimeout does not bound ssh).
2. Demote-only medicine, edge-fired distress, rate limits + ledger + self-suspension - the
   aggressive-watcher lesson.
3. Windowless everything (VBS SW_HIDE), ASCII-only ps1, parse-validate before install.
4. The sentinel structurally cannot reach system.slice; keep it that way.
5. protected-unit state is an observation channel, not a compute-pressure signal. Preserve all
   alerts without letting them trigger the fleet-wide pause contract; only measured
   `stack_squeezed` may do that.
