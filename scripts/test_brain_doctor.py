#!/usr/bin/env python3
"""Controls for brain_doctor.py, both ways.

    python scripts/test_brain_doctor.py            # installs into a temporary home, then breaks it one piece at a time

It runs bootstrap/install.py --apply --merge-settings --merge-codex into a fresh temporary home (a few
minutes; nothing outside the temporary folder is written), checks that the doctor calls the intact install
READY and writes nothing, then breaks one thing at a time and checks that exactly the check named for that
break fails, and that the install comes back READY once the break is undone.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def checkout_top():
    """The folder holding .git: REPO itself, or its parent in math-brain-public, which keeps the framework under
    framework/. Manifest paths are relative to it, as in the doctor."""
    d = REPO
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            return REPO
        d = os.path.dirname(d)
    return d


PREFIX = os.path.relpath(REPO, checkout_top()).replace(os.sep, "/")
PREFIX = "" if PREFIX == "." else PREFIX + "/"
DOCTOR = os.path.join(HERE, "brain_doctor.py")
FAILS = []


def doctor(home, *extra):
    p = subprocess.run([sys.executable, DOCTOR, "--home", home, "--json"] + list(extra),
                       capture_output=True, text=True, timeout=300)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {"checks": [], "raw": p.stdout[-2000:] + p.stderr[-2000:]}
    return p.returncode, out


def bad(out, status="FAIL"):
    return sorted(r["check"] for r in out.get("checks", []) if r["status"] == status)


def expect(name, home, want_code, want_checks, *extra, status="FAIL"):
    code, out = doctor(home, *extra)
    got = bad(out, status)
    ok = code == want_code and got == sorted(want_checks)
    print("%-4s %-34s exit %s, %s: %s" % ("ok" if ok else "BAD", name, code, status, ", ".join(got) or "-"))
    if not ok:
        FAILS.append(name)
        for r in out.get("checks", []):
            if r["status"] not in ("pass", "skip"):
                print("        %s %s %s" % (r["check"], r["status"], r["what"][:300]))
        if out.get("raw"):
            print(out["raw"])


def snapshot(root):
    snap = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            st = os.stat(p)
            snap[p] = (st.st_size, st.st_mtime_ns)
    return snap


class Broken:
    """Move a file aside (or replace its text) for the length of one check."""
    def __init__(self, path, text=None):
        self.path, self.text, self.aside = path, text, path + ".doctor_test_aside"

    def __enter__(self):
        os.replace(self.path, self.aside)
        if self.text is not None:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(self.text)

    def __exit__(self, *exc):
        if os.path.exists(self.path):
            os.remove(self.path)
        os.replace(self.aside, self.path)


def main():
    tmp = tempfile.mkdtemp(prefix="brain_doctor_test_")
    home = os.path.join(tmp, "home")
    proj = os.path.join(home, "project")
    os.makedirs(proj)
    os.makedirs(os.path.join(home, ".codex"))
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(REPO, "bootstrap", "install.py"), "--home", home,
                        "--project-dir", proj, "--python", sys.executable, "--apply", "--merge-settings",
                        "--merge-codex"], capture_output=True, text=True, timeout=1800)
    print("install rc %d in %.0f s" % (p.returncode, time.time() - t0))
    if p.returncode:
        print(p.stdout[-3000:], p.stderr[-3000:])
        return 1
    claude = os.path.join(home, ".claude")
    dreaming = os.path.join(claude, "dreaming")
    tsv = os.path.join(dreaming, "instincts.tsv")
    # the installer compiles the reflex table and says so in one line
    said = [ln for ln in p.stdout.splitlines() if "reflex table: rc" in ln]
    if said and "reflex table: rc 0" in said[-1] and os.path.exists(tsv):
        print("ok   installer compiled the reflex table: " + said[-1].strip()[:120])
    elif not said and not os.path.exists(tsv):
        # this installer leaves the first compile to the user (the doctor's reflex-table fix line);
        # run that step once, as a new user would, and require that it works
        env = dict(os.environ, HOME=home, USERPROFILE=home)
        q = subprocess.run([sys.executable, os.path.join(dreaming, "instincts.py")], capture_output=True,
                           text=True, timeout=600, env=env)
        if q.returncode == 0 and os.path.exists(tsv):
            print("ok   installer leaves the reflex table to its first run; instincts.py compiled it")
        else:
            FAILS.append("first reflex compile")
            print("BAD  instincts.py rc %d; table %s\n%s" % (q.returncode, "present" if os.path.exists(tsv)
                                                           else "ABSENT", (q.stdout + q.stderr)[-1500:]))
            return finish(tmp)
    else:
        FAILS.append("installer reflex table")
        print("BAD  installer did not compile the reflex table: %s; table %s"
              % (said[-1].strip()[:200] if said else "no 'reflex table:' line",
                 "present" if os.path.exists(tsv) else "ABSENT"))
        if not os.path.exists(tsv):
            return finish(tmp)
    bootstrap = os.path.join(REPO, "bootstrap")
    checkout_before = snapshot(bootstrap)
    log = os.path.join(claude, "compute", "hook_dispatch.log")
    manifest = os.path.join(tmp, "required_files.json")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"files": [{"path": PREFIX + "bootstrap/install.py", "need": "required"},
                             {"path": PREFIX + "scripts/brain_doctor.py", "need": "required"},
                             {"path": PREFIX + "no/such/optional.md", "need": "optional"}]}, f)
    M = ("--manifest", manifest)

    # nothing fired yet: undecided, not broken
    expect("fresh install, no fire yet", home, 2, ["hook-fired"], *M, status="held")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  UserPromptSubmit hooks=9 0.80s exit=0 out=120\n")

    before = snapshot(home)
    expect("intact install", home, 0, [], *M)
    if snapshot(home) != before:
        FAILS.append("doctor wrote into the home")
        print("BAD  the doctor changed files under the home")
    else:
        print("ok   the doctor wrote nothing")

    mem = None
    slug = open(os.path.join(dreaming, "BRAIN_SLUG"), encoding="utf-8").read().strip()
    mem = os.path.join(claude, "projects", slug, "memory")
    ident = [f for f in os.listdir(mem) if f.startswith("identity_who_you_are")][0]
    values = [f for f in os.listdir(mem) if f.startswith("values_what_we_stand_for")][0]

    with Broken(os.path.join(mem, ident)):
        expect("starter memory removed", home, 1, ["starter-memories"], *M)
    with Broken(os.path.join(mem, values), text=""):
        expect("starter memory blanked", home, 1, ["starter-memories"], *M)

    settings = os.path.join(claude, "settings.json")
    cfg = json.load(open(settings, encoding="utf-8"))
    cut = dict(cfg, hooks={k: v for k, v in cfg["hooks"].items() if k != "UserPromptSubmit"})
    with Broken(settings, text=json.dumps(cut)):
        expect("one hook event unwired", home, 1, ["claude-settings"], *M)
    odd = dict(cfg, hooks=dict(cfg["hooks"], UserPromptSubmit=[{"hooks": ["not an entry"]}]))
    with Broken(settings, text=json.dumps(odd)):
        expect("malformed hook entry", home, 1, ["claude-settings"], *M)
    with Broken(settings, text=json.dumps({"enabledMcpjsonServers": ["a"], "permissions": {"allow": []}})):
        code, out = doctor(home, *M)
        row = [r for r in out.get("checks", []) if r["check"] == "claude-settings"]
        ok = code == 1 and row and row[0]["status"] == "FAIL" and "malformed" not in row[0]["what"]
        print("%-4s %-34s exit %s: %s" % ("ok" if ok else "BAD", "settings never merged", code,
                                          row[0]["what"][:120] if row else out.get("raw", "")[-300:]))
        if not ok:
            FAILS.append("settings never merged")
    dlist = os.path.join(dreaming, "hook_dispatch_list.json")
    with Broken(dlist, text=json.dumps({"Stop": [{"hooks": [7]}]})):
        expect("malformed dispatch entry", home, 1, ["dispatch-list"], *M)
    with Broken(os.path.join(dreaming, "compact_nodes.py")):
        expect("a wired script removed", home, 1, ["claude-settings-scripts"], *M)
    with Broken(os.path.join(dreaming, "spread_recall.py")):
        expect("a dispatched script removed", home, 1, ["dispatch-list"], *M)
    with Broken(os.path.join(dreaming, "HOOK_DISPATCH_ENABLED")):
        expect("dispatcher switch removed", home, 1, ["dispatcher-switch"], *M)
    with Broken(os.path.join(dreaming, "bm25_index.json.gz")):
        expect("recall index removed", home, 1, ["recall-index"], *M)
    with Broken(tsv):
        expect("reflex table removed", home, 1, ["reflex-table"], *M)
    codex_hooks = os.path.join(home, ".codex", "hooks.json")
    with Broken(codex_hooks, text="{}"):
        expect("codex hooks emptied", home, 1, ["codex-hooks"], *M)

    with open(manifest + ".bad", "w", encoding="utf-8") as f:
        json.dump([{"path": "dreaming/no_such_strong_node_file.py", "need": "required"}], f)
    expect("required file missing", home, 1, ["required-files"], "--manifest", manifest + ".bad")
    with open(manifest + ".other", "w", encoding="utf-8") as f:
        json.dump({"files": [{"repo": "some-other-repo", "path": "x.py", "need": "required"}]}, f)
    expect("manifest has no rows for us", home, 2, ["required-files"], "--manifest", manifest + ".other",
           status="held")
    expect("no manifest", home, 2, ["required-files"], "--manifest", manifest + ".gone", status="held")
    with Broken(log, text="2020-01-01 00:00:00  Stop hooks=3 0.10s exit=0 out=0\n"):
        expect("last fire long ago", home, 2, ["hook-fired"], *M, status="held")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for name, line, want in (("hook exit 0 at end of line", "UserPromptSubmit spread_recall.py rc=0", False),
                             ("hook exit 1", "UserPromptSubmit spread_recall.py rc=1 0.30s boom", True)):
        with Broken(log, text="%s  UserPromptSubmit hooks=9 0.80s exit=0 out=120\n%s  %s\n" % (now, now, line)):
            code, out = doctor(home, *M)
            got = "hook-errors" in bad(out, "warn")
            print("%-4s %-34s hook-errors %s" % ("ok" if got == want else "BAD", name, "warn" if got else "absent"))
            if got != want:
                FAILS.append(name)

    expect("intact again after the breaks", home, 0, [], *M)
    if snapshot(bootstrap) != checkout_before:
        FAILS.append("doctor wrote into the checkout")
        print("BAD  the doctor changed files under %s (bytecode?)" % bootstrap)
    else:
        print("ok   the doctor wrote nothing into the checkout")
    return finish(tmp)


def finish(tmp):
    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
