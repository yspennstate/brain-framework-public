"""Git pre-push gate: run scan_secrets.py on every range a push would publish, and refuse the push unless each scan
exits 0 (scan_secrets: 1 = possible secrets, 2 = the scan could not complete; both block). Installed as the
pre-push hook of a clone, it makes the rule "scan_secrets --range must print clean before the push" a property of
the push instead of the pusher's command line - pushed on a scanner error twice (MISTAKES 2026-09-17 00:34 and
2026-09-25 01:0x), both times from a chained command whose exit did not gate the push.

git calls the hook with the remote name and url as arguments and one line per ref on stdin:
    <local ref> <local oid> <remote ref> <remote oid>
The range is <remote oid>..<local oid>. A new remote ref (all-zero remote oid), or a remote oid this clone does not
have, scans from a base the remote advertises NOW: git ls-remote is read in the hook, and one rev-list --boundary of the
local commit against the advertised commits this clone has names the remote commits the new ones rest on; the one that
leaves the fewest commits to scan is the base. Since 4 Oct 2026: the merge-base search per advertised commit it
replaced started three git processes per ref, and a new branch on a remote with 776 refs waited 22 minutes; it remains
the fallback for any answer the batched search cannot read. Everything reachable from that base is already on the
remote, so every commit the new ref makes reachable is in the scan (it may scan more, never less). Until 28 Sep the base came from the local tracking refs, and a tracking ref left
ahead of the remote by a rewind (or behind it by a missed fetch) could leave a commit the push made reachable out of
the scan (account02/B06 r65, R24-B06-PREPUSH-NEW-REF-FALLBACK-CAN-NARROW-20260928). The push refuses when the remote
cannot be read or none of its advertised commits shares history with the pushed one. A deletion (all-zero local oid)
sends no content and is not scanned. Git's hook environment (GIT_DIR and friends) is removed before the scanner runs,
because the scanner refuses inherited repository overrides.

The gate reads the commit graph the way the scanner does and the remote will: no replace objects, no grafts, no shallow
file. Until 28 Sep 20:5x its merge-base honored refs/replace while the scanner disabled replacements, so a local
replacement that grafted an unrelated remote commit onto the pushed history certified one commit and published three
(account03/B19 r69, B19-R51-FRESH-BASE-REPLACE-REF-GRAPH-MISMATCH-20260928). Those three settings go only to the gate's
own git calls; the scanner sets them itself and refuses them inherited.

A new ref's base rests on some other ref of the remote (the witness), and that ref can move or be deleted while the scan
runs, which leaves commits the scan treated as already published newly reachable (account02/B06 r68,
R51-B06-NEWREF-WITNESS-DELETE-CUSTOMREF-20260928). After the scan the base is chosen again from what the remote advertises
then; a base the first range does not cover is scanned too, and after three moves the push is refused. What the hook
cannot close is the moment between its own exit and the remote's ref update: an update of an existing ref is guarded by
the old id git sends with it, a new ref by nothing but this re-check.
"""
import os
import re
import subprocess
import sys

SCANNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_secrets.py")
ZERO = re.compile(r"^0+$")
GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
           "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
           "GIT_PREFIX", "GIT_QUARANTINE_PATH", "GIT_PUSH_OPTION_COUNT", "GIT_EXEC_PATH")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def clean_env():
    env = dict(os.environ)
    for k in list(env):
        if k in GIT_ENV or k.startswith("GIT_CONFIG_KEY_") or k.startswith("GIT_CONFIG_VALUE_") or k.startswith("GIT_PUSH_OPTION_"):
            env.pop(k, None)
    return env


def graph_env(env):
    """The environment for the gate's own graph reads: the true object graph, as the scanner reads it."""
    g = dict(env)
    g["GIT_NO_REPLACE_OBJECTS"] = "1"
    g["GIT_GRAFT_FILE"] = os.devnull
    g["GIT_SHALLOW_FILE"] = os.path.join(os.devnull, "prepush-gate-disabled")
    return g


def git(args, env):
    p = subprocess.run(["git"] + args, capture_output=True, text=True, env=env, creationflags=NOWIN)
    return p.returncode, p.stdout.strip()


def scan(base, local_oid, env):
    return subprocess.run([sys.executable, SCANNER, "--range", "%s..%s" % (base, local_oid)], env=env,
                          creationflags=NOWIN).returncode


def advertised(where, env):
    """Every object id the remote advertises now (git ls-remote), peeled tags included; None when it cannot be read.
    Local tracking refs are not used: a rewind or a missed fetch leaves them where the remote no longer is."""
    rc, out = git(["ls-remote", where], env)
    if rc != 0:
        return None
    oids = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and re.match(r"^[0-9a-f]{40}([0-9a-f]{24})?$", parts[0]):
            oids.add(parts[0])
    return sorted(oids)


def git_input(args, text, env):
    p = subprocess.run(["git"] + args, input=text, capture_output=True, text=True, env=env, creationflags=NOWIN)
    return p.returncode, p.stdout.strip()


_OIDLINE = re.compile(r"^-?[0-9a-f]{40}([0-9a-f]{24})?$")


def fresh_base(local_oid, where, env):
    """(base, why): a commit every object of which is on the remote now, chosen so base..local_oid is smallest (ties by
    object id, so the order the remote lists its refs in cannot change it). An advertised commit this clone lacks is
    skipped, which can only widen the scan.

    A handful of git processes for the whole advertisement, not three per advertised id (4 Oct 2026: math-research-brain
    advertised 776 refs, so a new branch's push started about 2,300 processes and ran for hours inside a
    process-creation stall). cat-file --batch-check peels the advertised ids this clone has to commits; rev-list
    --boundary of local_oid against them lists the commits the remote lacks and, prefixed '-', the remote commits they
    rest on. Each id goes in as a ^id line on stdin, because git does not apply a command-line --not to --stdin input.
    Every boundary commit is reachable from an advertised id, so boundary..local_oid holds every commit the push makes
    reachable. Any unexpected answer falls back to the one-id-at-a-time search below."""
    adv = advertised(where, env)
    if adv is None:
        return "", "the remote's refs could not be read (git ls-remote failed)"
    rc, out = git_input(["cat-file", "--batch-check=%(objectname) %(objecttype)"],
                        "".join(oid + "^{commit}\n" for oid in adv), env)
    if rc == 0:
        commits = sorted({line.split()[0] for line in out.splitlines()
                          if line.endswith(" commit") and _OIDLINE.match(line.split()[0])})
        rc, out = git_input(["rev-list", "--ignore-missing", "--boundary", local_oid, "--stdin"],
                            "".join("^" + oid + "\n" for oid in commits), env)
        lines = out.splitlines() if rc == 0 else []
        if rc == 0 and all(_OIDLINE.match(line) for line in lines):
            new = [line for line in lines if not line.startswith("-")]
            bounds = sorted({line[1:] for line in lines if line.startswith("-")})
            if not new:
                return local_oid, ""
            if not bounds:
                return "", "no commit the remote advertises now shares history with it"
            best = None
            for b in bounds:
                rc, n = git(["rev-list", "--count", "%s..%s" % (b, local_oid)], env)
                if rc != 0 or not n.isdigit():
                    best = None
                    break
                if best is None or (int(n), b) < best:
                    best = (int(n), b)
            if best is not None:
                return best[1], ""
    return _fresh_base_by_id(local_oid, adv, env)


def _fresh_base_by_id(local_oid, adv, env):
    """(base, why): a commit every object of which is on the remote now, chosen so base..local_oid is smallest (ties by
    object id, so the order the remote lists its refs in cannot change it). An advertised commit this clone lacks is
    skipped, which can only widen the scan."""
    best = None
    for oid in adv:
        if git(["cat-file", "-e", oid + "^{commit}"], env)[0] != 0:
            continue
        rc, mb = git(["merge-base", local_oid, oid], env)
        if rc != 0 or not mb:
            continue
        rc, n = git(["rev-list", "--count", "%s..%s" % (mb, local_oid)], env)
        if rc != 0 or not n.isdigit():
            continue
        if best is None or (int(n), mb) < best:
            best = (int(n), mb)
    if best is None:
        return "", "no commit the remote advertises now shares history with it"
    return best[1], ""


def main():
    remote = sys.argv[1] if len(sys.argv) > 1 else "origin"
    where = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else remote
    env = clean_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    genv = graph_env(env)
    lines = [l.split() for l in sys.stdin.read().splitlines() if l.strip()]
    for parts in lines:
        if len(parts) != 4:
            print("pre-push secret gate: an unreadable ref line - push refused", file=sys.stderr)
            return 1
        local_ref, local_oid, remote_ref, remote_oid = parts
        if ZERO.match(local_oid):
            continue
        base = remote_oid
        from_witness = ZERO.match(remote_oid) or git(["cat-file", "-e", remote_oid + "^{commit}"], genv)[0] != 0
        if from_witness:
            base, why = fresh_base(local_oid, where, genv)
            if not base:
                print("pre-push secret gate: %s: %s - push refused; fetch, or scan it by hand (scan_secrets.py --range "
                      "<base>..<oid>) and push once it is clean" % (local_ref, why), file=sys.stderr)
                return 1
        if base == local_oid:
            continue
        rc = scan(base, local_oid, env)
        for _ in range(3 if from_witness else 0):
            if rc != 0:
                break
            again, why = fresh_base(local_oid, where, genv)
            if not again:
                print("pre-push secret gate: %s: after the scan, %s - push refused" % (local_ref, why), file=sys.stderr)
                return 1
            if again == base or git(["merge-base", "--is-ancestor", base, again], genv)[0] == 0:
                break   # the range from the base chosen now lies inside the one already scanned
            base = again
            rc = scan(base, local_oid, env) if base != local_oid else 0
        else:
            if from_witness and rc == 0:
                print("pre-push secret gate: %s: the remote's refs kept moving while the scan ran - push refused"
                      % local_ref, file=sys.stderr)
                return 1
        if rc != 0:
            print("pre-push secret gate: scan_secrets exit %d on %s - push refused (1 = possible secrets, 2 = the scan "
                  "could not complete; both block)" % (rc, local_ref), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
