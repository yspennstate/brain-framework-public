"""test_dispatch_top_level.py - the hook dispatcher keeps a hook's top-level control fields at the top (28 Sep 2026; interactive audit IA-20260928-07).

    python test_dispatch_top_level.py [--module HOOK_DISPATCH.py] [--old OLD_HOOK_DISPATCH.py]

Drives merge() with synthetic hook results, as the dispatcher's control suite does:
 T1 a Stop hook's {"decision": "block", "reason": R} comes out as a top-level block with reason R and no hookSpecificOutput
 T2 two Stop hooks that block: one block, both reasons, in hook order
 T3 one hook continue:false with a stop reason, another continue:true, in both orders: continue false, that reason kept
 T4 two systemMessages: both, at the top
 T5 UserPromptSubmit, one hook's context and another's block: the block at the top, the context in hookSpecificOutput
 T6 PreToolUse: a nested deny still beats allow, and exit code 2 still blocks
 T7 a Stop hook that prints only a plain-text context: nothing is emitted (the event carries no context)
 T8 the work timer's own block line, verbatim: a top-level block
With --old, the dispatcher before this change must fail T1 and T8 (the check can fail).
"""
import importlib.machinery
import importlib.util
import json
import os
import sys

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.abspath(sys.argv[sys.argv.index("--module") + 1]) if "--module" in sys.argv else os.path.join(HERE, "hook_dispatch.py")
OLD = os.path.abspath(sys.argv[sys.argv.index("--old") + 1]) if "--old" in sys.argv else None
WT = json.dumps({"decision": "block", "reason": "[work-session] now 21:54; ~48m of 20h remain (until 22:43)."})
fails, ran = [], []


def load(path, name):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    m.log = lambda *a, **k: None        # the controls do not write the dispatcher's log
    return m


def out_of(m, results, event):
    rc, out, err = m.merge(results, event, "test-session")
    return rc, (json.loads(out) if out else None), err


def cases(m):
    r = {}
    rc, o, _ = out_of(m, [("wt", 0, json.dumps({"decision": "block", "reason": "R1"}), "")], "Stop")
    r["T1"] = (o is not None and o.get("decision") == "block" and o.get("reason") == "R1" and "hookSpecificOutput" not in o,
               "T1 Stop block: %s" % (json.dumps(o)[:160],))
    rc, o, _ = out_of(m, [("a", 0, json.dumps({"decision": "block", "reason": "first"}), ""),
                          ("b", 0, json.dumps({"decision": "block", "reason": "second"}), "")], "Stop")
    r["T2"] = (o is not None and o.get("decision") == "block" and o.get("reason") == "first\n\nsecond",
               "T2 two Stop blocks: %s" % (json.dumps(o)[:160],))
    ok3, shown = True, []
    for order in ((0, 1), (1, 0)):
        hooks = [("stopper", 0, json.dumps({"continue": False, "stopReason": "owner stop"}), ""),
                 ("goer", 0, json.dumps({"continue": True}), "")]
        rc, o, _ = out_of(m, [hooks[i] for i in order], "Stop")
        ok3 &= o is not None and o.get("continue") is False and o.get("stopReason") == "owner stop"
        shown.append(json.dumps(o)[:80])
    r["T3"] = (ok3, "T3 continue:false wins in both orders: %s" % shown)
    rc, o, _ = out_of(m, [("a", 0, json.dumps({"systemMessage": "one"}), ""),
                          ("b", 0, json.dumps({"systemMessage": "two"}), "")], "UserPromptSubmit")
    r["T4"] = (o is not None and o.get("systemMessage") == "one\ntwo" and "systemMessage" not in (o.get("hookSpecificOutput") or {}),
               "T4 system messages: %s" % (json.dumps(o)[:160],))
    rc, o, _ = out_of(m, [("ctx", 0, json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                                         "additionalContext": "a note"}}), ""),
                          ("gate", 0, json.dumps({"decision": "block", "reason": "not this prompt"}), "")], "UserPromptSubmit")
    h = (o or {}).get("hookSpecificOutput") or {}
    r["T5"] = (o is not None and o.get("decision") == "block" and o.get("reason") == "not this prompt"
               and "a note" in (h.get("additionalContext") or "") and "decision" not in h,
               "T5 prompt block beside a context: %s" % (json.dumps(o)[:200],))
    rc, o, _ = out_of(m, [("a", 0, json.dumps({"hookSpecificOutput": {"permissionDecision": "allow", "additionalContext": "x"}}), ""),
                          ("b", 0, json.dumps({"hookSpecificOutput": {"permissionDecision": "deny",
                                                                       "permissionDecisionReason": "no"}}), "")], "PreToolUse")
    deny_ok = o is not None and (o.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
    rc2, o2, err2 = out_of(m, [("g", 2, "", "GATE: stop here")], "PreToolUse")
    r["T6"] = (deny_ok and rc2 == 2 and "GATE" in err2, "T6 nested deny beats allow %s; exit 2 blocks rc=%s" % (deny_ok, rc2))
    rc, o, _ = out_of(m, [("note", 0, "a plain note printed at stop", "")], "Stop")
    r["T7"] = (o is None and rc == 0, "T7 Stop with a plain-text context only: %s" % (json.dumps(o)[:120],))
    rc, o, _ = out_of(m, [("work_timer.py", 0, WT, "")], "Stop")
    r["T8"] = (o is not None and o.get("decision") == "block" and o.get("reason", "").startswith("[work-session]")
               and "hookSpecificOutput" not in o, "T8 the work timer's line: %s" % (json.dumps(o)[:160],))
    return r


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def main():
    for k, (ok, what) in sorted(cases(load(MOD, "hd_under_test")).items()):
        check(ok, what)
    if OLD:
        old = cases(load(OLD, "hd_old"))
        for k, n in (("T1", "N1"), ("T8", "N2")):
            check(not old[k][0], "%s the dispatcher before this change fails %s (the check can fail): %s" % (
                n, k, old[k][1].split(": ", 1)[1]))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
