"""Replay a widened reflex trigger over the real tool inputs of recent sessions and list every fire it ADDS to the live
trigger, so each can be read by hand (28 Sep, claude-825b1305; generalizes replay_register_reflex.py).

Inputs are what the priming hook matched: json.dumps(tool_input, ensure_ascii=False) of each Bash, PowerShell, Write and
Edit call in the Claude transcripts, and of each function call in the Codex rollouts (its arguments object). The live
trigger is matched behind the live anchor sidecar, a candidate behind the anchors derived for it, both with re.I, as
instinct_prime._fire does. Read-only on every input.

    python replay_reflex_widening.py REFLEX_ID CANDS.json NAME --since 2026-09-24T00:00:00+03:00 [--until ISO] [--private OUT.tsv] [--json OUT.json]

--private writes the added fires WITH their surrounding text (for the hand-reading; keep it off every repository);
--json writes counts, a case number per session, the matched words, the hour and a sha256 of each input - no context.
"""
import argparse
import datetime as dt
import glob
import hashlib
import io
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
BG = os.path.join(HOME, ".claude", "brain_graph")
sys.path.insert(0, DR)
sys.path.insert(0, os.path.join(BG, "bin"))
import instinct_anchors as IA  # noqa: E402
import session_kinds as SK  # noqa: E402

TOOLS = ("Bash", "PowerShell", "Write", "Edit")


def claude_inputs(since_iso, since_epoch):
    for path in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
        if os.path.getmtime(path) < since_epoch:
            continue
        sid = os.path.basename(path)[:-6]
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = o.get("timestamp") or ""
                if ts < since_iso:
                    continue
                for b in (o.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in TOOLS:
                        yield "claude", sid, ts, b.get("name"), json.dumps(b.get("input") or {}, ensure_ascii=False)


def codex_inputs(since_iso, since_epoch):
    for path in glob.glob(os.path.join(HOME, ".codex", "sessions", "**", "rollout-*.jsonl"), recursive=True):
        if os.path.getmtime(path) < since_epoch:
            continue
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
        sid = m.group(1) if m else os.path.basename(path)
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"function_call"' not in line and '"custom_tool_call"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = o.get("timestamp") or ""
                if ts < since_iso or o.get("type") != "response_item":
                    continue
                p = o.get("payload") or {}
                if p.get("type") == "function_call":
                    try:
                        d = json.loads(p.get("arguments") or "{}")
                    except Exception:
                        d = {"input": p.get("arguments")}
                    if not isinstance(d, dict):
                        d = {"input": d}
                elif p.get("type") == "custom_tool_call":
                    d = {"input": p.get("input")}
                else:
                    continue
                yield "codex", sid, ts, p.get("name") or "?", json.dumps(d, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reflex")
    ap.add_argument("cands")
    ap.add_argument("name")
    ap.add_argument("--since", default="2026-09-24T00:00:00+03:00")
    ap.add_argument("--until", default=None)
    ap.add_argument("--private", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    since = dt.datetime.fromisoformat(a.since).astimezone(dt.timezone.utc)
    since_iso, since_epoch = since.strftime("%Y-%m-%dT%H:%M:%S"), since.timestamp()
    until_iso = (dt.datetime.fromisoformat(a.until).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                 if a.until else "9999")
    learned = json.load(io.open(os.path.join(DR, "instincts_learned.json"), encoding="utf-8"))
    live = [x["trigger"] for x in learned if x.get("id") == a.reflex][0]
    craw = io.open(a.cands, "rb").read()
    cand = json.loads(craw.decode("utf-8"))[a.reflex][a.name]
    live_table = IA.load()
    cand_table = {IA._key(cand): IA.anchors_for(cand)} if IA.anchors_for(cand) else {}
    rx_live, rx_cand = re.compile(live, re.I), re.compile(cand, re.I)

    def fires(rx, trig, table, text, low):
        return IA.could_match(trig, low, table) and bool(rx.findall(text))

    n = {"claude": 0, "codex": 0}
    live_n = {"claude": 0, "codex": 0}
    cand_n = {"claude": 0, "codex": 0}
    lost, added, cases = [], [], {}
    for src in (claude_inputs(since_iso, since_epoch), codex_inputs(since_iso, since_epoch)):
        for rt, sid, ts, tool, text in src:
            if ts >= until_iso:
                continue
            n[rt] += 1
            low = text.lower()
            fl = fires(rx_live, live, live_table, text, low)
            fc = fires(rx_cand, cand, cand_table, text, low)
            live_n[rt] += fl
            cand_n[rt] += fc
            if fl and not fc:
                lost.append((rt, sid, ts, tool))
            if fc and not fl:
                m = rx_cand.search(text)
                s0 = max(0, m.start() - 160)
                case = cases.setdefault(sid, len(cases) + 1)
                added.append({"runtime": rt, "case": case, "kind": SK.kind(sid), "hour": ts[:13], "tool": tool,
                              "match": m.group(0)[:80], "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                              "_ctx": text[s0:m.end() + 160].replace("\\n", " / ")})
    SK.save()
    uniq = {}
    for x in added:
        uniq.setdefault(x["input_sha256"], x)
    print("inputs: claude %d, codex %d | live fires: claude %d, codex %d | candidate fires: claude %d, codex %d"
          % (n["claude"], n["codex"], live_n["claude"], live_n["codex"], cand_n["claude"], cand_n["codex"]))
    print("added fires %d (%d distinct inputs), lost fires %d" % (len(added), len(uniq), len(lost)))
    for i, x in enumerate(sorted(uniq.values(), key=lambda r: (r["hour"], r["case"])), 1):
        print("%3d. %s case %d (%s, %s) %s | match: %r" % (i, x["hour"], x["case"], x["kind"], x["runtime"], x["tool"], x["match"]))
        print("      ...%s..." % x["_ctx"])
    if a.private:
        with io.open(a.private, "w", encoding="utf-8", newline="\n") as f:
            f.write("n\thour\tcase\tkind\truntime\ttool\tmatch\tinput_sha256\tjudgment\tcontext\n")
            for i, x in enumerate(sorted(uniq.values(), key=lambda r: (r["hour"], r["case"])), 1):
                f.write("\t".join([str(i), x["hour"], str(x["case"]), x["kind"], x["runtime"], x["tool"],
                                   x["match"].replace("\t", " "), x["input_sha256"], "",
                                   x["_ctx"].replace("\t", " ").replace("\n", " / ")]) + "\n")
    if a.json:
        out = {"reflex": a.reflex, "candidate": a.name, "cands_sha256": hashlib.sha256(craw).hexdigest(),
               "candidate_trigger_sha256": hashlib.sha256(cand.encode("utf-8")).hexdigest(), "since": a.since, "until": a.until,
               "inputs": n, "live_fires": live_n, "candidate_fires": cand_n, "lost": len(lost),
               "added": len(added), "added_distinct": len(uniq),
               "added_rows": [{k: v for k, v in x.items() if not k.startswith("_")}
                              for x in sorted(uniq.values(), key=lambda r: (r["hour"], r["case"]))]}
        tmp = a.json + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, a.json)


if __name__ == "__main__":
    main()
