"""The membrane's merge of the fast layer, checked against a hand computation (27 Sep). A tiny store and sidecar are
built from a synthetic log; a fast layer is written by hand with one pair the store also holds (file -> dir /d1) and one
it does not (file -> dir /dnew). The place scores the installed membrane reports must equal
ln((cnt / out) / (in / total)) x ln(1 + cnt) over the two layers, each faded to the moment asked; with the fast layer
off they must equal the store's own score, and the new folder must be absent."""
import json
import math
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.expanduser("~/.claude/brain_graph/bin"))
import graph_store as GS
import live_delta as LD
import live_membrane as LM
import live_sidecar as LS

tmp = tempfile.mkdtemp(prefix="merge_math_")
p = os.path.join(tmp, "events.jsonl")
T = 1790000000.0
ev = []
for r in range(6):                     # the file, then its folder, in six sessions; other folders for the marginals
    s = "s%d" % r
    t = T + r * 5000
    ev += [(t, s, "file", "/a/1.py"), (t + 10, s, "dir", "/d1"), (t + 20, s, "file", "/b/%d.py" % r),
           (t + 30, s, "dir", "/d%d" % (r + 2)), (t + 40, s, "file", "/a/1.py"), (t + 50, s, "dir", "/e%d" % r)]
ev.sort()
with open(p, "w", encoding="utf-8", newline="") as fh:
    for ts, s, k, i in ev:
        fh.write(json.dumps({"ts": ts, "session": s, "kind": k, "id": i}) + "\n")
store = os.path.join(tmp, "store")
GS.build(p, store, window=600.0, k=40, hub=1.01, tool_rate=1.0)
LS.MEM = os.path.join(tmp, "mem")
os.makedirs(LS.MEM)
side = os.path.join(tmp, "side.sqlite")
LS.build(store, out=side)
db = sqlite3.connect(side)
meta = {k: json.loads(v) for k, v in db.execute("SELECT k, v FROM meta")}
nid = {n: i for i, n in db.execute("SELECT id, name FROM node")}
src = "file:/a/1.py"
out_m = db.execute("SELECT out_sum FROM node WHERE name=?", (src,)).fetchone()[0]
in_m = db.execute("SELECT in_w FROM node WHERE name='dir:/d1'").fetchone()[0]
row = db.execute("SELECT cnt, s FROM edge WHERE src=? AND dst=?", (nid[src], nid["dir:/d1"])).fetchone()
assert row, "the store must hold file -> /d1"
cnt_m, s_m = row
tot_m = float(meta["total_w"])
until = float(meta["store_until"])
# the fast layer, by hand: file -> /d1 three more times, file -> /dnew once, and the totals they imply
H = 86400.0
edges = {(src, "dir:/d1"): [3.0, until + 100, until + 300], (src, "dir:/dnew"): [1.0, until + 200, until + 200]}
out_d = {src: 4.0}
in_d = {"dir:/d1": 3.0, "dir:/dnew": 1.0}
tot_d = 4.0
dpath = os.path.join(tmp, "delta.sqlite")
LD.write_db(dpath, edges, {}, out_d, in_d, tot_d, meta["kinds"],
            {"version": 1, "ref_time": until + 300, "half_life_s": H, "base_store_until": until})
ok = True


def places(delta, t):
    lv = LM.Live(sidecar=side, learned=os.path.join(tmp, "none.sqlite"), delta=delta)
    ev_ = lv.evaluate([(src, 0.0)], t, pol=dict(LM.policy(), use_delta=True))
    return {nm: v for nm, v, _u, _w in [(p_[0], p_[1], p_[2], p_[3]) for p_ in ev_["place"]]}, ev_.get("delta")


for t, label in ((until + 300, "at the fast layer's build"), (until + 300 + H, "one half-life later")):
    fm = 0.5 ** ((t - until) / H)
    fd = 0.5 ** ((t - (until + 300)) / H)
    tot = tot_m * fm + tot_d * fd
    c1 = cnt_m * fm + 3.0 * fd
    want1 = math.log((c1 / (out_m * fm + 4.0 * fd)) / ((in_m * fm + 3.0 * fd) / tot)) * math.log1p(c1)
    c2 = 1.0 * fd
    want2 = math.log((c2 / (out_m * fm + 4.0 * fd)) / ((1.0 * fd) / tot)) * math.log1p(c2)
    got, info = places(dpath, t)
    g1, g2 = got.get("dir:/d1"), got.get("dir:/dnew")
    good = g1 is not None and g2 is not None and abs(g1 - round(want1, 2)) <= 0.011 and abs(g2 - round(want2, 2)) <= 0.011
    print("%-26s /d1 %s (want %.3f)  /dnew %s (want %.3f)  merged %s -> %s" % (
        label, g1, want1, g2, want2, info, "PASS" if good else "FAIL"))
    ok &= good
off, info = places(None, until + 300)
neg = off.get("dir:/d1") is not None and abs(off["dir:/d1"] - round(s_m, 2)) <= 0.011 and "dir:/dnew" not in off
print("fast layer off: /d1 %s (the store's own %.3f), /dnew %s, merged %s -> %s" % (
    off.get("dir:/d1"), s_m, off.get("dir:/dnew"), info, "PASS" if neg else "FAIL"))
ok &= neg
# a fast layer built on another store is ignored by "auto"
ptr_ok = LD.open_delta({"store_until": until + 1e6})[0] is None
print("a fast layer built on another store is not opened: %s" % ("PASS" if ptr_ok else "FAIL"))
ok &= ptr_ok
print("ALL PASS" if ok else "SOME FAIL")
