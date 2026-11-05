"""One measured turn of the network-science development program, packaged as a single
command so the program self-runs (each dream, or on demand) instead of being a manual
sequence. It grows the network, measures the three signals the owner named, mines the
cascade rules, and appends a dated receipt to dev_loop.log - literal units kept separate,
no promotion of anything to live (that stays a deliberate, guarded step).

  python dev_loop_turn.py            # runs against the installed live network

Read-mostly and cheap: grow folds only new events; the audits parse logs; cascade mining
is a few seconds. Runs at BelowNormal. It never edits the live reflex cortex or settings -
it MEASURES and RECORDS, so a human (or a later guarded turn) decides what to wire.
"""
import io, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv=None):
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass

    home = os.path.expanduser("~")
    net = os.path.join(home, ".claude", "dreaming", "network")
    db = os.path.join(net, "net.sqlite3")
    log = os.path.join(net, "dev_loop.log")
    lines = []

    def rec(s):
        lines.append(s)

    rec("=" * 60)
    rec(f"dev-loop turn (network science program)  [{time.strftime('%Y-%m-%d %H:%M')}]")

    # 1. GROW - fold the day's new tools/prompts/keywords/memories
    try:
        import dream_grow
        # capture grow's stdout line
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dream_grow.main(["--home", home, "--db", db])
        rec("GROW: " + (buf.getvalue().strip().splitlines() or ["(no output)"])[-1])
    except Exception as e:
        rec(f"GROW: error {e}")

    from netstore import NetStore
    try:
        st = NetStore(db)
        n = st.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        e = st.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        by = dict(st.db.execute("SELECT kind, COUNT(*) FROM nodes GROUP BY kind"))
        st.close()
        rec(f"NETWORK: {n} nodes ({by}), {e} edges")
    except Exception as e:
        rec(f"NETWORK: error {e}")

    # 2. MEASURE firing sequences
    fire_log = os.path.join(home, ".claude", "dreaming", "graph_fire", "gf_shadow_fires.tsv")
    try:
        import firing_audit
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            firing_audit.main([fire_log])
        out = buf.getvalue().splitlines()
        top = next((l for l in out if "of fires)" in l and "<--" in l), "")
        rec("FIRING: " + (out[0] if out else "") + ("  top-overfire:" + top.strip() if top else ""))
    except Exception as e:
        rec(f"FIRING: error {e}")

    # 3. MINE cascades + persist for the (shadow) live hook
    try:
        import cascade_rules
        sets = cascade_rules.load_firing_sets(fire_log)
        rules, ns = cascade_rules.mine_rules(sets)
        cascade_rules.save_rules(rules, os.path.join(net, "cascade_rules.json"))
        strong = [r for r in rules if r[1] >= 10.0]   # high-lift = genuine cascades
        rec(f"CASCADES: {len(rules)} rules over {ns} sets; {len(strong)} high-lift (>=10x). "
            + (f"top: {{{', '.join(strong[0][3])}}} -> {strong[0][4]} ({strong[0][1]:.0f}x)"
               if strong else "no strong cascade"))
    except Exception as e:
        rec(f"CASCADES: error {e}")

    # 4. RECALL spot-check (does topic seeding still surface relevant memories)
    try:
        from fire import FireEngine
        st = NetStore(db)
        hits = FireEngine(st).recall_memories(["kw:option", "kw:gamma", "kw:put"],
                                              top=3, min_anchors=2)
        st.close()
        rec("RECALL(option/gamma/put): "
            + (", ".join(h[0][4:] for h in hits) if hits else "(none)"))
    except Exception as e:
        rec(f"RECALL: error {e}")

    rec("(nothing promoted live this turn - measurement only)")
    block = "\n".join(lines) + "\n"
    with io.open(log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(block)
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
