"""Controls for the spread server's df vocabulary filter (_vocab_hashes, _vocab_bg, _vocab_union, _vocab_absent).

 V1 the background build publishes the array for the reader's file; a later reader of that file carries it
 V2 with the array, words absent from the corpus cost no query on p and answer None; present words answer exactly
 V3 a new generation unions its changed words into the array: a word new in that generation is found, not dropped
 V4 a control: an array missing a present word makes that word read None (the filter is consulted)
 V5 SPREAD_SERVER_DF_VOCAB off: no array, absent words are asked of the database, answers unchanged

    python test_spread_df_vocab.py      (a second; ALL PASS at the end; needs numpy)
"""
import os
import pickle
import sqlite3
import sys
import tempfile
import time
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm25
import spread_server as ss

results = []
N_CHECKS = 6


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


TMP = tempfile.mkdtemp(prefix="df_vocab_")
BASE = bm25._SidecarDf


def make(name, docs):
    """A minimal sidecar: p(term, df), fp, fwd, and vocab('df') holding the df map as bm25 writes it."""
    path = os.path.join(TMP, name)
    con = sqlite3.connect(path)
    con.execute("create table p(term text primary key, df integer)")
    con.execute("create table fp(pos integer primary key, h blob)")
    con.execute("create table fwd(pos integer primary key, terms blob)")
    con.execute("create table vocab(k text primary key, blob blob)")
    df = {}
    for pos, (h, words) in enumerate(docs):
        for w in set(words):
            df[w] = df.get(w, 0) + 1
        con.execute("insert into fp values(?, ?)", (pos, h))
        con.execute("insert into fwd values(?, ?)", (pos, " ".join(sorted(set(words))).encode()))
    con.executemany("insert into p values(?, ?)", df.items())
    con.execute("insert into vocab values(?, ?)", ("df", zlib.compress(pickle.dumps(df, protocol=5), 1)))
    con.commit()
    con.close()
    return path, df


class Counting:
    def __init__(self, path):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.p = 0

    def execute(self, sql, *a):
        if " from p " in sql:
            self.p += 1
        return self.con.execute(sql, *a)


def settle():
    t0 = time.time()
    while ss.VOCAB["bg"] is not None and time.time() - t0 < 20:
        time.sleep(0.02)


def reset():
    if ss.DF_MEMO["hold"] is not None:
        ss.DF_MEMO["hold"].close()
    ss.DF_MEMO.update(key=None, got={}, hold=None, fp=None)
    ss.VOCAB.update(key=None, h=None, built=0.0, bg=None)


stub = types.SimpleNamespace(_SidecarDf=BASE, _fwd_terms=lambda b: b.decode().split())
reset()
ss.install_df_memo(stub)
Memo = stub._SidecarDf
words = ["apple", "pear", "plum", "kiwi", "zzz", "nope", "appel", "peer"]

try:
    docs1 = [(b"a1", ["apple", "pear"]), (b"b1", ["pear", "plum"]), (b"c1", ["kiwi"])]
    p1, df1 = make("gen1.sqlite", docs1)
    Memo(Counting(p1))
    settle()
    c = Counting(p1)
    r = Memo(c)
    check(r._vh is not None and len(r._vh) == len(df1) and ss.VOCAB["key"] == ss.DF_MEMO["key"],
          "V1 the array is built for the reader's file and carried by the next reader")

    r.prefetch(["zzz", "nope", "appel", "peer"])
    absent_queries = c.p
    r.prefetch(["apple", "pear"])
    check(absent_queries == 0 and c.p == 1 and all(r.get(w) == df1.get(w) for w in words),
          "V2 absent words cost no query (%d) and every answer matches the table" % absent_queries)

    docs2 = [(b"a1", ["apple", "pear"]), (b"b2", ["pear", "fig"]), (b"c1", ["kiwi"])]
    p2, df2 = make("gen2.sqlite", docs2)
    c2 = Counting(p2)
    r2 = Memo(c2)
    check(r2._vh is not None and ss.VOCAB["key"] == ss.DF_MEMO["key"],
          "V3 the new generation gets the unioned array at once, not after a rebuild")
    check(all(r2.get(w) == df2.get(w) for w in words + ["fig"]) and r2.get("fig") == 1,
          "V3 'fig', new in that generation, is found; every answer matches the new table")

    import numpy as np
    r3 = Memo(Counting(p2))
    r3._c = {}
    r3._vh = np.sort(np.asarray([hash(w) for w in df2 if w != "pear"], dtype=np.int64))
    check(r3.get("pear") is None and r3.get("fig") == 1, "V4 control: an array missing 'pear' reads it as None")

    reset()
    ss.DF_VOCAB = False
    p3, df3 = make("gen3.sqlite", docs1)
    c4 = Counting(p3)
    r4 = Memo(c4)
    r4.prefetch(["zzz", "nope"])
    check(r4._vh is None and ss.VOCAB["bg"] is None and c4.p == 1 and all(r4.get(w) == df3.get(w) for w in words),
          "V5 filter off: no array, absent words are asked of the database, same answers")
except Exception as e:
    check(False, "raised %s: %s" % (type(e).__name__, e))
finally:
    ss.DF_VOCAB = True
    reset()

ok = all(results) and len(results) == N_CHECKS
print("ALL PASS" if ok else "FAILED %d of %d" % (N_CHECKS - results.count(True), N_CHECKS))
sys.exit(0 if ok else 1)
