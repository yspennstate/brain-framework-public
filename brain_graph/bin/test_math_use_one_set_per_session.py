"""test_math_use_one_set_per_session.py - a session is one co-activation however many sources record it (29 Sep 2026,
claude-825b1305; scheduled audit account01/B06 r24, B06-R70-MATH-USE-DOUBLE-COUNTS-SESSION-COACTIVATION-20260929).

    python test_math_use_one_set_per_session.py [--old OLD_MATH_USE.py]

Every path the build reads or writes is in a temporary directory; nothing live is touched.
 M1 session S1 served {A,B} (its shard and its serve-log lines), S2 served {C}: two sets, no A-B edge
 M2 a third session S3 served {A,B}: now the A-B edge exists
 M3 S1's shard read live and again from the archive at another time: still one set
 M4 an old 8-character serve id that two of his sessions share: left out of co-activation, its uses kept
 M5 claim sizes (use histories) are the same as the module before this change gives
With --old, the module before this change must make the A-B edge from S1 alone in M1 (the check can fail).
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import math_use  # noqa: E402
import session_kinds as sk  # noqa: E402

ARGS = sys.argv[1:]
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
FAILS, RAN = [], []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    RAN.append(name)
    if not ok:
        FAILS.append(name)


def shard_name(sid):
    return hashlib.sha1(sid.encode("utf-8", "replace")).hexdigest()[:24]


def his_transcript(proj, sid):
    with open(os.path.join(proj, sid + ".jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "system", "entrypoint": "cli"}) + "\n")
        fh.write(json.dumps({"type": "user", "entrypoint": "cli",
                             "message": {"role": "user", "content": "prove the lemma"}}) + "\n")


S1 = "11111111-aaaa-4aaa-8aaa-000000000001"
S2 = "22222222-aaaa-4aaa-8aaa-000000000002"
S3 = "33333333-aaaa-4aaa-8aaa-000000000003"
T1 = "44444444-aaaa-4aaa-8aaa-000000000004"          # T1 and T2 share their first 8 characters
T2 = "44444444-bbbb-4bbb-8bbb-000000000005"


def build(mod, tmp, with_s3=False, archive_s1=False, legacy=False):
    proj = os.path.join(tmp, "projects", "C--work")
    shutil.rmtree(os.path.join(tmp, "projects"), ignore_errors=True)
    os.makedirs(proj)
    for sid in (S1, S2, S3, T1, T2):
        his_transcript(proj, sid)
    shards = os.path.join(tmp, "shards")
    shutil.rmtree(shards, ignore_errors=True)
    os.makedirs(shards)
    now = time.time()

    def put(sid, ids, ts):
        json.dump({"ids": ids, "ts": ts}, open(os.path.join(shards, shard_name(sid) + ".json"), "w"))
    put(S1, ["claim_A", "claim_B"], now - 3600)
    put(S2, ["claim_C"], now - 1800)
    if with_s3:
        put(S3, ["claim_A", "claim_B"], now - 900)
    archive = os.path.join(tmp, "archive.jsonl")
    with open(archive, "w", encoding="utf-8") as fh:
        if archive_s1:
            fh.write(json.dumps({"shard": shard_name(S1), "archived": now - 7200,
                                 "rec": {"ids": ["claim_A", "claim_B"], "ts": now - 7200}}) + "\n")
    serves = os.path.join(tmp, "serves.jsonl")
    with open(serves, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": now - 100, "sid": S1, "ids": ["claim_A", "claim_B"]}) + "\n")
        if legacy:
            fh.write(json.dumps({"t": now - 90, "sid": T1[:8], "ids": ["claim_L1", "claim_L2"]}) + "\n")
    mod.STORE = os.path.join(tmp, "store.json")
    mod.SHARDS, mod.ARCHIVE = shards, archive
    mod.SNAPSHOT = os.path.join(tmp, "no_snapshot.jsonl")
    mod.SERVES, mod.FETCHES = serves, os.path.join(tmp, "no_fetches.tsv")
    sk.PROJECTS, sk.CODEX = os.path.join(tmp, "projects"), os.path.join(tmp, "no_codex")
    sk.CACHE = os.path.join(tmp, "kinds.json")
    if os.path.exists(sk.CACHE):
        os.remove(sk.CACHE)
    sk.reset()
    r = mod.build()
    return r, json.load(open(mod.STORE, encoding="utf-8"))


def load_old(path):
    loader = importlib.machinery.SourceFileLoader("math_use_old", path)
    spec = importlib.util.spec_from_file_location("math_use_old", path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def main():
    tmp = tempfile.mkdtemp(prefix="mu_one_set_")
    live = {k: getattr(math_use, k) for k in ("STORE", "SHARDS", "ARCHIVE", "SNAPSHOT", "SERVES", "FETCHES")}
    live_sk = (sk.PROJECTS, sk.CODEX, sk.CACHE)
    try:
        r, st = build(math_use, tmp)
        check("M1 one session served A and B through two sources: 2 sets, no A-B edge",
              st["sets"] == 2 and "claim_B" not in st["co"].get("claim_A", {}), "%s %s" % (st["sets"], st["co"]))
        r, st = build(math_use, tmp, with_s3=True)
        check("M2 a second session serving A and B makes the edge", st["co"].get("claim_A", {}).get("claim_B", 0) > 0,
              str(st["co"]))
        r, st = build(math_use, tmp, archive_s1=True)
        check("M3 S1 live and archived at another time is still one set", st["sets"] == 2, str(st["sets"]))
        r, st = build(math_use, tmp, legacy=True)
        check("M4 an old 8-character id two of his sessions share is left out of co-activation, uses kept",
              st.get("legacy_ids_left_out") == 1 and st["sets"] == 2 and "claim_L1" in st["uses"]
              and "claim_L1" not in st["co"], "%s %s" % (st.get("legacy_ids_left_out"), st["sets"]))
        if OLD:
            old = load_old(OLD)
            _, so = build(old, tmp, with_s3=True, legacy=True)
            _, sn = build(math_use, tmp, with_s3=True, legacy=True)
            check("M5 claim sizes are the old module's",
                  {k: [w for _t, w in v] for k, v in so["uses"].items()}
                  == {k: [w for _t, w in v] for k, v in sn["uses"].items()}, "%s | %s" % (so["uses"], sn["uses"]))
            _, so1 = build(old, tmp)
            check("N1 the module before this change makes the A-B edge from S1 alone (the check can fail)",
                  so1["co"].get("claim_A", {}).get("claim_B", 0) > 0, str(so1["co"]))
    finally:
        for k, v in live.items():
            setattr(math_use, k, v)
        sk.PROJECTS, sk.CODEX, sk.CACHE = live_sk
        sk.reset()
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d controls pass" % (len(RAN) - len(FAILS), len(RAN)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
