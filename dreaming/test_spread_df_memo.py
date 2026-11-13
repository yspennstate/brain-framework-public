"""Controls for the spread server's spell-correct df memo (install_df_memo, _df_memo_for, _df_carry).

 M1 installed: bm25._SidecarDf is the memo reader, and a second install is a no-op
 M2 two readers of one sidecar file share their answers; the second asks the database nothing
 M3 answers match the table exactly, absent words included (None), through get, [] and in
 M4 a rewritten sidecar (new stat identity) starts from its own answers, not the old ones
 M5 the carry keeps a word in no changed document and drops a word of a changed one
 M6 SPREAD_SERVER_DF_CARRY=0 holds no connection and carries nothing
 M7 SPREAD_SERVER_DF_MEMO=0 leaves bm25's reader alone

    python test_spread_df_memo.py      (a second; ALL PASS at the end)
"""
import os
import sqlite3
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm25
import spread_server as ss

results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


TMP = tempfile.mkdtemp(prefix="df_memo_")
BASE = bm25._SidecarDf


def make(name, docs):
    """A minimal sidecar: p(term, df), fp(pos, h), fwd(pos, terms) with terms as the fwd blob bm25 reads."""
    path = os.path.join(TMP, name)
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("create table p(term text primary key, df integer)")
    con.execute("create table fp(pos integer primary key, h blob)")
    con.execute("create table fwd(pos integer primary key, terms blob)")
    df = {}
    for pos, (h, words) in enumerate(docs):
        for w in set(words):
            df[w] = df.get(w, 0) + 1
        con.execute("insert into fp values(?, ?)", (pos, h))
        con.execute("insert into fwd values(?, ?)", (pos, " ".join(sorted(set(words))).encode()))
    con.executemany("insert into p values(?, ?)", df.items())
    con.commit()
    con.close()
    return path, df


class Counting:
    """A connection wrapper that counts queries against table p."""

    def __init__(self, path):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.p = 0

    def execute(self, sql, *a):
        if " from p " in sql:
            self.p += 1
        return self.con.execute(sql, *a)


def fwd_terms(blob):
    return blob.decode().split()


bm25_stub = types.SimpleNamespace(_SidecarDf=BASE, _fwd_terms=fwd_terms)
ss.DF_MEMO.update(key=None, got={}, hold=None, fp=None)
check(ss.install_df_memo(bm25_stub) and getattr(bm25_stub._SidecarDf, "_df_memo", False)
      and not ss.install_df_memo(bm25_stub), "M1 installed once")
Memo = bm25_stub._SidecarDf

docs1 = [(b"a1", ["apple", "pear"]), (b"b1", ["pear", "plum"]), (b"c1", ["kiwi"])]
p1, df1 = make("gen1.sqlite", docs1)
c1 = Counting(p1)
r1 = Memo(c1)
r1.prefetch(["apple", "pear", "zzz"])
n1 = c1.p
c2 = Counting(p1)
r2 = Memo(c2)
r2.prefetch(["apple", "pear", "zzz"])
check(n1 >= 1 and c2.p == 0 and r2.get("pear") == 2, "M2 a second reader of the same file asks the database nothing")

ok = all(r2.get(w) == df1.get(w) for w in ["apple", "pear", "plum", "kiwi", "zzz", "nope"])
ok = ok and r2["kiwi"] == 1 and ("zzz" not in r2) and ("plum" in r2)
try:
    r2["nope"]
    ok = False
except KeyError:
    pass
check(ok, "M3 exact answers, absent words included")

# a new generation in a NEW file: doc b changed (plum -> fig); apple and kiwi are in no changed document
docs2 = [(b"a1", ["apple", "pear"]), (b"b2", ["pear", "fig"]), (b"c1", ["kiwi"])]
p2, df2 = make("gen2.sqlite", docs2)
r1.prefetch(["kiwi", "plum", "fig"])
c3 = Counting(p2)
r3 = Memo(c3)
got = ss.DF_MEMO["got"]
check("kiwi" in got and "apple" in got and "plum" not in got and "pear" not in got,
      "M5 the carry keeps apple and kiwi, drops plum and pear (words of the changed document)")
r3.prefetch(["plum", "fig", "kiwi"])
check(r3.get("plum") is None and r3.get("fig") == 1 and r3.get("kiwi") == 1 and r3.get("pear") == 2,
      "M4 the new generation answers from its own table")

# the same path rewritten in place: a new stat identity, so a new key (carry skipped: same file name)
before = ss.DF_MEMO["key"]
w = sqlite3.connect(p2)          # the memo holds the file open, so it is rewritten in place, not replaced
w.execute("delete from p")
w.execute("insert into p values('only', 1)")
w.commit()
w.close()
r4 = Memo(Counting(p2))
check(ss.DF_MEMO["key"] != before and r4.get("kiwi") is None and r4.get("only") == 1,
      "M4 a file rewritten at the same path starts its own memo")

ss.DF_CARRY = False
ss.DF_MEMO.update(key=None, got={}, hold=None, fp=None)
p3, _ = make("gen3.sqlite", docs1)
Memo(Counting(p3)).prefetch(["apple"])
p4, _ = make("gen4.sqlite", docs1)
Memo(Counting(p4))
check(ss.DF_MEMO["hold"] is None and ss.DF_MEMO["got"] == {}, "M6 carry off: nothing held, nothing carried")
ss.DF_CARRY = True

os.environ["SPREAD_SERVER_DF_MEMO"] = "0"
stub2 = types.SimpleNamespace(_SidecarDf=BASE, _fwd_terms=fwd_terms)
check(not ss.install_df_memo(stub2) and stub2._SidecarDf is BASE, "M7 SPREAD_SERVER_DF_MEMO=0 leaves the reader alone")
del os.environ["SPREAD_SERVER_DF_MEMO"]

if ss.DF_MEMO["hold"] is not None:
    ss.DF_MEMO["hold"].close()
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
