"""code_map.py - where in the brain's code does X live: a symbol map with a token-budgeted answer.

WHY: brain.py recall indexes memories, not code. A question like "which hook refuses a push that
would start billed CI" is answered by a file name and a function, and recall missed two of the first
five such questions. The idea comes from Graphify (safishamsi/graphify): parse the code, keep every function and class with its docstring and
what it calls, then answer with the matching symbols and their one-hop neighbours, cut to a token
budget. Nothing is imported from Graphify; this uses the standard library's ast only.

  python code_map.py build [--root DIR ...]      parse *.py under each root (default ~/.claude/dreaming)
  python code_map.py query "question" [--budget 800] [--files 5]
  python code_map.py stats

The map is one JSON file (code_map.json beside this script, or CODE_MAP_PATH). A build re-parses only
files whose size or mtime changed. Read-only on the code; writes only the map file.
"""
import ast, io, json, math, os, re, sys, time, warnings
warnings.simplefilter("ignore", SyntaxWarning)  # other files' escape warnings are not ours to print

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = os.environ.get("CODE_MAP_PATH") or os.path.join(HERE, "code_map.json")
DEFAULT_ROOTS = [os.path.expanduser("~/.claude/dreaming")]
VERSION = 1

STOP = set("""a an the of to in on for and or is are was be by with what which where who how does do
that this it its from at as into when why can any every there their then than not no all one""".split())
TOK = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def words(text):
    """Lower-case word stems: split snake_case and camelCase, drop stop words, crude plural fold."""
    out = []
    for w in TOK.findall(text.replace("_", " ")):
        for p in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", w) or [w]:
            p = p.lower()
            if len(p) < 2 or p in STOP:
                continue
            if len(p) > 4 and p.endswith("s") and not p.endswith("ss"):
                p = p[:-1]
            out.append(p)
    return out


def first_line(doc):
    if not doc:
        return ""
    for ln in doc.strip().splitlines():
        ln = ln.strip()
        if ln:
            return ln[:200]
    return ""


def parse_file(path):
    """Return {doc, syms:[[qualname, kind, line, docline, calls[]]]} or None when it does not parse."""
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except Exception:
        return None
    syms = []

    def calls_in(node):
        names = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        return sorted(names)[:40]

    def visit(body, prefix):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + n.name
                kind = "class" if isinstance(n, ast.ClassDef) else "def"
                syms.append([q, kind, n.lineno, first_line(ast.get_docstring(n)), calls_in(n) if kind == "def" else []])
                if kind == "class":
                    visit(n.body, q + ".")
    visit(tree.body, "")
    # string constants at module level often carry the user-facing text a question quotes
    consts = []
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(getattr(n, "value", None), ast.Constant) and isinstance(n.value.value, str):
            consts.append(n.value.value[:200])
    return {"doc": (ast.get_docstring(tree) or "")[:1200], "syms": syms, "consts": consts[:20]}


def build(roots):
    old = load(quiet=True) or {}
    oldf = old.get("files", {})
    files, reparsed = {}, 0
    for root in roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith((".", "__pycache__", "node_modules", "venv"))]
            for f in fn:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                key = os.path.relpath(p, root).replace("\\", "/") if len(roots) == 1 else p.replace("\\", "/")
                prev = oldf.get(key)
                if prev and "t" in prev and prev.get("size") == st.st_size and prev.get("mtime") == int(st.st_mtime):
                    files[key] = prev
                    continue
                r = parse_file(p)
                reparsed += 1
                if r is None:
                    continue
                r.update(size=st.st_size, mtime=int(st.st_mtime))
                # word lists are stored at build so a query does not re-tokenise 10k+ symbols
                stem = os.path.splitext(os.path.basename(key))[0]
                r["t"] = words(stem) * 3 + words(r.get("doc", "")) + words(" ".join(r.get("consts", [])))
                for s in r["syms"]:
                    s.append(words(s[0]) * 2 + words(s[3]) + words(stem))
                files[key] = r
    data = {"version": VERSION, "roots": roots, "built": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files}
    tmp = MAP + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, MAP)
    return len(files), reparsed


def load(quiet=False):
    try:
        with io.open(MAP, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        if not quiet:
            sys.exit("no map at %s - run: python code_map.py build" % MAP)
        return None


def query(q, budget=800, nfiles=5):
    data = load()
    files = data["files"]
    qw = [w for w in dict.fromkeys(words(q))]
    if not qw:
        return "no searchable words in the question"
    # documents: one per symbol, plus one per file (its name + module docstring + string constants)
    docs = []  # (file, symbol index or -1, tokens)
    for f, r in files.items():
        if "t" in r:
            docs.append((f, -1, r["t"]))
            for i, s in enumerate(r["syms"]):
                docs.append((f, i, s[5]))
            continue
        stem = os.path.splitext(os.path.basename(f))[0]
        docs.append((f, -1, words(stem) * 3 + words(r.get("doc", "")) + words(" ".join(r.get("consts", [])))))
        for i, s in enumerate(r["syms"]):
            docs.append((f, i, words(s[0]) * 2 + words(s[3]) + words(stem)))
    N = len(docs)
    df = {}
    for _, _, t in docs:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    avg = sum(len(t) for _, _, t in docs) / max(N, 1)
    idf = {w: math.log(1 + (N - df.get(w, 0) + 0.5) / (df.get(w, 0) + 0.5)) for w in qw}
    k1, b = 1.2, 0.75
    file_score, sym_hits = {}, {}
    for f, i, t in docs:
        if not t:
            continue
        tf = {}
        for w in t:
            if w in idf:
                tf[w] = tf.get(w, 0) + 1
        if not tf:
            continue
        s = sum(idf[w] * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg)) for w in tf)
        s *= (len(tf) / len(qw)) ** 0.5  # reward covering more of the question
        if i < 0:
            file_score[f] = file_score.get(f, 0) + 1.5 * s
        else:
            sym_hits.setdefault(f, []).append((s, i))
    for f, hits in sym_hits.items():
        hits.sort(reverse=True)
        file_score[f] = file_score.get(f, 0) + hits[0][0] + 0.3 * sum(h for h, _ in hits[1:3])
    ranked = sorted(file_score.items(), key=lambda kv: -kv[1])[:nfiles]
    # callers: which files call a name defined in a ranked file
    defined = {}
    for f, _ in ranked:
        for s in files[f]["syms"]:
            if s[1] == "def":
                defined.setdefault(s[0].split(".")[-1], f)
    out, used = [], 0
    cap = budget * 3  # about three characters a token, as Graphify's budget counts them

    def add(line):
        nonlocal used
        if used + len(line) + 1 > cap:
            return False
        out.append(line)
        used += len(line) + 1
        return True

    add("code_map: %d files, question words %s" % (len(files), " ".join(qw)))
    for rank, (f, sc) in enumerate(ranked, 1):
        r = files[f]
        if not add("%d. %s  (%.1f)  %s" % (rank, f, sc, first_line(r.get("doc", "")) or "")):
            break
        for s, i in sorted(sym_hits.get(f, []), reverse=True)[:3]:
            q_, kind, line, doc = r["syms"][i][:4]
            if not add("     L%d %s %s%s" % (line, kind, q_, (" - " + doc[:110]) if doc else "")):
                break
    # one hop: who else calls the top file's best symbols
    if ranked:
        f0 = ranked[0][0]
        best = [files[f0]["syms"][i][0].split(".")[-1] for _, i in sorted(sym_hits.get(f0, []), reverse=True)[:2]]
        callers = []
        if best:
            for f, r in files.items():
                if f == f0:
                    continue
                for s in r["syms"]:
                    if set(best) & set(s[4]):
                        callers.append("%s:L%d %s" % (f, s[2], s[0]))
                        break
        if callers:
            add("   called from: " + "; ".join(callers[:6]))
    return "\n".join(out)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "build":
        roots = [os.path.abspath(os.path.expanduser(a)) for a in argv[1:] if a != "--root"] or DEFAULT_ROOTS
        t = time.time()
        n, re_ = build(roots)
        print("built %s: %d files (%d parsed) in %.1f s" % (MAP, n, re_, time.time() - t))
        return 0
    if cmd == "query":
        q = argv[1] if len(argv) > 1 else ""
        budget = int(argv[argv.index("--budget") + 1]) if "--budget" in argv else 800
        nf = int(argv[argv.index("--files") + 1]) if "--files" in argv else 5
        print(query(q, budget, nf))
        return 0
    if cmd == "stats":
        d = load()
        ns = sum(len(r["syms"]) for r in d["files"].values())
        print("%s built %s: %d files, %d symbols" % (MAP, d["built"], len(d["files"]), ns))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
