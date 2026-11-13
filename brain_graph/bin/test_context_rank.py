"""Controls for context_rank.py on a synthetic store built by graph_store.build.

Six sessions each read memory a and then memory b; a few read c alone; filler sessions keep every node under the
hub cut. From a context holding a, the learned hop must bring b in ahead of c, every arm must return finite scores
only for nodes the store holds, and a moment before the store's own `until` must be refused (it would read the
future). Nothing outside a temporary folder is read or written.

    python test_context_rank.py
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import json
import os
import sys
import tempfile

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import graph_store  # noqa: E402
from context_rank import ARMS, ContextRanker  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def fixture(d):
    ev, t = [], 1.79e9

    def touch(s, i):
        nonlocal t
        t += 20.0
        ev.append({"ts": t, "session": s, "kind": "mem", "id": i})
    for n in range(6):
        touch("pair%04d" % n, "memory_a")
        touch("pair%04d" % n, "memory_b")
    for n in range(3):
        touch("lone%04d" % n, "memory_c")
        touch("lone%04d" % n, "filler_lone_%d" % n)
    for n in range(30):
        touch("fill%04d" % n, "filler_%d_x" % n)
        touch("fill%04d" % n, "filler_%d_y" % n)
    p = os.path.join(d, "events_touch.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for e in ev:
            fh.write(json.dumps(e) + "\n")
    return p, t


def main():
    d = tempfile.mkdtemp(prefix="ctx_rank_test_")
    ev, t_end = fixture(d)
    out = os.path.join(d, "store")
    graph_store.build(ev, out, until=t_end + 1.0)
    cr = ContextRanker(out)
    name = {n.split(":", 1)[-1]: n for n in cr.st.names}
    a, b, c = (name.get(x) for x in ("memory_a", "memory_b", "memory_c"))
    check("1 the store holds the three memories", None not in (a, b, c), str(sorted(name)[:10]))
    if FAILS:
        print("%d of 6 controls pass" % 0)
        return 1
    kind = a.split(":", 1)[0]
    t = t_end + 60.0
    hebb = [n for n, _ in cr.rank([a], t, kind=kind, arm="hebb", k=10, exclude=(a,))]
    check("2 from a, the learned hop ranks b first", hebb[:1] == [b], str(hebb))
    check("3 c, never read beside a, gets no learned weight from a", c not in hebb, str(hebb))
    fused = [n for n, _ in cr.rank([a], t, kind=kind, arm="fused", k=10, exclude=(a,))]
    check("4 the fused arm keeps b ahead of c", b in fused and (c not in fused or fused.index(b) < fused.index(c)),
          str(fused))
    sc = cr.score([a], t, kind=kind)
    check("5 every arm scores every candidate of the kind", all(len(sc[x]) == len(cr.cands[kind]) for x in ARMS))
    try:
        cr.score([a], t_end - 3600.0, kind=kind)
        refused = False
    except ValueError:
        refused = True
    check("6 a moment before the store's until is refused", refused)
    print("%d of 6 controls pass" % (6 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
