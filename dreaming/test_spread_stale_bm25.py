"""Controls for the spread server's BM25 build window: while the live generation's sidecar is still building, BM25()
inside the server answers from the newest finished older sidecar instead of unpickling the payload.

 S1 live sidecar missing, an older finished one on disk: BM25() reads n and avgdl from it; the payload is not loaded
 S2 the older sidecar is past STALE_BM25_MAX_S: the payload path runs
 S3 the older sidecar has another schema: the payload path runs
 S4 the live sidecar exists: it is used and the stale counter does not move
 S5 installing twice wraps the constructor once

A stand-in bm25 module is used, so no index is built.    python test_spread_stale_bm25.py   (ALL PASS at the end)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import sqlite3
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []
TMP = tempfile.mkdtemp(prefix="stale_bm25_")
INDEX = os.path.join(TMP, "bm25_index.json.gz")
open(INDEX, "wb").close()


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def sidecar(path, schema, n, avgdl):
    con = sqlite3.connect(path)
    con.execute("create table hdr (k text primary key, v text)")
    con.executemany("insert into hdr values (?, ?)", [("schema", schema), ("n", str(n)), ("avgdl", str(avgdl))])
    con.commit()
    con.close()


fake = types.ModuleType("bm25")
fake.INDEX = INDEX
fake.POSTINGS_SCHEMA = "7"
fake._POSTINGS_ON = True
fake.LIVE = {"conn": None}
fake.loads = [0]
fake._index_signature = lambda path: "sig1"
fake.postings_path = lambda path, sig=None: "%s.postings.%s.sqlite3" % (path, sig)


def _postings_base(path, live):
    import glob
    best = None
    for f in glob.glob(path + ".postings.*.sqlite3"):
        if os.path.basename(f) != os.path.basename(live):
            m = os.stat(f).st_mtime
            if best is None or m > best[0]:
                best = (m, f)
    return best[1] if best else None


fake._postings_base = _postings_base
fake._postings_conn = lambda path: fake.LIVE["conn"]


class BM25:
    def __init__(self, path=INDEX):
        self._path = os.fspath(path)
        self._sc = fake._postings_conn(self._path)
        if self._sc is not None:
            _con, self.n, self.avgdl = self._sc
            return
        fake.loads[0] += 1
        self.n, self.avgdl = -1.0, -1.0


fake.BM25 = BM25
sys.modules["bm25"] = fake
os.environ["SPREAD_SERVER_STALE_BM25"] = "1"
import spread_server as ss

old = INDEX + ".postings.sig0.sqlite3"
sidecar(old, "7", 120, 33.5)
ss.install_stale_bm25()
b = fake.BM25(INDEX)
check((b.n, b.avgdl) == (120.0, 33.5) and fake.loads[0] == 0 and ss.STALE_BM25_USED[0] == 1,
      "S1 an older finished sidecar answers while the live one builds")
b._sc[0].close()

past = time.time() - ss.STALE_BM25_MAX_S - 60
os.utime(old, (past, past))
b = fake.BM25(INDEX)
check(fake.loads[0] == 1 and b.n == -1.0, "S2 a sidecar past its age limit is not used")

os.remove(old)
other = INDEX + ".postings.sig9.sqlite3"
sidecar(other, "6", 50, 10.0)
b = fake.BM25(INDEX)
check(fake.loads[0] == 2 and b.n == -1.0, "S3 a sidecar of another schema is not used")

live = sqlite3.connect(":memory:")
fake.LIVE["conn"] = (live, 99.0, 7.0)
used = ss.STALE_BM25_USED[0]
b = fake.BM25(INDEX)
check((b.n, b.avgdl) == (99.0, 7.0) and ss.STALE_BM25_USED[0] == used and fake.loads[0] == 2,
      "S4 the live sidecar is used when it exists")

init = fake.BM25.__init__
ss.install_stale_bm25()
check(fake.BM25.__init__ is init, "S5 a second install leaves the one wrapper")

print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
