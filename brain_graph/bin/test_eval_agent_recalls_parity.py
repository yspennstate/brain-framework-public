"""Entry-point parity for the agents'-searches evaluator.

The command line of eval_expansion_agent_recalls.py once judged searches by a rule of its own while its helper used the
shared judge. These controls run the real command-line entry points (main() of both file names) on a small synthetic
recall log and touch-event stream, with stub index arms whose ranking is fixed, and compare every row with what
graph_learn.judge_searches says directly. Nothing live is read or written: the module's log paths point at a temporary
folder and the session classifier gets an empty transcript index.

    python test_eval_agent_recalls_parity.py
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import sys
import tempfile
import time

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import eval_expansion_agent_recalls as ear  # noqa: E402
import eval_agent_recalls_fixed as fixed  # noqa: E402
import graph_learn  # noqa: E402

FAILS = []
T = time.time() - 20000                        # old enough that every window has closed
A, B, C = "aaaaaaaa-1111", "bbbbbbbb-2222", "cccccccc-3333"


class Stub:
    """An index arm that always ranks the same memories in the same order."""
    def __init__(self, order):
        self.order = order

    def score(self, q, topk=60):
        return [(s, 1.0) for s in self.order[:topk]]


def fixture(d):
    recs = [
        # (a) two searches in session A five minutes apart: the first one's window ends at the second
        {"t": T, "q": "first search in a", "shown": ["m1", "m2"], "session": A, "entry": "sdk-cli"},
        {"t": T + 300, "q": "second search in a", "shown": ["m3"], "session": A, "entry": "sdk-cli"},
        # a search with no session field (logged before 02:40): joined by shown overlap, to session C
        {"t": T + 2000, "q": "old search", "shown": ["m5", "m6"]},
        # his interactive search, in session A later on
        {"t": T + 4000, "q": "his search", "shown": ["m1"], "session": A, "entry": "cli"},
    ]
    ev = [
        # session A: shows at each search, an open BEFORE the first search (c), then m2, then m3 after the second search
        (T - 30, A, "mem", "m9"), (T + 1, A, "recalled", "m1"), (T + 1, A, "recalled", "m2"),
        (T + 100, A, "mem", "m2"), (T + 200, A, "mem", "m4"),
        (T + 301, A, "recalled", "m3"), (T + 400, A, "mem", "m3"),
        # (b) session B carries the same shown slugs as A's first search, and opens m1 - it must not take the search
        (T + 2, B, "recalled", "m1"), (T + 2, B, "recalled", "m2"), (T + 50, B, "mem", "m1"),
        # session C: the old search's shows, then two opens
        (T + 2001, C, "recalled", "m5"), (T + 2001, C, "recalled", "m6"), (T + 2100, C, "mem", "m6"),
        (T + 2200, C, "mem", "m5"),
        # his search: one open
        (T + 4001, A, "recalled", "m1"), (T + 4050, A, "mem", "m7"),
    ]
    with open(os.path.join(d, "recall_log.jsonl"), "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "events_touch.jsonl"), "w", encoding="utf-8") as fh:
        for ts, s, kd, nid in sorted(ev):
            fh.write(json.dumps({"ts": ts, "session": s[:8], "kind": kd, "id": nid}) + "\n")
    return recs


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def main():
    d = tempfile.mkdtemp(prefix="ear_parity_")
    recs = fixture(d)
    ear.RECALL_LOG = os.path.join(d, "recall_log.jsonl")      # read at call time, not bound as default arguments
    ear.EVENTS = os.path.join(d, "events_touch.jsonl")
    ear._KIND_IDX[:] = [{}]                                   # no transcripts: every non-cli search is 'unknown'
    ear._KIND_CACHE.clear()
    order = ["m3", "m1", "m6", "m2", "m5", "m4", "m7"]        # m9 never ranked
    stubs = {"base": Stub(order), "exp": Stub(list(reversed(order)))}
    ear.load_engines = lambda: stubs                          # the command line gets the stubs through its own call

    by_s = {}
    for ts, s, kd, nid in ear.tail_events(T - 1000):
        by_s.setdefault(s, []).append((ts, kd, nid))
    direct = {round(r["t"], 1): (best, opened) for r, best, opened in graph_learn.judge_searches(
        [json.loads(l) for l in open(ear.RECALL_LOG, encoding="utf-8")], by_s)}

    out1, out2 = os.path.join(d, "cli.json"), os.path.join(d, "fixed.json")
    ear.main([out1])
    fixed.main([out2])
    r1, r2 = json.load(open(out1, encoding="utf-8")), json.load(open(out2, encoding="utf-8"))
    rows = {round(x["t"], 1): x for x in r1["rows"]}

    check("1 the command line judges exactly the searches the shared judge judges",
          set(rows) == {t for t, (b, o) in direct.items() if b and o}, "%s vs %s" % (sorted(rows), sorted(direct)))
    ok = all(rows[t]["base"] == order.index(o[0]) + 1 for t, (b, o) in direct.items() if b and o)
    check("2 every row's target is the judge's first open", ok, json.dumps(r1["rows"]))
    first = rows.get(round(T, 1), {})
    check("3 the first search's window ends at the same session's next search (m2 and m4, not m3)",
          first.get("n_open") == 2 and first.get("base") == order.index("m2") + 1,
          "n_open %s base %s; m4 at +200 is inside, m3 at +400 is after the second search" % (first.get("n_open"),
                                                                                             first.get("base")))
    check("4 session B, which shows the same slugs, does not take A's search",
          direct[round(T, 1)][0] == A[:8] and "m1" not in direct[round(T, 1)][1])
    check("5 an open before the search is not a target", "m9" not in direct[round(T, 1)][1])
    old = rows.get(round(T + 2000, 1), {})
    check("6 a record without a session is joined by shown overlap and judged (first open m6)",
          old.get("base") == order.index("m6") + 1 and r1["joins"]["joined_by_overlap"] == 1, json.dumps(r1["joins"]))
    check("7 the any-open count is kept beside the first-open target",
          first.get("base_any") == min(order.index("m2"), order.index("m4")) + 1 and first["base_any"] <= first["base"])
    check("8 both file names give the same result", {k: v for k, v in r1.items() if k not in ("built", "seconds")} ==
          {k: v for k, v in r2.items() if k not in ("built", "seconds")})
    check("9 the result names its judge, target and source hashes",
          r1["judge"] == "graph_learn.judge_searches" and len(r1["judge_source_sha256"]) == 64 and "first memory" in
          r1["target"] and r1["joins"]["window_cut_by_next_search"] == 1)
    out3 = os.path.join(d, "his.json")
    ear.main([out3, "--kinds", "his"])
    r3 = json.load(open(out3, encoding="utf-8"))
    check("10 --kinds his keeps only the interactive search", r3["judged"] == 1 and r3["rows"][0]["kind"] == "his" and
          round(r3["rows"][0]["t"], 1) == round(T + 4000, 1), json.dumps(r3["rows"]))
    import subprocess
    probe = ("import os, sys; sys.path.insert(0, %r); os.environ.pop('BRAIN_DOC_EXPANSION', None); "
             "import eval_expansion_agent_recalls as e; "
             "print(os.environ.get('BRAIN_DOC_EXPANSION'), e.bm25._EXPANSION_ON)") % BIN
    got = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout.split()
    check("11 importing the module leaves the environment as it was (its own bm25 still reads expansion off)",
          got == ["None", "False"], str(got))
    print("%d of 11 controls pass" % (11 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
