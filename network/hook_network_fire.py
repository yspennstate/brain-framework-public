"""Hook adapter: feed session events into the firing engine; log completions.

SHADOW MODE by default: completions are appended to network_shadow.tsv next to the
db, never injected, until the replay eval earns live mode (create the LIVE flag file
beside the db to inject). Wire as PostToolUse (tool events) and UserPromptSubmit
(prompt keywords) with the db path in NETWORK_DB.

Fail-safe: any error exits 0 silently - a retrieval layer must never break the turn.
"""
import io, json, os, sys, time


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        db = os.environ.get("NETWORK_DB", os.path.join(
            os.path.expanduser("~"), ".claude", "dreaming", "network", "net.sqlite3"))
        if not os.path.exists(db):
            return 0
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        from netstore import NetStore
        from fire import FireEngine
        import build_graph as bg

        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        try:
            evt = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            evt = {}

        nodes = []
        tool = evt.get("tool_name")
        if tool:
            nodes.append(f"tool:{tool}")
        text = ""
        ti = evt.get("tool_input")
        if isinstance(ti, dict):
            text = " ".join(str(v)[:400] for v in ti.values() if isinstance(v, str))
        text += " " + str(evt.get("prompt", ""))[:2000]
        for t in bg.tokenize(text):
            nodes.append(f"kw:{t}")
        if not nodes:
            return 0

        # engine state persists across hook invocations via a tiny json sidecar
        state_p = db + ".buffer.json"
        st = NetStore(db)
        eng = FireEngine(st)
        try:
            s = json.load(io.open(state_p, encoding="utf-8"))
            eng.act.update(s.get("act", {}))
            eng.direct.update(s.get("direct", {}))
            eng.last_fired.update(s.get("last_fired", {}))
        except Exception:
            pass
        eng.observe(nodes)
        hits = eng.complete()
        json.dump({"act": dict(eng.act), "direct": dict(eng.direct),
                   "last_fired": eng.last_fired},
                  io.open(state_p, "w", encoding="utf-8"))
        if hits:
            led = os.path.join(os.path.dirname(db), "network_shadow.tsv")
            with io.open(led, "a", encoding="utf-8", newline="\n") as fh:
                for pn, score, k in hits:
                    fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{pn}\t{score}\t{k}\n")
            if os.path.exists(os.path.join(os.path.dirname(db), "LIVE")):
                out = {"hookSpecificOutput": {
                    "hookEventName": evt.get("hook_event_name", "PostToolUse"),
                    "additionalContext":
                        "[network] the present resembles a stored episode; past prompts that "
                        "followed this pattern: " + ", ".join(p for p, _, _ in hits)}}
                print(json.dumps(out))
        st.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
