# User mandate (verbatim, 2026-05-27)

> my computer crashed again, look for dignosis, i want to start building methods so that this stops happening, this problem, first download everything you cna to ensure thid eso nto happen, second set up systmes that start silently that make sure that if this is about to happene then rather then crashing we toggle back on what is cuasing the problme, it can even end me warnng meggssages, and it should always start silently in the backround when i start the computer, then audit and make sure that it is implentinted correctly, and none of the previous shutdowns, and there have been a lot, woudl have gotten through, run simulations, hundrest, and hundres of audits, and start a new project for this, making sure the computer does not shut down. we can even close application, slow things down, move ting for cpu to gpu and so on, i don't care how, also check that all of the current backround method to ensure tings like this get incorpreated into the whole system, and again run multiple test, just keep running tests. setup a watchdog that wakes you up every minute, fi you ever stop working, and keep giving you this propt and tell you to keep building the system, but you shoudl remember yourslef to simply keep building and experimenting, and this build and testings and audit thing shoudl take at least the next 20 hours until we are sure tat the project is built proof, and idely sallows things to run as smothly and quickly on the computer as possible without slowing down unless needed and while using all fo the computational capacilities of this computer perfectly

> (paraphrased) also check the workloads that typically run on this machine - the protected production track, multiple observers, many concurrent agent sessions - and build the policies around them.

> make sure to include that watchdog i mentioned and have it give you this full rpt iw wrote, and work on this fro the next 20 hours, and test and audit and simulate to make sure everythng is working perfectly

## Distilled to-dos (the persistent loop should do these each wake)

1. Verify all 4 CrashGuard services healthy via `crashguard-health-check.ps1`. If anything stale, `crashguard-launch-all.ps1`.
2. Check no new BSODs since last wake — `Get-WinEvent -FilterHashtable @{LogName='System';ID=1001} -MaxEvents 5`. If new, fingerprint and add to replay corpus.
3. Run another 500-round audit + edge-case batch. Append to results.
4. If any audit failed, investigate; refine thresholds or policies.
5. If everything passed, expand: add new scenarios, new mitigation actions, refine the workload policies for the specific apps the user names.
6. Once-an-hour: run a live CPU+RAM stress and confirm sentinel reacts.
7. Track cumulative test count in `state\test-counter.txt`. Goal: thousands more by end of 20-hour mandate.
