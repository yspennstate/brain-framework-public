#!/usr/bin/env python3
"""mechanisms.py - the map of the machinery: every script in the brain, its one-line purpose, and WHEN it
actually runs (a live hook, a dream/persist step, or an on-demand tool). Self-maintaining - it reads the
scripts, settings.json, the dream prompts, and run_dream.ps1 each time, so it can't drift the way a
hand-written index would.

The brain grew to dozens of scripts across many sessions and six concurrent minds, with no map. A tool
nobody can find is half-built; a hook you can't see the wiring of is hard to trust. This answers, in one
screen: what does this brain have, and what fires when? Read-only; writes nothing.

  python mechanisms.py           # grouped: hooks (auto) / dream (periodic) / tools (on-demand)
  python mechanisms.py NAME      # the docstring + wiring for one script
"""
import os, io, ast, json, re, sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")


def _summary(path):
    """First line of the module docstring - the scripts follow a '<name>.py - <summary>' convention."""
    try:
        doc = ast.get_docstring(ast.parse(io.open(path, encoding="utf-8", errors="replace").read()))
    except Exception:
        return "(unparseable)"
    if not doc:
        return "(no docstring)"
    first = doc.strip().splitlines()[0]
    # drop the leading 'name.py - ' so the summary reads clean in a column
    m = re.match(r"[\w.]+\.py\s*[-–—:]\s*(.+)", first)
    return (m.group(1) if m else first).strip()


def _hook_map():
    """script basename -> set of hook events it's wired into, from settings.json."""
    m = {}
    try:
        d = json.load(io.open(SETTINGS, encoding="utf-8"))
    except Exception:
        return m
    for ev, groups in d.get("hooks", {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                for a in (h.get("args", []) or []):
                    if isinstance(a, str) and a.endswith(".py"):
                        m.setdefault(os.path.basename(a), set()).add(ev)
                # some hooks put the script in the command string, not args
                cmd = h.get("command", "") if isinstance(h.get("command", ""), str) else ""
                for name in re.findall(r"([\w]+\.py)", cmd):
                    m.setdefault(name, set()).add(ev)
    return m


def _dream_map():
    """script basename -> set of dream prompts that invoke it."""
    m = {}
    for f in os.listdir(DR):
        if not (f.startswith("dream_prompt") and f.endswith(".md")):
            continue
        try:
            t = io.open(os.path.join(DR, f), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for name in set(re.findall(r"([\w]+\.py)", t)):
            m.setdefault(name, set()).add(f.replace("dream_prompt", "dream").replace(".md", "").strip("_") or "dream")
    return m


def _runner_map():
    """script basename -> runner(s) that execute it outside the model-authored dream prompt.

    The persist/maintenance half of run_dream.ps1 directly invokes many mechanisms after the
    headless dream exits (BM25 rebuild, evals, attribution, liveness, mirror verification).  The
    first version of this self-map inspected only dream_prompt*.md, so it labelled those scheduled
    jobs "on-demand" even though they ran every dream.  That is a metacognitive failure: the brain's
    map of its own machinery was wrong precisely where orchestration lived.
    """
    m = {}
    runners = ("run_dream.ps1",)
    for runner in runners:
        path = os.path.join(DR, runner)
        try:
            text = io.open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        # The runner uses (Join-Path $base 'name.py'); constrain to basenames so comments and
        # repo-external build tools do not turn into phantom local mechanisms.
        for name in set(re.findall(r"Join-Path\s+\$base\s+['\"]([\w.]+\.py)['\"]", text, re.I)):
            if os.path.exists(os.path.join(DR, name)):
                m.setdefault(name, set()).add(runner.replace(".ps1", ""))
    return m


def main():
    try:
        scripts = sorted(f for f in os.listdir(DR) if f.endswith(".py"))
    except Exception:
        print("no dreaming dir at %s" % DR)
        return
    hooks = _hook_map()
    dreams = _dream_map()
    runners = _runner_map()

    if len(sys.argv) > 1:
        q = sys.argv[1].replace(".py", "")
        for f in scripts:
            if q in f:
                path = os.path.join(DR, f)
                print("%s" % f)
                print("  purpose: %s" % _summary(path))
                w = []
                if f in hooks:
                    w.append("hooks: " + ", ".join(sorted(hooks[f])))
                if f in dreams:
                    w.append("dream prompts: " + ", ".join(sorted(dreams[f])))
                if f in runners:
                    w.append("scheduled runners: " + ", ".join(sorted(runners[f])))
                print("  runs as: %s" % ("; ".join(w) if w else "on-demand tool (nothing invokes it automatically)"))
        return

    auto, indream, tools = [], [], []
    for f in scripts:
        summ = _summary(os.path.join(DR, f))
        if f in hooks:
            auto.append((f, summ, ", ".join(sorted(hooks[f]))))
        elif f in dreams or f in runners:
            where = []
            if f in dreams:
                where.append("prompt: " + ", ".join(sorted(dreams[f])))
            if f in runners:
                where.append("runner: " + ", ".join(sorted(runners[f])))
            indream.append((f, summ, "; ".join(where)))
        else:
            tools.append((f, summ))

    print("=" * 72)
    print(" BRAIN MECHANISMS  -  %d scripts  (%d auto-hooked, %d dream/scheduled, %d on-demand)"
          % (len(scripts), len(auto), len(indream), len(tools)))
    print("=" * 72)

    print("\nAUTOMATIC  (fire on a hook event, every session):")
    for f, summ, where in auto:
        print("  %-22s [%s]" % (f, where))
        print("       %s" % summ[:150])

    print("\nDREAM / SCHEDULED  (run by a dream prompt or its persist/maintenance runner):")
    for f, summ, where in indream:
        print("  %-22s [%s]" % (f, where))
        print("       %s" % summ[:150])

    print("\nON-DEMAND  (a session or the owner runs these - the diagnostics + observers):")
    for f, summ in tools:
        print("  %-22s %s" % (f, summ[:130]))
    print()


if __name__ == "__main__":
    main()
