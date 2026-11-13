"""study_answer_coverage.py - does a report that leaves part of his ask unaddressed predict that he restates that part next?


Why: his corrections follow the agent's answer by a median 39 s and nothing in the trace before it predicts them, so a mistake of his kind can only be
caught at the answer. The cheapest answer-time check is coverage: split his ask into clauses and see which the agent's
reply never mentions. If uncovered clauses are the ones he restates in his next prompt, a check at the moment of the
report would have caught those repeats; if restating is no more likely for an uncovered clause than a covered one, the
check would only nag.

Data: his interactive sessions (session_kinds 'his') since --since, from the prompt ledger (his prompts, with their
session) and the session's transcript (every assistant text block between one prompt of his and the next). A clause is
a piece of his prompt between sentence breaks, commas and joining words ("and", "also", "then"), with at least 3 content
words; it is covered when half or more of its content stems appear in the agent's reply to that prompt. It is restated
when his next prompt in the same session, within 6 hours, carries half or more of its stems (2 at least).

    python study_answer_coverage.py [--since 2026-09-01] [--out FILE.json] [--show N]
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

H = os.path.expanduser("~")
PROMPTS = os.path.join(H, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory", "prompts", "PROMPTS.jsonl")
PROJECTS = os.path.join(H, ".claude", "projects")
sys.path.insert(0, os.path.join(H, ".claude", "brain_graph", "bin"))

STOP = frozenset("""a an the and or but if then so to of in on at for from by with about into over under this that these those
it its i me my we our you your he she they them their is are was were be been being do does did done doing have has had
having will would can could should shall may might must not no yes also just only very really please thanks thank ok okay
like want wanted need needs make made makes sure all any some every each more most much many other others same such than
too there here what which who whom whose when where why how now yet still even again well get got go going gonna one
two lot lots thing things stuff way ways it's i'm don't dont can't cant also u ur pls pleas plz make""".split())
SPLIT = re.compile(r"[.!?;\n]+|,\s+|\s+(?:and then|and also|also|then|and)\s+", re.I)
WORD = re.compile(r"[a-z][a-z0-9']+")


def stems(text):
    out = []
    for w in WORD.findall(text.lower()):
        w = w.strip("'")
        if len(w) < 3 or w in STOP:
            continue
        out.append(w[:5])
    return out


PASTE = re.compile(r"<pasted_content|pasted markdown|\*\*|—|│|─|```|❯|\bworked for \d|\bran \d+ (?:shell )?command|"
                   r"^\s*[>|#]|\bfile\s*$", re.I | re.M)


def own_words(prompt):
    """His typing only: everything before the first sign of pasted material (a paste tag, bold, an em dash or box
    drawing, a code fence, another agent's transcript), at most 1,000 characters."""
    m = PASTE.search(prompt)
    return (prompt[:m.start()] if m else prompt)[:1000]


def clauses(prompt):
    out = []
    for piece in SPLIT.split(own_words(prompt)):
        st = stems(piece)
        if len(set(st)) >= 3:
            out.append((piece.strip()[:200], sorted(set(st))))
    return out[:12]


def his_prompts(since):
    by_sid = collections.defaultdict(list)
    for ln in io.open(PROMPTS, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("origin") != "owner" or r.get("source") != "hook":
            continue
        t = str(r.get("text") or "")
        ts = str(r.get("ts") or "")
        if not t or ts < since or t.startswith("/loop") or t.startswith("<"):
            continue
        by_sid[str(r.get("session") or "")[:8]].append((ts, t))
    return by_sid


def transcript_for(sid8):
    for p in glob.glob(os.path.join(PROJECTS, "*", sid8 + "*.jsonl")):
        return p
    return None


def assistant_texts(path):
    """[(iso ts, text)] of the assistant's text blocks, in order."""
    out = []
    for ln in io.open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("type") != "assistant":
            continue
        ts = str(r.get("timestamp") or "")
        cont = (r.get("message") or {}).get("content")
        if isinstance(cont, list):
            for b in cont:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    out.append((ts, b["text"]))
    return out


def to_local(iso_z):
    """Transcript stamps are UTC ('...Z'); the prompt ledger is his local clock. Both as naive local datetimes."""
    try:
        d = datetime.fromisoformat(iso_z.replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--out", default="")
    ap.add_argument("--show", type=int, default=12)
    a = ap.parse_args()
    import session_kinds as sk
    by_sid = his_prompts(a.since)
    rows = []
    n_sessions = 0
    for sid8, prompts in sorted(by_sid.items()):
        if sk.kind(sid8) != "his" or len(prompts) < 2:
            continue
        path = transcript_for(sid8)
        if not path:
            continue
        texts = [(to_local(ts), t) for ts, t in assistant_texts(path)]
        texts = [(d, t) for d, t in texts if d]
        prompts.sort()
        n_sessions += 1
        for i in range(len(prompts) - 1):
            t0 = datetime.fromisoformat(prompts[i][0][:19])
            t1 = datetime.fromisoformat(prompts[i + 1][0][:19])
            if (t1 - t0).total_seconds() > 6 * 3600 or t1 <= t0:
                continue
            reply = " ".join(t for d, t in texts if t0 <= d < t1)
            if not reply.strip():
                continue
            rs = set(stems(reply))
            cur = set(stems(own_words(prompts[i][1])))
            nxt = set(stems(own_words(prompts[i + 1][1])))
            if cur and len(cur & nxt) / float(len(cur)) >= 0.7:
                continue            # the same prompt again (a /loop re-fire, a resend): not a restatement of a part
            for text, st in clauses(prompts[i][1]):
                cov = sum(1 for w in st if w in rs) / float(len(st))
                again = sum(1 for w in st if w in nxt)
                rows.append({"sid": sid8, "t": prompts[i][0][:16], "clause": text, "n": len(st),
                             "covered": cov >= 0.5, "cov": round(cov, 2),
                             "restated": again >= 2 and again / float(len(st)) >= 0.5,
                             "next": prompts[i + 1][1][:200]})
    tab = collections.Counter((r["covered"], r["restated"]) for r in rows)
    def rate(c):
        n = tab[(c, True)] + tab[(c, False)]
        return tab[(c, True)], n, (tab[(c, True)] / float(n) if n else 0.0)
    ru, nu, pu = rate(False)
    rc, nc, pc = rate(True)
    print("his interactive sessions: %d, clauses: %d" % (n_sessions, len(rows)))
    print("uncovered clauses restated next: %d of %d (%.1f pct)" % (ru, nu, 100 * pu))
    print("covered clauses restated next:   %d of %d (%.1f pct)" % (rc, nc, 100 * pc))
    if pc:
        print("ratio uncovered / covered: %.2f" % (pu / pc))
    rest_unc = [r for r in rows if r["restated"] and not r["covered"]]
    print("share of all restated clauses that the reply had not covered: %d of %d" % (
        len(rest_unc), len([r for r in rows if r["restated"]])))
    for r in rest_unc[:a.show]:
        print("--- %s %s cov %.2f\n  clause: %s\n  next:   %s" % (r["sid"], r["t"], r["cov"], r["clause"][:160], r["next"][:160]))
    if a.out:
        json.dump({"since": a.since, "sessions": n_sessions, "rows": rows, "table": {str(k): v for k, v in tab.items()}},
                  io.open(a.out, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
