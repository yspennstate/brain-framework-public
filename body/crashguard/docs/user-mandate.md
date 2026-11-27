# User mandate (summary, 2026-05-27)

The machine had crashed several times. The request, in short:

- Find the cause of the crashes and build methods so they stop happening.
- Set up systems that start silently in the background at boot and, when a crash is about to happen, back off whatever is causing it instead of crashing. Warning messages are acceptable.
- Audit the setup, replay every earlier shutdown against it, and run hundreds of simulations and audits.
- Closing applications, slowing work down or moving load from CPU to GPU are all allowed.
- Fold the existing background protections into one system, and keep testing.
- Run a watchdog that wakes the agent every minute with this request, so the build, test and audit work continues for at least 20 hours, until the system is proven, while still letting the machine run as smoothly and quickly as possible.
- Build the policies around the workloads that usually run on the machine: a protected production track, several observers, and many concurrent agent sessions.

## Distilled to-dos (the persistent loop should do these each wake)

1. Verify all 4 CrashGuard services healthy via `crashguard-health-check.ps1`. If anything stale, `crashguard-launch-all.ps1`.
2. Check no new BSODs since last wake — `Get-WinEvent -FilterHashtable @{LogName='System';ID=1001} -MaxEvents 5`. If new, fingerprint and add to replay corpus.
3. Run another 500-round audit + edge-case batch. Append to results.
4. If any audit failed, investigate; refine thresholds or policies.
5. If everything passed, expand: add new scenarios, new mitigation actions, refine the workload policies for the specific apps the user names.
6. Once-an-hour: run a live CPU+RAM stress and confirm sentinel reacts.
7. Track cumulative test count in `state\test-counter.txt`. Goal: thousands more by end of 20-hour mandate.
