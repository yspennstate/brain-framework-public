# -*- coding: utf-8 -*-
"""verify_replay.py - how would verify_gate and verify_sense have behaved on real sessions? (30 Sep 2026)

    python verify_replay.py [--days 14] [--workers 4] [--out DIR]

Streams every Claude session transcript of the last N days, rebuilds each turn the way verify_gate reads it (commands,
code edits, Lean outputs, the final message), runs the gate's own evaluate() on it, and pairs the turn with the next
prompt he typed, labelled by the brain's own correction detector (brain_graph/bin/extract_touch_events.his_verdicts,
the labels the capture writes). It also runs verify_sense.classify() on every one of his prompts.

What it answers: how often each rule would fire (noise), and whether turns the gate would have stopped are followed by
his correction more often than turns it would have let through (signal). A fire is only a candidate: the case file
keeps the claim, the gate's reason and the next prompt's first words for hand review. Nothing live is read or written
except the transcripts themselves, read-only.
"""
import argparse
import json
import os
import re
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")
BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")


def _mods():
    sys.path[:0] = [HERE, BIN]
    import verify_gate
    import verify_sense
    import extract_touch_events as ete
    return verify_gate, verify_sense, ete


def scan(path):
    g, s, ete = _mods()
    turns, prompts = [], []
    events, final, t_turn, pending_lean = [], "", None, {}
    first_prompt_seen = False

    def close(next_text, next_ts, verdicts):
        if first_prompt_seen and final.strip():
            hit, ev = g.evaluate(events, final)
            turns.append({"file": os.path.basename(path), "t": t_turn, "rule": hit[0] if hit else None,
                          "claim": hit[1][:80] if hit else "", "detail": hit[2] if hit else "",
                          "ev": ev, "final_head": final[:400], "next_head": (next_text or "")[:240],
                          "next_verdicts": verdicts, "next_t": next_ts,
                          "claims": {"code": bool(g.CODE_CLAIM.search(final)), "lean": bool(g.LEAN_CLAIM.search(final)),
                                     "proof": bool(g.PROOF_CLAIM.search(final)), "status": bool(g.STATUS_RE.search(final))}})

    with open(path, "rb") as fh:
        for raw in fh:
            head = raw[:700]
            if b'"isSidechain":true' in head:
                continue
            # a user record names its type in the first few hundred bytes; an assistant record names it after the
            # whole message body (byte 3,946 of 4,329 in a sampled line), so it is found in the whole line, and only
            # once the line is known not to be a user record whose tool output happens to quote that string
            if b'"type":"user"' not in head and b'"type":"assistant"' in raw:
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                c = (o.get("message") or {}).get("content")
                if not isinstance(c, list):
                    continue
                texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                if any(t.strip() for t in texts):
                    final = "\n".join(texts)
                for b in c:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name") or "", b.get("input") or {}
                    if name in ("Bash", "PowerShell"):
                        cmd = str(inp.get("command") or "")
                        events.append({"k": "cmd", "cmd": cmd, "id": b.get("id")})
                        for p in g.SHELL_EDIT.findall(cmd):
                            events.append({"k": "edit", "path": p, "via": "shell"})
                        if g.LEAN_CMD.search(cmd):
                            pending_lean[b.get("id")] = True
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        events.append({"k": "edit", "path": str(inp.get("file_path") or inp.get("notebook_path") or "")})
                continue
            if b'"type":"user"' not in head:
                continue
            if b'"tool_result"' in raw:
                if not pending_lean or not any(k.encode() in raw for k in pending_lean):
                    continue
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                for b in ((o.get("message") or {}).get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending_lean:
                        events.append({"k": "out", "id": b.get("tool_use_id"), "text": g._text_of(b.get("content"))[:6000],
                                       "err": bool(b.get("is_error"))})
                        pending_lean.pop(b.get("tool_use_id"), None)
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            c = (o.get("message") or {}).get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            if not isinstance(c, str):
                continue
            his = ete.his_text(o, c) if hasattr(ete, "his_text") else ""
            if not his:
                continue
            verdicts = ete.his_verdicts(o, his)
            ts = o.get("timestamp") or ""
            close(his, ts, verdicts)
            prompts.append({"file": os.path.basename(path), "t": ts, "kinds": s.classify(his),
                            "session": o.get("sessionId") or ""})
            events, final, t_turn, pending_lean = [], "", ts, {}
            first_prompt_seen = True
    close("", "", [])
    return turns, prompts


def scan_ops(path):
    """Return (session, tool-call count, raw candidates, simulated emitted cards).

    The simulation starts with an empty per-transcript cooldown. Raw candidates
    are unique operation/path pairs per call, before cooldown and the call cap.
    """
    sys.path[:0] = [HERE]
    import verify_ops as vo
    fires, emitted, seen, writes = [], [], set(), 0
    with open(path, "rb") as fh:
        for raw in fh:
            if b'"tool_use"' not in raw or not (b'"Edit"' in raw or b'"Write"' in raw or b'"MultiEdit"' in raw):
                continue
            if not any(e in raw for e in (b'.tex"', b'.lean"', b'.md"')):
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            if o.get("type") != "assistant":
                continue
            for b in (o.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("name") not in ("Edit", "Write", "MultiEdit"):
                    continue
                inp = b.get("input") or {}
                p = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
                if os.path.splitext(p.lower())[1] not in (".tex", ".lean", ".md"):
                    continue
                writes += 1   # Keep the denominator in tool calls, including an empty MultiEdit.
                call_fires = []
                for p, text, old in vo._written_chunks(b["name"], inp):
                    for op, _card in vo.obligations(p, text, old):
                        if (op, p) not in call_fires:
                            call_fires.append((op, p))
                fires += call_fires
                call_count = 0
                for op, p in call_fires:
                    key = (os.path.normcase(p), op)
                    if key in seen:
                        continue
                    seen.add(key)
                    emitted.append((op, p))
                    call_count += 1
                    if call_count == 2:
                        break
    return os.path.basename(path)[:-6], writes, fires, emitted


def ops_summary(records):
    """Aggregate explicit candidate and emitted streams without reordering gates."""
    writes, raw, cards = 0, {}, {}
    for _sess, w, candidates, emitted in records:
        writes += w
        for op, _path in candidates:
            raw[op] = raw.get(op, 0) + 1
        for op, _path in emitted:
            cards[op] = cards.get(op, 0) + 1
    return {"math_file_writes": writes, "fires_raw": raw, "cards_after_dedup": cards,
            "raw_unit": "unique operation/path candidates per tool call, before cooldown and card cap",
            "card_model": "empty per-transcript session state; cooldown before the two-card call cap"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ops", action="store_true", help="replay verify_ops (math writes) instead of the gate and the card")
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "studies", "verify_replay"))
    a = ap.parse_args()
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    now = time.time()
    files = [os.path.join(PROJ, f) for f in os.listdir(PROJ) if f.endswith(".jsonl")]
    files = [f for f in files if now - os.path.getmtime(f) < a.days * 86400]
    # his sessions only: since 24 Sep most transcripts are headless test workers and scripts, whose "next prompt" is
    # a bank item, not his reaction (the_logged_use_of_the_brain_was_mostly_test_workers_2026_09_27)
    sys.path.insert(0, BIN)
    import session_kinds
    kinds = {f: session_kinds.kind(os.path.basename(f)[:-6]) for f in files}
    print("session kinds:", {k: sum(1 for v in kinds.values() if v == k) for k in set(kinds.values())})
    files = sorted((f for f in files if kinds[f] == "his"), key=os.path.getsize, reverse=True)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    if a.ops:
        with Pool(a.workers) as pool:
            out = ops_summary(pool.imap_unordered(scan_ops, files))
        out.update({"days": a.days, "sessions": len(files), "seconds": round(time.time() - t0)})
        with open(os.path.join(a.out, "ops_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print(json.dumps(out, indent=1))
        return
    turns, prompts = [], []
    with Pool(a.workers) as pool:
        for i, (tt, pp) in enumerate(pool.imap_unordered(scan, files)):
            turns += tt
            prompts += pp
            if i % 25 == 0:
                print("%d/%d files, %d turns, %.0fs" % (i + 1, len(files), len(turns), time.time() - t0), flush=True)
    with open(os.path.join(a.out, "turns.jsonl"), "w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(os.path.join(a.out, "prompts.jsonl"), "w", encoding="utf-8") as fh:
        for p in prompts:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    summary = summarize(turns, prompts)
    summary.update({"days": a.days, "files": len(files), "seconds": round(time.time() - t0)})
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary, indent=1))


def summarize(turns, prompts):
    def corr(rows):
        rows = [r for r in rows if r["next_t"]]
        k = sum(1 for r in rows if "correction" in r["next_verdicts"])
        return {"n_with_next": len(rows), "next_is_correction": k, "rate": round(k / len(rows), 4) if rows else None}
    out = {"turns": len(turns), "fires": {}, "correction_after": {}}
    for rule in ("R1", "R2", "R3"):
        rows = [t for t in turns if t["rule"] == rule]
        out["fires"][rule] = len(rows)
        out["correction_after"][rule] = corr(rows)
    out["correction_after"]["no_fire"] = corr([t for t in turns if not t["rule"]])
    for key in ("code", "lean", "proof"):
        claimed = [t for t in turns if t["claims"][key]]
        out["correction_after"]["claim_" + key] = corr(claimed)
    out["correction_after"]["all"] = corr(turns)
    kinds = {}
    for p in prompts:
        for k in p["kinds"]:
            kinds[k] = kinds.get(k, 0) + 1
    out["prompts"] = len(prompts)
    out["sense_kind_matches"] = kinds
    out["sense_any"] = sum(1 for p in prompts if p["kinds"])
    return out


if __name__ == "__main__":
    main()
