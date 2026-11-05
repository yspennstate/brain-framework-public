"""Health check for the math-brain injection hook: does it still SPEAK?

WHY THIS EXISTS. math_sense.py is fail-safe silent by design - it swallows every exception
so that a broken brain can never break or slow a session that is not doing mathematics.
That is the right default and it has one nasty consequence: a hook that is completely
broken looks exactly like a hook that is correctly staying quiet. On 2026-08-30 a missing
constant made _select raise NameError, and every campaign query silently returned an empty
block for several minutes. Nothing looked wrong. It surfaced only because the one query
being tested by hand happened to take that code path; the other control passed throughout,
because it never reached the failing line.

So this asserts the POSITIVE side: queries that must produce a block, and queries that must
not. Silence where a block is expected is the failure this catches.

    python math_sense_healthcheck.py          exit 0 if healthy, 1 if not

Run it after ANY edit to math_sense.py or to math_brain.rendering, and before trusting a
measurement taken through the hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


# Overridable so the check can be pointed at a deliberately BROKEN copy, which is how
# its own negative control is run. A gate nobody has watched fail is not evidence.
HOOK = Path(os.environ.get("MATH_SENSE_HOOK",
                           Path(__file__).resolve().parent / "math_sense.py"))

# A must-speak block counts only when every card in it is whole - a claim id and a trust tag - read by the shared
# parser. Until 30 Sep a block over 120 characters passed, so eleven header-only blocks (no id, no tag) read healthy
# (account04/B07, B07-R79-INCOMPLETE-CARD-PASSES-REGRESSION-GATES).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from math_block_parse import delivery_problem  # noqa: E402

# Prompts that MUST return a block. Spread deliberately across the retrieval paths a change
# is likely to break: textbook material, campaign material, a claim node with an Assumes
# clause, a duplicate-title group, and one drawn from the newest appends.
MUST_SPEAK = [
    ("textbook/probability",
     "State the Rao-Blackwell theorem and what conditioning on a sufficient statistic does "
     "to the variance of an estimator"),
    ("textbook/analysis",
     "What does the dominated convergence theorem require, and what is its conclusion for "
     "the integral of a limit?"),
    ("textbook/duplicate-title group",
     "State Hoeffding's inequality for bounded independent random variables and give the "
     "tail bound"),
    ("campaign/first-passage percolation",
     "What is the block-increment exponent gamma measured for first-passage percolation, "
     "and what does the deterministic occupation lemma give for E[Delta_L^2] in a W by W "
     "block?"),
    ("campaign/wall reduction (was a gate miss until GREEK_ANCHORS)",
     "At which M is the block-chaos engine's inequality tight for chi = 1/3, and what value "
     "must beta take there?"),
    ("campaign/perceptron",
     "What is the verified enclosure for the Ising perceptron capacity constant alpha, and "
     "what proof assistant was used for the exact-arithmetic layer?"),
    # An enumerated numeric family: 2,000 near-identical self-avoiding-walk counts that differ
    # only in their parameters. Until 2026-09-05 the margin test read the runner-up (the same
    # sentence with other parameters) as a competing answer and went SILENT on 34 of 60 such
    # questions (the H1 test). A block must come back; which row is right is the reader's job.
    ("enumerated numeric family (H1 shape; margin rule 2026-09-05)",
     "Enumerative combinatorics on the lattice Z^2: how many self-avoiding walks of length n = 10 "
     "start at the origin (0,0), stay in the strip 0 <= y < 3 and avoid the site (-4,2)? Give the "
     "exact integer count."),
    # The plain question form an agent would actually type (the H1 test's known gate miss until
    # 2026-09-05 22:4x): one structural notation, and before ENUMERATION_ANCHORS zero word anchors.
    ("plain numeric-family question (was a gate miss until ENUMERATION_ANCHORS)",
     "How many self-avoiding walks of length 10 on the square lattice Z^2 start at (0,0), stay "
     "in the strip 0 <= y < 3, and avoid the sites (-4,2)?"),
    # 2026-09-11: three regressions in one week that a "did it speak" probe cannot see. (a) The on-topic
    # gate's raw BM25 floor (25) silenced one-line questions - fresh bank through the hook 84.3 -> 34.8 pct;
    # (b) the block budget of 1400 held ONE whole card after cards stopped being clipped, so the theorem at
    # rank 2 was "omitted whole for the context budget" - here Hoeffding sits at rank 2 behind a tensor tail
    # bound; (c) the cost gate had no plural ("Sylow theorems") and no combinatorics or algebra names. These
    # three probes require a NAMED title in the block, not just a block. The third form carries the word
    # "theorems" only, no notation, no other anchor.
    ("rank-2 theorem must survive the block budget (Hoeffding unnamed)",
     "Sum of n independent variables each bounded in [a_i, b_i]: I need an exponential tail bound for the "
     "deviation from the mean.", "hoeffding"),
    ("combinatorics with no anchor word (Turan unnamed; weak anchors 2026-09-11)",
     "Maximum number of edges in an n-vertex graph with no complete subgraph on r + 1 vertices, and the "
     "extremal graph.", "tur"),
    ("plural strong anchor (Sylow theorems)", "Sylow theorems.", "sylow"),
]

# Prompts that MUST stay silent: no mathematical content, so the gate should refuse before
# retrieval runs. A hook that speaks here is over-firing, which costs every session time.
# KNOWN GATE FALSE NEGATIVES: genuinely mathematical questions the cost filter refuses
# before retrieval runs. Reported every run, never failing - a permanently red check gets
# ignored, which is its own failure mode. The wall-reduction question lived here until
# 2026-08-30, when GREEK_ANCHORS fixed it; it is now a must-speak case above.
KNOWN_GATE_MISSES = [
    # (2026-09-05 22:4x) the plain numeric-family question lived here for three hours - it carried
    # no anchor the gate accepts - until ENUMERATION_ANCHORS (measured at +0 fires on 33,278
    # archived prompts) fixed it; it is now a must-speak case above.
]

MUST_STAY_SILENT = [
    ("ordinary shell work", "rename the deploy script and push it to the branch"),
    ("short prompt", "hello there"),
    ("code, not mathematics",
     "the function returns None when the config file is missing, add a default"),
    # The options Greeks are deliberately NOT anchors: on this machine alpha, beta, gamma,
    # delta, theta, rho, sigma and vega belong to trading, and admitting them cost +21.95pp
    # of false fires when measured. This proves the exclusion still holds.
    ("trading, not mathematics",
     "set the target beta to 0.30 on the hedged book and check the delta and theta on the "
     "<ticker> leg before the roll"),
    # "eta" matches ETA and lit the gate 21 times on a customer-support assistant prompt.
    ("delivery ETA, not mathematics",
     "tell the customer the eta for the order is twenty minutes and offer a refund option"),
    # ENUMERATION_ANCHORS (2026-09-05) are weak anchors: "enumerate" and "lattice" are everyday
    # words in code and infrastructure prompts, and one of them without notation or a second
    # anchor must stay silent (measured +0 fires on the archive; this keeps the exclusion honest).
    ("enumerate the files, a lattice config - code, not mathematics",
     "enumerate the files in the build directory, rename the lattice config and push the branch"),
]


TIMEOUT = float(os.environ.get("MATH_SENSE_HEALTHCHECK_TIMEOUT", "60"))

# 2026-09-29 (scheduled audit B19-R79-HEALTHCHECK-IGNORES-HOOK-EXIT): ask() used to keep stdout only,
# so a hook that printed a valid block and then exited 7 passed a must-speak row, and one that crashed
# before printing passed a must-stay-silent row. The reply now carries the exit status, and a row is
# judged on its content only when the hook exited 0 and printed nothing or the hook protocol's JSON.
_HUNG = []   # the label of the first row that timed out; later rows are not asked


class Reply(NamedTuple):
    stdout: str
    returncode: int | None   # None: the hook did not finish (timeout) or could not start
    error: str               # bounded ASCII: the tail of stderr, or why it did not finish


def _tail(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())[-limit:]
    return text.encode("ascii", "replace").decode("ascii")


_RETRIED = []   # labels (prompt heads) whose first call timed out and whose second call answered


def _call(prompt: str) -> Reply:
    try:
        out = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"prompt": prompt}),
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return Reply("", None, "timed out after %g s" % TIMEOUT)
    except OSError as exc:
        return Reply("", None, "could not start: %s" % type(exc).__name__)
    return Reply(out.stdout.strip(), out.returncode, _tail(out.stderr))


def ask(prompt: str) -> Reply:
    # 2026-10-03 (claude-4a88bc43): one timed-out call is asked once more in a fresh process before the row is
    # given up. The nightly gate read "BREACH: math_sense_healthcheck rc=1" on 09-26, 09-29, 10-02 and 10-03, each
    # time one row past the 60 s cap at the 05:10 run and every later row then "not asked"; the same 17 rows took
    # 0.4-2.1 s each at 22:58 that evening. A row that times out twice is still not judged, and the check then
    # exits 3 (could not judge), never 0.
    if _HUNG:
        return Reply("", None, "not asked: the hook hung on an earlier row")
    reply = _call(prompt)
    if reply.returncode is None and reply.error.startswith("timed out"):
        reply = _call(prompt)
        if reply.returncode is None and reply.error.startswith("timed out"):
            _HUNG.append(prompt)
            return Reply("", None, "timed out twice after %g s each" % TIMEOUT)
        _RETRIED.append(prompt[:60])
    return reply


def broken(reply: Reply) -> str:
    """Why the reply cannot be judged on its content, or '' when it can. Empty output from a hook
    that crashed is not silence, and a block from a hook that crashed is not speech."""
    if reply.returncode is None:
        return reply.error
    if reply.returncode != 0:
        return "exit %d" % reply.returncode + (" (%s)" % reply.error if reply.error else "")
    if reply.stdout:
        try:
            json.loads(reply.stdout)
        except json.JSONDecodeError:
            return "printed something that is not the hook's JSON"
    return ""


def block_of(raw: str) -> str:
    if not raw:
        return ""
    try:
        return json.loads(raw)["hookSpecificOutput"]["additionalContext"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""


def main() -> int:
    failures = []
    unjudged = []   # rows the hook never answered (timed out twice, or could not start): not a verdict on the brain

    print("must SPEAK:")
    for entry in MUST_SPEAK:
        label, prompt = entry[0], entry[1]
        need = entry[2].lower() if len(entry) > 2 else None
        reply = ask(prompt)
        why = broken(reply)
        block = block_of(reply.stdout)
        ok = not why and len(block) > 120
        problem = delivery_problem(block) if ok else ""
        if why and reply.returncode is None:
            unjudged.append(f"hook did not answer ({why}) where a block was required: {label}")
        elif why:
            failures.append(f"hook failed ({why}) where a block was required: {label}")
        elif not ok:
            failures.append(f"SILENT where a block was required: {label}")
        elif problem:
            ok = False
            failures.append(f"SPOKE but the block is broken ({problem}): {label}")
        elif need and need not in block.lower():
            ok = False
            failures.append(f"SPOKE but the required title '{need}' is not in the block: {label}")
        head = block.split("\n")[1][:76] if "\n" in block else "(empty)"
        head = ("hook failed: " + why)[:76] if why else head
        head = head.encode("ascii", "replace").decode("ascii")
        print(f"  [{'ok ' if ok else 'FAIL'}] {label:38s} {head}")

    print("known gate false negatives (reported, not failing):")
    for label, prompt in KNOWN_GATE_MISSES:
        reply = ask(prompt)
        why = broken(reply)
        block = block_of(reply.stdout)
        if why:
            (unjudged if reply.returncode is None else failures).append(
                f"hook failed ({why}) on a known gate miss: {label}")
            state = "hook failed: " + why
        else:
            state = "still refused by the gate" if not block else "NOW SPEAKS - gate widened?"
        print(f"  [note] {label:38s} {state}")

    print("must STAY SILENT:")
    for label, prompt in MUST_STAY_SILENT:
        reply = ask(prompt)
        why = broken(reply)
        block = block_of(reply.stdout)
        ok = not why and not block
        if why and reply.returncode is None:
            unjudged.append(f"hook did not answer ({why}) where silence was required: {label}")
        elif why:
            failures.append(f"hook failed ({why}) where silence was required: {label}")
        elif not ok:
            failures.append(f"SPOKE where silence was required: {label}")
        shown = ("hook failed: " + why)[:76] if why else f"{len(block)} chars"
        print(f"  [{'ok ' if ok else 'FAIL'}] {label:38s} {shown}")

    print()
    for r in _RETRIED:
        print(f"  retried once after a timeout, answered the second time: {r}")
    if failures:
        for f in failures:
            print(f"  FAILURE: {f}")
        for u in unjudged:
            print(f"  NOT JUDGED: {u}")
        print("  hook is NOT healthy")
        return 1
    if unjudged:
        for u in unjudged:
            print(f"  NOT JUDGED: {u}")
        print(f"  could not judge: the hook did not answer {len(unjudged)} row(s) in time (exit 3, not a verdict)")
        return 3
    print(f"  hook healthy: {len(MUST_SPEAK)} speak, {len(MUST_STAY_SILENT)} silent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
