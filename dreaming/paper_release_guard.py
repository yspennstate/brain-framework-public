"""Paper release guard (owner, 2 Nov 2026 05:0x).

His words, after a paper went up on a public GitHub repo with an overflowing line and no funding statement or AI
acknowledgment: "this is not how we write paper, fix that repo right now, and put in the brian this ifnroamtion and
hooks so that this never ever happens agian".

A paper (a .tex file with \\documentclass, \\begin{document} and an abstract or \\maketitle) pushed to a PUBLIC
GitHub repository must have:
  1. a funding section: a \\section* heading containing "funding" (or the \\FUNDING macro);
  2. a declaration of generative AI: a \\section* heading naming generative AI / AI-assisted / use of AI;
  3. a competing-interest declaration (a heading containing "competing interest");
  4. no finished grant (NSF OISE-2401227 ended; owner 16 Jul);
  5. no line running into the margin: it is compiled with pdflatex and any "Overfull \\hbox" over 1pt refuses.

Use:
  python paper_release_guard.py check <file.tex or folder> [...]    run by hand before any push or API upload
  called from github_push_cost_guard.py on every git push (pre-push), so every clone that has the cost guard has this
  python paper_release_guard.py --selftest

A file that is not a paper (a table fragment, a beamer talk) is skipped. A draft that must go to a public repo
before it is finished does not exist: finish it first. There is no bypass.
"""
import sys as _sys
if __name__ == "__main__" and any(_a in ("-h", "--help") for _a in _sys.argv[1:]):
    # Asking for help must not run the script's real work.
    print((__doc__ or "").strip() or "usage: python " + _sys.argv[0])
    raise SystemExit(0)
import io, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time, urllib.request

LOG = os.path.expanduser("~/.claude/dreaming/logs/paper_release_guard.log")
NOWIN = 0x08000000 if os.name == "nt" else 0
OVERFULL_PT = 1.0
FINISHED_GRANTS = ("2401227",)


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def strip_comments(t):
    return re.sub(r"(?<!\\)%.*", "", t)


def is_paper(t):
    t = strip_comments(t)
    if not re.search(r"\\documentclass", t) or "\\begin{document}" not in t:
        return False
    if re.search(r"\\documentclass(\[[^\]]*\])?\{(beamer|standalone)\}", t):
        return False
    return bool(re.search(r"\\begin\{abstract\}|\\maketitle", t))


HEAD = r"\\(?:section|subsection|paragraph)\*?\{([^}]*)\}"


def section_problems(text, extra=""):
    """text: the paper; extra: the other .tex/.sty files beside it (macros such as \\FUNDING)."""
    t = strip_comments(text)
    heads = [h.lower() for h in re.findall(HEAD, t)]
    # Journal classes (imsart for AAP/AoP, among others) carry these as environments, not headings.
    acks = " ".join(re.findall(r"\\begin\{acks\}(.*?)\\end\{acks\}", t, re.S))
    probs = []
    if not (any("funding" in h for h in heads) or "\\FUNDING" in t or "\\begin{funding}" in t):
        probs.append("no funding section (\\section*{Acknowledgments and funding})")
    if not (any(re.search(r"generative ai|ai-assisted|use of ai|\bai\b.*declaration|declaration.*\bai\b|"
                          r"artificial intelligence", h) for h in heads)
            or re.search(r"\bAI systems?\b|\bClaude\b|\bChatGPT\b|\bGPT\b|\bCodex\b", acks)):
        probs.append("no AI declaration (\\section*{Declaration of generative AI and AI-assisted technologies})")
    if not any("competing interest" in h or "conflict of interest" in h or "conflicts of interest" in h
               for h in heads):
        probs.append("no competing-interest declaration (\\section*{Declaration of competing interest})")
    for g in FINISHED_GRANTS:
        if g in t or g in strip_comments(extra):
            probs.append("names the finished grant NSF OISE-%s (owner 16 Jul: never list it again)" % g)
    return probs


def overfull(folder, main):
    """Compile main (a path relative to folder) and return (list of overfull lines, note)."""
    exe = shutil.which("pdflatex")
    if not exe:
        return [], "pdflatex not found - overfull lines NOT checked"
    wd = os.path.dirname(os.path.join(folder, main)) or folder
    name = os.path.basename(main)
    stem = name[:-4]
    out = tempfile.mkdtemp(prefix="paperguard_")
    cmd = [exe, "-interaction=nonstopmode", "-disable-installer", "-output-directory", out, name]
    env = dict(os.environ, BIBINPUTS=wd + os.pathsep, TEXINPUTS=wd + os.pathsep)
    try:
        for i in range(2):
            subprocess.run(cmd, cwd=wd, capture_output=True, timeout=240, creationflags=NOWIN, env=env)
            aux = os.path.join(out, stem + ".aux")
            if i == 0 and os.path.exists(aux) and "\\bibdata" in io.open(aux, encoding="latin-1").read():
                bt = shutil.which("bibtex")
                if bt:
                    subprocess.run([bt, stem], cwd=out, capture_output=True, timeout=120, creationflags=NOWIN, env=env)
        logf = os.path.join(out, stem + ".log")
        if not os.path.exists(logf):
            return [], "did not compile - overfull lines NOT checked"
        lg = io.open(logf, encoding="latin-1").read()
        hits = []
        for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt too wide\)([^\n]*)", lg):
            if float(m.group(1)) > OVERFULL_PT:
                hits.append("%spt too wide%s" % (m.group(1), m.group(2)))
        note = "" if os.path.exists(os.path.join(out, stem + ".pdf")) else "compile errors - check the log"
        return hits, note
    except subprocess.TimeoutExpired:
        return [], "compile timed out - overfull lines NOT checked"
    finally:
        shutil.rmtree(out, ignore_errors=True)


INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}|\\input\s+([^\s{}\\]+)")


def expand_inputs(t, dirs, depth=0):
    """Inline \\input/\\include targets (so that declarations kept in a separate file such as
    sections/declarations.tex are read). dirs: the paper's folder first, then the repo root; LaTeX resolves against both."""
    if depth > 4:
        return t

    def sub(m):
        name = (m.group(1) or m.group(2) or "").strip()
        for d in dirs:
            for cand in (name, name + ".tex"):
                f = os.path.join(d, cand)
                if os.path.isfile(f):
                    try:
                        return expand_inputs(io.open(f, encoding="utf-8", errors="replace").read(), dirs, depth + 1)
                    except OSError:
                        pass
        return m.group(0)
    return INPUT.sub(lambda m: "\n" + sub(m) + "\n", strip_comments(t))


def check_folder(folder, rel_files):
    """Check the papers among rel_files (paths relative to folder). Returns list of (file, problems, notes)."""
    res = []
    for rel in rel_files:
        p = os.path.join(folder, rel)
        try:
            t = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not is_paper(t):
            continue
        t = expand_inputs(t, [os.path.dirname(p), folder])
        d = os.path.dirname(p)
        extra = ""
        for f in os.listdir(d):
            if f.endswith((".tex", ".sty")) and os.path.join(d, f) != p:
                try:
                    extra += io.open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
                except OSError:
                    pass
        probs = section_problems(t, extra)
        hits, note = overfull(folder, rel)
        probs += ["line runs into the margin: Overfull \\hbox (%s)" % h for h in hits]
        res.append((rel, probs, note))
    return res


def report(res, where):
    bad = [r for r in res if r[1]]
    for rel, probs, note in res:
        log("%s %s %s %s" % ("REFUSE" if probs else "ok", where, rel, " | ".join(probs + ([note] if note else []))))
    if not bad:
        for rel, _, note in res:
            if note:
                sys.stderr.write("[paper-guard] %s: %s\n" % (rel, note))
        return True
    sys.stderr.write("[paper-guard] REFUSED (owner, 2 Nov 2026: \"this is not how we write paper ... so that this "
                     "never ever happens agian\"). A public paper needs a funding section, a competing-interest "
                     "declaration, a declaration of generative AI, and no line in the margin:\n")
    for rel, probs, note in bad:
        sys.stderr.write("  %s\n" % rel)
        for pr in probs:
            sys.stderr.write("    - %s\n" % pr)
    sys.stderr.write("  Model block: yspennstate/bilateral-preference-connectivity-window paper/main.tex (Acknowledgements "
                     "and funding / Declaration of competing interest / Declaration of generative AI). The AI "
                     "declaration says truthfully which systems did what. Fix, rebuild the PDF, push again.\n")
    return False


def repo_is_public(url):
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?/?$", url or "")
    if not m:
        return False
    try:
        r = urllib.request.urlopen("https://api.github.com/repos/%s/%s" % (m.group(1), m.group(2)), timeout=15)
        return not json.load(r).get("private", True)
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else True
    except Exception:
        return True  # cannot tell: check it


def git(*a, cwd=None):
    r = subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True, creationflags=NOWIN)
    return r.returncode, r.stdout


def pre_push(remote_url, lines):
    """Called with the pre-push stdin lines. Returns (allow, None); writes its own messages."""
    zero = "0" * 40
    changed = set()
    sha_for = None
    for l in lines:
        parts = l.split()
        if len(parts) != 4 or parts[1] == zero:
            continue
        lsha, rsha = parts[1], parts[3]
        if rsha == zero:
            rc, out = git("ls-tree", "-r", "--name-only", lsha)
        else:
            rc, out = git("diff", "--name-only", rsha, lsha)
            if rc != 0:
                rc, out = git("ls-tree", "-r", "--name-only", lsha)
        if rc == 0:
            changed |= {f for f in out.decode("utf-8", "replace").splitlines() if f.endswith(".tex")}
            sha_for = lsha
    if not changed:
        return True
    if not repo_is_public(remote_url):
        return True
    # Papers among the changed .tex: read the pushed content, not the working tree.
    tmp = tempfile.mkdtemp(prefix="paperguard_tree_")
    try:
        dirs = sorted({os.path.dirname(f) or "." for f in changed})
        cand = []
        for f in changed:
            rc, blob = git("show", "%s:%s" % (sha_for, f))
            if rc == 0 and is_paper(blob.decode("utf-8", "replace")):
                cand.append(f)
        if not cand:
            return True
        pdirs = sorted({os.path.dirname(f) for f in cand})
        args = ["archive", "--format=tar", sha_for, "--"] + [d if d else "." for d in pdirs]
        r = subprocess.run(["git"] + args, capture_output=True, creationflags=NOWIN)
        if r.returncode != 0:
            log("archive failed for %s: %r" % (remote_url, r.stderr[:200]))
            return True
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
            tf.extractall(tmp)
        return report(check_folder(tmp, cand), remote_url)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_paths(paths):
    """The `check` command. Fails closed: a path that does not exist, or that holds no paper the guard
    recognises, is a refusal, not a pass (a typo'd path used to exit 0 with nothing checked). One verdict
    line per paper goes to stdout so the caller sees what was read."""
    ok = True
    for p in paths:
        p = os.path.abspath(p)
        if not os.path.exists(p):
            sys.stderr.write("[paper-guard] REFUSED: %s does not exist - nothing was checked\n" % p)
            ok = False
            continue
        if os.path.isdir(p):
            files = []
            for root, _, fs in os.walk(p):
                files += [os.path.relpath(os.path.join(root, f), p) for f in fs if f.endswith(".tex")]
            res = check_folder(p, files)
        else:
            res = check_folder(os.path.dirname(p), [os.path.basename(p)])
        if not res:
            sys.stderr.write("[paper-guard] REFUSED: no paper found in %s (needs \\documentclass, \\begin{document} "
                             "and an abstract or \\maketitle) - nothing was checked\n" % p)
            ok = False
            continue
        for rel, probs, _ in res:
            print("[paper-guard] %s %s" % ("REFUSE" if probs else "ok", rel))
        ok &= report(res, p)
    return ok


def selftest():
    good = ("\\documentclass{amsart}\\begin{document}\\begin{abstract}x\\end{abstract}\\maketitle\n"
            "\\section*{Acknowledgments and funding} ERC.\\section*{Declaration of competing interest} None."
            "\\section*{Declaration of generative AI and AI-assisted technologies} Claude.\\end{document}\n")
    bad = "\\documentclass{amsart}\\begin{document}\\begin{abstract}x\\end{abstract}\\maketitle\nText.\\end{document}\n"
    frag = "\\begin{tabular}{cc}a&b\\end{tabular}"
    over = good.replace("Text", "").replace("ERC.", "ERC. \\mbox{" + "W" * 120 + "}")
    cases = [("good passes", section_problems(good) == []),
             ("bad lacks three", len(section_problems(bad)) == 3),
             ("fragment is not a paper", not is_paper(frag)),
             ("good is a paper", is_paper(good)),
             ("commented heading does not count", len(section_problems(good.replace("\\section*{Acknowledgments and funding} ERC.", "%\\section*{Acknowledgments and funding} ERC.\n"))) == 1),
             ("finished grant refused", any("2401227" in p for p in section_problems(good.replace("ERC.", "NSF OISE-2401227.")))),
             ("imsart acks/funding environments pass", section_problems(
                 "\\begin{acks}[Acknowledgments]AI systems developed the arguments.\\end{acks}"
                 "\\begin{funding}ERC.\\end{funding}\\section*{Competing interests}None.") == []),
             ("acks without an AI system is not a declaration", len(section_problems(
                 "\\begin{acks}[Acknowledgments]We thank a referee.\\end{acks}"
                 "\\begin{funding}ERC.\\end{funding}\\section*{Competing interests}None.")) == 1)]
    d = tempfile.mkdtemp(prefix="paperguard_st_")
    try:
        io.open(os.path.join(d, "o.tex"), "w").write(over)
        io.open(os.path.join(d, "g.tex"), "w").write(good)
        r = dict((f, p) for f, p, _ in check_folder(d, ["o.tex", "g.tex"]))
        cases.append(("overfull line refused", any("Overfull" in p for p in r.get("o.tex", []))))
        cases.append(("clean compile passes", r.get("g.tex") == []))
        decl = good[good.index("\\section*{Ack"):good.index("\\end{document}")]
        os.makedirs(os.path.join(d, "sec"))
        io.open(os.path.join(d, "sec", "decl.tex"), "w").write(decl)
        io.open(os.path.join(d, "i.tex"), "w").write(good.replace(decl, "\\input{sec/decl}"))
        io.open(os.path.join(d, "j.tex"), "w").write(good.replace(decl, "%\\input{sec/decl}\n"))
        r = dict((f, p) for f, p, _ in check_folder(d, ["i.tex", "j.tex"]))
        cases.append(("declarations through \\input pass", r.get("i.tex") == []))
        cases.append(("a commented \\input does not count", len(r.get("j.tex", [])) == 3))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    for n, c in cases:
        print("  %s %s" % ("PASS" if c else "FAIL", n))
    return 0 if all(c for _, c in cases) else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if len(sys.argv) > 2 and sys.argv[1] == "check":
        sys.exit(0 if check_paths(sys.argv[2:]) else 1)
    print(__doc__)
