#!/usr/bin/env python3
"""sourced_memory.py - provenance tagging on memory writes (Johnson, Hashtroudi & Lindsay 1993, the
Source-Monitoring Framework).

The cross-session form of the reality-monitoring failure: a figure written into a memory with no note of
HOW it's known becomes un-attributable later - a future session re-reads a simulated or assumed number as
a measured fact. The confusable pair here is exactly measured-vs-modeled. This fires only when a memory
write asserts an empirical figure (a %, a Sharpe, an 'x RV', a drawdown, a dollar amount, a percentile)
and carries NO provenance marker, and nudges: tag how you know it. Advisory, deduped once per session -
most memory writes already say 'measured/sim/verified', so it stays quiet and speaks on the unsourced one.

PreToolUse on Write|Edit|MultiEdit, memory dir only. Fail-safe silent.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".sourced_fired.json")

# an empirical figure - the kind whose provenance matters
QUANT = re.compile(r"\d+(\.\d+)?\s*%|\bsharpe\b|\d+(\.\d+)?\s*x\b|x\s*RV\b|\bdrawdown\b|\bmax ?dd\b|\$\s?\d|"
                   r"\bbps\b|\bpercentile\b|\bp\d{2}\b|\bcarry\b|\breturns?\b\s*(of\s*)?\d|\bwin[- ]?rate\b", re.I)
# any of these means the figure is sourced (how it's known is stated)
PROV = re.compile(r"measured|observed|simulat|modell?ed|\bmodel\b|assumed|owner (said|states|told|corrected)|"
                  r"from the tape|on the box|\blive\b|backtest|verified|reproduc|his book|lived|per the|"
                  r"according to|estimat|\bguess\b|hand-?tuned|OOS\b|in[- ]?sample|\bREADME\b", re.I)


def _fresh(sess):
    if not sess:
        return True
    now = time.time()
    try:
        st = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    except Exception:
        st = {}
    st = {k: v for k, v in st.items() if now - v < 86400}
    if now - st.get(sess, 0) < 300:
        return False
    st[sess] = now
    try:
        # atomic replace (2026-09-02): same lost-update race as the reflex hook's shared backoff file
        _tmp = STATE + ".%d.tmp" % os.getpid()
        with io.open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(st, _f)
        os.replace(_tmp, STATE)
    except Exception:
        pass
    return True


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        if data.get("hook_event_name") != "PreToolUse":
            return
        ti = data.get("tool_input", {}) or {}
        path = (ti.get("file_path") or ti.get("path") or "").replace("\\", "/").lower()
        if "/memory/" not in path or not path.endswith(".md"):
            return
        # the text being written (Write.content / Edit.new_string / MultiEdit edits)
        text = ti.get("content") or ti.get("new_string") or ""
        if not text and isinstance(ti.get("edits"), list):
            text = " ".join(e.get("new_string", "") for e in ti["edits"] if isinstance(e, dict))
        if not text or not QUANT.search(text):
            return
        if PROV.search(text):
            return  # the figure is already sourced - fine
        if not _fresh(data.get("session_id") or ""):
            return
        ctx = ("[source-tag] this memory asserts a figure with no provenance marker - a future session "
               "won't be able to tell MEASURED from MODELED from ASSUMED, and an unsourced number is how a "
               "sim value gets re-read as fact. Tag how you know it (measured on the box / simulated / "
               "owner-stated / from the tape / assumed).")
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}   # a note, not an approval
        sys.stdout.write(json.dumps(out))
    except Exception:
        return


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
