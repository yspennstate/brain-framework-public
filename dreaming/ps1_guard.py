#!/usr/bin/env python3
"""ps1_guard.py - PostToolUse machinery for the .ps1 parse-validate rule.

heed_audit (2026-07-15) measured the ps1_hook_edit reflex as the fleet's most read-past
warning: fired 185 times in 4 days, followed by an actual ParseFile check only 12% of the
time (control 7%). The owner's law: a rule that must always hold is a spec for MACHINERY,
not a promise to be careful - so this hook runs the parse itself after every Edit/Write
that touches a .ps1/.psm1 file and injects a LOUD context line only when the file fails
to parse or carries non-ASCII in a string-literal-bearing file (the exact 07-08 scar that
stopped sessions waking).

Fail-open by construction (the instinct_prime discipline): any internal error -> exit 0,
no output, never block or slow the chain. Cost only on .ps1 edits (rare), one powershell
-NoProfile invocation. Silent on a clean parse - no nag, no token cost.

Selftest: python ps1_guard.py --selftest (writes temp .ps1 files, asserts loud-on-broken
and silent-on-clean).
"""
import json
import os
import subprocess
import sys
import tempfile


def _check(path):
    """Return (parse_errors, non_ascii_count) for a PowerShell file."""
    ps = (
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile(%r,[ref]$tokens,[ref]$errors)|Out-Null;"
        "$bad=@([System.IO.File]::ReadAllText(%r).ToCharArray()|Where-Object{[int]$_ -gt 127}).Count;"
        "Write-Output (\"$($errors.Count)|$bad\")" % (path, path)
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    out = (r.stdout or "").strip().splitlines()
    line = out[-1] if out else "0|0"
    n_err, n_bad = (line.split("|") + ["0"])[:2]
    return int(n_err or 0), int(n_bad or 0)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        ti = data.get("tool_input", {}) or {}
        path = ti.get("file_path", "") or ""
        if not path.lower().endswith((".ps1", ".psm1", ".psd1")):
            return
        if not os.path.exists(path):
            return
        n_err, n_bad = _check(path)
        if n_err == 0 and n_bad == 0:
            return  # clean parse, pure ASCII - silent
        msgs = []
        if n_err:
            msgs.append("%d PARSE ERROR(S)" % n_err)
        if n_bad:
            msgs.append("%d non-ASCII char(s)" % n_bad)
        ctx = ("[ps1_guard] %s in %s - a broken/mojibake .ps1 on the hook path can stop "
               "sessions waking into the brain (the 07-08 scar). Fix it NOW and re-run: "
               "powershell [Parser]::ParseFile validation. This is machinery for the "
               "ps1_hook_edit rule (measured 12%% manual heed - hence the hook)."
               % (" + ".join(msgs), os.path.basename(path)))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": ctx}}))
    except Exception:
        return  # fail-open, always


def _selftest():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        good = os.path.join(d, "good.ps1")
        bad = os.path.join(d, "bad.ps1")
        open(good, "w", encoding="ascii").write('Write-Output "hello"\n')
        open(bad, "w", encoding="utf-8").write('Write-Output "unterminated\n')
        for path, expect_loud in ((good, False), (bad, True)):
            payload = json.dumps({"tool_input": {"file_path": path}})
            r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                               input=payload, capture_output=True, text=True, timeout=60)
            loud = bool(r.stdout.strip())
            print("%s -> %s (expected %s)" % (os.path.basename(path),
                                              "loud" if loud else "silent",
                                              "loud" if expect_loud else "silent"))
            ok = ok and (loud == expect_loud)
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    main()
