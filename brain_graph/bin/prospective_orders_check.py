"""prospective_orders_check.py - before he has to say an order again, did the standing-orders channel have it in front of
the agent? (27 Sep, claude-825b1305; the prospective check pre-registered 18:1x in study/TWO_DAY_RUN_2026-09-26.md.)

For every correction or repeat of his since SINCE (events_verdicts.jsonl, the capture's reading of his reactions):
  - his words: the owner row of the prompt ledger (memory/prompts/PROMPTS.jsonl) in the same session within 90 s;
  - the order they restate: the channel's own order_candidates over his words (the word index, his stored orders only);
    under BAR it is "no stored order matched" and counted apart;
  - what the channel had in the two hours before, in that session: its logged leaders ('orders' in live/fires.jsonl for
    Claude, live/codex_orders.jsonl for Codex) and what it served.
A hit is the restated order leading an evaluation (or being served) before he said it. The labels are automatic, so the
report lists every case with his words for a reader to check.

    python prospective_orders_check.py [--since "YYYY-MM-DD HH:MM"] [--bar 30] [--out PATH]
"""
import collections
import json
import os
import sys
import time

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
LIVE = os.path.join(H, ".claude", "brain_graph", "live")
VERDICTS = os.path.join(H, ".claude", "brain_graph", "events_verdicts.jsonl")
PROMPTS = os.path.join(H, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory", "prompts", "PROMPTS.jsonl")
FIRES = os.path.join(LIVE, "fires.jsonl")
CODEX = os.path.join(LIVE, "codex_orders.jsonl")
sys.path.insert(0, BIN)


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def tail_rows(path, since, ts_of, chunk=4_000_000):
    """Rows of an append-ordered JSON-lines file whose time is >= since, reading back from the end in chunks."""
    size = os.path.getsize(path)
    start = size
    while True:
        start = max(0, start - chunk)
        with open(path, "rb") as f:
            f.seek(start)
            if start:
                f.readline()
            first = f.readline()
        try:
            t0 = ts_of(json.loads(first)) if first else None
        except Exception:
            t0 = None
        if start == 0 or (t0 is not None and t0 < since - 3600):
            break
        chunk *= 2
    out = []
    with open(path, "rb") as f:
        f.seek(start)
        if start:
            f.readline()
        for raw in f:
            try:
                r = json.loads(raw)
                t = ts_of(r)
            except Exception:
                continue
            if t is not None and t >= since:
                out.append(r)
    return out


def local_ts(s):
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def main():
    since = time.mktime(time.strptime(arg("--since", "2026-09-27 18:56"), "%Y-%m-%d %H:%M"))
    bar = float(arg("--bar", "30"))
    out_path = arg("--out", os.path.join(H, "ai-memories-and-functionality", "12_cognitive_architecture", "brain_graph",
                                         "results", "ORDERS_PROSPECTIVE_%s.json" % time.strftime("%Y-%m-%d")))
    import live_membrane as LM
    import extract_touch_events as X
    lv = LM.Live(delta=None)
    verd = [r for r in tail_rows(VERDICTS, since, lambda r: r.get("ts"))
            if r.get("kind") == "verdict" and r.get("id") in ("correction", "repeat")]
    cases = collections.OrderedDict()
    for r in sorted(verd, key=lambda r: r["ts"]):
        k = (r["session"], round(r["ts"], 1))
        cases.setdefault(k, set()).add(r["id"])
    prompts = [r for r in tail_rows(PROMPTS, since - 120, lambda r: local_ts(r.get("ts", "")))
               if r.get("origin") == "owner"]
    fires = tail_rows(FIRES, since - 7200, lambda r: float(r.get("t", 0)))
    codex = tail_rows(CODEX, since - 7200, lambda r: float(r.get("t", 0))) if os.path.exists(CODEX) else []
    rows = []
    for (s8, ts), labels in cases.items():
        words = [p["text"] for p in prompts if str(p.get("session", "")).startswith(s8)
                 and abs((local_ts(p["ts"]) or 0) - ts) <= 90]
        text = words[0] if words else ""
        oc = LM.order_candidates(" ".join(X.words(text, cap=40)), set(), lv.db, topn=3) if text else []
        restated = [c["slug"] for c in oc if c["score"] >= bar]
        led, top3, served = [], set(), set()
        for f in fires:
            if not str(f.get("sid", "")).startswith(s8) or not (ts - 7200 <= float(f.get("t", 0)) < ts):
                continue
            o = f.get("orders") or []
            if o:
                led.append(o[0][0])
                top3.update(x[0] for x in o[:3])
            for sv in f.get("served") or []:
                if sv and sv[0] == "order":
                    served.add(str(sv[1]).split(":", 1)[-1])
        for c in codex:
            if str(c.get("sid", "")).startswith(s8) and ts - 7200 <= float(c.get("t", 0)) < ts:
                if c.get("lead"):
                    led.append(c["lead"])
                top3.update(x[0] for x in c.get("top") or [])
                if c.get("served"):
                    served.add(c["served"])
        rows.append({"session": s8, "t": ts, "at": time.strftime("%m-%d %H:%M", time.localtime(ts)),
                     "labels": sorted(labels), "his_words": text[:300], "found_prompt": bool(text),
                     "restated": restated, "best": [[c["slug"], c["score"]] for c in oc],
                     "evaluations_before": len(led), "led_before": sorted({x for x in restated if x in set(led)}),
                     "top3_before": sorted({x for x in restated if x in top3}),
                     "served_before": sorted({x for x in restated if x in served})})
    matched = [r for r in rows if r["restated"]]
    summ = {"since": time.strftime("%Y-%m-%d %H:%M", time.localtime(since)), "bar": bar, "cases": len(rows),
            "with_his_words": sum(1 for r in rows if r["found_prompt"]),
            "restated_a_stored_order": len(matched),
            "channel_led_with_it_before": sum(1 for r in matched if r["led_before"]),
            "in_its_top3_before": sum(1 for r in matched if r["top3_before"]),
            "served_before": sum(1 for r in matched if r["served_before"]),
            "no_evaluation_in_the_window": sum(1 for r in matched if not r["evaluations_before"])}
    print(json.dumps(summ, indent=1))
    for r in matched:
        print("  %s %s %s restated %s | led before: %s | his words: %s" % (
            r["at"], r["session"], "+".join(r["labels"]), r["restated"][0], bool(r["led_before"]), r["his_words"][:120]))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"summary": summ, "cases": rows}, fh, indent=1)
    print("written", out_path)


if __name__ == "__main__":
    main()
