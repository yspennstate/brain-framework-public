"""test_memory_pull.py - memory_pull copies only what is missing, never overwrites, never deletes.

    python test_memory_pull.py [PATH_TO_memory_pull.py]

Runs in a temporary folder with a directory source and a fake repository source; no network, no installed brain.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "memory_pull.py")
sys.path.insert(0, os.path.dirname(PATH))
spec = importlib.util.spec_from_file_location("memory_pull_under_test", PATH)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
FAILS = []


def run(name, ok):
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        FAILS.append(name)


def note(desc):
    return ("---\nname: x\ndescription: %s\n---\n\nbody\n" % desc).encode()


def write(folder, name, body):
    with open(os.path.join(folder, name), "wb") as fh:
        fh.write(body)


def read(folder, name):
    with open(os.path.join(folder, name), "rb") as fh:
        return fh.read()


root = tempfile.mkdtemp(prefix="memory_pull_test_")
src, here = os.path.join(root, "src"), os.path.join(root, "here")
os.makedirs(src)
os.makedirs(here)
write(src, "alpha_fact_2026_01_05.md", note("alpha"))
write(src, "beta_fact_2026_03_01.md", note("beta"))
write(src, "shared_fact_2026_02_01.md", note("theirs"))
write(src, "no_front_matter_2026_02_02.md", b"just text\n")
write(src, "MEMORY.md", b"- index line\n")
write(src, "Bad Name.md", note("bad"))
write(here, "shared_fact_2026_02_01.md", note("mine"))
write(here, "local_only_2026_01_01.md", note("local"))
quiet = lambda *_: None

# 1. dry run writes nothing
got = M.pull(M.DirSource(src), here, apply=False, refresh=False, out=quiet)
run("1 a dry run writes nothing", got == [] and sorted(os.listdir(here)) == ["local_only_2026_01_01.md",
                                                                          "shared_fact_2026_02_01.md"])
# 2. apply copies the missing memories, newest slug date first
got = M.pull(M.DirSource(src), here, apply=True, refresh=False, out=quiet)
run("2 the missing memories arrive, newest first: %s" % got,
    got == ["beta_fact_2026_03_01.md", "alpha_fact_2026_01_05.md"])
# 3. a local file with the same name keeps its own content
run("3 a local edit is never overwritten", read(here, "shared_fact_2026_02_01.md") == note("mine"))
# 4. nothing local is deleted
run("4 a file only here stays", os.path.exists(os.path.join(here, "local_only_2026_01_01.md")))
# 5. indexes, bad names and files without frontmatter are skipped
run("5 MEMORY.md, a bad name and a file without frontmatter are skipped",
    not any(os.path.exists(os.path.join(here, n)) for n in ("MEMORY.md", "Bad Name.md", "no_front_matter_2026_02_02.md")))
# 6. a second run is a no-op
run("6 a second run copies nothing", M.pull(M.DirSource(src), here, apply=True, refresh=False, out=quiet) == [])
# 7. the cap takes the newest first
here2 = os.path.join(root, "here2")
os.makedirs(here2)
got = M.pull(M.DirSource(src), here2, apply=True, cap=1, refresh=False, out=quiet)
run("7 --max 1 takes the newest: %s" % got, got == ["beta_fact_2026_03_01.md"])


# 8. the repository source reads one tree and decodes contents (fake api, no network)
class FakeRepo(M.RepoSource):
    def __init__(self, files):
        self.repo, self.folder, self.branch = "o/r", "notes/memory", "main"
        self.calls = []

        def api(method, url, timeout=30):
            self.calls.append(url)
            if url.startswith("/repos/o/r/contents/notes?"):
                return 200, [{"name": "memory", "type": "dir", "sha": "T"}]
            if url == "/repos/o/r/git/trees/T":
                return 200, {"tree": [{"path": n, "type": "blob", "sha": "s"} for n in files]}
            name = url.split("/notes/memory/")[1].split("?")[0]
            return 200, {"content": base64.b64encode(files[name]).decode()}
        self.api = api


here3 = os.path.join(root, "here3")
os.makedirs(here3)
fake = FakeRepo({"gamma_fact_2026_04_01.md": note("gamma"), "MISTAKES.md": b"log\n"})
got = M.pull(fake, here3, apply=True, refresh=False, out=quiet)
run("8 the repository source: one listing, one fetch per missing file: %s" % got,
    got == ["gamma_fact_2026_04_01.md"] and read(here3, "gamma_fact_2026_04_01.md") == note("gamma")
    and len(fake.calls) == 3)
import shutil
shutil.rmtree(root, ignore_errors=True)
print("%d of 8 controls pass (%s)" % (8 - len(FAILS), PATH))
sys.exit(1 if FAILS else 0)
