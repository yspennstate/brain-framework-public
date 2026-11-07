"""code_map.py - where in the brain's code does X live: a symbol map with a token-budgeted answer.

WHY: brain.py recall indexes memories, not code. A question like "which hook refuses a push that
would start billed CI" is answered by a file name and a function, and recall missed two of the first
five such questions. The idea comes from Graphify (safishamsi/graphify): parse the code, keep every function and class with its docstring and
what it calls, then answer with the matching symbols and their one-hop neighbours, cut to a token
budget. Nothing is imported from Graphify; this uses the standard library's ast only.

  python code_map.py build [--root DIR ...]      parse *.py under each root (default ~/.claude/dreaming)
  python code_map.py query "question" [--budget 800] [--files 5] [--meaning]
  python code_map.py stats

The map is one JSON file (code_map.json beside this script, or CODE_MAP_PATH). A build re-parses only
files whose size or mtime changed. Read-only on the code; writes only the map file and its vectors.

Meaning lane (7 Nov 2026): a build also embeds each changed file (name, docstring, symbols) with the brain's
bge-small encoder into code_map_emb.npz. A query keeps the word ranking's top two files and fills the rest
by cosine to the question; on 20 held questions the right file reached the top five 19 times against 15.
Loading the encoder costs 3-8 s, so the lane runs only when the word ranking's top score is under 15
(CODE_MAP_MEANING_GATE); --meaning runs it on every query. On 8 later questions the gate held 5 of 8 and the
lane on every query reached 6 of 8, none worse. CODE_MAP_MEANING=0 turns it off; without the vectors file or the
encoder the word ranking answers alone.
"""
import ast, io, json, math, os, re, sys, time, warnings
warnings.simplefilter("ignore", SyntaxWarning)  # other files' escape warnings are not ours to print

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = os.environ.get("CODE_MAP_PATH") or os.path.join(HERE, "code_map.json")
DEFAULT_ROOTS = [os.path.expanduser("~/.claude/dreaming")]
EMB = (MAP[:-5] if MAP.endswith(".json") else MAP) + "_emb.npz"  # one bge-small vector per file
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
    if os.environ.get("CODE_MAP_MEANING", "1") != "0":
        try:
            build_emb(files)
        except Exception:
            pass
    try:
        os.remove(MAP + ".refresh")
    except OSError:
        pass
    return len(files), reparsed


def _embedder():
    """The brain's bge-small encoder (query_embed.py beside this script), or None when it or numpy is absent."""
    if "query_embed" in sys.modules:
        qe = sys.modules["query_embed"]
        return qe if qe.available() else None
    for d in (HERE, os.path.expanduser("~/.claude/dreaming")):
        if not os.path.exists(os.path.join(d, "query_embed.py")):
            continue
        try:
            sys.path.insert(0, d)
            import numpy  # noqa: F401
            import query_embed
            return query_embed if query_embed.available() else None
        except Exception:
            return None
        finally:
            if sys.path and sys.path[0] == d:
                sys.path.pop(0)
    return None


def emb_text(key, r):
    """What a file's vector is made of: its name, module docstring and every symbol with its docstring."""
    return (os.path.basename(key) + ". " + (r.get("doc") or "")[:600] + " " + " ".join(
        "%s: %s." % (s[0], s[3]) if len(s) > 3 and s[3] else str(s[0]) for s in r["syms"]))[:2000]


def build_emb(files, limit=None):
    """Embed the files whose size or mtime changed since the last build; keep the rest. Returns files embedded."""
    qe = _embedder()
    if qe is None:
        return 0
    import numpy as np
    old = {}
    try:
        with np.load(EMB, allow_pickle=False) as z:  # closed at once: an open npz blocks the replace on Windows
            for k, sig, v in zip(z["keys"], z["sig"], np.array(z["E"])):
                old[str(k)] = (tuple(int(x) for x in sig), v)
    except Exception:
        pass
    keys, sigs, rows, done = [], [], [], 0
    for k in sorted(files):
        r = files[k]
        sig = (int(r.get("size", 0)), int(r.get("mtime", 0)))
        prev = old.get(k)
        if prev and prev[0] == sig:
            v = prev[1]
        elif limit is not None and done >= limit:
            continue
        else:
            v = qe.embed(emb_text(k, r))
            done += 1
            if v is None:
                continue
        keys.append(k)
        sigs.append(sig)
        rows.append(np.asarray(v, "f4"))
    if not rows:
        return 0
    tmp = EMB + ".tmp.npz"
    np.savez(tmp, keys=np.array(keys), sig=np.array(sigs, dtype=np.int64), E=np.vstack(rows))
    os.replace(tmp, EMB)
    return done


def meaning_rank(q, top=50):
    """Files ranked by cosine between the question and each file's vector: [(key, cos)], or None."""
    if os.environ.get("CODE_MAP_MEANING", "1") == "0" or not os.path.exists(EMB):
        return None
    qe = _embedder()
    if qe is None:
        return None
    import numpy as np
    try:
        with np.load(EMB, allow_pickle=False) as z:
            E, keys = np.array(z["E"]), np.array(z["keys"])
        v = qe.embed(q)
        if v is None:
            return None
        sc = E @ np.asarray(v, "f4")
        return [(str(keys[j]), float(sc[j])) for j in np.argsort(-sc)[:top]]
    except Exception:
        return None


def load(quiet=False):
    try:
        with io.open(MAP, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        if not quiet:
            sys.exit("no map at %s - run: python code_map.py build" % MAP)
        return None


def refresh_if_stale(max_age=3600):
    """Start a windowless background rebuild when the map is over max_age seconds old.

    The query still answers from the map on disk; the next one sees the new files. A build changing
    nothing takes about 2 s warm, so this costs the caller only the process start. CODE_MAP_REFRESH=0
    turns it off; a lock file younger than 10 minutes means a rebuild is already running.
    """
    if os.environ.get("CODE_MAP_REFRESH", "1") == "0":
        return False
    try:
        if time.time() - os.path.getmtime(MAP) < max_age:
            return False
        lock = MAP + ".refresh"
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 600:
            return False
        io.open(lock, "w").close()
        import subprocess
        exe = sys.executable
        if os.name == "nt":
            w = os.path.join(os.path.dirname(exe), "pythonw.exe")
            exe = w if os.path.exists(w) else exe
        cmd = [exe, os.path.abspath(__file__), "build"]
        roots = load(quiet=True) or {}
        cmd += [r for r in roots.get("roots", []) if os.path.isdir(r)]
        flags = 0x08000000 | 0x00000008 | 0x00000200 if os.name == "nt" else 0  # no window, detached, own group
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, creationflags=flags)
        return True
    except Exception:
        return False


def query(q, budget=800, nfiles=5):
    raw_q = q
    data = load()
    refresh_if_stale()
    files = data["files"]
    if os.environ.get("CODE_MAP_SYN", "1") != "0":
        # the brain's hand-reviewed question-word -> stored-word table ("out of date" -> stale); original words kept.
        # 7 Nov bench, 20 questions: top-5 14 -> 15, no question worse. Absent module: no change.
        try:
            sys.path.insert(0, HERE)
            import brain_syn
            q = brain_syn.expand(q)
        except Exception:
            pass
        finally:
            if sys.path and sys.path[0] == HERE:
                sys.path.pop(0)
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
    allr = sorted(file_score.items(), key=lambda kv: -kv[1])
    # The meaning lane costs 3-8 s (loading the encoder), so it runs only when the word ranking looks weak:
    # on the 7 Nov bench every miss had a top score of 14.0 or less and 14 of 16 hits scored above 15.
    gate = float(os.environ.get("CODE_MAP_MEANING_GATE", "15"))
    mr = []
    if not allr or allr[0][1] < gate:
        mr = [(k, c) for k, c in (meaning_rank(raw_q) or []) if k in files]
    if mr:
        # keep the word lookup's top two, then fill by meaning (7 Nov bench, 20 questions: top-5 15 -> 19, none worse)
        ranked, seen, mcos = list(allr[:2]), {f for f, _ in allr[:2]}, dict(mr)
        for f, c in mr + allr[2:]:
            if len(ranked) >= nfiles:
                break
            if f not in seen:
                seen.add(f)
                ranked.append((f, file_score.get(f, 0.0)))
    else:
        ranked, mcos = allr[:nfiles], {}
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
        tag = ("%.1f" % sc) if rank <= 2 or f not in mcos else "meaning %.2f" % mcos[f]
        if not add("%d. %s  (%s)  %s" % (rank, f, tag, first_line(r.get("doc", "")) or "")):
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
        if "--meaning" in argv:
            os.environ["CODE_MAP_MEANING_GATE"] = "1e9"
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
