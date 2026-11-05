#!/usr/bin/env python3
"""guard_orphans.py - the REVERSE-direction wiring audit: a guard that exists but fires into nothing.

hook_liveness.py asks the forward question - "every hook WIRED in settings.json, does its script exist and
compile?" - and so is structurally blind to the opposite failure: a guard that is BUILT and passes its
selftest but is wired to no hook and called by no umbrella. It never appears in settings.json, so
hook_liveness never considers it. That is how claim_check.py and retraction_guard.py sat dead on 2026-07-11
(found by hand) - selftests green, firing nothing. This makes that class visible.

Method: enumerate the guard-shaped files (source emits a hook decision: a Stop-block, hookSpecificOutput,
additionalContext, or reads hook_event_name). Compute what is REACHABLE - the scripts wired in settings.json,
plus everything they import, transitively (an umbrella like pre_report_check imports pre_report imports
gfcore). A guard-shaped file not in that reachable set fires into nothing: an ORPHAN.

Read-only. Stdlib, forward-slash paths, fail-open. Usage: python guard_orphans.py
"""
import os, io, re, json

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
DIRS = [HERE, os.path.join(HERE, "graph_fire")]

# a file is "guard-shaped" if it can act as a hook / gate - it emits one of these decision surfaces
GUARD_SIG = re.compile(r'"decision"\s*:\s*"block"|hookSpecificOutput|additionalContext|hook_event_name'
                       r'|def assess\(|def check\(', re.I)
IMPORT_RE = re.compile(r'^\s*(?:from\s+([A-Za-z_]\w*)\s+import|import\s+([A-Za-z_]\w*))', re.M)


def _py_files():
    out = {}
    for d in DIRS:
        try:
            for f in os.listdir(d):
                if f.endswith(".py"):
                    out[f[:-3]] = os.path.join(d, f)
        except Exception:
            pass
    return out


def _read(path):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def _wired_modules():
    """Module stems named directly in settings.json hooks (args or command)."""
    mods = set()
    try:
        d = json.load(io.open(SETTINGS, encoding="utf-8"))
    except Exception:
        return mods
    blob = json.dumps(d)
    for m in re.findall(r'([A-Za-z_][\w-]*)\.py', blob):
        mods.add(os.path.basename(m))
    return mods


def main():
    files = _py_files()                     # stem -> path
    src = {stem: _read(p) for stem, p in files.items()}
    # import graph over local modules only
    deps = {}
    for stem, text in src.items():
        d = set()
        for a, b in IMPORT_RE.findall(text):
            mod = a or b
            if mod in files and mod != stem:
                d.add(mod)
        deps[stem] = d

    wired = _wired_modules() & set(files)   # only those that are real local scripts
    # transitive reachability from the wired set (what a wired hook can actually invoke)
    reachable, frontier = set(wired), list(wired)
    while frontier:
        cur = frontier.pop()
        for nxt in deps.get(cur, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)

    guard_shaped = {stem for stem, text in src.items() if GUARD_SIG.search(text)}
    # A REAL live-hook surface is HARNESS-DRIVEN: it reads sys.stdin AND writes a decision/injection to
    # stdout. That structural pair excludes the false positives the first pass caught - evals that merely
    # SIMULATE a decision (eval_*), a fixture gate that drives OTHER guards (humility_check), and this tool
    # itself (its regexes contain the very signatures). A def assess/check with no stdin is a helper,
    # reachable via import by its hook, not an orphan.
    STDIN = re.compile(r'sys\.stdin')
    DECISION = re.compile(r'"decision"\s*:\s*"block"|hookSpecificOutput|additionalContext')
    hook_surface = {stem for stem, text in src.items()
                    if STDIN.search(text) and DECISION.search(text)
                    and not stem.startswith("eval") and stem != "guard_orphans"}

    orphans = sorted(s for s in hook_surface if s not in reachable)
    called_helpers = sorted(s for s in (guard_shaped - hook_surface)
                            if s not in reachable and s not in wired)

    print("=" * 74)
    print(" GUARD ORPHANS  -  built but firing into nothing (reverse wiring audit)")
    print("=" * 74)
    print("\n%d guard-shaped files; %d wired directly; %d reachable (wired + imported)."
          % (len(guard_shaped), len(wired), len(reachable)))
    if orphans:
        print("\nORPHANED HOOKS (a real firing surface, but no hook reaches them - they protect nothing):")
        for s in orphans:
            has_selftest = "--selftest" in src[s] or "def selftest" in src[s] or "def _selftest" in src[s]
            print("  %-24s %s" % (s, "(selftest present - looks done, fires nothing)" if has_selftest else ""))
        print("\n  -> wire each into a hook (settings.json) or an umbrella an existing hook already calls,")
        print("     OR retire it. A green selftest on an unwired guard is false comfort.")
    else:
        print("\nNo orphaned hook surfaces: every guard-shaped hook is reachable from a wired entry point.")
    if called_helpers:
        print("\nnote - assess/check helpers not reached (may be fine if only used by a selftest/eval): %s"
              % ", ".join(called_helpers[:12]))


if __name__ == "__main__":
    main()
