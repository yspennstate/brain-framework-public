# What the brain does when the machine is saturated

This note collects cases from one long-running installation of the framework: a single workstation shared by many coding agents, film renders and background jobs, which spends long stretches near its memory limit. On the evening these notes were gathered, the commit charge sat at 94 to 98.5 percent of 122.7 GB, with 1 to 5 GB of RAM available, a page file grown to 37 GB, cold file opens of 1.6 s, and 35 s to start a shell.

Under that kind of load, most of the brain's useful work is diagnostic and preventive rather than computational. It tells an agent what is actually wrong: paging rather than hung, starved rather than dead, the page file rather than new files. It stops agents from making the pressure worse, by killing work that would restart from zero, stacking duplicate copies, or launching into a full commit charge. And it carries each fix forward as a memory and a reflex, so the next agent does not rediscover it at the same cost. It has a price too: under the same pressure the hooks get slow, and some of the brain's own jobs time out.

These are cases, not a controlled measurement. The last section says how to measure the effect.

## Telling slow from stuck

On a saturated machine a slow job and a hung job look alike from outside, and the obvious move, killing it and starting again, is usually the expensive one.

- A speech-generation job sat for over an hour with one thread, about 2 CPU seconds, no GPU memory and an empty log, at 92 to 96 percent commit. It was paging: page faults climbed while read bytes stayed flat. A kill would have thrown away the model load done so far. The memory written afterwards gives the check, which is to watch page faults for 15 s before any kill.
- A proof-assistant build ran 78 minutes and was read as progress. It was hung: 50 CPU seconds against 78 minutes of wall clock, and no compiler child process. The rule since then is to compare CPU time with wall time before trusting a long run.
- A cleanup pass held its lock for three hours with no output and near-zero CPU. It was one thread waiting to run at low priority on a box at 100 percent CPU: starved, not hung. The fix was to raise that one process to normal priority, which is reversible, rather than kill it.
- A render coordinator at zero CPU looked starved. It does no work itself, and its children were busy. Measure the children before changing anything.
- The other direction: two idle orphan processes held 774 MB. What made stopping them safe was that their output already parsed cleanly on disk, not any sign of life from the processes.

## Finding where the memory and the disk went

- Free disk fell from 23 GB to 9 GB in an hour while no file grew. At 96 to 99 percent commit, Windows grows the page file (here 32 GB initial, 96 GB maximum) and gives the space back only at a reboot. The disk-low hook now points at the page file before anyone goes hunting for files to delete.
- The reverse coupling: with the disk nearly full, the page file cannot grow, the commit limit stops rising, and allocations fail with MemoryError, failed process starts and hung windows. In one case the page file grew from 46.9 to 51.0 GB, and the commit limit from 101 to 120 GB, before it hit the wall.
- A speech-checking worker was assumed to be light. Each instance holds 4.4 to 4.5 GB of private commit once it is running; the 2.2 GB seen at load time was misleading. Near the limit, the page file grew from 40.5 to 55.6 GB in a day. The lesson is to gate launches on free commit and free disk, not on a process count.
- A false alarm prevented: free disk read 6.9, 4.5 and 4.1 GB over 15 minutes, and an alert to the operator was drafted. Seven minutes later it read 10.4 GB, and the alert was never sent. Free space breathes with memory load, and the brain now says so before anyone raises an alarm.

## Not making it worse

- The installation runs under an operator rule that no agent stops or throttles running work to free resources, because a killed render starts over and costs more than the memory was worth. Every distress hook carries that rule, so an agent that sees a critical-memory signal reports the largest holders instead of killing them.
- Gate on the resource the job actually consumes. New agent workers here sit behind a launch gate that waits for at least 2.5 GB available and commit at or under 92 percent. Over 34 minutes on the evening of these notes it held two workers at every check (2.8 to 5.2 GB available, commit 0.94 to 0.98), rather than letting them start into a commit charge already at the edge. The gate came out of an earlier day with two freezes. One governor was blind to the GPU; the other governed threads while memory filled.
- Duplicates multiply on a starved machine. An uploader took its lock only after an hours-long walk of its input tree, so ten copies stacked up and none uploaded for 22 hours. Lock first, walk second. In another case, 41 copies of one helper each read every process command line every 20 s, at about 15 percent of a core each. A useless-work hook now asks for that check first.
- Priority can be the bottleneck. A git fetch took about 12 minutes at low priority and 26 s at normal priority, on the same clone at the same time. Push and fetch jobs now launch at normal priority.

## Scars that fire when they are needed

The mechanism behind most of the above is the reflex layer: a short rule linked to the memory of the incident that produced it, which surfaces when an agent's action matches. Three fired on the evening of these notes.

- While text was being appended to a worker's instruction file, the exact-name hook surfaced a scar from earlier that day. A one-line rewrite had opened its output for writing before reading its source; when the source read failed, it emptied three instruction files to 0 bytes. The append went through a plain append instead, and the file was checked afterwards.
- A slowness hook reported 1.6 s cold file opens with the virus scanner at 19.8 percent CPU, and said plainly that re-running the scanner speed-up would do nothing. That headed off a fix aimed at the wrong cause.
- The disk hook pointed at the page file before suggesting any deletes, and restated the rule that data leaves the machine only once two independent copies hold it.

## The brain making itself cheaper

Saturation also exposed the brain's own waste, and several fixes came straight from jobs that died at their time caps.

- A nightly audit read 19 whole transcripts (33.7 GB) for a one-day window and was killed at its 2,700 s cap. It now seeks straight to the window: 94 s, with the same results on 4 of 4 checks.
- An index check ran a 14.7 MB substring scan once per memory file and hit the same cap, leaving 2,377 memories unindexed. It is now a set lookup: 119 s, same result.
- A staleness check parsed a 55 MB JSON index just to get its key set, and one run sat in it for over 34 minutes under load. It now reads the keys from a compact sidecar: 56,416 keys in 0.11 s.
- Recall at about 2.1 s per call was measured as CPU-bound rather than waiting: 24.3 s of CPU against 25.1 s of wall over 8 calls. Turning off thread spinning in the embedding runtime saves about 40 ms a call.

## What it costs

- Session wake-up took up to 6.5 minutes on a loaded day, and compactions now re-serve a stored copy instead of re-running it.
- On one night the nightly dream's start-up hooks took 73 minutes before its first turn, and a single shell `date` took ten minutes.
- A regression gate for the math brain came back inconclusive on the evening of these notes, because the hook dispatcher timed out at 300 s and recall at 1,800 s. It could say only that the machine was too loaded to tell.
- Agent workers stayed alive but made little progress, with tool calls taking minutes. The brain's rules kept them from being killed and restarted; they did not make them faster.
- Hook output is context. Each prompt that evening carried several kilobytes of distress, disk and inbox lines, paid again on every turn.

The two freezes mentioned above were not prevented by the brain. What it did was turn them into the launch gate, which then held.

## How to measure it

A fair test runs matched tasks on the same loaded machine with the hooks on and off, and counts:

1. wrong kills (a slow job killed and restarted) and the work each one lost;
2. time from the first symptom to the correct diagnosis;
3. launches into a commit charge above the gate;
4. false alarms raised to the operator;
5. the hooks' own cost in wall time and context tokens.

The cases suggest the largest difference is in the first two. That stays a hypothesis until the paired run is done.
