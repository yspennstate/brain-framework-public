"""_sup_quiet with `old`: a route-named file edited in place (there before and after, no pointer either time) is quiet;
every other kind of change still goes to the real reader. Whenever the new rule says quiet, the real reader's map must
be the same before and after. Synthetic folder in a temp dir; the frontmatter cache goes to the temp dir too."""
import importlib.util, os, sys, tempfile, time
os.environ.update(BRAIN_RECALL_LOG="0", BRAIN_RECALL_REFRESH="0", BRS_SAMPLE="0", BRL_LOG="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import supersession as ss
spec = importlib.util.spec_from_file_location("brain_cand", os.environ.get("CAND", os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "brain.py")))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

tmp = tempfile.mkdtemp(prefix="supq_")
MEM = os.path.join(tmp, "memory")
os.makedirs(MEM)
TSV = os.path.join(tmp, "superseded.tsv")
ss._fm_cache_path = lambda d: os.path.join(tmp, "fm_cache.json")
B.MEM = MEM


def w(name, body, fm=None):
    with open(os.path.join(MEM, name + ".md"), "w") as fh:
        if fm is not None:
            fh.write("---\nname: %s\n%s---\n" % (name, fm))
        fh.write(body)


def rows():
    return [(e.name, tuple(ss._stat_key(e.stat()))) for e in ss._md_files(MEM)]


def real():
    return ss.active_supersessions(MEM, index_path=TSV, include_frontmatter=True)


def facts():
    """ptr and inv as brain._sup_base builds them, from a fresh read of every file (a tiny folder)."""
    ptr = {}
    for e in ss._md_files(MEM):
        h = ss._read_frontmatter(e.path)
        s = ss._frontmatter_target(h)
        c = "" if s else ss._lenient_candidate(h)
        if s or c:
            ptr[e.name] = (s, c)
    nc = os.path.normcase
    inv = set()
    for name, pair in ptr.items():
        inv.add(nc(name[:-3]))
        inv.update(nc(t) for t in pair if t)
    for line in open(TSV):
        inv.update(nc(t.strip()) for t in line.rstrip("\n").split("\t")[:2] if t.strip())
    return ptr, inv


def reset():
    for f in os.listdir(MEM):
        os.remove(os.path.join(MEM, f))
    w("a_old", "old fact\n", "superseded_by: b_fix\n")
    w("b_fix", "the correction\n", "description: fix\n")
    w("c_plain", "unrelated\n", "description: plain\n")
    w("d_old", "index-routed old\n", "description: d\n")
    w("e_new", "index-routed new\n", "description: e\n")
    with open(TSV, "w") as fh:
        fh.write("old\tcorrection\nd_old\te_new\nf_old\tg_later\n")
    w("f_old", "routes once g_later exists\n", "description: f\n")
    for f in os.listdir(MEM):   # older than the 6 s window, and older than any bump
        p = os.path.join(MEM, f)
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns - 60_000_000_000))


def bump(name, body, fm=None):
    time.sleep(0.01)
    w(name, body, fm)
    p = os.path.join(MEM, name + ".md")
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns - 20_000_000_000))   # older than the 6 s window


CASES = [
    ("edit route target b_fix in place", lambda: bump("b_fix", "the correction, reworded\n", "description: fix 2\n"), True),
    ("edit index-named e_new in place", lambda: bump("e_new", "longer body now\n", "description: e\n"), True),
    ("edit index-named d_old in place", lambda: bump("d_old", "x\n", "description: d changed\n"), True),
    ("edit plain c_plain", lambda: bump("c_plain", "still unrelated\n", "description: plain\n"), True),
    ("c_plain gains a pointer", lambda: bump("c_plain", "now old\n", "superseded_by: b_fix\n"), False),
    ("b_fix gains a pointer (chain)", lambda: bump("b_fix", "fix\n", "superseded_by: c_plain\n"), False),
    ("pointer holder a_old edited", lambda: bump("a_old", "old fact again\n", "superseded_by: b_fix\n"), False),
    ("route target b_fix deleted", lambda: os.remove(os.path.join(MEM, "b_fix.md")), False),
    ("route target g_later created", lambda: bump("g_later", "now here\n", "description: g\n"), False),
    ("new plain file", lambda: bump("h_new", "new memory\n", "description: h\n"), True),
]
ok = True
for label, act, want_new in CASES:
    reset()
    r0 = rows()
    a0 = real()
    ptr, inv = facts()
    act()
    r1 = rows()
    a1 = real()
    old, cur = dict(r0), dict(r1)
    now_ns = time.time_ns()
    recent = {n for n, k in r1 if now_ns - k[1] < 6_000_000_000}
    changed = [n for n, k in r1 if n in recent or old.get(n) != k] + [n for n in old if n not in cur]
    q_old = B._sup_quiet(ss, changed, cur, ptr, inv)
    q_new = B._sup_quiet(ss, changed, cur, ptr, inv, old)
    same = a0 == a1
    good = (q_new == want_new) and (not q_new or same) and (not q_old or same)
    ok &= good
    print("%-36s changed=%s old_rule=%s new_rule=%s map_same=%s %s" % (
        label, changed, q_old, q_new, same, "ok" if good else "FAIL"))
os.environ["BRAIN_SUP_NAMED_EDIT"] = "0"
reset(); r0 = rows(); ptr, inv = facts(); bump("b_fix", "y\n", "description: y\n"); r1 = rows()
off = B._sup_quiet(ss, ["b_fix.md"], dict(r1), ptr, inv, dict(r0))
print("switch off -> old rule:", off is False)
ok &= off is False
print("RESULT", "PASS" if ok else "FAIL")
