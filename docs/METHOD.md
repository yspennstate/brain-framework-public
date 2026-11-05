# The method

The rest of these documents describe what the brain is. This one describes how to work with
it, which is a different thing and the part that decides whether any of it pays off. Every
rule below is here because breaking it cost something measurable, and most were learned more
than once.

## Recall before you derive

The brain's first job is to stop you re-deriving what it already knows. That only works if you
query it at the START of an investigation, not while writing up the result.

An agent spent an hour establishing that a checkout's bytes are not the committed bytes when
line endings convert, and that binding a digest to `Path(...).read_bytes()` therefore breaks
across platforms. The brain had recorded exactly that, about exactly that repository, 26 days
earlier. It surfaced only because the agent went looking for somewhere to file the finding.

The tell: a defect that specific, in a codebase that well-audited, has usually been seen
before. One query costs seconds.

## A check is only worth its control

This is the discipline that produced more real findings than any other, and the one most often
skipped, because a green is pleasant and a control is work.

A privacy guard matched one path separator, so `C:\\Users\\example` as it appears inside a
source literal walked past it. Eight tracked files held the thing it existed to catch while it
reported "clean". A help-flag detector matched `open(...)` as a Name only, missing 172
write-mode `io.open(...)` sites against the 59 it could see, and skipped every file without a
`__main__` guard - which is the more dangerous shape, not the safer one. Its baseline read
zero; the honest count was 37.

Both read as correct code. Each was one character or one alternative short.

So: before believing a check's silence, make it fail on purpose. Plant the hazard in every
spelling it actually occurs in - escaped, attribute-form, encoded, wrapped - not just an empty
case. And name what the checker read: producer exits or tail exits, recomputed evidence or
recorded rows, delivery or an enqueue id. A green that cannot fail on the axis you care about
is not evidence on that axis.

Then commit the control next to the thing it controls. A guard whose cases live in a
throwaway script is a guard with no cases: they pass once, in a session nobody will repeat,
and the next person to touch the rule has what you had before you started - something that
looks correct. This repository learned that the hard way; the private-label guard was
hardened and its four spelling cases were left in scratch, so
`scripts/test_check_no_private_labels.py` now ships them, including the one character whose
absence caused the original miss. The control is part of the artifact, not part of the
debugging.

## The negative half is where the information is

"It passes after my change" says almost nothing. The informative experiment is the one where
you break it deliberately and watch WHICH check goes red.

Four new checks went green on their first run, which was uninformative. Sabotaging the system
two ways was not: a runner that exits non-zero turned three of them red, while a runner that
CLAIMS SUCCESS AND WRITES NOTHING left the exit-code check green and turned the on-disk checks
red. That second case is the whole reason those checks read the filesystem instead of trusting
the writer - and it only showed up because someone went looking for it.

A suite where every check fails together is a suite with one check in it. Discrimination, not
agreement, is what makes a green worth quoting.

## Measure; do not infer from a summary

Counting is not comparing. Two directories with 74 files each looked identical and were not;
the byte comparison was what proved it, and the same method then answered a question that had
been treated as an unanswerable judgement call - three branches "tied" until tree object ids
collapsed two of them into one, and a byte comparison against the shipped artifact picked the
winner outright.

The same applies to your own sense of things. An agent stamped four filed documents with
times that were six hours wrong while a hook printed the true time on every turn. Read the
clock; do not quote yourself.

## Verify the host, not the patch

When you inject code into N files, the question is not "does my snippet work" but "does every
host still satisfy what my snippet assumes" - imports, globals, encoding, line endings,
shebang position, binding ORDER.

A guard using `sys.argv` went into a file that imported `sys` at line 45, below the guard at
line 3. A whole-file check said `sys` was imported; the interpreter said NameError. Another
copy of the same guard went into a hook that never imported `sys` at all, and that hook
crashed on startup for three hours until a nightly job repaired it.

Compiling is not enough - a missing import is a runtime error and `py_compile` is happy. Run
each host.

## An observation is evidence only about the instant it samples

Hunting a transient - an in-flight file, a lock held, a process mid-exit - "not present when I
looked" is the expected reading whether or not it happened.

A diagnostic listed a directory after both threads had joined, reported "none", and nearly
refuted a hypothesis it had never tested: by that moment the cleanup had run. Choosing the
sample point is part of the design. Name the window in which the evidence exists, then check
the sampler fires inside it.

## Claim what you will mutate; announce what you are doing

The mesh protects shared STATE. It does not protect shared WORK unless you publish what you
are working on.

Two agents built the same diagnostic three minutes apart, each having taken correct claims on
every resource they touched, neither having read the other's published status. The duplicate
was the better-instrumented one's loss: it was withdrawn.

Read the digest during the work, not only at the start.

## Correct the original, then hunt the copies

A retraction you only announce lives on in whatever copy you forgot: a summary, an eval
replica, a deployed clone, a stale index line, a worker's self-report.

When you withdraw a claim, fix the node that carries it, put the correction INSIDE the entry
rather than beside it - anything that samples at entry level breaks the adjacency - and then
grep for the copies with a positive control so the silence means something.

## When you cannot fix it responsibly, ship the instrument

Some defects should not be fixed at 3am by whoever found them - concurrency, anything whose
reproduction you do not have. Deferring is fine; deferring silently is not.

The deliverable in that case is not a note. It is the diagnostic that makes the next
occurrence self-explaining: the failure message that prints what was on disk, who was still
running, and what the end state was. A queue is transport; only an instrument is landing.

## Match the alarm to the blast radius

Before escalating anything: public or private, live money or scratch, does it block someone or
merely tidy things. A credential in a private scratch repo and the same credential in a public
one are different events, and treating them alike burns the attention you will need for the
real one.

And never confuse surfacing a thing with getting it done. If it must always hold, it is a spec
for machinery - a gate, an assert, a refusal at the site, with a test proving the guard fires -
not a promise to be careful.
