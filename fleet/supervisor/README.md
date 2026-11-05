# Codex continuity supervisor

This is the safe answer to “prompt a Codex terminal that stopped.” A stopped
process cannot receive keystrokes; its saved thread is resumed headlessly with a
fixed continuation envelope. The original context, disk state, brain, and mesh
remain authoritative.

Safety properties:

- explicit manifest enrollment only—historical/recent sessions are never swept in;
- absolute `service_until` plus evidenced active seconds, so a reboot does not
  restart a relative four-hour timer or count downtime as work;
- one timestamped roster row every minute in `events.jsonl`;
- at most two managed turns at once while the owner is present;
- existing matching processes are adopted, never duplicated or killed;
- atomic CreateNew launch locks and a named singleton mutex;
- rollout growth without an exact managed process is quarantined as ambiguous,
  never treated as permission to launch;
- `codex exec resume` without approval/sandbox bypass;
- native `codex.exe` (not the Node wrapper), with `--ignore-user-config`,
  enforced read-only containment, auto-deny approval policy, the supported
  unelevated Windows sandbox variant, and a minimal non-secret environment;
- a bounded, read-only owner-correction digest embedded in every continuation
  envelope, so `--ignore-user-config` does not also silence the four repeated
  ask/verification/last-hard-mile/timed-work warnings; up to two bounded lexical
  task matches are read from `instincts.tsv` without executing its regexes,
  fail-open, or writing reflex state. Low-validity domains are labeled as
  requiring current lived-data verification, and retired financial magnitudes
  are never copied into the prompt;
- every managed generation must finish with an exact `FLEET_CHECKPOINT v1`
  receipt carrying its generation and injected warning IDs, plus substantive
  hardest-unverified, verified-now, not-verified, and next-front fields;
- completion is accepted only after the new rollout suffix contains a matching
  final-answer / task-complete pair and the footer passes the shared durable
  owner-report contract. Tool chatter, an old final, malformed/truncated output,
  a stale financial oracle, or a sign/fixed-sides/leveraged-ETF contradiction
  queues a bounded corrective continuation instead of becoming â€œcompleteâ€;
- controller state stores only reason codes, warning IDs, turn/generation IDs,
  timestamps, and a final hashâ€”never the worker's answer prose;
- `pythonw` + VBS SW_HIDE + CREATE_NO_WINDOW + BelowNormal—no terminal window;
- the VBS returns the controller exit code and the installer validates both the
  runtime and the registered task definition;
- `STOP` prevents new supervision; `enabled:false` prevents a worker from being
  relaunched, and neither has a force bypass.

Validate without launching or writing state:

    python supervisor.py --validate
    python supervisor.py --once --dry-run
    python -m unittest discover -s tests -v
    python integration_probe.py

Install only after those tests and the <=50 ms flash hunt pass:

    powershell -File install_task.ps1

The current gate supports read-only Codex audit/verification turns. Write-capable
continuity stays disabled until the native elevated sandbox passes its integration
probe; the current helper exceeds the Windows command-line limit. Claude stopped-session resume uses the same
architecture but needs a separately tested lineage update (`claude -p --resume`
can return a new session id). Until that adapter passes, do not route Claude
through the unsafe visible `launch_one.ps1`. A still-running idle terminal is a
different transport problem: use the mesh when it polls; raw mailbox lifecycle
injection remains DARK until its untrusted-input security gate passes.
