"""Find hook scripts wired more than once under the same event and matcher in hook_dispatch_list.json.

WHY (1 Nov 2026). The 28 Oct 17:13 film-hook landing added a second full copy of the 221-hook film
group instead of appending one hook. For four days every film hook ran twice on every prompt and every Bash/Edit call
in every Claude session: PreToolUse went from 259 hooks to 481, every fire was printed twice, and the film group's
dispatch took a median 8.5 s wall against 4.5 s with one copy on a loaded machine. Nothing flagged
it. hook_liveness.py calls double_wired() and reports any count above one.

A script wired under two DIFFERENT matchers (Bash in one group, Edit in another) is not a double; the same script
under the same matcher twice in one event is.

    python hook_double_wiring.py [list.json]    prints the doubles; exit 3 if any
"""
import collections, json, os, sys

LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_dispatch_list.json")


def double_wired(path=LIST):
    """{event: [(matcher, script, count), ...]} for every script wired more than once under one event and matcher."""
    d = json.load(open(path, encoding="utf-8"))
    out = {}
    for ev, groups in d.items():
        if not isinstance(groups, list):
            continue
        c = collections.Counter()
        for g in groups:
            if not isinstance(g, dict):
                continue
            m = str(g.get("matcher", ""))
            for h in g.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                key = " ".join([str(h.get("command", ""))] + [str(a) for a in h.get("args", []) or []]).replace("\\", "/")
                c[(m, key)] += 1
        dup = [(m, k, n) for (m, k), n in c.items() if n > 1]
        if dup:
            out[ev] = dup
    return out


if __name__ == "__main__":
    res = double_wired(sys.argv[1] if len(sys.argv) > 1 else LIST)
    for ev, dup in res.items():
        print("%s: %d script(s) wired more than once, e.g. %s" % (ev, len(dup), dup[0][1][-90:]))
    sys.exit(3 if res else 0)
