#!/usr/bin/env python3
"""eval_reasoning_guards.py - real-data shadow eval for the two ORPHANED reasoning-error guards.

claim_check.py and retraction_guard.py are built and pass their curated selftests, but nothing wires them:
no hook calls them, and pre_report_check (the LIVE Stop hook) does not either. Before proposing they go
live, this measures what the selftest cannot - their behaviour on the REAL assistant-message corpus:

  1. FALSE-FIRE RATE: how often each fires across thousands of actual assistant messages. A caution that
     fires on a big fraction of turns is cry-wolf and must not be wired (the ERN's own law).
  2. MARGINAL CATCH (the crux): of the messages a guard fires on, how many does the already-live
     pre_report NOT block? A guard that only fires where pre_report already fires adds nothing - the
     justification for wiring is the ADDITIVE catches (fire here, pre_report quiet).

Read-only. Samples recent transcripts (projects/*.jsonl), capped and fail-open, forward-slash paths.
Usage: python eval_reasoning_guards.py [max_files] [max_msgs]
"""
import os, io, json, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "graph_fire"))
import claim_check                     # dreaming/claim_check.py
import retraction_guard                # dreaming/retraction_guard.py
import pre_report                      # graph_fire/pre_report.py (the live check)

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")


def _ascii(s):
    """ASCII-fold for a cp1252 console (a minus sign / curly quote in a sampled snippet must not crash the
    report). Display only."""
    return (s or "").replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-") \
        .replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"') \
        .encode("ascii", "replace").decode("ascii")


def iter_assistant_texts(max_files, max_msgs):
    """Yield (session8, text) for assistant prose messages from the most-recent transcripts. Skips tiny
    tool-only messages; caps total so this stays light on the laptop."""
    files = sorted(glob.glob(os.path.join(PROJ, "*.jsonl")), key=os.path.getmtime, reverse=True)[:max_files]
    n = 0
    for fp in files:
        try:
            for ln in io.open(fp, encoding="utf-8", errors="replace"):
                if '"assistant"' not in ln:
                    continue
                try:
                    obj = json.loads(ln)
                except Exception:
                    continue
                msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
                if (msg.get("role") or obj.get("role")) != "assistant":
                    continue
                content = msg.get("content") or obj.get("content") or ""
                if isinstance(content, list):
                    txt = " ".join(c.get("text", "") for c in content
                                   if isinstance(c, dict) and c.get("type") == "text")
                elif isinstance(content, str):
                    txt = content
                else:
                    txt = ""
                txt = txt.strip()
                if len(txt) < 40:            # tool-call-only / trivial turns are not "reported to the owner"
                    continue
                yield os.path.basename(fp)[:8], txt
                n += 1
                if n >= max_msgs:
                    return
        except Exception:
            continue


def _pr_blocks(txt):
    try:
        return bool(pre_report.check(txt).get("should_block"))
    except Exception:
        return False


def main():
    max_files = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    max_msgs = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
    total = 0
    cc_fires, rg_fires = [], []
    cc_add, rg_add = 0, 0                 # additive: guard fires AND pre_report does NOT block
    for sess, txt in iter_assistant_texts(max_files, max_msgs):
        total += 1
        cc = claim_check.assess(txt)
        rg = retraction_guard.assess(txt)
        if not (cc or rg):
            continue
        pr = _pr_blocks(txt)              # only pay the graph-fire cost when a guard actually fired
        if cc:
            cc_fires.append((sess, txt))
            cc_add += 0 if pr else 1
        if rg:
            rg_fires.append((sess, txt))
            rg_add += 0 if pr else 1

    print("=" * 74)
    print(" REASONING-GUARD SHADOW EVAL  (real assistant-message corpus)")
    print("=" * 74)
    print("\nscanned %d assistant messages across the %d most-recent transcripts\n" % (total, max_files))
    for name, fires, add in (("claim_check", cc_fires, cc_add), ("retraction_guard", rg_fires, rg_add)):
        rate = 100.0 * len(fires) / total if total else 0.0
        print("%s: fired on %d/%d messages (%.2f%%); %d ADDITIVE (pre_report would NOT block those)"
              % (name, len(fires), total, rate, add))
        verdict = ("wire-safe (rare) " if rate < 1.0 else "TUNE FIRST (cry-wolf risk) ") + \
                  ("and additive" if add > 0 else "but NOT additive over pre_report - folding adds little")
        print("   verdict: " + verdict)
        for sess, txt in fires[:4]:
            snippet = _ascii(" ".join(txt.split())[:150])
            print("     [%s] %s" % (sess, snippet))
        print()
    print("read: a guard is worth wiring only if it is BOTH rare (low false-fire) AND additive (catches")
    print("what the live pre_report misses). Eyeball the samples above - a fire on a genuine bare money")
    print("claim / re-asserted retraction is a real catch; a fire on hedged or benign prose is a false alarm.")


if __name__ == "__main__":
    main()
