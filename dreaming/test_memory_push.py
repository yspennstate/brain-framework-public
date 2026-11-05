"""test_memory_push.py - memory_push sends new and descended memories, holds two-sided edits, never loses a line.

    python test_memory_push.py [PATH_TO_memory_push.py]

Runs in a temporary folder with a folder target and a fake repository target; no network, no installed brain.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64
import importlib.util
import os
import random
import shutil
import string
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "memory_push.py")
sys.path.insert(0, os.path.dirname(PATH))
spec = importlib.util.spec_from_file_location("memory_push_under_test", PATH)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
FAILS = []


def run(name, ok):
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        FAILS.append(name)


def note(desc, body="body"):
    return ("---\nname: x\ndescription: %s\n---\n\n%s\n" % (desc, body)).encode()


def write(folder, name, data, age=600):
    p = os.path.join(folder, name)
    with open(p, "wb") as fh:
        fh.write(data)
    t = time.time() - age
    os.utime(p, (t, t))


def read(folder, name):
    with open(os.path.join(folder, name), "rb") as fh:
        return fh.read()


quiet = lambda *_: None
root = tempfile.mkdtemp(prefix="memory_push_test_")
mem, shared = os.path.join(root, "mem"), os.path.join(root, "shared")
os.makedirs(mem)
os.makedirs(shared)

# at the target already: an equal file, one this computer last synced and then edited, one grown in place,
# one edited on both sides, and one the other computer changed while this one did not
write(shared, "equal_fact_2026_01_01.md", note("same"))
write(shared, "ff_fact_2026_01_02.md", note("v1"))
write(shared, "grown_fact_2026_01_03.md", note("short"))
write(shared, "both_fact_2026_01_04.md", note("theirs"))
write(shared, "undated_seed.md", note("seed v1"))
write(mem, "equal_fact_2026_01_01.md", note("same"))
write(mem, "ff_fact_2026_01_02.md", note("v2"))
write(mem, "grown_fact_2026_01_03.md", note("short, and longer now", "body\nmore"))
write(mem, "both_fact_2026_01_04.md", note("mine"))
write(mem, "undated_seed.md", note("seed v1", "an edit"))
write(mem, "new_fact_2026_02_01.md", note("new"))
write(mem, "newer_fact_2026_03_01.md", note("newer"))
write(mem, "fresh_fact_2026_03_02.md", note("still being written"), age=5)
write(mem, "blank_template.md", note("template"))          # undated, not at the target: never sent
write(mem, "MEMORY.md", b"- index\n")
write(mem, "big_fact_2026_02_02.md", b"---\n" + b"x" * M.MAX_BYTES)
tok = "ghp_" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(36))
write(mem, "leaky_fact_2026_02_03.md", note("leak", "token: " + tok))
state = {"synced": {"ff_fact_2026_01_02.md": M.blob_sha(note("v1")),
                    "undated_seed.md": M.blob_sha(note("seed v1"))}}

# 1. a dry run sends nothing
r = M.push(M.DirTarget(shared), mem, apply=False, state=state, out=quiet, trust_listing=True)
run("1 a dry run changes nothing at the target", read(shared, "ff_fact_2026_01_02.md") == note("v1")
    and not os.path.exists(os.path.join(shared, "new_fact_2026_02_01.md")) and r["sent"] == [])

# 2. apply: new, fast-forward, superset and an edited undated name already there go up; newest first
r = M.push(M.DirTarget(shared), mem, apply=True, state=state, out=quiet, trust_listing=True)
want = ["newer_fact_2026_03_01.md", "new_fact_2026_02_01.md", "grown_fact_2026_01_03.md", "ff_fact_2026_01_02.md",
        "undated_seed.md"]
run("2 sent new, fast-forward, superset: %s" % r["sent"], sorted(r["sent"]) == sorted(want))
run("3 the sent files equal the local ones", all(read(shared, n) == read(mem, n) for n in want))
# 4. a file changed on both sides is held and the target copy is untouched
run("4 a two-sided edit is held, target kept", r["held"] == ["both_fact_2026_01_04.md"]
    and read(shared, "both_fact_2026_01_04.md") == note("theirs"))
# 5. never sent: a file still being written, a template seed, an index, a file of 2 MB or more
run("5 still-fresh, seed, MEMORY.md and big file stay home",
    not any(os.path.exists(os.path.join(shared, n)) for n in
            ("fresh_fact_2026_03_02.md", "blank_template.md", "MEMORY.md", "big_fact_2026_02_02.md")))
# 6. the secret scan drops the leaky file
run("6 a file with a token is dropped by the scan", r["dropped"] == ["leaky_fact_2026_02_03.md"]
    and not os.path.exists(os.path.join(shared, "leaky_fact_2026_02_03.md")))
# 7. a second run sends nothing and holds the same file
r = M.push(M.DirTarget(shared), mem, apply=True, state=state, out=quiet)
run("7 a second run sends nothing new", r["sent"] == [] and r["held"] == ["both_fact_2026_01_04.md"])
# 8. after the other computer edits a synced file, a local edit is held, not overwritten
write(shared, "new_fact_2026_02_01.md", note("new, edited elsewhere"))
write(mem, "new_fact_2026_02_01.md", note("new, edited here"))
r = M.push(M.DirTarget(shared), mem, apply=True, state=state, out=quiet)
run("8 an edit on both sides after a sync is held", "new_fact_2026_02_01.md" in r["held"]
    and read(shared, "new_fact_2026_02_01.md") == note("new, edited elsewhere"))
# 9. a listing that has lost the names synced before stops the run (it would make every memory look new)
empty = os.path.join(root, "empty")
os.makedirs(empty)
r = M.push(M.DirTarget(empty), mem, apply=True, state={"synced": dict(state["synced"])}, out=quiet)
run("9 a listing missing the synced names stops the run", r.get("stopped") == "listing" and os.listdir(empty) == [])
# 9b. with no history, an empty listing against a folder of more than 20 memories stops too
mem3 = os.path.join(root, "mem3")
os.makedirs(mem3)
for i in range(25):
    write(mem3, "m%02d_fact_2026_05_01.md" % i, note("m%d" % i))
r = M.push(M.DirTarget(empty), mem3, apply=True, state={}, out=quiet)
run("9b no history and an empty listing stops", r.get("stopped") == "listing" and os.listdir(empty) == [])
# 9c. new memories do not count against the listing: 25 new ones beside 5 synced still go up
part = os.path.join(root, "part")
os.makedirs(part)
for i in range(5):
    shutil.copy(os.path.join(mem3, "m%02d_fact_2026_05_01.md" % i), part)
hist = {"synced": {"m%02d_fact_2026_05_01.md" % i: M.blob_sha(note("m%d" % i)) for i in range(5)}}
r = M.push(M.DirTarget(part), mem3, apply=True, state=hist, out=quiet)
run("9c 20 new beside 5 synced all go up (%d)" % len(r["sent"]), len(r["sent"]) == 20)
# 10. --max takes the newest
r = M.push(M.DirTarget(empty), mem, apply=True, state={}, out=quiet, trust_listing=True, cap=1)
run("10 --max 1 takes the newest: %s" % r["sent"], r["sent"] == ["fresh_fact_2026_03_02.md"] or
    r["sent"] == ["newer_fact_2026_03_01.md"])


# 11. the repository target: one listing, superset read through the blob API, publish names repo paths (fake api)
class FakeRepo(M.RepoTarget):
    def __init__(self, files):
        self.repo, self.folder, self.branch = "o/r", "notes/memory", "main"
        self.files = {n: (M.blob_sha(b), b) for n, b in files.items()}
        self.published = None
        outer = self

        class Src:
            def names(self):
                return {n: s for n, (s, _) in outer.files.items()}

            def api(self, method, url, timeout=30):
                sha = url.rsplit("/", 1)[1]
                body = next(b for s, b in outer.files.values() if s == sha)
                return 200, {"content": base64.b64encode(body).decode()}
        self.src = Src()

    def publish(self, mem_, names, message):
        self.published = (names, message)
        return 0, "landed"


mem2 = os.path.join(root, "mem2")
os.makedirs(mem2)
write(mem2, "a_fact_2026_04_01.md", note("a", "one\ntwo"))
write(mem2, "b_fact_2026_04_02.md", note("b"))
fake = FakeRepo({"a_fact_2026_04_01.md": note("a", "one"), "c_fact_2026_04_03.md": note("c")})
st = {}
r = M.push(fake, mem2, apply=True, state=st, out=quiet, trust_listing=True)
run("11 repo target: superset via blob read, new file, state records both: %s" % (fake.published,),
    fake.published and sorted(fake.published[0]) == ["a_fact_2026_04_01.md", "b_fact_2026_04_02.md"]
    and "[skip ci]" in fake.published[1]
    and st["synced"]["a_fact_2026_04_01.md"] == M.blob_sha(read(mem2, "a_fact_2026_04_01.md")))
# 12. blob ids match git's
import subprocess
p = os.path.join(mem2, "b_fact_2026_04_02.md")
g = subprocess.run(["git", "hash-object", p], capture_output=True, text=True)
run("12 blob_sha equals git hash-object", g.returncode != 0 or g.stdout.strip() == M.blob_sha(read(mem2, "b_fact_2026_04_02.md")))
shutil.rmtree(root, ignore_errors=True)
print("%d of 14 controls pass (%s)" % (14 - len(FAILS), PATH))
sys.exit(1 if FAILS else 0)
