"""hook_dispatch_control.py - the equivalence + blocking controls for hook_dispatch.py.

Three things have to be true before the dispatcher may be wired in:
  A. EQUIVALENCE. For each real hook of each event, running it in-process must produce the same exit code and the same
     output SHAPE as running it as its own process. Many hooks are deliberately state-dependent (fire once per session,
     once per 30 min, dedup by signature), so an exact text match is only demanded when the hook proves itself
     deterministic: it is run twice as a subprocess with two fresh session ids, and only if those two agree is the
     in-process run required to agree with them exactly. Otherwise exit code and shape (json / plain / silent, has
     additionalContext, has permissionDecision) must match.
  B. BLOCKING. A hook that exits 2 must still block, and a hook that returns permissionDecision deny must still deny.
     These are driven through merge() with synthetic results, because no real gate can be fired on demand.
  C. COST. The dispatcher must actually be cheaper, measured on the same payload in the same minute.

Run:  python -X utf8 ~/.claude/dreaming/hook_dispatch_control.py
Exit 0 = all controls pass. Anything else = do not wire it in.
"""
import json, os, subprocess, sys, time, uuid

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook_dispatch as HD

CREATE_NO_WINDOW = (0x08000000 if os.name == "nt" else 0)
EVENTS = [("PreToolUse", "Bash"), ("PostToolUse", "Bash"), ("UserPromptSubmit", ""), ("PreToolUse", "Edit"),
          # 2026-09-17 09:4x: the Stop chain (14 hooks a turn, two of them gates that block a stop with exit 2) and the
          # PostToolUse Read/Edit arms, so the dispatcher can be wired for those events on the same evidence as the first two
          ("Stop", ""), ("PostToolUse", "Read"), ("PostToolUse", "Edit")]


def payload(event, tool, sid):
    d = {"session_id": sid, "hook_event_name": event, "cwd": os.path.expanduser("~"),
         "transcript_path": os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/%s.jsonl") % sid}
    if tool:
        d["tool_name"] = tool
        d["tool_input"] = {"command": "echo hi"} if tool in ("Bash", "PowerShell") else {"file_path": r"C:\tmp\x.txt", "old_string": "a", "new_string": "b"}
    if event == "UserPromptSubmit":
        d["prompt"] = "check the machine speed"
    if event == "PostToolUse" and tool:
        d["tool_response"] = ({"stdout": "hi", "stderr": "", "interrupted": False} if tool in ("Bash", "PowerShell")
                              else {"filePath": r"C:\tmp\x.txt", "success": True})
    if event == "Stop":
        d["stop_hook_active"] = False
    return json.dumps(d)


def shape(rc, out):
    t = (out or "").strip()
    if not t:
        return (rc, "silent", False, False)
    if t.startswith("{"):
        try:
            h = (json.loads(t).get("hookSpecificOutput") or {})
            return (rc, "json", bool(h.get("additionalContext")), bool(h.get("permissionDecision")))
        except Exception:
            return (rc, "badjson", False, False)
    return (rc, "text", True, False)


def run_sub(cmd, args, pl):
    try:
        if args:
            p = subprocess.run([cmd] + args, input=pl, capture_output=True, text=True, timeout=180, creationflags=CREATE_NO_WINDOW)
        else:
            p = subprocess.run(cmd, shell=True, input=pl, capture_output=True, text=True, timeout=180, creationflags=CREATE_NO_WINDOW)
        return p.returncode, p.stdout
    except Exception as e:
        return 1, "ERR %r" % (e,)


def control_a():
    """Probes with allow_all=True so every candidate is TESTED in-process, and returns the set that earned the fast
    path. A hook that is not deterministic, or whose in-process output differs at all, simply does not join the list."""
    print("== A. EQUIVALENCE (per hook: subprocess x2 vs in-process)")
    bad, checked, det = [], 0, 0
    passed = set()
    for event, tool in EVENTS:
        entries = HD.hook_entries(event, tool)
        print("  %s/%s: %d hooks" % (event, tool or "-", len(entries)))
        for cmd, args in entries:
            path, argv_tail = HD.as_python_argv(cmd, args, allow_all=True)
            label = os.path.basename(path) if path else (cmd[:38] + "...")
            rc1, o1 = run_sub(cmd, args, payload(event, tool, "ctlA1-" + uuid.uuid4().hex[:8]))
            rc2, o2 = run_sub(cmd, args, payload(event, tool, "ctlA2-" + uuid.uuid4().hex[:8]))
            pl = payload(event, tool, "ctlB-" + uuid.uuid4().hex[:8])
            if path:
                rc3, o3, _ = HD.run_in_process(path, pl, argv_tail)
            else:
                rc3, o3, _ = HD.run_subprocess(cmd, args, pl)
            checked += 1
            # A hook the dispatcher runs as its own subprocess takes the IDENTICAL code path as the harness, so exact
            # text equality would only be testing the hook's own determinism (the strong-nodes hook varies run to run
            # and produced a false "deterministic" reading on 2026-09-15). Shape is the honest bar for those.
            deterministic = (rc1, o1) == (rc2, o2) and path is not None
            if deterministic:
                det += 1
                ok = (rc3, o3) == (rc1, o1)
                why = "exact"
            else:
                ok = shape(rc3, o3) == shape(rc1, o1) or shape(rc3, o3) == shape(rc2, o2)
                why = "shape"
            if not ok:
                bad.append((event, label, why, (rc1, shape(rc1, o1)), (rc3, shape(rc3, o3))))
            elif path and why == "exact":
                passed.add(os.path.basename(path).lower())     # only an exact match earns the fast path
            print("     %-34s %-5s %s" % (label[:34], why, "PASS" if ok else "FAIL"))
    print("  checked %d hook-runs, %d deterministic, %d mismatches" % (checked, det, len(bad)))
    for b in bad:
        print("     MISMATCH", b)
    # a hook that mismatched anywhere is excluded everywhere, even if it matched under another event
    for _, lbl, _, _, _ in bad:
        passed.discard(lbl.lower())
    return (not bad), passed


def control_b():
    print("== B. BLOCKING and DENY propagate")
    ok = True
    code, out, err = HD.merge([("h1", 0, '{"hookSpecificOutput":{"additionalContext":"ctx"}}', ""),
                               ("gate", 2, "", "ACK GATE: answer the scar")], "PreToolUse")
    p = (code == 2 and "ACK GATE" in err)
    print("   exit-2 blocks:      %s (code=%s err=%r)" % ("PASS" if p else "FAIL", code, err[:40])); ok &= p
    code, out, err = HD.merge([("a", 0, '{"hookSpecificOutput":{"permissionDecision":"allow","additionalContext":"x"}}', ""),
                               ("b", 0, '{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"no gpu training"}}', "")], "PreToolUse")
    h = json.loads(out)["hookSpecificOutput"]
    p = (code == 0 and h["permissionDecision"] == "deny" and h["permissionDecisionReason"] == "no gpu training")
    print("   deny beats allow:   %s" % ("PASS" if p else "FAIL")); ok &= p
    code, out, err = HD.merge([("a", 0, '{"hookSpecificOutput":{"additionalContext":"first"}}', ""),
                               ("b", 0, "plain text note", ""), ("c", 0, "", "")], "UserPromptSubmit")
    h = json.loads(out)["hookSpecificOutput"]
    p = h["additionalContext"] == "first\nplain text note"
    print("   contexts joined:    %s (%r)" % ("PASS" if p else "FAIL", h.get("additionalContext"))); ok &= p
    code, out, err = HD.merge([("a", 0, "", ""), ("b", 0, "", "")], "PreToolUse")
    p = (code == 0 and out == "")
    print("   all silent -> silent:%s" % ("PASS" if p else "FAIL")); ok &= p
    # 28 Sep (interactive audit IA-20260928-07): a Stop hook's top-level block must leave at the top. shape() above
    # reads hookSpecificOutput only, so these two are checked on the merged JSON itself.
    code, out, err = HD.merge([("wt", 0, '{"decision":"block","reason":"keep going"}', "")], "Stop")
    o = json.loads(out) if out else {}
    p = (code == 0 and o.get("decision") == "block" and o.get("reason") == "keep going" and "hookSpecificOutput" not in o)
    print("   Stop block top-level:%s (%s)" % ("PASS" if p else "FAIL", out[:80])); ok &= p
    code, out, err = HD.merge([("go", 0, '{"continue":true}', ""),
                               ("halt", 0, '{"continue":false,"stopReason":"owner stop"}', "")], "Stop")
    o = json.loads(out) if out else {}
    p = (o.get("continue") is False and o.get("stopReason") == "owner stop")
    print("   continue:false wins: %s (%s)" % ("PASS" if p else "FAIL", out[:80])); ok &= p
    code, out, err = HD.merge([("a", 1, "", "boom"), ("b", 0, '{"hookSpecificOutput":{"additionalContext":"ok"}}', "")], "PreToolUse")
    p = (code == 0 and "ok" in out)
    print("   crash is non-blocking:%s" % ("PASS" if p else "FAIL")); ok &= p
    bad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ctl_bad_hook.py")
    open(bad, "w", encoding="utf-8").write("import sys\nsys.stdout.write('before')\nraise RuntimeError('boom')\n")
    rc, out, err = HD.run_in_process(bad, "{}")
    p = (rc == 1 and "boom" in err)
    print("   raising hook caught: %s (rc=%s)" % ("PASS" if p else "FAIL", rc)); ok &= p
    open(bad, "w", encoding="utf-8").write("import sys\nsys.exit(2)\n")
    rc, out, err = HD.run_in_process(bad, "{}")
    p = (rc == 2)
    print("   sys.exit(2) caught:  %s (rc=%s)" % ("PASS" if p else "FAIL", rc)); ok &= p
    open(bad, "w", encoding="utf-8").write("import os\nos.environ['CTL_LEAK']='1'\nimport sys\nsys.path.insert(0,'C:/ctl-leak')\n")
    HD.run_in_process(bad, "{}")
    p = ("CTL_LEAK" not in os.environ and "C:/ctl-leak" not in sys.path)
    print("   env/path restored:   %s" % ("PASS" if p else "FAIL")); ok &= p
    os.remove(bad)
    return ok


def control_c():
    print("== C. COST on the same payload, same minute")
    saved = []
    for event, tool in [("PreToolUse", "Bash"), ("PostToolUse", "Bash")]:
        entries = HD.hook_entries(event, tool)
        t = time.perf_counter()
        for cmd, args in entries:
            run_sub(cmd, args, payload(event, tool, "ctlC1-" + uuid.uuid4().hex[:8]))
        one_by_one = time.perf_counter() - t
        t = time.perf_counter()
        HD.dispatch(event, payload(event, tool, "ctlC2-" + uuid.uuid4().hex[:8]), tool)
        dispatched = time.perf_counter() - t
        print("   %-12s %2d hooks: separate %6.2fs -> dispatched %6.2fs  (%.1fx)" % (event, len(entries), one_by_one, dispatched, one_by_one / max(dispatched, 1e-6)))
        saved.append(one_by_one - dispatched)
    return sum(saved) > 0


if __name__ == "__main__":
    a, passed = control_a(); b = control_b(); c = control_c()
    print("\nRESULT: equivalence %s | blocking %s | cost %s" % ("PASS" if a else "FAIL", "PASS" if b else "FAIL", "PASS" if c else "FAIL"))
    ok = a and b and c
    if ok:
        import time as _t
        json.dump({"written_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
                   "dispatcher_mtime": os.path.getmtime(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_dispatch.py")),
                   "in_process_ok": sorted(passed)},
                  open(HD.ALLOW, "w", encoding="utf-8"), indent=1)
        print("allowlist written: %d hooks earned the in-process path -> %s" % (len(passed), HD.ALLOW))
        print("  " + ", ".join(sorted(passed)))
    else:
        try:
            os.remove(HD.ALLOW); print("controls failed - allowlist REMOVED, every hook falls back to its own process")
        except FileNotFoundError:
            print("controls failed - no allowlist exists, every hook runs in its own process")
    sys.exit(0 if ok else 1)
