#!/usr/bin/env python3
"""referee_suite.py - one command that re-asserts the firing-stack + crash-recovery INVARIANTS this session
validated, so a regression (like the 07-11 _DISCUSS re-wire that quietly opened a 3/5 false-negative hole in
pre_report and was caught only by luck) fails LOUD instead of silently. Read-only; MEASURE + PASS/FAIL.

Run on demand, or wire into the nightly (a suite going red = something regressed a safety guard).
Usage: python referee_suite.py
"""
import io, json, subprocess, sys, os

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
GF = os.path.join(HERE, "graph_fire")
PY = sys.executable
sys.path.insert(0, GF)
sys.path.insert(0, HERE)

results = []  # (name, passed, detail)


def rec(name, passed, detail=""):
    results.append((name, passed, detail))


# --- INVARIANT 1: shared contract blocks durable contradictions, never a stale remembered P&L rate ---
# Numeric book observations are frame/regime dependent and are not present-tense oracles. The durable
# owner-confirmed signs remain enforceable: his current strategy is profitable and its sides are fixed,
# and a daily-reset leveraged ETF is not a clean linear multiple.
try:
    import owner_report_contract as contract
    DURABLE = [
        ("His current strategy loses money every year.", "profitable_lived_sign"),
        ("He should sell puts and collect premium.", "fixed_sides_only"),
        ("LEV3S carry is just the expense ratio and negligible because it is a linear -3x fund.",
         "leveraged_etf_path"),
    ]
    blocked = sum(1 for text, code in DURABLE if contract.check(text).get("code") == code)
    rec("owner contract: durable owner-confirmed contradictions block",
        blocked == len(DURABLE), "%d/%d blocked" % (blocked, len(DURABLE)))
    durable_discuss = [
        ("Setting the withdrawn anchor aside, his current strategy loses money every year.",
         "profitable_lived_sign"),
        ("Per the old memory, he should sell puts and collect premium.", "fixed_sides_only"),
        ("Ignore the retracted note; LEV3S is linear -3x and its carry is just a negligible expense ratio.",
         "leveraged_etf_path"),
    ]
    discuss_blocks = sum(
        1 for text, code in durable_discuss if contract.check(text).get("code") == code
    )
    rec("owner contract: discuss/retract vocabulary cannot suppress durable contradictions",
        discuss_blocks == len(durable_discuss), "%d/%d blocked" % (discuss_blocks, len(durable_discuss)))
    retired = [
        "The grid backtest earns $180k per year.",
        "The grid backtest makes $1.9M per year.",
        "Account7 makes $40k per month.",
    ]
    stale_blocks = sum(1 for text in retired if contract.check(text))
    rec("owner contract: remembered P&L rates are not blocking oracles", stale_blocks == 0,
        "%d/%d stale rates blocked (want 0)" % (stale_blocks, len(retired)))
except Exception as e:
    rec("owner contract checks", False, "EXCEPTION: %r" % e)


def _sub(argv, timeout=90):
    try:
        p = subprocess.run([PY] + argv, cwd=HERE, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return "EXC:%r" % e


# --- INVARIANT 2: all wired Stop hooks fail-safe on malformed input ---
# 300 s, not the default 90 (2026-10-29, Mac): each hook start inside is already capped at 20 s and a hang shows as its
# own TIMEOUT row; the outer cap only measured load. At nice 10 on a box at load 30 this call took 123 s (and 1,295 s
# once) while the same run un-niced took 4 s, so the invariant read as broken with every hook fail-safe.
out = _sub(["referee_hook_failsafe.py"], timeout=300)
rec("all wired Stop hooks fail-safe on garbage input",
    "ALL wired Stop hooks are fail-safe" in out, out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 3: registry checkpoint-freshness canary fires on a real stale checkpoint (guard proves itself) ---
out = _sub(["registry_freshness_canary.py", "--selftest"])
rec("registry canary selftest (proves the guard fires on a stale checkpoint)",
    "PASS: registry_freshness_canary selftest" in out, out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 4: registry_refresh selftest (BOM-tolerant read still holds) ---
out = _sub(["registry_refresh.py", "--selftest"])
rec("registry_refresh selftest (utf-8-sig BOM read holds)",
    "PASS: registry_refresh selftest" in out, out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 5: independent Codex warning lifecycle contract rejects known-dead configurations ---
# The contract validates Windows Codex wiring (commandWindows, C:\Python314, ~/.brain-framework/runtime). On the Mac
# its own known-good baseline fails before any negative case runs, so it read as a regressed guard every run (29-30
# Oct). It is SKIPPED off Windows, not passed: the Mac's Codex hooks are a separate gap the wake check reports.
if os.name != "nt":
    results.append(("Codex warning hook contract selftest (known-dead wiring is rejected)", None,
                    "SKIPPED off Windows: the contract checks Windows Codex wiring; this machine has no Codex brain "
                    "hooks wired (~/.codex/hooks.json %s)" % ("present" if os.path.exists(
                        os.path.expanduser("~/.codex/hooks.json")) else "missing")))
else:
    out = _sub(["codex_warning_acceptance.py", "--selftest"])
    rec("Codex warning hook contract selftest (known-dead wiring is rejected)",
        "PASS: codex_warning_acceptance selftest" in out,
        out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 6: shared outgoing-prose contract blocks only durable, non-numeric owner invariants ---
out = _sub(["owner_report_contract.py", "--selftest"])
rec("shared owner-report contract selftest (Codex/headless use one durable gate)",
    "owner_report_contract selftest: PASS" in out,
    out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 7: stream_integrity active/frozen classifier holds (the cry-wolf guard added 2026-07-12) ---
# A checker that can't tell active corruption from frozen residue cries wolf; regression-guard the classifier.
out = _sub(["stream_integrity.py", "--selftest"])
rec("stream_integrity active/frozen selftest (cry-wolf classifier holds)",
    "stream_integrity _classify selftest: OK" in out, out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 8: exact owner operators survive intake without persisting owner prose ---
out = _sub(["exact_clauses.py"])
rec("exact owner-clause intake selftest (ONLY/NOT/count/role/paired-leg + privacy)",
    "exact_clauses selftest: PASS" in out,
    out.strip().splitlines()[-1][:80] if out.strip() else "(no output)")

# --- INVARIANT 9: a normal reflex co-fire cannot put exact owner prose back into FIRELOG ---
try:
    import instinct_prime
    original = {"prompt": "Only deploy website; never commit.", "session_id": "12345678-test"}
    safe = instinct_prime._firelog_data("UserPromptSubmit", original, "[exact-owner-clauses]")
    preserved = instinct_prime._firelog_data("UserPromptSubmit", original, "")
    rec("exact-clause co-fire redacts owner prose before FIRELOG",
        safe is not original
        and safe.get("prompt") == "<exact-owner-clause-prompt-redacted>"
        and safe.get("session_id") == original["session_id"]
        and original["prompt"] == "Only deploy website; never commit."
        and preserved is original,
        "metadata preserved; no-clause path is unchanged")
except Exception as e:
    rec("exact-clause co-fire redacts owner prose before FIRELOG", False, "EXCEPTION: %r" % e)

# --- INVARIANT 10: unrelated lexical-reflex corruption cannot erase an exact intake advisory ---
try:
    old_load, old_in, old_out = instinct_prime._load, sys.stdin, sys.stdout
    captured = io.StringIO()

    def _broken_load():
        raise OSError("synthetic missing instincts.tsv")

    instinct_prime._load = _broken_load
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit",
                                        "session_id": "exact-isolation",
                                        "prompt": "Only deploy website."}))
    sys.stdout = captured
    instinct_prime.main()
    isolated = captured.getvalue()
    rec("exact-clause intake survives missing/corrupt ordinary reflex data",
        "exact-owner-clauses" in isolated and "Only deploy website" not in isolated,
        "advisory survived synthetic TSV failure")
except Exception as e:
    rec("exact-clause intake survives missing/corrupt ordinary reflex data", False, "EXCEPTION: %r" % e)
finally:
    try:
        instinct_prime._load, sys.stdin, sys.stdout = old_load, old_in, old_out
    except Exception:
        pass

# --- report ---
print("=" * 72)
print(" REFEREE SUITE - firing-stack + crash-recovery invariants")
print("=" * 72)
checked = [r for r in results if r[1] is not None]   # passed=None is a skip: neither a pass nor a fail
npass = sum(1 for _, p, _ in checked if p)
nskip = len(results) - len(checked)
for name, passed, detail in results:
    print("  %-4s %s" % ("SKIP" if passed is None else "PASS" if passed else "FAIL", name))
    if detail:
        print("        %s" % detail)
print("-" * 72)
print("%d/%d invariants hold%s." % (npass, len(checked), " (%d skipped on this machine)" % nskip if nskip else ""),
      "ALL GREEN." if npass == len(checked) else "!! A GUARD REGRESSED - investigate the FAIL(s) above.")
sys.exit(0 if npass == len(checked) else 1)
