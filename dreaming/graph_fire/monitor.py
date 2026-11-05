#!/usr/bin/env python3
"""monitor.py - count the deployed watcher's interventions (healer-first-do-no-harm).

A Stop hook that BLOCKS is a doctor that medicates; the standing rule is to count the medicine and, if the
count is high, suspect the HEALER, not the patient. This reads gf_shadow_fires.tsv and reports what the
mechanism actually did on REAL sessions - how many Stop-hook blocks, on what messages, and the command-
surface fire rate by reflex - so over-firing is visible instead of silent. Run it, or let the dream run it.

Test noise is excluded: session ids from this build (89f84043) and hand-run fixtures (deadbeef/abcdef/
zztest/selftest/deadfeed) are not real workload; Stop blocks whose message matches a known test string are
flagged so the real-vs-test split is honest. Read-only, stdlib, forward-slash paths.
"""
import os, io, re, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DREAM = os.path.dirname(HERE)
LOG = os.path.join(HERE, "gf_shadow_fires.tsv")
DEDUP = os.path.join(DREAM, ".instinct_fired.json")   # per-session reflex fire counts (read-past signal)
TEST_SESS = re.compile(r"^(deadbeef|abcdef|zztest|selftest|deadfeed|89f84043)", re.I)
TEST_MSG = re.compile(r"grid backtest earns|20x (delta-neutral )?book|losing money every single year|"
                      r"leveraged carry is negligible|selling puts to collect premium|9999k per year|"
                      r"draws? down 60 to 8|moving the abc leg", re.I)


def main():
    if not os.path.exists(LOG):
        print("no shadow log yet")
        return
    stop_block, stop_would, cmd_fires = [], [], collections.Counter()
    cmd_actions_real = 0
    for l in io.open(LOG, encoding="utf-8"):
        p = l.rstrip("\n").split("\t")
        if len(p) < 4:
            continue
        if p[1] == "STOP":
            outcome, fires, msg = p[2], p[3], (p[4] if len(p) >= 5 else "")
            is_test = bool(TEST_MSG.search(msg))
            if outcome == "block":
                stop_block.append((msg, fires, is_test))
            elif outcome == "would-block":
                stop_would.append((msg, fires, is_test))
        else:
            sess, fires = p[1], (p[2] if len(p) >= 3 else "")
            if TEST_SESS.match(sess):
                continue
            cmd_actions_real += 1
            for r in fires.split(","):
                if r and r != "-":
                    cmd_fires[r] += 1

    real_blocks = [b for b in stop_block if not b[2]]
    test_blocks = [b for b in stop_block if b[2]]
    print("=== deployed watcher intervention report ===")
    print("Stop-hook BLOCKS: %d total  (%d real-session, %d test-fixture)"
          % (len(stop_block), len(real_blocks), len(test_blocks)))
    if real_blocks:
        print("  REAL blocks (review each - a false block on real work is the healer-is-the-outage failure):")
        for msg, fires, _ in real_blocks[:15]:
            print("    [%s] %s" % (fires[:40], msg.encode("ascii", "replace").decode()[:90]))
    else:
        print("  (no real-session blocks - only test fixtures have blocked so far)")
    print("Stop-hook WOULD-block (shadow, not live): %d (%d real)"
          % (len(stop_would), sum(1 for b in stop_would if not b[2])))
    print("Command-surface graph fires on %d real logged actions - top reflexes:" % cmd_actions_real)
    for r, n in cmd_fires.most_common(10):
        print("    %-30s %d" % (r, n))
    # read-past / mindware-override signal: reflexes surfaced >=3x in a session (escalation territory). This
    # is the referee's key finding - detection worked, heeding didn't. A high-charge reflex here got an
    # un-ignorable escalation; a reflex read past a lot is where the brain's ACTION, not detection, is failing.
    try:
        st = json.load(io.open(DEDUP, encoding="utf-8"))
    except Exception:
        st = {}
    read_past = []
    for k, v in st.items():
        cnt = v[1] if (isinstance(v, list) and len(v) >= 2) else (1 if v else 0)
        try:
            cnt = int(cnt)
        except Exception:
            cnt = 0
        if cnt >= 3:
            read_past.append((k.split("|")[-1], cnt))
    read_past.sort(key=lambda x: -x[1])
    print("Read-past (a reflex surfaced >=3x in one session - detection working, heeding maybe not):")
    if read_past:
        for reflex, cnt in read_past[:12]:
            print("    %-30s x%d" % (reflex, cnt))
    else:
        print("    (none in the last day - no reflex surfaced >=3x in a live session)")

    # a crude health flag
    if real_blocks:
        print("\nFLAG: real-session blocks exist - eyeball them above; if any is a correct message being "
              "blocked, tighten that branch or `rm graph_fire/ENABLED` to revert to shadow.")


if __name__ == "__main__":
    main()
