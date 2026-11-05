"""Will tonight's nightly persist be blocked by the secret gate, and by what?

run_dream.ps1 stages the two owned paths, runs `scan_secrets.py --staged`, and
fails CLOSED on any nonzero exit. That is the right design - a skipped push is
cheap, a pushed credential is not - but it has one bad property: from outside,
a real secret and a crashed scanner look identical, the scanner's output is
deliberately suppressed from unattended logs, and the marker file it leaves says
only that something failed. So nobody learns WHAT until a human goes digging.

On 2026-08-10 that cost eight hours. A memory documenting an earlier false
positive quoted the offending line verbatim; the quote landed inside the gate's
scanned scope and re-tripped the same rule, so every agent's memories stopped
being committed from 20:12 until someone read the marker at 04:26.

This answers the question in one command, at any time, without waiting for a
dream and WITHOUT STAGING ANYTHING - which matters because several agents are
usually live in that repo and the faithful reproduction mutates a shared index.

It imports the gate's own detectors and runs them over the working-tree files
under the owned paths, so it cannot drift from the gate's rules: there is no
second copy of the patterns here.

Deliberately NOT a fix to the gate. It does not exempt, allowlist, or soften
anything - it only tells you what the gate will say before it says it.

    python check_gate.py                # report on the whole scanned scope
    python check_gate.py --quiet        # exit code only
    python check_gate.py --file X [Y]   # pre-flight files not yet in the scope
    python check_gate.py --live         # the live memory dir, before it is mirrored

--file is the preventive half, and the cheaper one: check a memory BEFORE you
file it, rather than diagnosing a blocked push eight hours later. Use it on any
write-up that quotes code.

exit 0 = the gate will pass    1 = findings, the push will block
exit 2 = this checker could not answer (never read as "clean")
"""
import importlib.util
import os
import subprocess
import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.expanduser("~/brain-repo")
# Must mirror $ownedPaths in run_dream.ps1.
OWNED = ["01_ai_consciousness/brain", "01_ai_consciousness/claude/memory"]
# Where memories are authored before they are mirrored into OWNED.
LIVE_MEMORY = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}/memory")


def _load_scanner():
    path = os.path.join(BASE, "scan_secrets.py")
    spec = importlib.util.spec_from_file_location("scan_secrets", path)
    module = importlib.util.module_from_spec(spec)
    # @dataclass in the scanner resolves cls.__module__ through sys.modules.
    sys.modules["scan_secrets"] = module
    spec.loader.exec_module(module)
    return module


def main():
    quiet = "--quiet" in sys.argv[1:]

    def say(*a):
        if not quiet:
            print(*a)

    try:
        ss = _load_scanner()
    except Exception as exc:
        print("CANNOT ANSWER: the scanner would not import (%s)" % exc)
        return 2

    # Prove the detector works before trusting a zero. A checker that cannot
    # demonstrate it fires is worse than no checker: it manufactures calm.
    #
    # The planted marker is assembled at runtime rather than written out: a file that
    # carries the literal shape trips every scanner that reads it, including this one's
    # own subject, and a checker whose source is quarantined checks nothing. The same
    # idiom the scanner uses for its own pattern list.
    control = []
    planted = (b"-----BEGIN RSA PRI" + b"VATE KEY-----\n"
               b"MIIEowIBAAKCAQEA1234567890abcdef\n"
               b"-----END RSA PRI" + b"VATE KEY-----\n")
    ss._scan_bytes(planted, control, "control")
    if not control:
        print("CANNOT ANSWER: the detector did not fire on a planted private key.")
        return 2
    say("detector control: fires correctly (%s)" % control[0][0])

    # --file: pre-flight arbitrary paths that are not in the scanned scope yet.
    argv = [a for a in sys.argv[1:] if a != "--quiet"]
    if argv and argv[0] == "--file":
        targets = argv[1:]
        if not targets:
            print("CANNOT ANSWER: --file needs at least one path")
            return 2
        found = []
        for path in targets:
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except (OSError, IOError) as exc:
                print("CANNOT ANSWER: %s (%s)" % (path, exc))
                return 2
            before = len(found)
            ss._scan_bytes(data, found, path)
            for kind, where in found[before:]:
                print("WILL BLOCK  %-24s %s" % (kind, where))
        if found:
            print()
            print("Do not file this as-is: it would block the nightly persist for every")
            print("agent. Reword the offending span - describe the shape, or split the")
            print("quote - rather than pasting the literal line into a scanned path.")
            return 1
        say("%d file(s) checked, no findings - safe to file." % len(targets))
        return 0

    # --live: memories are AUTHORED here and mirrored into the repo later, so
    # the gate cannot see a problem until it is already in the scanned scope.
    # Catching it on this side is the difference between one reworded line and
    # a blocked persist for every agent.
    if argv and argv[0] == "--live":
        found, count = [], 0
        for root, _, names in os.walk(LIVE_MEMORY):
            for name in names:
                path = os.path.join(root, name)
                try:
                    with open(path, "rb") as fh:
                        data = fh.read()
                except (OSError, IOError):
                    continue
                count += 1
                before = len(found)
                ss._scan_bytes(data, found, os.path.relpath(path, LIVE_MEMORY))
                for kind, where in found[before:]:
                    print("WILL BLOCK ONCE MIRRORED  %-24s %s" % (kind, where))
        say("scanned %d files in the live memory dir" % count)
        if found:
            print()
            print("%d finding(s) here are not blocking yet - they will the moment"
                  % len(found))
            print("these files are mirrored into the repo. Fix BOTH copies: a mask")
            print("applied to only one of them is undone by the next sync.")
            return 1
        say("no findings - nothing here will block when mirrored.")
        return 0

    # Exactly the set `git add -A -- <owned>` would consider: tracked plus
    # untracked-and-not-ignored. Read-only plumbing, no index written.
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--"]
            + OWNED, cwd=REPO, capture_output=True, text=True)
    except OSError as exc:
        print("CANNOT ANSWER: git would not run (%s)" % exc)
        return 2
    if out.returncode != 0:
        print("CANNOT ANSWER: git ls-files exit %d" % out.returncode)
        return 2

    files = [f for f in out.stdout.splitlines() if f.strip()]
    findings, unreadable = [], 0
    for rel in files:
        try:
            with open(os.path.join(REPO, rel), "rb") as fh:
                data = fh.read()
        except (OSError, IOError):
            unreadable += 1
            continue
        before = len(findings)
        ss._scan_bytes(data, findings, rel)
        for kind, where in findings[before:]:
            print("WILL BLOCK  %-24s %s" % (kind, where))

    say("scanned %d files under %s (%d unreadable)"
        % (len(files) - unreadable, " + ".join(OWNED), unreadable))

    if findings:
        print()
        print("%d finding(s): tonight's persist WILL be blocked and nothing will be"
              % len(findings))
        print("committed or pushed. If a finding is real, mask or rotate it. If it is")
        print("prose quoting code that merely looks like an assignment, reword it -")
        print("do NOT soften the gate, and do not quote the literal line again, which")
        print("is what re-armed the trap the last time.")
        return 1

    say("no findings - the gate will pass on content.")
    say("(A crashed scanner can still block: this checks findings, not the run.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
