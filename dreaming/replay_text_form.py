"""Replay a day's real tool inputs through every compiled trigger in two text forms and diff the fire sets.

Form A is what the priming hook matches: json.dumps(tool_input), where a line break is the two characters
backslash-n, so an alternative that begins with a word boundary cannot match a line-initial token and a
[^\\n] class never stops at a line. Form B is the same text with the escaped newline and tab turned back
into the real character. Per trigger: hits under A, hits under B, gained (B only), lost (A only), with up
to three example snippets each. Read-only; writes judge_sheets/replay_text_form_<stamp>.json beside this
file and prints the most gained and most lost.

    python replay_text_form.py "2026-09-02 00:00" 2500

Judge the examples before believing either column: on 2026-09-02 the real-newline form gained 46 hits and
lost 230 over 2,500 inputs, and the losses were mostly one trigger that matched a single backslash followed
by a letter (every line break on form A) plus true positives of triggers written for form A - so the form
stayed and the trigger was rewritten to its referent instead.
"""
import glob
import io
import json
import os
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.expanduser("~/.claude/projects")
OUT_DIR = os.path.join(HERE, "judge_sheets")
MATCH_CAP = 16384
TOOLS = ("Bash", "PowerShell", "Write", "Edit")


def triggers():
    out = []
    for line in io.open(os.path.join(HERE, "instincts.tsv"), encoding="utf-8", errors="replace"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 4 and p[3].startswith("(?") and p[1] in ("fire", "guide"):
            try:
                out.append((p[0], p[1], re.compile(p[3])))
            except re.error:
                pass
    return out


def inputs_since(since, limit):
    rows = []
    for path in glob.glob(os.path.join(PROJ, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < since:
                continue
        except OSError:
            continue
        sid = os.path.basename(path)[:-6]
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get("timestamp")
                if not t:
                    continue
                try:
                    ts = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                if ts < since:
                    continue
                for c in (o.get("message", {}).get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in TOOLS:
                        rows.append((ts, sid, c.get("name"), json.dumps(c.get("input", {}))[:MATCH_CAP]))
    rows.sort()
    if len(rows) > limit:
        step = len(rows) / float(limit)
        rows = [rows[int(i * step)] for i in range(limit)]
    return rows


def main():
    since_s = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d 00:00")
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2500
    since = time.mktime(time.strptime(since_s, "%Y-%m-%d %H:%M"))
    trig = triggers()
    rows = inputs_since(since, limit)
    print("triggers %d | inputs %d" % (len(trig), len(rows)))
    NL, TAB = chr(92) + "n", chr(92) + "t"
    forms = [(text, text.replace(NL, "\n").replace(TAB, "\t")) for _, _, _, text in rows]
    res = {}
    t0 = time.time()
    for rid, mode, rx in trig:
        a_hits, b_hits, ex_gain, ex_lost = set(), set(), [], []
        for i, (text, textb) in enumerate(forms):
            a, b = bool(rx.search(text)), bool(rx.search(textb))
            if a:
                a_hits.add(i)
            if b:
                b_hits.add(i)
            if b and not a and len(ex_gain) < 3:
                m = rx.search(textb)
                ex_gain.append((rows[i][2], textb[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")))
            if a and not b and len(ex_lost) < 3:
                m = rx.search(text)
                ex_lost.append((rows[i][2], text[max(0, m.start() - 40):m.end() + 40]))
        res[rid] = {"mode": mode, "fires_A": len(a_hits), "fires_B": len(b_hits), "gained": len(b_hits - a_hits),
                    "lost": len(a_hits - b_hits), "ex_gained": ex_gain, "ex_lost": ex_lost}
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "replay_text_form_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump({"since": since_s, "n_inputs": len(rows), "took_s": round(time.time() - t0), "triggers": res}, f, ensure_ascii=False, indent=1)
    ta = sum(v["fires_A"] for v in res.values())
    tb = sum(v["fires_B"] for v in res.values())
    print("hits: escaped form %d, real-newline form %d, gained %d, lost %d (%.0fs) -> %s" % (
        ta, tb, sum(v["gained"] for v in res.values()), sum(v["lost"] for v in res.values()), time.time() - t0, out))
    for label, key in (("most gained under real newlines", "gained"), ("most lost under real newlines", "lost")):
        print("\n" + label + ":")
        for rid, v in sorted(res.items(), key=lambda kv: -kv[1][key])[:12]:
            if v[key]:
                ex = v["ex_" + key][0][1][:90] if v["ex_" + key] else ""
                print("  %-44s A %4d B %4d %s %3d | %s" % (rid, v["fires_A"], v["fires_B"], key, v[key], ex))


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: replay_text_form.py   (help is inert; run without flags to do the work)")
        raise SystemExit(0)
    main()
