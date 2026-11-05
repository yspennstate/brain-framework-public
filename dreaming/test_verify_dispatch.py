# -*- coding: utf-8 -*-
"""Do the verification hooks' answers survive the dispatcher? (30 Sep 2026)

    python test_verify_dispatch.py

Runs each verification hook in-process the way hook_dispatch.py does (run_in_process with the wired arguments) and
passes its output through hook_dispatch.merge, the function whose Stop handling dropped every JSON block from 15 to 28
Sep (IA-20260928-07). Checks that a verify_gate block arrives as a top-level decision:block, and that verify_sense and
verify_ops contexts arrive in hookSpecificOutput.additionalContext. The hooks' state and logs are pointed at a
temporary directory through environment variables the hooks read, so no live state is written.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hook_dispatch as hd          # noqa: E402
import test_verify_hooks as tvh     # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def main():
    td = tempfile.mkdtemp(prefix="vdisp_")
    os.environ["VERIFY_HOOKS_STATE_DIR"] = td
    tp = os.path.join(td, "t.jsonl")
    tvh.claude_transcript(tp, "fix it", [("edit", "C:/repo/src/mod.py"), ("cmd", "python -m pytest tests/test_mod.py -q", "4 passed"),
                                         ("edit", "C:/repo/src/mod.py")], "Fixed; all 4 tests pass.")
    stop = json.dumps({"hook_event_name": "Stop", "session_id": "disp-%d" % os.getpid(), "transcript_path": tp,
                       "stop_hook_active": False})
    rc, out, err = hd.run_in_process(os.path.join(HERE, "verify_gate.py"), stop)
    code, merged, merr = hd.merge([("verify_gate", rc, out, err)], "Stop", "disp")
    obj = json.loads(merged) if merged.strip().startswith("{") else {}
    check("gate block survives the dispatcher as a top-level decision", obj.get("decision") == "block"
          and "[verify-gate R1]" in (obj.get("reason") or ""), merged[:160])

    prompt = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "disp-p-%d" % os.getpid(),
                         "prompt": "prove that the lemma holds for all n and check it in lean"})
    rc, out, err = hd.run_in_process(os.path.join(HERE, "verify_sense.py"), prompt)
    code, merged, merr = hd.merge([("verify_sense", rc, out, err)], "UserPromptSubmit", "disp")
    obj = json.loads(merged) if merged.strip().startswith("{") else {}
    ctx = (obj.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    check("sense card survives the dispatcher as additionalContext", "[verify]" in ctx and "lean_check" in ctx, ctx[:120])

    post = json.dumps({"hook_event_name": "PostToolUse", "session_id": "disp-o-%d" % os.getpid(), "tool_name": "Write",
                       "tool_input": {"file_path": "C:/papers/x/proof.tex",
                                      "content": "We interchange the limit and the integral to conclude."}})
    rc, out, err = hd.run_in_process(os.path.join(HERE, "verify_ops.py"), post)
    code, merged, merr = hd.merge([("verify_ops", rc, out, err)], "PostToolUse", "disp")
    obj = json.loads(merged) if merged.strip().startswith("{") else {}
    ctx = (obj.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    check("ops card survives the dispatcher as additionalContext", "[verify-op exchange]" in ctx, ctx[:120])

    entries = {ev: json.dumps(hd.hook_entries(ev, tool)) for ev, tool in
               (("Stop", ""), ("UserPromptSubmit", ""), ("PostToolUse", "Write"))}
    check("the dispatcher lists verify_gate on Stop", "verify_gate.py" in entries["Stop"])
    check("the dispatcher lists verify_sense on UserPromptSubmit", "verify_sense.py" in entries["UserPromptSubmit"])
    check("the dispatcher lists verify_ops on PostToolUse for Write", "verify_ops.py" in entries["PostToolUse"])
    print("%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
