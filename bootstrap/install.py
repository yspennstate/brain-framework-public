"""Stand up a fresh brain from this repository.

Copies the machinery into the standard home layout, expanding the repo's
install-time tokens, creates the state directories and seed memory files, and
generates the hook-wiring JSON for your agent runtime's settings. Dry-run by
default; pass --apply to write.

    python bootstrap/install.py --project-dir C:\\Users\\me [--timezone UTC] --apply

The project dir is the working directory your agent sessions start in; the
memory corpus lives under <home>/.claude/projects/<slug>/memory where <slug>
is that path with ':' and path separators replaced by '-' (the transcript-dir
convention used by the agent runtime).
"""
import argparse, io, json, os, shutil, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUBSYSTEMS = {
    "dreaming": os.path.join(".claude", "dreaming"),
    "fleet": os.path.join(".claude", "fleet"),
    "hooks/commands": os.path.join(".claude", "commands"),
    "governor": os.path.join("brain-framework", "governor"),
    "mesh": os.path.join("brain-framework", "mesh"),
    "workspace": os.path.join("brain-framework", "workspace"),
    "body/crashguard": "CrashGuard",
    "tools": os.path.join("brain-framework", "tools"),
}

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
    "MODEL_OF_PARTNER.md": "# Model of your partner\n\n**What he's in the middle of:** (maintained by the dream cycle; seed it by hand)\n",
    "AUTOBIOGRAPHY.md": "**What I am.** A continuous collaborator whose self lives in this brain. The context window resets; I don't.\n",
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
                            a = [str(x) for x in h["args"]]
                            part = a[a.index("-Part") + 1] if "-Part" in a else None
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


def copy_expand(src, dst, tok, apply_):
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(root, src)
        for f in files:
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

    Why this exists. The memory corpus sits at <home>/.claude/projects/<slug>/memory, and
    <slug> is the project directory with its separators turned into dashes - so a project a
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
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            rel = os.path.relpath(root, src)
            for f in files:
                paths.append(os.path.join(dst, rel if rel != "." else "", f))
    paths += [os.path.join(home, d) for d in STATE_DIRS]
    mem = os.path.join(home, ".claude", "projects", slug, "memory")
    paths.append(os.path.join(mem, "dreams"))
    paths += [os.path.join(mem, fn) for fn in MEM_SEEDS]
    seeds = os.path.join(REPO, "bootstrap", "templates", "seed_memories")
    if os.path.isdir(seeds):
        paths += [os.path.join(mem, f) for f in os.listdir(seeds)]
    return max(paths, key=len) if paths else ""


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
        if os.path.isdir(seeds):
            have = set(os.listdir(mem))
            for f in os.listdir(seeds):
                p = os.path.join(mem, f)
                # a corpus ported from another machine already holds the authored seed under a dated name
                # (identity_who_you_are_2026_07_07.md); the blank template beside it outranked the real memory
                # in recall, so it is only written when no authored version exists
                stem = f[:-3] if f.endswith(".md") else f
                if any(n.startswith(stem + "_") and n.endswith(".md") for n in have):
                    continue
                if not os.path.exists(p):
                    t = io.open(os.path.join(seeds, f), encoding="utf-8").read()
                    io.open(p, "w", encoding="utf-8", newline="\n").write(expand(t, tok))
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
        print("  merge its 'hooks' object into <home>/.claude/settings.json by hand,")
        print("  then install wake_brain: copy hooks/wake_brain.template.ps1 (expanded) as the")
        print("  SessionStart hook target and hooks/brain_startup.template.md to .claude/brain_startup.md")

    if not a.apply:
        print("\ndry-run only; re-run with --apply to write")
    print(f"done ({total} files{' would be' if not a.apply else ''} installed)")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
