"""brain._marshal_load_memo / _marshal_dump_memo keep a cache file's value in memory under the file's identity. Every
read after a change to the file must return the new content; a read with no change must come from the memo.

Runs in a temporary HOME on scratch files. Run: python3 test_marshal_memo.py   (exit 0 = pass)"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import marshal, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

BODY = r'''
import marshal, os, sys
sys.path.insert(0, sys.argv[1])
import brain
d = sys.argv[2]
fails = []
def check(name, ok):
    if not ok:
        fails.append(name)
p = os.path.join(d, "x.marshal")
brain._MARSHAL_MEMO.clear()
os.environ["BRAIN_MARSHAL_MEMO"] = "1"
v1 = {"a": [1, 2, 3], "k": (1, 2)}
brain._marshal_dump_memo(p, v1)
r = brain._marshal_load_memo(p)
check("after its own dump, a load returns the dumped object", r is v1)
check("control: an unchanged file stays on the memo", brain._marshal_load_memo(p) is v1)
v2 = {"a": [9], "k": (3,)}
with open(p + ".other", "wb") as fh:
    marshal.dump(v2, fh)
os.replace(p + ".other", p)
r = brain._marshal_load_memo(p)
check("replaced by another writer -> new content", r == v2 and r is not v1)
v3 = {"a": [7], "k": (4,)}          # the same marshalled size as v2, rewritten in place (same inode)
ino = os.stat(p).st_ino
with open(p, "r+b") as fh:
    fh.truncate(0)
    marshal.dump(v3, fh)
check("setup: the in-place rewrite kept the inode", os.stat(p).st_ino == ino)
check("in-place same-size rewrite -> new content", brain._marshal_load_memo(p) == v3)
os.environ["BRAIN_MARSHAL_MEMO"] = "0"
brain._marshal_dump_memo(p, v1)
check("switch off: a dump does not fill the memo", brain._MARSHAL_MEMO.get(p, (None, None))[1] is not v1)
r = brain._marshal_load_memo(p)
check("switch off: a load reads the file", r == v1 and r is not v1)
check("no temp file left behind", sorted(os.listdir(d)) == ["x.marshal"])
for f in fails:
    print("FAIL", f)
print("PASS" if not fails else "%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
'''


def main():
    tmp = tempfile.mkdtemp(prefix="mmemo_")
    try:
        work = os.path.join(tmp, "files")
        os.makedirs(work)
        env = dict(os.environ, HOME=tmp, BRAIN_RECALL_LOG="0", BRAIN_RECALL_REFRESH="0")
        env.pop("BRAIN_MARSHAL_MEMO", None)
        r = subprocess.run([sys.executable, "-c", BODY, HERE, work], cwd=HERE, env=env, capture_output=True, text=True,
                           timeout=120)
        sys.stdout.write(r.stdout)
        if r.returncode and not r.stdout.strip():
            print("FAIL could not run:", r.stderr[-400:])
        return r.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
