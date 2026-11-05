# Dreaming

The dream is the brain's offline pass. While no one is working, it reads what the day added, files it into the
store's existing structure, links what should be linked, turns repeated mistakes into reflexes, and grades how
well yesterday's reflexes fired. Agents working in the day write records quickly and in pieces; the dream is what
keeps those pieces from becoming a pile.

This folder is the full version, taken from a working system. A smaller version that needs only Python and any
agent command-line tool is in [`examples/field_brain_starter/dream/`](../../examples/field_brain_starter/dream/README.md);
start there, and come here when the starter's six agenda sections are no longer enough.

## The two halves

A dream has a mechanical half and a model half.

**The agenda.** `dream.py` scans the memory corpus with no model call and writes `DREAM_AGENDA.md`: what tonight's
session should look at. Its choices imitate sleep pressure: recent records, pairs that look related but have no
link, beliefs that a newer record may have overtaken, links that point at records nobody wrote, and a random draw
of old and unlinked records so that each night differs. The sections it writes are:

| section | what it holds | what the dreaming agent does with it |
|---|---|---|
| A | recent records that may tell one story | writes one consolidated summary and links the sources; sources are kept |
| B | pairs that look related but are not linked | links them with a clause saying why, or skips a false echo |
| C | beliefs a newer record may have overtaken | appends a `superseded:` note pointing at the current fact; never deletes |
| D | records the corpus keeps linking to but nobody wrote | writes them from what the corpus already knows, or repoints the links |
| E | a random replay of old and unlinked records | wires in the ones that still matter |
| F | one painful record and one good one, by salience | for a mistake with no reflex yet, walks back from the bad outcome to the step that caused it and drafts that step as a reflex |
| G | two vivid records from distant corners | tries a real analogy; most nights the answer is no |
| H | the archive of the requests the agents were given | checks every request was saved and classified |
| I | predictions whose date has passed | marks each hit or miss |
| J | yesterday's reflex fires, and stale-fact pairs | judges a sample of fires on-domain or off; resolves the pairs |
| M | a rotating sample of corrections | about a third of the night: is each lesson learned, does it recur, is there a reflex |

**The session.** `run_dream.ps1` builds the agenda, starts one headless agent session with a prompt, watches it with
a hard timeout, and then runs the mechanical tail: it compiles the reflexes (`instincts.py`), rebuilds the search
index (`bm25.py`) and the link files, refreshes the views the wake step reads, measures the reflexes' precision from
the night's judgments, and runs `hook_liveness.py`, which reports any part of the brain that has stopped firing.
Every step runs with its own time limit and its exit code checked, because a step that hangs holds the dream's
lock and cancels the next dream, and a step that fails in silence is a step nobody notices for days.

## Three kinds of dream

The runner picks the kind from the hour (before 09:00 a filing dream, 09:00 to 16:00 a nap, after 16:00 a
connecting dream), each with its own prompt:

- **Filing** (`dream_prompt_sws.md`), early morning. Sections A to D: consolidate, link, mark what was superseded,
  rescue important records that have gone unread, repair dangling links. Tidy work that has to be reliable.
- **Connecting** (`dream_prompt_rem.md`), evening. Sections E to H and the mistakes: replay, the walk back from a
  bad outcome, the distant analogy, and parked questions that a newer record may answer. Most of the night's new
  reflexes come from here.
- **Nap** (`dream_prompt_nap.md`), midday. A few minutes: at most one summary, resolve due predictions, judge the
  fires the gates silenced. Light upkeep so the evening dream does not start from a full day.

`dream_prompt.md` holds the discipline every kind shares: refresh the state notes the wake step reads, keep the
predictions ledger, check the writing voice, and write a journal entry. The filing and connecting split follows
the sleep literature cited in the prompts (slow-wave sleep for consolidation, REM for recombination); in practice
its value is that the reliable work is never crowded out by the creative work.

## Rules the dream keeps

- It adds and annotates; it never deletes a record. A wrong record is marked superseded and linked to its
  correction, because the next agent needs to know the old answer was tried.
- It does not push to any repository; the runner commits after the session exits, so a session that dies leaves
  nothing half-written in the history.
- It runs windowless (`launch_dream.vbs` on Windows) at low priority, and a file named `STOP` ends it.
- A note it writes carries a status (`verified` or `provisional`) and the measurement behind it, so the agent that
  reads it later knows how far to trust it.

## Schedule it

On Windows, point a scheduled task at `wscript.exe //B launch_dream.vbs` three times a day (for example 03:30,
11:30 and 19:30), set to run only when the user is logged on and to not start a second copy. The starter's
`dream/README.md` has the equivalent lines for cron and launchd. Read the dream log for the first week. The two
ways this system decays without anyone noticing are a dream that stops committing its results and a part that
stops firing; the log and `hook_liveness.py` are where both show up.

## Make it fit your field

The sections are general; what changes is what counts as a mistake and what counts as evidence. A company brain
might give section C the policies a later decision replaced and section M the incidents that recurred. A film
studio might add a section for renders that failed review twice for the same reason. A lab might add one for
results that were never reproduced. Add a section to `dream.py` when you can say what the agent should do with
each item in it, and add a line for it to the prompt of the kind of dream that should work it.
