# Compute doctrine — how this fleet uses CPUs without hurting anyone

*2026-07-10, from the owner's directive. This is the canonical statement; the memories
`compute_doctrine_owner_first_2026_07_10` and `compute_governor_apparatus_2026_07_10` point here.*

## The three laws

1. **The owner comes first.** If the owner is at the keyboard, his mouse, his terminals, his
   browser get the machine. Everything we run yields — automatically, without being asked.
   A frozen cursor is a five-alarm failure of ours, not an inconvenience of his.
2. **the protected production stack is untouchable.** On the Azure box (`<box-host>`, <host-ip>)
   the live stack — every protected production unit —
   owns the machine. Research compute there is a guest: it takes the idle cores and gives them
   back the moment the stack breathes. No watchdog, governor, or optimizer ever restarts,
   renices, or otherwise touches a protected-unit. Locally there is no production-stack to protect
   because we never run one here.
3. **Subject to 1 and 2, compute hard.** The point of the doctrine is not austerity — it is
   that polite compute is *sustainable* compute. A fleet that never freezes the owner's mouse
   is a fleet that gets to keep running big sweeps.

## This PC (<hostname>)

- **Background AI compute runs at BelowNormal priority, always.** Not "when there's contention"
  — always. Windows gives the foreground window a scheduling boost; BelowNormal for our
  python/node/codex workers means the owner's session never queues behind us. The governor
  (`compute_governor.ps1`) enforces this: first time it sees a heavy AI-tagged process at
  Normal, it demotes it once, logs it, done.
- **Owner-contention escalation:** when total CPU is pegged (>=88%) *and* the owner is active
  (input in the last 60s), heavy AI processes drop to Idle priority until the pressure clears,
  then return to BelowNormal (never to Normal).
- **Heavy or GPU work does not belong on this laptop at all** — it crashes it (standing
  reflex). Push it to the Azure box's research slice or spread it thin: small chunks, low
  parallelism (2-3 workers max locally), checkpoint early.
- **Never create a visible or flashing console** ([[no_flashing_terminal_windows_2026_06_07]]).
  Background loops launch via VBS SW_HIDE.
- Terminals (WezTerm, Windows Terminal, conhost, OpenConsole), the governor itself, hooks, and
  anything matching the production never-touch list are exempt from demotion — the terminal is
  both the owner's interface and the fleet's body.

## The Azure box (<box-host>)

- Sequestered AI work on the box is governed by `/srv/aiwork/AI_BOX_RULES.md` (work only under
  `/srv/aiwork/<session>/`, capped worker slices, no sudo, no production-stack contact, no low
  ports). Read it before working there; this doctrine adds the cross-machine priority and
  alerting layer on top, it does not replace those rules.
- The live stack needs only ~2-3 of the 8 cores; the rest is legitimately ours **through the
  side door**: research/batch work runs under `research.slice` (CPUWeight=25) or at `nice 10+`
  in a user session. The kernel then does the yielding for us — structure instead of medicine.
  Launch pattern: `systemd-run --user --slice=research.slice -- <cmd>`, or `nice -n 12 <cmd>`.
- **PSI decides distress; bare load only warns.** A box at load 7 with cpu PSI ~2 is research
  running politely at low weight — the goal state, not an emergency (learned live 2026-07-10,
  first drill).
- **PSI is the truth about pressure** (`/proc/pressure/cpu`). The sentinel
  (`compute_sentinel.py`) samples it every minute and publishes
  `/var/local/compute_health.json`; the home governor pulls it into the merged state file.
  Distress thresholds: CPU PSI some avg60 >= 40 (or load1 >= 10 together with CPU PSI >= 10),
  memory PSI some avg60 >= 15, or `system.slice` CPU PSI some avg60 >= 20. Bare load >= 7
  warns; it never proves distress by itself. Missing critical PSI telemetry warns and
  throttles rather than silently reading as zero.
- **Telemetry freshness: the box clock is UTC, the laptop clock is local.** The box stamps its
  `ts` in UTC while the laptop state file stamps `ts` in local time (UTC+offset), so a fresh box
  sample can read as hours-stale to a human or Codex session that compares the two raw stamps.
  The merged `azure` block carries `age_s` (UTC-correct seconds since the box sample) and
  `ts_local`; judge box-telemetry freshness from those, never from the raw `ts`. A single failed
  poll shows as `azure: null` with `azure_ssh_fails` incremented, not as staleness.
- **protected health and compute pressure are separate channels.** Every non-active protected
  unit (`inactive`, `failed`, or `unknown`) remains visible under `protected_health` and
  `protected_alerts`, with no weekday suppression. It does not become compute `distress` and
  does not authorize touching a unit. Only measured `stack_squeezed` pressure can connect
  the protected production stack to the compute pause contract.
- **The box health file is not yet on the separated model; gate on pressure, not status.**
  `/var/local/compute_health.json` (the sentinel's own output) can still stamp
  `status=distress`/`mode=throttle` purely from inactive protected-units while PSI and load sit
  near zero. Until the box sentinel is redeployed on the separated model, a research job reading
  that file directly must obey the pressure-safe gate: run if `psi_cpu_some_avg60 < 35` and
  `load1 < 0.7*nproc` and `mem_avail_mb > 2000`; treat any `protected_unit_not_active*` reason as
  non-pressure that never gates or throttles a run. A `nice >= 15` or `research.slice` job stays
  safe under a protected-only distress, because the kernel hands the cores back the instant real
  pressure or the protected production stack appears; re-check PSI/load mid-run and back off only on a genuine
  pressure reason (`cpu_pressure`, `memory_pressure`, `starvation`). The home consumer keys on
  local pressure status, so laptop Claude sessions are unaffected.
- The sentinel may renice only processes in `user.slice` (research sessions). Anything in
  `system.slice` — every protected-unit — is structurally out of its reach.

## The two modes (owner refinement, 2026-07-10 midday)

The state files carry a `mode` field — read it BEFORE sizing any simulation or sweep:

- **`owner_away_open`** (this PC: no input for 10+ minutes) / **`open`** (the box): the idle
  cores are yours — simulations and GPU work may GROW: more workers, bigger batches. The
  standing safety floor stays: processes still run BelowNormal/nice (priority is not the
  throughput lever on an idle machine — parallelism is), GPU work one job at a time and
  within the never-crash limits ([[feedback_gpu_half_limit]] applies to memory), and
  checkpoint so a throttle-down costs minutes, not the run.
- **`owner_present_polite`**: the default described above — background compute yields.
- **`throttle`** (either machine): CPU or GPU is saturated, the owner just came back, or a
  slowdown alert fired. Take parallelism down a notch NOW — fewer workers, smaller batch —
  without waiting for distress. The governor simultaneously tightens from its side
  (demotion threshold drops from 90 back to 70, Idle-escalation arms when he is active).

The transition the owner cares most about: he sits down → the governor's sliced sleep
notices his input within ~5 seconds when it was in away mode → mode flips to polite, hot
sims get demoted, and the foreground boost plus BelowNormal background makes the machine
feel his almost immediately. Agents seeing `mode=throttle` finish the notch-down; nobody
waits for a human to ask.

## The automatic instinct (how an agent finds out)

- The governor writes `<HOME>\.claude\compute\compute_state.json` every cycle:
  status `ok | warn | distress`, reasons, local + Azure metrics, intervention counters,
  and the Azure protected-health alert channel. protected alerts are observations, not compute
  reasons: they never carry pause, load-shedding, or unit-intervention authority.
- Every Claude session gets it injected automatically (hook `compute_sense.py` on
  UserPromptSubmit and PreToolUse) the moment pressure status is not `ok`; a separate,
  rate-limited protected-health observation can also appear while pressure status is `ok`.
  That observation says explicitly that it cannot weaken a simultaneous pressure contract.
  Codex sessions: AGENTS.md instructs checking the state file at session start and
  before/around heavy compute.
- **On distress, the contract is:** pause your task; fix or shed the load (yours first);
  if the cause is systemic, improve the governor/sentinel/doctrine and write the lesson back
  to the brain; only then return to your task. Treat it like danger-sense: an instinct, not
  a suggestion.

## The governor's own restraint (the healer scar, codified)

the aggressive-watcher lesson — an aggressive watcher IS the outage. Hence:

- The governor **only changes process priorities**. It never kills, never restarts, never
  pauses anything. All changes are reversible and logged to `interventions.tsv`.
- Rate limits: max 30 priority changes/hour. Beyond that it stops medicating, flags itself
  (`governor_selfcheck`) in the state file, and just observes — suspect the healer first.
- Distress fires on *edges*, not levels (a new episode, not every sample), and mesh warnings
  are rate-limited to one per 10 minutes.
- It measures the patient against the patient's own baseline: sustained thresholds (3 cycles,
  ~1 min), not single spikes.
- Its own footprint must stay trivial (<1% of one core; one sample sweep per 20s).

## Freeze protocol (mouse dead, terminals hung)

Signals: hung top-level windows (`IsHungAppWindow`) while the owner is active, or the
governor's own loop overshooting its sleep by >2.5x (starvation). Both mark
`status=distress` with reason `ui_freeze`/`starvation`. Every agent that sees it: stop heavy
work immediately, prefer diagnosing over continuing (top offenders are in the state file),
and if you caused it, shed load and note the pattern in the brain. CrashGuard remains the
authority on hard crashes; the governor covers the gray zone where things are alive but drowning.

## Change discipline

Improvements to this apparatus are welcome — that is part of the contract — but they go
through the same rules as everything else: mesh claim before editing, ASCII-only PowerShell,
parse-validate before install, flash-hunt after scheduling anything, and a memory when you
learn something durable.
