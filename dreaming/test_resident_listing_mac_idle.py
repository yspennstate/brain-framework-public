"""macOS only: the recall server's FSEvents resident listing runs its periodic full walk between calls
(ResidentBulk.refresh_if_due), and the walk must leave the listing equal to a fresh _bulk_listing.

Builds a small brain in a temporary HOME and, in one server Brain, checks: not due -> no walk (control); due -> it
walks and the listing equals a fresh walk (names in order, kind, every stat slot), keeping unchanged entry objects;
the next listing is not a full walk; served recall equals a cold process; a planted stale size is replaced.
Run: python3 test_resident_listing_mac_idle.py   (exit 0 = pass; prints SKIP and exits 0 off macOS)"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

BODY = r'''
import copy, os, subprocess, sys
D = sys.argv[1]
sys.path.insert(0, D)
import brain_recall_server as S
import brain_resident_listing_mac as L
B = S.Brain()
rb = B.resident
fails = []
def check(name, ok):
    if not ok:
        fails.append(name)
if not hasattr(rb, "refresh_if_due"):
    print("FAIL the server did not install the FSEvents listing: %r" % (rb,))
    sys.exit(1)
env = dict(os.environ)
B.call(["recall", "warm the server"], env, D)
def view(lst):
    slots = type(lst[0]._st).__slots__
    return [(e.name, e._dir, tuple(getattr(e._st, k, None) for k in slots)) for e in lst]
check("not due -> no walk", rb.refresh_if_due(60) is False)
rb.full_t -= L.FULL_EVERY
before = dict(rb.d)
walked = rb.refresh_if_due(60)
fresh = rb.bulk(rb.folder)
check("due -> walks, and the listing equals a fresh walk", walked and view(rb.order) == view(fresh))
kept = sum(1 for e in rb.order if before.get(e.name) is e)
check("unchanged entries keep their object (%d of %d)" % (kept, len(rb.order)), kept == len(rb.order))
full0 = rb.counts["full"]
rb.listing()
check("the next listing is not a full walk", rb.counts["full"] == full0)
cold_env = dict(os.environ, BRAIN_RECALL_SERVER="0")
for q in ("station calibration serial swap", "garden tomatoes basil watering"):
    r = B.call(["recall", q], env, D)
    p = subprocess.run([sys.executable, os.path.join(D, "brain.py"), "recall", q], env=cold_env, capture_output=True,
                       text=True)
    check("served equals cold for %r" % q, (r["out"], r["rc"]) == (p.stdout, p.returncode))
victim = next(e for e in rb.order if not e._dir)
fake = copy.copy(victim)
st = copy.copy(victim._st)
st.st_size = victim._st.st_size + 1
fake._st = st
rb.d[victim.name] = fake
rb.full_t -= L.FULL_EVERY
rb.refresh_if_due(60)
check("a planted stale size is replaced by the walk's entry",
      rb.d[victim.name] is not fake and rb.d[victim.name]._st.st_size == victim._st.st_size)
for f in fails:
    print("FAIL", f)
print("PASS" if not fails else "%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
'''


def main():
    if sys.platform != "darwin":
        print("SKIP not macOS")
        return 0
    tmp = tempfile.mkdtemp(prefix="rlidle_")
    try:
        mem = os.path.join(tmp, ".claude", "projects", "t", "memory")
        os.makedirs(mem)
        idx = ["# Memory Index"]
        texts = ["Station QX-7731 carried calibration serial 7KDP-2210 until the overnight swap.",
                 "Water the tomatoes twice a week and the basil every day in summer."]
        texts += ["Filler note %d about a quiet afternoon and nothing in particular." % i for i in range(30)]
        for i, text in enumerate(texts):
            slug = "note_%02d" % i
            with open(os.path.join(mem, slug + ".md"), "w") as f:
                f.write("---\nname: %s\ndescription: \"%s\"\nmetadata:\n  type: reference\n---\n\n%s\n"
                        % (slug, text[:40], text))
            idx.append("- [%s.md](%s.md) - %s" % (slug, slug, text[:40]))
        with open(os.path.join(mem, "MEMORY.md"), "w") as f:
            f.write("\n".join(idx) + "\n")
        env = dict(os.environ, HOME=tmp, BRAIN_SLUG="t", BRAIN_BM25_INDEX=os.path.join(tmp, "bm25.json.gz"),
                   BRAIN_RECALL_LOG="0", BRAIN_RECALL_REFRESH="0", BRAIN_RECALL_QUERY_EMBED="0", BRS_SAMPLE="0",
                   BRL_LOG="0", BRS_CALL_LOG="0")
        env.pop("BRAIN_MEM_DIR", None)
        env.pop("BRS_RESIDENT_LISTING", None)
        subprocess.run([sys.executable, "-c", "import bm25; bm25.build()"], cwd=HERE, env=env, capture_output=True)
        r = subprocess.run([sys.executable, "-c", BODY, HERE], cwd=HERE, env=env, capture_output=True, text=True,
                           timeout=300)
        sys.stdout.write(r.stdout)
        if r.returncode and not r.stdout.strip():
            print("FAIL could not run:", r.stderr[-500:])
        return r.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
