#!/usr/bin/env python3
"""referee_forward_feed_precision.py - REFEREE (afec84b0): measure forward_feed's two structural detectors
(windows_backslash_path, lookahead-in-code) on the REAL sampled-actions corpus, and classify the backslash
fires bare-arg (Git Bash mangles -> TRUE) vs quoted-literal (preserved -> FALSE, a working command nagged).

Prompted by a LIVE false-fire: my own `python -c "...open(r'C:\\Users\\...')"` compute-check tripped the
detector but worked fine (backslash inside a python raw-string in a double-quoted bash arg is preserved).
The builder calls it caution-biased ("fire on the shape"); this measures how much of the fire is false.
Read-only; MEASURE, don't wire. Usage: python referee_forward_feed_precision.py
"""
import os, io, json, re, sys
GF = os.path.dirname(os.path.abspath(__file__)) + os.sep + "graph_fire"
GF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph_fire")
sys.path.insert(0, GF)
import forward_feed
CORPUS = os.path.join(GF, "typical_actions.jsonl")


def classify_backslash(cmd):
    """In the REAL command: a drive path right after a quote (' " ) or r'/r" is a LITERAL Git Bash
    preserves (works -> the fire is FALSE). A bare drive path (whitespace/start/=/: before it) is an ARG
    Git Bash mangles (breaks -> the fire is TRUE / correctly caught)."""
    verdicts = []
    for m in re.finditer(r"[A-Za-z]:\\", cmd or ""):
        i = m.start()
        pre2 = cmd[max(0, i - 2):i]
        prev = pre2[-1:] if pre2 else ""
        if prev in ("'", '"') or pre2 in ("r'", 'r"', "R'", 'R"'):
            verdicts.append("FALSE")   # quoted literal -> preserved -> works
        else:
            verdicts.append("TRUE")    # bare arg -> mangled -> breaks
    if not verdicts:
        return "none"
    return "TRUE" if "TRUE" in verdicts else "FALSE"


def main():
    tot = bs = la = bs_true = bs_false = 0
    samples_f, samples_t = [], []
    for ln in io.open(CORPUS, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        text, tool = rec.get("text", ""), rec.get("tool")
        tot += 1
        try:
            fires = forward_feed.forward_feed(text, tool)
        except Exception:
            continue
        ids = [f.get("id") for f in fires]
        if "windows_backslash_path" in ids:
            bs += 1
            try:
                cmd = json.loads(text).get("command", "") if isinstance(json.loads(text), dict) else text
            except Exception:
                cmd = text
            cls = classify_backslash(cmd)
            if cls == "TRUE":
                bs_true += 1
                if len(samples_t) < 8:
                    samples_t.append(cmd[:120])
            else:
                bs_false += 1
                if len(samples_f) < 10:
                    samples_f.append(cmd[:120])
        if "lookahead" in ids:
            la += 1
    print("records: %d" % tot)
    print("windows_backslash_path fires: %d (%.2f%%)  ->  TRUE bare-arg(breaks)=%d  FALSE quoted-literal(works)=%d"
          % (bs, 100.0 * bs / max(1, tot), bs_true, bs_false))
    print("   => false-fire share of backslash fires: %.0f%% ; false-fire rate over ALL actions: %.2f%%"
          % (100.0 * bs_false / max(1, bs), 100.0 * bs_false / max(1, tot)))
    print("lookahead-in-code fires: %d (%.2f%%)" % (la, 100.0 * la / max(1, tot)))
    print("\n  FALSE-fire samples (quoted literal that WORKS but got nagged):")
    for c in samples_f:
        print("   " + c)
    print("\n  TRUE-fire samples (bare backslash arg Git Bash would break):")
    for c in samples_t:
        print("   " + c)


if __name__ == "__main__":
    main()
