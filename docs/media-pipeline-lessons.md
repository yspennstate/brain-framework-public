# Lessons from an agent-run media pipeline

Several agents on the reference deployment render short videos, review them, publish them and clear the local
disk afterwards, with no person in the loop for most of a day. Each rule below was learned the hard way: the
pipeline did the wrong thing quietly, and the fault surfaced only when somebody counted the output. They are
written for any pipeline in which agents make files and other agents ship them.

## 1. Balance by what was published today, not by what each target holds

The publisher chose its destination by the smallest lifetime count, so that output would spread evenly. The
destination with the largest back catalogue therefore never came up, however little it had received that day:
it held several times more than the others, and a rule that ranks by lifetime holdings ranks it last forever. A
morning's output went everywhere except where it was most wanted.

Rank by the count published in the current quota period, and let any destination with a stated share go first
until that share is met. Lifetime holdings answer a different question.

## 2. The publisher does not review its own file

The agent that made a file knows what it meant to put there and reads that into what it sees. The second agent
reads what is there. Publishing waits for a receipt from a different agent that names the file by its sha256, so
a file re-rendered after review needs a fresh receipt; a receipt that names only the file is worth nothing once
the file changes. `dreaming/receipt_gate.py` is a minimal version of this gate as a PreToolUse hook
(`python dreaming/receipt_gate.py --selftest` runs its controls both ways).

Two details matter in practice. A receipt records what the reviewer read (the script, the frames, both), so the
next reader knows how much it covers. And the check belongs inside the publishing script as well as in the hook:
a hook that times out on a loaded machine is a silent allow.

## 3. Delete locally only after the remote copy is checked

"Uploaded" is not "safe to delete". The offload step compares the remote copy's size and digest with the local
file, and deletes only on a match; a mismatch, or a remote listing that does not yet show the file, leaves it in
place and says so in the log. Remote listings can be paginated or capped: an asset that exists can still be absent
from the first page, so a missing entry is a reason to look again, never a reason to upload a second copy or to
give up silently.

## 4. Time the stages before adding lanes

Two render lanes in parallel produced no more files per hour than one, because the slow stage was the copy through
a relay host, not the render, and the two lanes simply shared it. One copy also dropped mid-transfer and passed on a
retry. Before adding parallel workers, log each stage's wall time; if the transfer dominates, send one archive
stream over one connection instead of many small copies, and keep a single lane until the bottleneck moves.

## 5. Wrappers do not expand globs for you

A file pattern passed through a wrapper to a remote shell arrived as a literal string, matched nothing there, and
the step failed in seconds with a line that was easy to miss in a long log. Resolve names on the side that has the
files, pass exact names, and treat a step that finishes far faster than usual as a failure until shown otherwise.

## 6. Do not edit a script while it runs

Shells read a script in pieces as they execute it. Rewriting a running script in place shifted the bytes under the
reader, and the run ended with "command not found" on a line that was never written. Write the new version beside
the old one and rename it into place, or wait for the run to finish.

## 7. A held item says why it is held

A queue that only counts "held" items invites an agent to release them in bulk. Each hold records its reason
(no receipt, receipt for different contents, a reviewer's flag, a remote check that did not match), so the next
agent can clear the ones whose reason has gone and leave the rest alone.
