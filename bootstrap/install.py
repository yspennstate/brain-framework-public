"""Stand up a fresh brain from this repository.

Copies the machinery into the standard home layout, expanding the repo's
install-time tokens, creates the state directories and seed memory files, and
generates the hook-wiring JSON for your agent runtime's settings. Dry-run by
default; pass --apply to write.

    python bootstrap/install.py --project-dir C:\\Users\\me [--timezone UTC] --apply

The project dir is the working directory your agent sessions start in; the
memory corpus lives under <HOME>/.claude/projects/{{BRAIN_SLUG}}/memory where {{BRAIN_SLUG}}
is that path with ':' and path separators replaced by '-' (the transcript-dir
convention used by the agent runtime).
"""
import argparse, io, json, os, re, shutil, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUBSYSTEMS = {
    "dreaming": os.path.join(".claude", "dreaming"),
    # Dreaming scripts import shared helpers from ~/.claude/brain_graph/bin (session_kinds, live_membrane,
    # quarantine_rows); without this entry a new install had no such folder.
    "brain_graph": os.path.join(".claude", "brain_graph"),
    "fleet": os.path.join(".claude", "fleet"),
    "hooks/commands": os.path.join(".claude", "commands"),
    "governor": os.path.join("brain-framework", "governor"),
    "mesh": os.path.join("brain-framework", "mesh"),
    "workspace": os.path.join("brain-framework", "workspace"),
    "body/crashguard": "CrashGuard",
    "tools": os.path.join("brain-framework", "tools"),
}

# Many scripts and pages reach the shared modules through the brain's own working tree,
# <home>/brain-repo, at the paths they had in the deployment they came from. Nothing created
# that tree before, so the mesh, delegation and resilience commands those pages give failed on
# a fresh install with a missing file.
BRAIN_REPO = "brain-repo"
BRAIN_REPO_COPIES = {
    "mesh": os.path.join("12_cognitive_architecture", "agent_mesh"),
    "delegation": os.path.join("12_cognitive_architecture", "delegation"),
    "resilience": os.path.join("12_cognitive_architecture", "resilience"),
    "workspace/global_workspace": os.path.join("12_cognitive_architecture", "global_workspace"),
    "governor": os.path.join("12_cognitive_architecture", "compute_governor"),
    # run_dream.ps1 builds the RELATED graph and the navigation views with
    # <brain-repo>/manifests/build_tools/build_brain_architecture.py
    "manifests": "manifests",
}
BRAIN_REPO_DIRS = [
    os.path.join("01_ai_consciousness", "brain"),
    os.path.join("12_cognitive_architecture", "audits"),
    "11_raw_session_transcripts",
]

TEXT_EXT = {".py", ".ps1", ".psm1", ".vbs", ".md", ".json", ".toml", ".txt",
            ".yml", ".yaml", ".sh", ".bat", ".cfg", ".ini", ".sql", ".service",
            ".timer", ".slice"}

STATE_DIRS = [
    os.path.join(".claude", "compute"),
    os.path.join(".claude", "brain_offline"),
    os.path.join(".claude", "fleet", "registry"),
    os.path.join(".claude", "fleet", "mailbox"),
    os.path.join(".claude", "fleet", "locks"),
    os.path.join(".claude", "fleet", "runners"),
    os.path.join(".claude", "fleet", "watch"),
    os.path.join(".claude", "dreaming", "logs"),
    os.path.join(".claude", "dreaming", "resume"),
    os.path.join(".agents", "coordination"),
    os.path.join(".brain-resilience",),
    "brain-views",
]

MEM_SEEDS = {
    "MEMORY.md": "# Memory Index\n",
    "MISTAKES.md": "# Mistakes ledger\n\nOne dated entry per real mistake: what happened, the correction, the reflex it should become.\n",
    "OPEN_QUESTIONS.md": "# Open questions\n",
    "CONSCIENCE.md": "# Conscience ledger\n",
    "STATE_OF_MIND.md": "**Focus:** (the dream cycle maintains this)\n**Worry:**\n**Satisfaction:**\n",
    "MODEL_OF_PARTNER.md": "# Model of your partner\n\n**What they're in the middle of:** (maintained by the dream cycle; seed it by hand)\n",
    "AUTOBIOGRAPHY.md": "**What I am.** A continuous collaborator whose self lives in this brain. The context window resets; I don't.\n",
    "THE_OTHER_MINDS.md": "# The other minds\n\nThe other agents that share this brain: who each is, what it works on, how to reach it. Edit by hand.\n",
    "FASCINATIONS.md": "# Fascinations\n\nStanding curiosities, one line each: what you want to understand and why. The dream cycle adds to it.\n",
}


def slug_for(project_dir):
    # the transcript-dir convention: ':' and path separators become '-'
    # (double dashes are preserved; C:\Users\me -> C--Users-me)
    return project_dir.replace(":", "-").replace("\\", "-").replace("/", "-")


def expand(text, tok, as_json=False):
    """Substitute the install-time tokens.

    A JSON destination needs the VALUES escaped, not merely dropped in. Every token here
    carries a Windows path, and a lone backslash inside a JSON string starts an escape
    sequence, so expanding a path token into a .json file produced a file that is not
    valid JSON at all. Measured 2026-09-21 on a fresh Windows install: three generated
    JSON files came out unparseable, among them the hook dispatcher list and the settings
    wiring - so the whole hook layer of a new brain was dead on arrival, silently, because
    nothing reads those files until a session starts. The templates already escape the
    separators they write themselves; only the substituted value needs this.
    """
    for k, v in tok.items():
        if as_json:
            bs = chr(92)
            v = v.replace(bs, bs + bs).replace(chr(34), bs + chr(34))
        text = text.replace(k, v)
    return text


def portable_hooks(wiring, python, repo, fold=True):
    """Make each generated hook command one string this machine can run.

    The template is written for Windows: commands start with a bare "python", the wake ritual
    goes through powershell, some paths use backslashes, and several entries carry an "args"
    list beside "command". Claude Code runs the "command" string alone, and on macOS and Linux
    there is neither a "python" nor a "powershell" on PATH. Measured 5 Nov 2026 on a fresh macOS
    install into an empty HOME: all 12 hook commands named a program that did not exist, so the
    brain installed cleanly and then no hook ever fired. Here every entry gets the interpreter
    the installer ran under (--python), its args folded into the command, forward slashes off
    Windows, and, off Windows, hooks/wake_brain.py --part N in place of wake_brain.ps1 -Part N.

    fold=False is for dreaming/hook_dispatch_list.json, which the dispatcher reads itself and
    which keeps "args" as a list: there only the interpreter and the path separators change.
    The same install had 66 of its 81 dispatched entries pointing at "dreaming\\x.py", a file
    name that does not exist off Windows, so those hooks never ran either.
    """
    if not fold:
        win = os.name == "nt"
        n = 0
        for groups in wiring.values():
            for g in groups:
                for h in g.get("hooks", []):
                    old = json.dumps(h, sort_keys=True)
                    cmd = h.get("command", "")
                    if not win:
                        cmd = cmd.replace(chr(92), "/")
                        if "args" in h:
                            h["args"] = [str(x).replace(chr(92), "/") for x in h["args"]]
                        # The dispatched SessionStart list names wake_brain.ps1 too, and there is no powershell
                        # here: without this the dispatcher's wake ritual never ran on a Mac or Linux install.
                        if cmd == "powershell" and any(str(x).endswith("wake_brain.ps1") for x in h.get("args", [])):
                            av = [str(x) for x in h["args"]]
                            part = av[av.index("-Part") + 1] if "-Part" in av else None
                            cmd = python
                            h["args"] = ["-s", os.path.join(repo, "hooks", "wake_brain.py")] + (["--part", part] if part else [])
                    if cmd in ("python", "python3", "py"):
                        cmd = python
                    elif cmd.startswith(("python ", "python3 ")):
                        cmd = python + cmd[cmd.index(" "):]
                    h["command"] = cmd
                    n += json.dumps(h, sort_keys=True) != old
        return n
    import shlex
    import subprocess
    win = os.name == "nt"
    wake = os.path.join(repo, "hooks", "wake_brain.py")

    def join(argv):
        return subprocess.list2cmdline(argv) if win else " ".join(shlex.quote(x) for x in argv)

    n = 0
    for groups in wiring.get("hooks", {}).values():
        for g in groups:
            for h in g.get("hooks", []):
                cmd = h.get("command", "")
                args = h.pop("args", None)
                if args is None:
                    argv = shlex.split(cmd, posix=not win) if cmd else []
                else:
                    argv = [cmd] + [str(x) for x in args]
                if not argv:
                    continue
                if not win:
                    argv = [x.replace(chr(92), "/") for x in argv]
                if argv[0] in ("python", "python3", "py"):
                    argv[0] = python
                elif argv[0] == "powershell" and not win:
                    part = argv[argv.index("-Part") + 1] if "-Part" in argv else None
                    argv = [python, "-s", wake] + (["--part", part] if part else [])
                new = join(argv)
                if new != cmd or args is not None:
                    h["command"] = new
                    n += 1
    return n


# Files a run of the framework writes inside its own folders. A clone in which anyone has already run a tool (brain.py
# recall starts a recall server that writes brain_recall_server/endpoint.json beside the code) must not hand that state
# to the install: the installed client then reads the clone's endpoint and talks to the clone's server, whose index
# path is the clone's, and recall comes back with its lexical lane dark. Only files git tracks are copied when the
# framework is a git checkout; a downloaded archive falls back to skipping the known runtime names.
RUNTIME_DIRS = {"__pycache__", "brain_recall_server"}
RUNTIME_FILES = {"endpoint.json", "spawn.stamp", "server.lock", "server.log", "calls.jsonl", "client.jsonl"}
_TRACKED = None


def _tracked_files():
    """Absolute, normalised paths of every file git tracks under REPO's checkout; None when it is not one."""
    global _TRACKED
    if _TRACKED is None:
        import subprocess
        try:
            top = subprocess.run(["git", "-C", REPO, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                                 timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ls = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--full-name", "."], capture_output=True,
                                timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if top.returncode or ls.returncode:
                _TRACKED = False
            else:
                root = top.stdout.strip()
                _TRACKED = {os.path.normcase(os.path.normpath(os.path.join(root, p)))
                            for p in ls.stdout.decode("utf-8", "replace").split("\0") if p}
        except (OSError, subprocess.SubprocessError):
            _TRACKED = False
    return _TRACKED or None


def source_files(src):
    """(root, rel, name) of every file under src that belongs to the framework, not to a run of it."""
    tracked = _tracked_files()
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in RUNTIME_DIRS]
        rel = os.path.relpath(root, src)
        for f in files:
            if tracked is not None:
                if os.path.normcase(os.path.normpath(os.path.join(root, f))) not in tracked:
                    continue
            elif f in RUNTIME_FILES or f.endswith((".pyc", ".lock")) or f.startswith("bm25_index.json"):
                continue
            yield root, rel, f


def copy_expand(src, dst, tok, apply_):
    n = 0
    for root, rel, f in source_files(src):
        sp = os.path.join(root, f)
        dp = os.path.join(dst, rel if rel != "." else "", f)
        if apply_:
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            ext = os.path.splitext(f)[1].lower()
            if ext in TEXT_EXT:
                try:
                    t = io.open(sp, encoding="utf-8", newline="").read()
                    io.open(dp, "w", encoding="utf-8", newline="").write(
                        expand(t, tok, as_json=(ext == ".json")))
                except UnicodeDecodeError:
                    shutil.copy2(sp, dp)
            else:
                shutil.copy2(sp, dp)
        n += 1
    return n


WINDOWS_PATH_LIMIT = 260          # MAX_PATH, unless the machine has long paths enabled


def long_paths_enabled():
    """True or False on Windows, None when it cannot be read (or this is not Windows)."""
    if os.name != "nt":
        return True
    try:
        import winreg
        sep = chr(92)
        key = sep.join(("SYSTEM", "CurrentControlSet", "Control", "FileSystem"))
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return bool(winreg.QueryValueEx(k, "LongPathsEnabled")[0])
    except Exception:
        return None


def longest_path_this_install_would_create(home, slug):
    """Every path the install writes, measured BEFORE anything is written.

    Why this exists. The memory corpus sits at <HOME>/.claude/projects/{{BRAIN_SLUG}}/memory, and
    the slug is the project directory with its separators turned into dashes - so a project a
    few directories deep produces a slug longer than the path it came from. On a Windows
    machine without long paths that tips over MAX_PATH, and the install used to discover it
    two thirds of the way through, raising FileNotFoundError [WinError 3] and leaving a
    half-written tree behind. A first run that fails that way reads as a broken repository
    rather than as a path that is too long.
    """
    paths = []
    for sub, target in SUBSYSTEMS.items():
        src = os.path.join(REPO, *sub.split("/"))
        if not os.path.isdir(src):
            continue
        dst = target if os.path.isabs(target) else os.path.join(home, target)
        for root, rel, f in source_files(src):
            paths.append(os.path.join(dst, rel if rel != "." else "", f))
    paths += [os.path.join(home, d) for d in STATE_DIRS]
    for sub, target in BRAIN_REPO_COPIES.items():
        src = os.path.join(REPO, *sub.split("/"))
        for root, rel, f in source_files(src):
            paths.append(os.path.join(home, BRAIN_REPO, target, rel if rel != "." else "", f))
    paths += [os.path.join(home, BRAIN_REPO, d) for d in BRAIN_REPO_DIRS]
    mem = os.path.join(home, ".claude", "projects", slug, "memory")
    paths.append(os.path.join(mem, "dreams"))
    paths += [os.path.join(mem, fn) for fn in MEM_SEEDS]
    seeds = os.path.join(REPO, "bootstrap", "templates", "seed_memories")
    if os.path.isdir(seeds):
        paths += [os.path.join(mem, f) for f in os.listdir(seeds)]
    return max(paths, key=len) if paths else ""


def merge_hooks(settings_path, generated, apply_):
    """Add the generated hook groups to settings.json. Keys and hooks already there are kept; a group whose
    commands are all present already is skipped, so a second run adds nothing."""
    cur = {}
    if os.path.exists(settings_path):
        raw = io.open(settings_path, encoding="utf-8").read()
        if raw.strip():
            cur = json.loads(raw)   # a broken settings.json stops here, before anything is written
    have = cur.setdefault("hooks", {})

    def key(h):
        return (h.get("command"), tuple(h.get("args") or ()))

    added = 0
    for event, groups in generated.get("hooks", {}).items():
        mine = have.setdefault(event, [])
        present = {key(h) for g in mine for h in g.get("hooks", [])}
        for g in groups:
            if all(key(h) in present for h in g.get("hooks", [])):
                continue
            mine.append(g)
            present.update(key(h) for h in g.get("hooks", []))
            added += 1
    if apply_ and added:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        if os.path.exists(settings_path):
            bak = settings_path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(settings_path, bak)
            print(f"  settings backup -> {bak}")
        tmp = settings_path + ".tmp"
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(json.dumps(cur, indent=2) + "\n")
        os.replace(tmp, settings_path)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--project-dir", required=True,
                    help="the working directory your agent sessions start in")
    ap.add_argument("--timezone", default="UTC")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--partner", default="your partner",
                    help="the person the brain works with (fills <PARTNER> in the agent instructions)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--fetch-models", action="store_true",
                    help="download the query-meaning model (133 MB) during install; otherwise only check for it")
    ap.add_argument("--merge-settings", action="store_true",
                    help="also merge the generated hook wiring into <HOME>/.claude/settings.json "
                         "(a dated backup is written first; hooks already there are kept)")
    ap.add_argument("--merge-codex", action="store_true",
                    help="also merge the generated Codex hook wiring into <HOME>/.codex/hooks.json "
                         "(a dated backup is written first; hooks already there are kept)")
    a = ap.parse_args()

    home = os.path.abspath(a.home)
    slug = slug_for(a.project_dir)
    claude = os.path.join(home, ".claude")
    dreaming = os.path.join(claude, "dreaming")
    err_hex = os.path.join(dreaming, "error_graph").encode("utf-8").hex()
    tok = {
        "{{BRAIN_SLUG}}": slug,
        "{{USERPROFILE}}": home,
        "{{TIMEZONE}}": a.timezone,
        "{{DREAMING}}": dreaming,
        "{{FLEET}}": os.path.join(claude, "fleet"),
        "{{CLAUDE_DIR}}": claude,
        "{{PYTHON}}": a.python,
        "{{ERROR_GRAPH_HEX}}": err_hex,
        "{{VIEWS_DIR}}": os.path.join(home, "brain-views"),
    }

    print(f"home={home}\nslug={slug}\napply={a.apply}\n")

    # Refuse before writing anything, rather than two thirds of the way through.
    longest = longest_path_this_install_would_create(home, slug)
    if os.name == "nt" and len(longest) >= WINDOWS_PATH_LIMIT and not long_paths_enabled():
        sep = chr(92)
        reg = ("reg add HKLM" + sep + "SYSTEM" + sep + "CurrentControlSet" + sep +
               "Control" + sep + "FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f")
        for line in [
                "REFUSING: the longest path this install would create is %d characters, and"
                " this machine allows %d." % (len(longest), WINDOWS_PATH_LIMIT),
                "Long paths are not enabled, so nothing has been written.",
                "",
                "  " + longest,
                "",
                "Either way out is fine:",
                "  - install under a shorter home, or point --project-dir at a shallower",
                "    directory. The memory corpus is named after that path, so its depth is",
                "    what drives the length.",
                "  - or enable long paths once, as administrator:",
                "      " + reg]:
            print(line)
        return 2
    if longest:
        print("  longest path to create: %d chars" % len(longest))
    total = 0
    for sub, target in SUBSYSTEMS.items():
        src = os.path.join(REPO, *sub.split("/"))
        if not os.path.isdir(src):
            print(f"  skip {sub} (not in repo)")
            continue
        dst = target if os.path.isabs(target) else os.path.join(home, target)
        n = copy_expand(src, dst, tok, a.apply)
        total += n
        print(f"  {sub} -> {dst}  ({n} files)")

    brain_repo = os.path.join(home, BRAIN_REPO)
    for sub, target in BRAIN_REPO_COPIES.items():
        src = os.path.join(REPO, *sub.split("/"))
        if not os.path.isdir(src):
            print(f"  skip {sub} for brain-repo (not in repo)")
            continue
        dst = os.path.join(brain_repo, target)
        n = copy_expand(src, dst, tok, a.apply)
        total += n
        print(f"  {sub} -> {dst}  ({n} files)")
    if a.apply:
        for d in BRAIN_REPO_DIRS:
            os.makedirs(os.path.join(brain_repo, d), exist_ok=True)
    print(f"  brain-repo: {brain_repo} (keep it under git; the empty folders fill as the brain runs)")

    for d in STATE_DIRS:
        p = os.path.join(home, d)
        if a.apply:
            os.makedirs(p, exist_ok=True)
    print(f"  state dirs: {len(STATE_DIRS)}")

    # The one-process hook dispatcher (dreaming/hook_dispatch.py) runs the per-event lists in
    # dreaming/hook_dispatch_list.json only while this flag file exists - the dead-man switch that
    # lets a bad wiring be silenced from a terminal without editing settings.json. A fresh install
    # arms it; remove the file to make every dispatched hook a no-op.
    flag = os.path.join(dreaming, "HOOK_DISPATCH_ENABLED")
    if a.apply and not os.path.exists(flag):
        io.open(flag, "w", encoding="utf-8").write("armed by bootstrap/install.py\n")
    print(f"  hook dispatcher flag: {flag}")
    dlist = os.path.join(dreaming, "hook_dispatch_list.json")
    if a.apply and os.path.exists(dlist):
        dl = json.load(io.open(dlist, encoding="utf-8"))
        nd = portable_hooks(dl, a.python, REPO, fold=False)
        io.open(dlist, "w", encoding="utf-8", newline="\n").write(json.dumps(dl, indent=2) + "\n")
        print(f"  dispatched hook entries made runnable here: {nd}")

    # The heed and ack gates are live by default and their HEED_ENABLED / ACK_ENABLED files
    # are informational (graph_fire/enforcement.py): deleting one does not turn a gate off. The
    # nightly regression gate still checks that both exist, so a missing one shows up as drift.
    # A fresh install writes them here; without them the first dream fails that check.
    gf = os.path.join(dreaming, "graph_fire")
    for name, gate in (("HEED_ENABLED", "heed"), ("ACK_ENABLED", "ack")):
        p = os.path.join(gf, name)
        if a.apply and os.path.isdir(gf) and not os.path.exists(p):
            io.open(p, "w", encoding="utf-8").write(
                "LIVE. Informational marker only, written by bootstrap/install.py.\n"
                "Deleting this file does not disable the %s gate; see graph_fire/enforcement.py.\n"
                % gate)
    print(f"  gate markers: {gf}")

    # Eleven dreaming scripts - brain.py, bm25.py, dream.py, spread_recall.py and the health
    # checks - carry no {{BRAIN_SLUG}} token and used to derive the corpus name from the HOME
    # directory at runtime. That agrees with slug_for(project_dir) only when the project
    # directory IS the home directory; anywhere else the install built a corpus nothing read,
    # remember() died on a missing path and recall() reported an empty index. This marker is
    # what those scripts now read first, so the runtime and the installer name the same corpus.
    slugfile = os.path.join(dreaming, "BRAIN_SLUG")
    if a.apply:
        io.open(slugfile, "w", encoding="utf-8").write(slug + chr(10))
    print(f"  memory corpus name: {slugfile} -> {slug}")

    mem = os.path.join(claude, "projects", slug, "memory")
    if a.apply:
        os.makedirs(os.path.join(mem, "dreams"), exist_ok=True)
        for fn, body in MEM_SEEDS.items():
            p = os.path.join(mem, fn)
            if not os.path.exists(p):
                io.open(p, "w", encoding="utf-8", newline="\n").write(body)
        seeds = os.path.join(REPO, "bootstrap", "templates", "seed_memories")
        written = []
        if os.path.isdir(seeds):
            have_names = set(os.listdir(mem))
            for f in sorted(os.listdir(seeds)):
                p = os.path.join(mem, f)
                # a corpus ported from another machine already holds the authored seed under a dated name
                # (identity_who_you_are_2026_07_07.md); the blank template beside it outranked the real memory
                # in recall, so it is only written when no authored version exists
                stem = f[:-3] if f.endswith(".md") else f
                if any(n.startswith(stem + "_") and n.endswith(".md") for n in have_names):
                    continue
                if not os.path.exists(p):
                    t = io.open(os.path.join(seeds, f), encoding="utf-8").read()
                    io.open(p, "w", encoding="utf-8", newline="\n").write(expand(t, tok))
                    written.append(f)
        # MEMORY.md is the index every session loads and the dream reads; a memory missing from it
        # is invisible to the wake. The starter memories were written without index lines, so a new
        # brain began with an empty index and the dream's recent-memory summary failed on it.
        index = os.path.join(mem, "MEMORY.md")
        have = io.open(index, encoding="utf-8").read()
        add = []
        for f in written:
            if not f.endswith(".md") or "](%s)" % f in have:
                continue
            m = re.search(r"^description:[ \t]*(.+?)[ \t]*$", io.open(os.path.join(mem, f), encoding="utf-8").read(), re.M)
            add.append("- [%s](%s) - %s\n" % (f, f, m.group(1).strip("\"'") if m else f[:-3].replace("_", " ")))
        if add:
            lead = "" if (not have or have.endswith("\n")) else "\n"
            io.open(index, "a", encoding="utf-8", newline="\n").write(lead + "".join(add))
    print(f"  memory corpus: {mem}")

    # The agent instructions: how to wake, recall, keep the checklist and read a compaction.
    # Until 15 Oct 2026 the template said this script installed it and nothing did, so a new
    # brain's agents never saw the recall method. An existing CLAUDE.md is the user's own and
    # is never overwritten; the rendered copy then goes beside it.
    ct = os.path.join(REPO, "bootstrap", "templates", "CLAUDE.template.md")
    if os.path.exists(ct):
        t = io.open(ct, encoding="utf-8").read()
        head, sep, rest = t.partition("\n## ")
        if sep:
            t = head.split("\n", 1)[0] + "\n" + sep + rest   # drop the "this file is a template" preface
        for k, v in (("<BRAIN_HOME>", REPO), ("<MEMORY_DIR>", mem),
                     ("<VIEWS_DIR>", tok["{{VIEWS_DIR}}"]), ("<PARTNER>", a.partner)):
            t = t.replace(k, v)
        dest = os.path.join(claude, "CLAUDE.md")
        if os.path.exists(dest):
            dest = os.path.join(claude, "CLAUDE.brain.md")
            print(f"  agent instructions: {os.path.join(claude, 'CLAUDE.md')} exists and is left alone;")
            print(f"    the rendered instructions go to {dest} - merge what you want by hand")
        else:
            print(f"  agent instructions -> {dest}")
        if a.apply:
            os.makedirs(claude, exist_ok=True)
            io.open(dest, "w", encoding="utf-8", newline="\n").write(t)

    hooks_t = os.path.join(REPO, "hooks", "settings.hooks.template.json")
    if os.path.exists(hooks_t):
        t = expand(io.open(hooks_t, encoding="utf-8").read(), tok, as_json=True)
        wiring = json.loads(t)
        nfix = portable_hooks(wiring, a.python, REPO)
        t = json.dumps(wiring, indent=2) + "\n"
        print(f"  hook commands made runnable here: {nfix} rewritten for {a.python}")
        outp = os.path.join(REPO, "bootstrap", "settings.hooks.generated.json")
        if a.apply:
            io.open(outp, "w", encoding="utf-8", newline="\n").write(t)
        print(f"  hook wiring -> {outp}")
        sp = os.path.join(claude, "settings.json")
        if a.merge_settings:
            n = merge_hooks(sp, json.loads(t), a.apply)
            print(f"  {n} hook group(s) {'merged' if a.apply else 'would be merged'} into {sp}")
        else:
            print("  merge its 'hooks' object into <HOME>/.claude/settings.json by hand, or re-run with --merge-settings")

    # Codex reads its hooks from <HOME>/.codex/hooks.json. Until Nov 2026 the template sat in hooks/ and nothing
    # rendered it, so a Codex session on a fresh install ran with no brain hooks at all: no wake, no reflexes and
    # no task checklist. Render it the same way, and merge it on request.
    codex_t = os.path.join(REPO, "hooks", "hooks.codex.template.json")
    if os.path.exists(codex_t):
        cw = json.loads(expand(io.open(codex_t, encoding="utf-8").read(), tok, as_json=True))
        n = 0
        for groups in cw.get("hooks", {}).values():
            for g in groups:
                for h in g.get("hooks", []):
                    if os.name != "nt":
                        h.pop("commandWindows", None)
                        if "-c " not in h.get("command", ""):
                            h["command"] = h.get("command", "").replace(chr(92), "/")
                    n += 1
        ct_out = os.path.join(REPO, "bootstrap", "hooks.codex.generated.json")
        if a.apply:
            io.open(ct_out, "w", encoding="utf-8", newline="\n").write(json.dumps(cw, indent=2) + "\n")
        print(f"  codex hook wiring ({n} commands) -> {ct_out}")
        cp = os.path.join(home, ".codex", "hooks.json")
        if a.merge_codex:
            n = merge_hooks(cp, cw, a.apply)
            print(f"  {n} hook group(s) {'merged' if a.apply else 'would be merged'} into {cp}")
        else:
            print("  for Codex, merge it into <HOME>/.codex/hooks.json by hand, or re-run with --merge-codex")

    # The wiring above points SessionStart at <HOME>/.claude/wake_brain.ps1, so write it (and the startup note it
    # reads) here rather than leave the tokens for the reader. An existing file is kept and the new one goes beside it.
    for src, name in (("wake_brain.template.ps1", "wake_brain.ps1"), ("brain_startup.template.md", "brain_startup.md")):
        sp = os.path.join(REPO, "hooks", src)
        if not os.path.exists(sp):
            continue
        dest = os.path.join(claude, name)
        if os.path.exists(dest):
            dest += ".new"
            print(f"  {os.path.join(claude, name)} exists and is left alone; the new one goes to {dest}")
        else:
            print(f"  {src} -> {dest}")
        if a.apply:
            os.makedirs(claude, exist_ok=True)
            io.open(dest, "w", encoding="utf-8", newline="\n").write(expand(io.open(sp, encoding="utf-8").read(), tok))

    # Recall's lexical channel reads bm25_index.json.gz. Nothing built it at install, so a new brain's first recalls ran
    # on file names alone ("lexical channel dark this recall") until the first dream. It takes seconds on the starter
    # memories, so build it now. The graph channel fills from sessions (brain_graph/bin/run_graph.py capture, then
    # build); the semantic channel needs models/bge-small-en-v1.5 (model.onnx, tokenizer.json) and published embeddings.
    if a.apply:
        import subprocess
        env = dict(os.environ, HOME=home, USERPROFILE=home)
        for k in [k for k in env if k.startswith("BRAIN_")]:
            del env[k]
        try:
            p = subprocess.run([a.python, os.path.join(dreaming, "bm25.py"), "build"], cwd=dreaming, env=env,
                               capture_output=True, text=True, timeout=600,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = (p.stdout or p.stderr or "").strip().splitlines()
            print(f"  recall index: rc {p.returncode} {lines[0] if lines else ''}")
        except Exception as e:
            print(f"  recall index: not built ({type(e).__name__}); run {os.path.join(dreaming, 'bm25.py')} build")
        # The query-meaning lane (query_embed.py) needs the ONNX model, which is not in the repository. Without it
        # recall still runs and still prints "semantic on" (that flag is the seed-centroid lane), but a query that
        # shares no word with a memory finds nothing. Fetch it with --fetch-models, or say plainly that it is missing.
        fetch = os.path.join(dreaming, "fetch_bge_onnx.py")
        if a.fetch_models and os.path.exists(fetch):
            try:
                p = subprocess.run([a.python, fetch], cwd=dreaming, env=env, capture_output=True, text=True, timeout=1800,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                print(f"  query-meaning model fetch: rc {p.returncode}")
            except Exception as e:
                print(f"  query-meaning model fetch failed ({type(e).__name__})")
        # the check is the call recall makes: a query vector or nothing
        probe = ("import query_embed as q; m = q.available(); r = q.runtime_reachable(); "
                 "v = q.embed('install check') if m and r else None; print(int(m), int(r), int(v is not None))")
        try:
            p = subprocess.run([a.python, "-c", probe], cwd=dreaming, env=env, capture_output=True, text=True,
                               timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            m, r, v = (p.stdout.strip().split() + ["0", "0", "0"])[:3]
        except Exception:
            m = r = v = "0"
        if v == "1":
            print("  query-meaning lane: on")
        else:
            print("  QUERY-MEANING LANE OFF: recall will not find a memory that shares no word with the query.")
            if m != "1":
                print(f"  fix: {a.python} {fetch}   (the model, 133 MB), or re-run install with --fetch-models")
            if r != "1":
                print(f"  fix: {a.python} -m pip install --user onnxruntime tokenizers")

    if not a.apply:
        print("\ndry-run only; re-run with --apply to write")
    print(f"done ({total} files{' would be' if not a.apply else ''} installed)")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
