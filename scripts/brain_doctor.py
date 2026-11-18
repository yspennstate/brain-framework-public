#!/usr/bin/env python3
"""brain_doctor.py - is this brain installed, wired, firing and answering?

    python scripts/brain_doctor.py                 # check the install under your home directory
    python scripts/brain_doctor.py --home /tmp/h   # check an install made with install.py --home /tmp/h
    python scripts/brain_doctor.py --json          # one JSON object, for Codex or a script

It reads files and changes nothing. It never sends a payload to a hook, never runs recall through
brain.py (which refreshes the index) and never touches the network.

Four levels, each reported on its own, because each fails for a different reason:

  INSTALLED   the folders bootstrap/install.py copies, the corpus name file, the memory folder, the
              starter memories (present, filled in, listed in MEMORY.md) and every required file in
              manifests/required_files.json
  WIRED       ~/.claude/settings.json carries every hook event of hooks/settings.hooks.template.json,
              every script those hooks name exists, the dispatcher's on-switch exists and its hook list
              names only scripts that exist; the same for ~/.codex/hooks.json when Codex is installed
  DELIVERING  the dispatcher's own log shows a recent fire (a real session, not a test)
  WORKING     the recall index loads, holds the starter memories and ranks the right one first for a
              plain question; the query-meaning model is present, or the report says it is off

Exit 0 when every check passes, 1 when any check fails, 2 when nothing failed but something could not
be decided yet (no manifest, no fire seen). Every failure prints the command that fixes it.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL, HELD, WARN, SKIP = "pass", "FAIL", "held", "warn", "skip"
EVENTS_RE = re.compile(r"\b(UserPromptSubmit|PreToolUse|PostToolUse|SessionStart|Stop|PreCompact|PostCompact)"
                       r" hooks=(\d+)")
SCRIPT_RE = re.compile(r"[\w.\-]+\.(?:py|ps1|vbs|sh)\b")
PATH_RE = re.compile(r"""(?:"([^"]+\.(?:py|ps1|vbs|sh))"|((?:[A-Za-z]:)?[^\s"']*[\\/][^\s"']*\.(?:py|ps1|vbs|sh)))""")
TOKEN = re.compile(r"[a-z0-9_]{3,}")
RC_RE = re.compile(r"\brc=(-?\d+)\b")


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, cid, status, what, fix=""):
        self.rows.append({"level": level, "check": cid, "status": status, "what": what, "fix": fix})

    def exit_code(self):
        st = {r["status"] for r in self.rows}
        return 1 if FAIL in st else (2 if HELD in st else 0)


def read_text(p):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def read_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def repo_top():
    """The checkout's root: REPO itself, or the folder above it that holds .git (math-brain-public keeps the
    framework under framework/, so there REPO is <checkout>/framework)."""
    d = REPO
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return REPO
        d = up


TOP = repo_top()


def repo_identity():
    """The repository's name from its origin url (a clone may sit in a folder of any name)."""
    cfg = os.path.join(TOP, ".git", "config")
    if os.path.isfile(cfg):
        m = re.search(r"url\s*=\s*\S*?([\w.\-]+?)(?:\.git)?\s*$", read_text(cfg), re.M)
        if m:
            return m.group(1)
    return os.path.basename(os.path.normpath(TOP))


def load_installer():
    """Import bootstrap/install.py for its constants and helpers (its main() is guarded). Bytecode writing is off
    for the import, so the doctor leaves no __pycache__ in the checkout."""
    spec = importlib.util.spec_from_file_location("brain_install", os.path.join(REPO, "bootstrap", "install.py"))
    mod = importlib.util.module_from_spec(spec)
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


def hook_commands(cfg):
    """{event: [command, ...]} from a Claude settings.json or a Codex hooks.json. Raises ValueError on an entry of
    the wrong shape, so the caller reports a FAIL row instead of the doctor crashing."""
    if not isinstance(cfg, dict):
        raise ValueError("the top level is a %s, not an object" % type(cfg).__name__)
    if "hooks" in cfg and not isinstance(cfg["hooks"], dict):
        raise ValueError('"hooks" is a %s, not an object' % type(cfg["hooks"]).__name__)
    nested = "hooks" in cfg
    events = cfg["hooks"] if nested else cfg   # the dispatch list keeps its events at the top level
    out = {}
    for ev, groups in events.items():
        # at the top level only an event name (UserPromptSubmit, Stop, ...) is an event; a settings.json with no
        # "hooks" yet has other keys there (permissions, enabledPlugins), which mean the hooks are not merged
        if not isinstance(groups, list) or (not nested and not ev[:1].isupper()):
            continue
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks") or [], list):
                raise ValueError("a hook group under %s is malformed: %.80r" % (ev, g))
            for h in (g.get("hooks") or []):
                if not isinstance(h, dict) or not isinstance(h.get("args") or [], list):
                    raise ValueError("a hook entry under %s is malformed: %.80r" % (ev, h))
                # the dispatch list keeps the script in an args array ({"command": "python3", "args": [...]})
                args = "".join(' "%s"' % x for x in (h.get("args") or []))
                for key in ("command", "commandWindows"):
                    if h.get(key):
                        if not isinstance(h[key], str):
                            raise ValueError("a hook %s under %s is not a string: %.80r" % (key, ev, h[key]))
                        out.setdefault(ev, []).append(h[key] + args)
    return out


def scripts_in(cmd):
    """Script names without extension: on macOS and Linux the installer wires wake_brain.py where the
    Windows template says wake_brain.ps1, and both are the same hook."""
    return {os.path.splitext(s)[0] for s in SCRIPT_RE.findall(cmd)}


def paths_in(cmd, home):
    found = []
    for a, b in PATH_RE.findall(cmd):
        p = (a or b).strip()
        if "{{" in p or "$" in p or "%" in p:
            continue
        found.append(os.path.expanduser(p.replace("~", home, 1) if p.startswith("~") else p))
    return found


# ---------------------------------------------------------------- INSTALLED
def check_installed(rep, home, inst, manifest_path):
    L = "INSTALLED"
    missing = []
    for sub, target in inst.SUBSYSTEMS.items():
        if not os.path.isdir(os.path.join(REPO, *sub.split("/"))):
            continue
        dst = target if os.path.isabs(target) else os.path.join(home, target)
        if not os.path.isdir(dst):
            missing.append(dst)
    install_cmd = "python bootstrap/install.py --home %s --project-dir <your project dir> --apply" % home
    rep.add(L, "subsystems", FAIL if missing else PASS,
            ("missing: " + ", ".join(missing)) if missing else "%d folders in place" % len(inst.SUBSYSTEMS),
            install_cmd if missing else "")

    dreaming = os.path.join(home, ".claude", "dreaming")
    slugfile = os.path.join(dreaming, "BRAIN_SLUG")
    slug = read_text(slugfile).strip() if os.path.exists(slugfile) else ""
    rep.add(L, "corpus-name", PASS if slug else FAIL,
            ("BRAIN_SLUG = " + slug) if slug else "no corpus name at " + slugfile,
            "" if slug else install_cmd)
    mem = os.path.join(home, ".claude", "projects", slug, "memory") if slug else ""
    index = os.path.join(mem, "MEMORY.md") if mem else ""
    ok = bool(mem) and os.path.exists(index)
    rep.add(L, "memory-index", PASS if ok else FAIL,
            ("memory folder " + mem) if ok else "no MEMORY.md in " + (mem or "(no corpus name)"),
            "" if ok else install_cmd)

    seeds_dir = os.path.join(REPO, "bootstrap", "templates", "seed_memories")
    seeds = sorted(f for f in os.listdir(seeds_dir) if f.endswith(".md")) if os.path.isdir(seeds_dir) else []
    bad = []
    if ok:
        names = os.listdir(mem)
        idx = read_text(index)
        for f in seeds:
            stem = f[:-3]
            have = [n for n in names if n == f or (n.startswith(stem + "_") and n.endswith(".md"))]
            if not have:
                bad.append(f + " missing")
                continue
            body = read_text(os.path.join(mem, have[0]))
            text = re.sub(r"^---.*?---", "", body, count=1, flags=re.S).strip()
            if "{{" in body or len(text) < 40:
                bad.append(have[0] + " is blank or unfilled")
            elif "](%s)" % have[0] not in idx:
                bad.append(have[0] + " not listed in MEMORY.md")
    rep.add(L, "starter-memories", FAIL if (bad or not ok) else PASS,
            "; ".join(bad) if bad else ("%d starter memories filled in and indexed" % len(seeds) if ok
                                        else "memory folder missing"),
            ("re-run the installer (it rewrites only missing starter memories): " + install_cmd) if bad else "")

    if not os.path.exists(manifest_path):
        rep.add(L, "required-files", HELD, "no manifest at " + manifest_path,
                "pull the repo again; manifests/required_files.json ships with it")
        return slug, mem
    try:
        man = read_json(manifest_path)
    except ValueError as e:
        rep.add(L, "required-files", FAIL, "manifest does not parse: %s" % e, "pull the repo again")
        return slug, mem
    entries = man.get("files", man) if isinstance(man, dict) else man
    repo_name = repo_identity()
    absent = []
    n = 0
    for e in entries:
        if str(e.get("need", e.get("required", "required"))).lower() not in ("required", "true"):
            continue
        r = e.get("repo")
        # a zip download sits in <repo>-main or <repo>-<sha>, so match the start of the name
        if r and not repo_name.startswith(r.split("/")[-1]):
            continue
        n += 1
        if not os.path.exists(os.path.join(TOP, *str(e["path"]).split("/"))):
            absent.append(e["path"])
    if not n:
        rep.add(L, "required-files", HELD, "the manifest has no required rows for '%s'" % repo_name,
                "run the doctor from a checkout of brain-framework-public or math-brain-public")
        return slug, mem
    rep.add(L, "required-files", FAIL if absent else PASS,
            ("missing from this checkout: " + ", ".join(absent)) if absent else "%d required files present" % n,
            "git pull, or git checkout -- <path>" if absent else "")
    return slug, mem


# ---------------------------------------------------------------- WIRED
def check_wiring(rep, level_id, cfg_path, template_path, home, fix):
    if not os.path.exists(cfg_path):
        rep.add("WIRED", level_id, FAIL, "no " + cfg_path, fix)
        return
    try:
        cfg = read_json(cfg_path)
    except ValueError as e:
        rep.add("WIRED", level_id, FAIL, "%s does not parse: %s" % (cfg_path, e), "fix the JSON by hand, then " + fix)
        return
    try:
        have = hook_commands(cfg)
    except ValueError as e:
        rep.add("WIRED", level_id, FAIL, "%s: %s" % (cfg_path, e), "fix the entry by hand, then " + fix)
        return
    want = hook_commands(read_json(template_path)) if os.path.exists(template_path) else {}
    gaps = []
    for ev, cmds in want.items():
        need = set().union(*[scripts_in(c) for c in cmds]) if cmds else set()
        got = set().union(*[scripts_in(c) for c in have.get(ev, [])]) if have.get(ev) else set()
        if not have.get(ev):
            gaps.append("%s not wired" % ev)
        elif need - got:
            gaps.append("%s lacks %s" % (ev, ", ".join(sorted(need - got))))
    rep.add("WIRED", level_id, FAIL if gaps else PASS,
            "; ".join(gaps) if gaps else "%d hook events wired as the template says" % len(want), fix if gaps else "")
    if not have:
        rep.add("WIRED", level_id + "-scripts", SKIP, "nothing wired, so no scripts to check")
        return
    named = {p for cmds in have.values() for c in cmds for p in paths_in(c, home)}
    lost = sorted(p for p in named if not os.path.exists(p))
    rep.add("WIRED", level_id + "-scripts", FAIL if (lost or not named) else PASS,
            ("hooks name scripts that do not exist: " + ", ".join(lost)) if lost
            else ("the hooks name no script path" if not named else "the %d scripts the hooks name exist" % len(named)),
            fix if (lost or not named) else "")


def check_wired(rep, home, inst, want_codex):
    dreaming = os.path.join(home, ".claude", "dreaming")
    fix = "python bootstrap/install.py --home %s --project-dir <your project dir> --apply --merge-settings" % home
    check_wiring(rep, "claude-settings", os.path.join(home, ".claude", "settings.json"),
                 os.path.join(REPO, "hooks", "settings.hooks.template.json"), home, fix)
    flag = os.path.join(dreaming, "HOOK_DISPATCH_ENABLED")
    rep.add("WIRED", "dispatcher-switch", PASS if os.path.exists(flag) else FAIL,
            "dispatcher armed" if os.path.exists(flag) else "no %s: every dispatched hook is silent" % flag,
            "" if os.path.exists(flag) else "create the file: echo armed > " + flag)
    dlist = os.path.join(dreaming, "hook_dispatch_list.json")
    if os.path.exists(dlist):
        try:
            dl = read_json(dlist)
            named = {p for cmds in hook_commands(dl).values() for c in cmds for p in paths_in(c, home)}
            lost = sorted(p for p in named if not os.path.exists(p))
            rep.add("WIRED", "dispatch-list", FAIL if (lost or not named) else PASS,
                    ("dispatch list names missing scripts: " + ", ".join(lost[:8])) if lost
                    else ("dispatch list names no script at all" if not named
                          else "dispatch list parses; its %d scripts exist" % len(named)),
                    fix if (lost or not named) else "")
        except ValueError as e:
            rep.add("WIRED", "dispatch-list", FAIL, "hook_dispatch_list.json does not parse: %s" % e, fix)
    else:
        rep.add("WIRED", "dispatch-list", FAIL, "no " + dlist, fix)
    codex = os.path.join(home, ".codex")
    if want_codex or os.path.isdir(codex):
        check_wiring(rep, "codex-hooks", os.path.join(codex, "hooks.json"),
                     os.path.join(REPO, "hooks", "hooks.codex.template.json"), home,
                     fix.replace("--merge-settings", "--merge-codex"))
        rep.add("WIRED", "codex-trust", WARN,
                "Codex runs a hook only after you trust it, and that state is not on disk: open Codex, type /hooks "
                "and trust the brain entries", "")
        if os.name == "nt" and (" " in home or " " in sys.executable):
            rep.add("WIRED", "codex-windows-paths", WARN,
                    "a space in the home or python path: Codex on Windows runs the unquoted commandWindows "
                    "entries, so those hooks will not start", "install under a path with no spaces")
    else:
        rep.add("WIRED", "codex-hooks", SKIP, "no ~/.codex here; Codex not checked (pass --codex to require it)")


# ---------------------------------------------------------------- DELIVERING
def check_delivering(rep, home, max_age_h):
    log = os.path.join(home, ".claude", "compute", "hook_dispatch.log")
    if not os.path.exists(log):
        rep.add("DELIVERING", "hook-fired", HELD, "no fire seen yet (%s absent)" % log,
                "open Claude Code in your project, send one prompt, then run this again")
        return
    with io.open(log, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 200000))
        tail = f.read().decode("utf-8", "replace").splitlines()
    fires = [ln for ln in tail if EVENTS_RE.search(ln)]
    if not fires:
        rep.add("DELIVERING", "hook-fired", HELD, "the dispatcher log has no fire line yet",
                "open Claude Code in your project, send one prompt, then run this again")
        return
    last = fires[-1]
    try:
        t = time.mktime(time.strptime(last[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        t = os.path.getmtime(log)
    age_h = (time.time() - t) / 3600.0
    m = EVENTS_RE.search(last)
    events = sorted({EVENTS_RE.search(ln).group(1) for ln in fires})
    what = "last fire %.1f h ago (%s, %s hooks); events seen: %s" % (age_h, m.group(1), m.group(2), ", ".join(events))
    if age_h > max_age_h:
        rep.add("DELIVERING", "hook-fired", HELD, what + " - older than %g h" % max_age_h,
                "send a prompt in a session, then run this again")
    else:
        rep.add("DELIVERING", "hook-fired", PASS, what)
    # the dispatcher writes "<event> <hook> rc=<n> <secs>s <stderr>" for a hook that did not exit cleanly
    errs = [ln for ln in tail[-400:] if any(int(x) != 0 for x in RC_RE.findall(ln))]
    if errs:
        rep.add("DELIVERING", "hook-errors", WARN, "%d recent hook errors; last: %s" % (len(errs), errs[-1][:200]),
                "read " + log)


# ---------------------------------------------------------------- WORKING
def bm25_rank(z, query, k1=1.2, b=0.75):
    n, avgdl, df, dl, tf = z["n"], z["avgdl"] or 1.0, z["df"], z["dl"], z["tf"]
    terms = TOKEN.findall(query.lower())
    scores = {}
    for doc, tfs in tf.items():
        s = 0.0
        for t in terms:
            c = tfs.get(t)
            if not c:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * c * (k1 + 1) / (c + k1 * (1 - b + b * dl.get(doc, avgdl) / avgdl))
        if s:
            scores[doc] = s
    return sorted(scores, key=scores.get, reverse=True)


def check_working(rep, home, mem):
    dreaming = os.path.join(home, ".claude", "dreaming")
    idx = os.path.join(dreaming, "bm25_index.json.gz")
    fix = "python %s build" % os.path.join(dreaming, "bm25.py")
    if not os.path.exists(idx):
        rep.add("WORKING", "recall-index", FAIL, "no recall index at " + idx, fix)
    else:
        try:
            with gzip.open(idx, "rt", encoding="utf-8") as f:
                z = json.load(f)
            docs = list(z["tf"])
            seeds = [d for d in docs if d.startswith("identity_who_you_are")]
            if not seeds:
                rep.add("WORKING", "recall-index", FAIL,
                        "index loads (%d memories) but holds no starter memory" % len(docs), fix)
            else:
                top = bm25_rank(z, "who you are identity")[:3]
                hit = any(d.startswith("identity_who_you_are") for d in top)
                rep.add("WORKING", "recall-index", PASS if hit else FAIL,
                        "index holds %d memories; 'who you are' ranks %s" % (len(docs), ", ".join(top) or "nothing"),
                        "" if hit else fix)
        except Exception as e:
            rep.add("WORKING", "recall-index", FAIL, "recall index unreadable: %s: %s" % (type(e).__name__, e), fix)
    model = os.path.join(dreaming, "models", "bge-small-en-v1.5")
    have = all(os.path.exists(os.path.join(model, f)) for f in ("model.onnx", "tokenizer.json"))
    rt = bool(importlib.util.find_spec("onnxruntime") and importlib.util.find_spec("tokenizers"))
    if have and rt:
        rep.add("WORKING", "query-meaning", PASS, "query-meaning model and runtime present")
    else:
        why = [] if have else ["model absent"]
        why += [] if rt else ["onnxruntime/tokenizers not importable"]
        rep.add("WORKING", "query-meaning", WARN,
                "query-meaning lane OFF (%s): recall finds only memories that share a word with the question"
                % ", ".join(why),
                "python %s   and   python -m pip install --user onnxruntime tokenizers"
                % os.path.join(dreaming, "fetch_bge_onnx.py"))

    # reflexes: the priming hook reads the compiled table and is silent, rc 0, when it is missing
    tsv = os.path.join(dreaming, "instincts.tsv")
    compile_fix = "python %s" % os.path.join(dreaming, "instincts.py")
    if not os.path.exists(tsv):
        rep.add("WORKING", "reflex-table", FAIL, "no compiled reflex table at %s: the reflex hook fires nothing" % tsv,
                compile_fix)
    else:
        rep.add("WORKING", "reflex-table", PASS, "compiled reflex table present")
        stale = reflex_receipt_stale(dreaming)
        if stale:
            rep.add("WORKING", "reflex-compile", WARN, stale, compile_fix)

    # [[links]] reach recall only through spread_edges.tsv, which only a build writes
    if not os.path.exists(os.path.join(dreaming, "spread_edges.tsv")):
        rep.add("WORKING", "link-lane", WARN,
                "no spread_edges.tsv: [[links]] between memories do not reach recall yet",
                "python %s" % os.path.join(dreaming, "build_spread_edges.py"))


def reflex_receipt_stale(dreaming):
    """A message when instincts_compile_receipt.json names a different instincts_learned.json than the one on
    disk; None when they agree or the receipt says nothing checkable ("absent" and still absent agrees)."""
    receipt = os.path.join(dreaming, "instincts_compile_receipt.json")
    learned = os.path.join(dreaming, "instincts_learned.json")
    try:
        rec = read_json(receipt)
    except (OSError, ValueError):
        return None
    recorded = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "instincts_learned.json" and isinstance(v, str):
                    recorded.append(v.lower())
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(rec)
    if not recorded:
        return None
    if not os.path.exists(learned):
        now = "absent"
    else:
        with open(learned, "rb") as f:
            raw = f.read()
        now = [hashlib.sha256(raw).hexdigest(), hashlib.sha1(raw).hexdigest()]
    for v in recorded:
        if v == "absent":
            ok = now == "absent"
        elif now == "absent" or len(v) < 8 or not re.fullmatch(r"[0-9a-f]+", v):
            ok = now == "absent" and not re.fullmatch(r"[0-9a-f]{8,}", v)
        else:
            ok = any(h.startswith(v) for h in now)
        if not ok:
            return "instincts_learned.json changed since the reflex table was compiled"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--manifest", default=os.path.join(TOP, "manifests", "required_files.json"))
    ap.add_argument("--codex", action="store_true", help="require the Codex wiring too")
    ap.add_argument("--max-age-hours", type=float, default=24.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    home = os.path.abspath(os.path.expanduser(a.home))
    rep = Report()
    inst = load_installer()
    slug, mem = check_installed(rep, home, inst, a.manifest)
    check_wired(rep, home, inst, a.codex)
    check_delivering(rep, home, a.max_age_hours)
    check_working(rep, home, mem)
    code = rep.exit_code()
    verdict = {0: "READY", 1: "BROKEN", 2: "NOT YET DECIDED"}[code]
    if a.json:
        print(json.dumps({"verdict": verdict, "exit": code, "home": home, "repo": TOP, "checks": rep.rows}, indent=1))
    else:
        print("brain doctor: %s  (home %s)" % (verdict, home))
        for r in rep.rows:
            print("  %-10s %-20s %-5s %s" % (r["level"], r["check"], r["status"], r["what"]))
            if r["fix"] and r["status"] in (FAIL, HELD, WARN):
                print("  %-10s %-20s       fix: %s" % ("", "", r["fix"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
