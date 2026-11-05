"""Fail if the framework tree carries the owner's private vocabulary.

This repo is the framework; the brain that runs on it is not. The two live in the same working
directory on the author's machine, so a file copied across without thinking carries reflex ids,
memory slugs, and system names that belong on the private side. That happened once already: an
earlier sync put private-stack reflex ids and a named private memory into this tree, and it was
caught by reading the files rather than by anything that could fail.

The rules in this file are shapes only: home directories, project slugs, routable addresses. The
names of a particular person's hosts, repositories and vocabulary are data, and this file is
published, so they live in a local rule file that never leaves the machine (see
_local_private_rules). The checker scans itself like any other file, so "clean" covers the rule
table too.

Run it before a push:  python scripts/check_no_private_labels.py
Exit 0 = clean, 1 = something private is in the tree.
"""
import io
import os
import re
import subprocess
import sys

# Shapes that identify a private system whoever runs the framework. A rule here must name a
# SHAPE, never one person's actual names: this file is published, and a literal rule publishes
# the string it hunts for. Literal names go in the local rule file (see _local_private_rules).
PRIVATE = [
    # A Claude Code project directory is named after the home path with ':' and the
    # separators replaced by '-', so a hardcoded one is the author's username in
    # disguise. 15 files carried it until it was derived instead; this keeps it out.
    # Deriving is the fix, not a {{TEMPLATE}} token - a placeholder in a runtime
    # default raises at import, which is a bug this tree has already had.
    # bootstrap/install.py documents the convention with C:\Users\me, and the guard must
    # not flag the framework's own example - a check that fires on correct code teaches
    # people to skim its output, which is how the real hit gets missed.
    (r"\b[A-Za-z]--(?:Users|home)-(?!me\b|you\b|user\b|username\b|example\b)[A-Za-z0-9._-]+",
     "hardcoded project slug"),
    # An absolute home directory is the same leak in its undisguised form. 17 files
    # carried one until they were derived from expanduser / $env:USERPROFILE. The
    # placeholder names stay allowed so docs and fixtures can still show a path.
    # The separator repeats. A path written inside a source literal carries escaped
    # backslashes ("C:\\Users\\username"), and a class matching exactly one of them walks
    # straight past the very files most likely to hardcode a home directory. This rule
    # read as correct and reported a clean tree while eight tracked files held the real
    # username; the missing character was the "+".
    # "..." is a written placeholder and private-sentinel is a fixture that exists to be
    # obviously fake; both are correct code and flagging them would train people to skim.
    (r"(?i)\b[a-z]:[\\/]+Users[\\/]+"
     r"(?!me\b|you\b|user\b|username\b|example\b|private-sentinel\b|<|\.\.\.)"
     r"[A-Za-z0-9._-]+", "absolute home directory"),
    (r"(?i)(?<![A-Za-z0-9])/(?:home|Users)/"
     r"(?!me\b|you\b|user\b|username\b|example\b|private-sentinel\b|<|\.\.\.)"
     r"[a-z][a-z0-9._-]+", "absolute home directory"),
    # MODEL_OF_PARTNER is the framework's own generic name for this hub document, so match the
    # private forms only. A guard broad enough to flag the framework's own vocabulary trains
    # people to skim its output, which is how the real hit gets skipped.
    (r"MODEL_OF_(?!PARTNER\b)[A-Z]{4,}", "private profile document"),
    # Only routable addresses. Loopback, RFC1918 and the wildcard are ordinary in server code and
    # reveal nothing; the address worth catching is a real host someone could reach.
    # ...and not the RFC 5737 documentation ranges, which exist so tests can name an address
    # without naming a host. Flagging those would fire on correct code.
    (r"\b(?!127\.|10\.|0\.0\.0\.0|255\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\."
     r"|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)"
     r"\d{1,3}(\.\d{1,3}){3}\b(?!\.\d)", "routable IP address"),
]
# Literal names were kept here until 2026-09-28 on the reasoning that a literal catches the
# instance that exists at no cost. The cost was this file: every clone carries it, and it printed
# every name it guarded while exempting itself from its own scan. The literals moved to the local
# rule file with the same coverage on any machine that has it; the self-exemption is gone.

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache"}
# Every publishable file is read unless its content is binary. Until 2026-09-29 only an
# allowlist of suffixes was, so .jsonl, .log, .lock, .sha256, .service, .timer, .slice and
# files with no suffix at all were never opened - a log beside the dreaming scripts held a
# home directory and would have been committed unread (account04/B17 r13, B17-F1). A
# suffix says nothing about what a file can leak.
BINARY_PROBE = 8192


def read_text(path):
    """The file's text, or None when it is binary: a NUL byte in its first 8 KB, or bytes there
    that are not UTF-8 (a character cut by the 8 KB boundary is still text)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    head = data[:BINARY_PROBE]
    if b"\0" in head:
        return None
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:
            return None
    return data.decode("utf-8", "replace")



def publishable_files(root):
    """Every file git would publish from this tree: tracked, plus untracked and not ignored.

    The gate exists to protect what leaves the machine, and a .gitignored file never does.
    Scanning the raw directory instead meant an ordinary install or dream run - which writes
    bootstrap/settings.hooks.generated.json and dreaming/supersession_frontmatter_cache.json,
    both already ignored - printed hits that no public clone could ever carry. Ten false lines
    above one real question is how a reader learns to skim, and skimming is how the real hit
    gets through.

    If git cannot answer (no repository, no git on PATH), fall back to walking the tree. A gate
    that quietly scans nothing is worse than one that scans too much.
    """
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others",
                              "--exclude-standard", "-z"],
                             capture_output=True, timeout=120)
        if out.returncode == 0:
            names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
            if names:
                return [os.path.join(root, n.replace("/", os.sep)) for n in names]
    except (OSError, subprocess.SubprocessError):
        pass
    walked = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        walked.extend(os.path.join(dirpath, fn) for fn in filenames)
    return walked


def local_rule_files():
    """Where the local rules live: beside this script, in the per-user framework folder, and in
    any file named by $BRAIN_PRIVATE_LABELS (several joined with os.pathsep). The per-user file
    covers every clone on the machine, so a fresh worktree is not silently rule-less."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_labels.local")
    home = os.path.join(os.path.expanduser("~"), ".brain-framework", "private_labels.local")
    named = [p for p in os.environ.get("BRAIN_PRIVATE_LABELS", "").split(os.pathsep) if p]
    seen, out = set(), []
    for p in [here, home] + named:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _local_private_rules(paths=None):
    """Literal rules from the local rule files - one `pattern|label` per line; the label is what
    follows the last "|", so a pattern may contain one.

    A person's own names - hosts, repositories, a home directory, private vocabulary - are exact
    strings, not shapes. A checker in a published tree that hunts for them has to print them, which
    publishes the very strings it exists to keep out. They live in these files instead, never in a
    repository, so a machine that has them gets full coverage and a clone discloses nothing.
    """
    out = []
    for path in (local_rule_files() if paths is None else paths):
        try:
            for raw in io.open(path, encoding="utf-8"):
                line = raw.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                pattern, _, label = line.rpartition("|")
                if pattern:
                    out.append((pattern, label or "private label (local)"))
        except OSError:
            pass
    return out


PUBLIC_RULES = list(PRIVATE)
PRIVATE = PUBLIC_RULES + _local_private_rules()


def main(root=None, rules=None):
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules = PRIVATE if rules is None else rules
    findings = []
    files = read = binary = 0
    # This file is scanned like every other: it used to skip itself, which made "clean" mean
    # "clean outside the rule table" while the rule table held the names.
    for path in publishable_files(root):
        files += 1
        text = read_text(path)
        if text is None:
            binary += 1
            continue
        read += 1
        for pattern, what in rules:
            for m in re.finditer(pattern, text, re.I):
                line = text.count("\n", 0, m.start()) + 1
                findings.append((os.path.relpath(path, root), line, what, m.group(0)))

    # "clean" says how much was looked at: a gate whose clean output had no denominator could not
    # show that it had read nothing (account04/B17 r13, B17-F3).
    counts = "%d files: %d read as text, %d binary skipped" % (files, read, binary)
    if not findings:
        print("clean: no private labels in the tree (%s)" % counts)
        return 0
    print("FAIL: %d private label(s) in the framework tree (%s)" % (len(findings), counts))
    for rel, line, what, hit in findings[:40]:
        print("  %s:%d  %s (%s)" % (rel, line, hit, what))
    if len(findings) > 40:
        print("  ... and %d more" % (len(findings) - 40))
    return 1


if __name__ == "__main__":
    # Discovery is not execution: this script writes, so a help request must exit
    # before any work. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
