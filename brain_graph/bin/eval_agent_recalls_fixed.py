"""eval_agent_recalls_fixed.py - the agents'-searches comparison with the target fixed.

A scheduled audit showed two faults in eval_expansion_agent_recalls.py:
the search was joined to a session by the overlap of shown slugs within 90 s, and each index arm scored the best rank of
ANY memory the session opened in the next ten minutes, so the target could differ between arms. This file first fixed the
target to the first memory opened, but kept a join and an uncut ten-minute window of its own;
its result, results/DOC_EXPANSION_AGENT_RECALLS_FIXED_2026-09-27.json, is from that rule.

Since 27 Sep 07:1x it is a second name for eval_expansion_agent_recalls.py, whose command line now judges every search
with graph_learn.judge_searches (the live learner's rule) and reports the first-open target and the any-open count side
by side, so the two cannot disagree again.

    python eval_agent_recalls_fixed.py OUT_JSON [--kinds his,script]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_expansion_agent_recalls as ear  # noqa: E402


def main(argv=None):
    return ear.main(argv)


if __name__ == "__main__":
    sys.exit(main())
