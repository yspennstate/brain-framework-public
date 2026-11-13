"""study_scar_fire_posthoc.py - the post-hoc reading of the scar-fire study (28 Sep 09:xx, claude-825b1305).

The registered study (study_scar_fire_before_repeat.py) links a restated order to a reflex only when the reflex's source
names the order's memory slug. Read case by case, that link is too narrow in one direction and too loose in another, so
this script re-reads the same 22 corrections by CONTENT and records the evidence. It is post-hoc: its classes were not
registered and are reported beside the registered ones, never instead of them.

  content map     the reflex whose wording carries the order, whatever slug it cites: the GPU orders ->
                  heavy_compute_remote, the pictures orders -> the_picture_is_the_beat, public writing -> voice_human;
                  the narration-voice orders, the retired film and the Codex-pictures share have none.
  existence       a reflex with no "added" stamp exists from its first fire or suppression row (an upper bound; the
                  reflex-file backups give the lower bound, written into the result by hand where it matters).
  wording         for the GPU reflex, the wording each session actually received (the hook injects the gut without the
                  id, so a bullet is matched by its known openings) - first and last delivery before the correction.
  reach           for the pictures reflex, how many of the session's tool inputs before the correction look like film
                  work and how many of those its trigger matches (re.I over json.dumps(tool_input), as the hook does).
  class           no reflex for the order at the time / reflex existed but never matched in the session / matched in
                  the window but held back / fired earlier in the session only / fired between his ask and correction.

    python study_scar_fire_posthoc.py LABELS_JSON REGISTERED_JSON OUT_JSON [V1_JSON]

Version 2, 28 Sep 12:3x, after two scheduled audits of version 1:
  One review: "matched in the window but held
      back" merged the suppression reasons, and the text called all seven "held by the fire budget" when only five carry
      the cap (two were held by a recent mention alone); and the content map listed no reflex for the retired film while
      a generic one, his_call_is_final, existed. Now every hold keeps its typed reasons (cap, backoff, mention, prose),
      a fire-budget count is taken from rows with the cap only, and his_call_is_final is read in every case's window
      beside the order's own reflex (fires, holds and trigger reach) - a generic candidate, not an order's reflex.
  Another review: version 1 kept the first 300 characters of the wording each GPU case received, and the
      8-character session key and minute-level times beside it. The wording is now its sha256 and the words the study
      tested (last_wording_carries); a row carries the case number, not the session key, and the ask to the hour. The
      classes never read the wording; with V1_JSON given, each class is checked equal to version 1's.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import glob
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
TRANSCRIPTS = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
CONTENT = {"gpu": "heavy_compute_remote", "pictures": "the_picture_is_the_beat", "public writing": "voice_human"}
GPU_OPENINGS = ("Heavy or GPU compute", "Owner policy")      # every 'Owner policy' bullet must also name the GPU early
GPU_WORDS = ("RESEARCH BOX", "80 percent", "70 percent", "UNCAPPED", "stopped for movie")
FILMISH = re.compile(r"movie|film|manim|render|ffmpeg|blender|storyboard|lecture", re.I)
GENERIC = "his_call_is_final"   # a final-decision reflex; it names no order, so it is read in every case as a candidate


def log_rows(pattern, ids):
    out = collections.defaultdict(list)
    for p in glob.glob(os.path.join(DR, pattern)):
        with io.open(p, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                f = ln.rstrip("\n").split("\t")
                if len(f) >= 6 and f[0].isdigit() and f[3] in ids:
                    out[f[3]].append((int(f[0]), f[4], f[5]))
    return out


def transcript(sid):
    f = glob.glob(os.path.join(TRANSCRIPTS, sid + "*.jsonl"))
    return f[0] if f else None


def stamp(o):
    ts = o.get("timestamp")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else None


def strings(x, out):
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for v in x.values():
            strings(v, out)
    elif isinstance(x, list):
        for v in x:
            strings(v, out)
    return out


def gpu_wording(sid, ask, t):
    f = transcript(sid)
    if not f:
        return None
    seen, got = set(), []
    for ln in io.open(f, encoding="utf-8", errors="replace"):
        if "[unconscious]" not in ln:
            continue
        o = json.loads(ln)
        ep = stamp(o)
        if ep is None:
            continue
        if ep > t:
            break
        for s in strings(o, []):
            if "[unconscious]" in s:
                for b in s.split("\n  - ")[1:]:
                    if b.startswith(GPU_OPENINGS) and "GPU" in b[:300]:
                        k = (int(ep // 60), b[:120])
                        if k not in seen:
                            seen.add(k)
                            got.append((ep, b.split("\n")[0]))
    if not got:
        return {"deliveries_before_correction": 0}
    last = got[-1][1]
    return {"deliveries_before_correction": len(got), "first_min_vs_ask": round((got[0][0] - ask) / 60),
            "last_min_vs_ask": round((got[-1][0] - ask) / 60),
            "last_wording_carries": [w for w in GPU_WORDS if w in last],
            "last_wording_sha256": hashlib.sha256(last.encode("utf-8")).hexdigest()}


def trigger_reach(sid, t, trig):
    f = transcript(sid)
    if not f:
        return None
    total = filmish = hits = 0
    for ln in io.open(f, encoding="utf-8", errors="replace"):
        if '"tool_use"' not in ln:
            continue
        o = json.loads(ln)
        ep = stamp(o)
        if ep is None or ep > t:
            continue
        for b in (o.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                s = json.dumps(b.get("input"), ensure_ascii=False)
                total += 1
                if FILMISH.search(s):
                    filmish += 1
                    hits += bool(trig.search(s))
    return {"tool_inputs_before_correction": total, "film_shaped": filmish, "trigger_matches_film_shaped": hits}


def generic_reach(sid, ask, t, trig):
    """How many of the session's tool inputs, before his ask and between his ask and his correction, the generic
    reflex's trigger matches (re.I over json.dumps(tool_input), as the hook reads a tool call)."""
    f = transcript(sid)
    if not f:
        return None
    n_before = n_window = m_before = m_window = 0
    for ln in io.open(f, encoding="utf-8", errors="replace"):
        if '"tool_use"' not in ln:
            continue
        o = json.loads(ln)
        ep = stamp(o)
        if ep is None or ep > t:
            continue
        for b in (o.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                hit = bool(trig.search(json.dumps(b.get("input"), ensure_ascii=False)))
                if ep < ask - 5:
                    n_before += 1
                    m_before += hit
                else:
                    n_window += 1
                    m_window += hit
    return {"tool_inputs_before_ask": n_before, "trigger_matches_before_ask": m_before,
            "tool_inputs_in_window": n_window, "trigger_matches_in_window": m_window}


def main():
    lab = {c["n"]: c for c in json.load(io.open(sys.argv[1], encoding="utf-8"))["preventable"]}
    reg = {r["n"]: r for r in json.load(io.open(sys.argv[2], encoding="utf-8"))["rows"]}
    v1 = {}
    if len(sys.argv) > 4:
        v1 = {r["n"]: r["posthoc_class_by_content"] for r in json.load(io.open(sys.argv[4], encoding="utf-8"))["rows"]}
    learned = {e["id"]: e for e in json.load(io.open(os.path.join(DR, "instincts_learned.json"), encoding="utf-8"))}
    trig = re.compile(learned["the_picture_is_the_beat"]["trigger"], re.I)
    gtrig = re.compile(learned[GENERIC]["trigger"], re.I)
    ids = set(CONTENT.values()) | {GENERIC}
    fires, supp = log_rows("instinct_fires*.tsv*", ids), log_rows("instinct_suppressed*.tsv*", ids)
    first_row = {i: min([r[0] for r in fires.get(i, [])] + [r[0] for r in supp.get(i, [])] or [None]) for i in ids}
    rows, reg_tally, post_tally, typed_tally = [], collections.Counter(), collections.Counter(), collections.Counter()
    for n in sorted(reg):
        r, c = reg[n], lab[n]
        t, ask, sid = float(c["t"]), float(c.get("ask_t") or c["t"]), str(c["sid"])[:8]
        # the registered class, strict links only (the run also printed strict-or-loose; both are kept)
        registered = r["class"] if r["linked_strict"] else "no linked reflex"
        reg_tally[registered] += 1
        rid = CONTENT.get(r["theme"])
        ev = {"content_reflex": rid}
        if rid is None:
            cls = "no reflex for the order at the time"
        else:
            added = learned.get(rid, {}).get("added")
            exists_from = first_row[rid] if not added else None
            ev["reflex_first_log_row"] = datetime.fromtimestamp(first_row[rid]).strftime("%Y-%m-%d %H:%M") if first_row[rid] else None
            if exists_from and exists_from > t:
                cls = "no reflex for the order at the time"
                ev["note"] = "the reflex's first fire or suppression row comes after the correction"
            else:
                f_in = [x for x in fires.get(rid, []) if x[2].startswith(sid) and ask - 5 <= x[0] <= t]
                h_in = [x for x in supp.get(rid, []) if x[2].startswith(sid) and ask - 5 <= x[0] <= t]
                f_before = [x for x in fires.get(rid, []) if x[2].startswith(sid) and x[0] < ask - 5]
                h_before = [x for x in supp.get(rid, []) if x[2].startswith(sid) and x[0] < ask - 5]
                ev.update(fired_in_window=len(f_in), held_in_window=sorted({x[1] for x in h_in}),
                          fired_before_ask=len(f_before), held_before_ask=len(h_before))
                if h_before:
                    ev["last_hold_min_before_ask"] = round((ask - max(x[0] for x in h_before)) / 60)
                if f_in:
                    cls = "fired between his ask and his correction"
                elif h_in:
                    cls = "matched in the window but held back"
                elif f_before:
                    cls = "fired earlier in the session only"
                elif h_before:
                    cls = "matched earlier in the session, held back each time"
                else:
                    cls = "reflex existed but never matched in the session"
            if rid == "heavy_compute_remote":
                ev["wording_received"] = gpu_wording(sid, ask, t)
            if rid == "the_picture_is_the_beat":
                ev["trigger_reach"] = trigger_reach(sid, t, trig)
        # the typed reading: a hold keeps its reasons, and only a hold with the cap is a fire-budget hold
        held = ev.get("held_in_window") or []
        if cls == "matched in the window but held back":
            typed = ("held back in the window, the cap among the reasons" if "cap" in held else
                     "held back in the window without the cap (%s)" % "+".join(sorted(held)))
        else:
            typed = cls
        ev["coverage"] = ("no order-specific reflex at the time" if cls == "no reflex for the order at the time"
                          else "exact-order reflex")
        g_added = learned.get(GENERIC, {}).get("added")
        g_in = [x for x in fires.get(GENERIC, []) if x[2].startswith(sid) and ask - 5 <= x[0] <= t]
        gh_in = [x for x in supp.get(GENERIC, []) if x[2].startswith(sid) and ask - 5 <= x[0] <= t]
        g_first = first_row.get(GENERIC)
        ev["generic_candidate"] = {
            "reflex": GENERIC, "exists_by_first_log_row": bool(g_first and g_first <= t),
            "fired_in_window": len(g_in), "held_in_window": sorted({x[1] for x in gh_in}),
            "reach": generic_reach(sid, ask, t, gtrig)}
        post_tally[cls] += 1
        typed_tally[typed] += 1
        row = {"n": n, "theme": r["theme"], "ask_hour": datetime.fromtimestamp(ask).strftime("%m-%d %Hh"),
               "registered_class_strict": registered, "run_class_strict_or_loose": r["class"],
               "posthoc_class_by_content": cls, "posthoc_class_typed": typed, "held_by_cap": "cap" in held,
               "evidence": ev}
        if v1:
            row["same_class_as_version_1"] = v1.get(n) == cls
        rows.append(row)
    budget = sum(1 for x in rows if x["posthoc_class_typed"] == "held back in the window, the cap among the reasons")
    out = {"version": 2, "cases": len(rows), "registered_classes_strict": dict(reg_tally),
           "posthoc_classes_by_content": dict(post_tally), "posthoc_classes_typed": dict(typed_tally),
           "fire_budget_holds_in_window": budget,
           "generic_candidate_in_window": {"fired": sum(1 for x in rows if x["evidence"]["generic_candidate"]["fired_in_window"]),
                                           "held": sum(1 for x in rows if x["evidence"]["generic_candidate"]["held_in_window"])},
           "rows": rows}
    if v1:
        out["classes_equal_to_version_1"] = sum(1 for x in rows if x.get("same_class_as_version_1"))
    json.dump(out, io.open(sys.argv[3], "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
    for x in rows:
        g = x["evidence"]["generic_candidate"]
        print(x["n"], x["theme"], "|", x["posthoc_class_typed"], "| generic fired %d held %s reach %s" % (
            g["fired_in_window"], g["held_in_window"], (g["reach"] or {}).get("trigger_matches_in_window")))


if __name__ == "__main__":
    main()
