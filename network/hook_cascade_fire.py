"""Live cascade-completion hook (SHADOW by default).

When a reflex fires, this checks the mined cascade rules: if the recently-fired reflexes
complete a rule's antecedent, it surfaces the predicted high-value consequent - "if a
cascade fires in a certain way, the correct node fires." Unlike memory retrieval (which
needs a resident daemon for its 156ms episode load), cascade completion is cheap - a few
hundred rules loaded once, subset checks per fire - so it is safe on the per-fire path.

SHADOW by default: logs what it WOULD surface to cascade_shadow.tsv and injects nothing.
Create a LIVE flag file beside the rules to inject the completion into the turn. Fail-safe:
any error exits 0 - a completion hook must never break the turn.

Wire (once the owner activates it): a hook that receives the just-fired reflex names, or a
post-step over the firing shadow log. Rules come from `cascade_rules.py --save rules.json`,
refreshed each dream. State (the recent-fired window) persists in a tiny json sidecar.

  RULES: <dir>/cascade_rules.json     STATE: <dir>/cascade_state.json
  LIVE flag: <dir>/CASCADE_LIVE
"""
import io, json, os, sys, time

WINDOW_S = 120.0     # a reflex is "recently active" for this long
MIN_LIFT = 5.0       # only surface completions to rare consequents (informative)


def _load(path, default):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return default


def main(argv=None):
    _args = list(argv) if argv is not None else sys.argv[1:]
    if any(a in ("-h", "--help") for a in _args):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return 0
    try:
        d = os.environ.get("CASCADE_DIR", os.path.join(
            os.path.expanduser("~"), ".claude", "dreaming", "network"))
        rules_p = os.path.join(d, "cascade_rules.json")
        if not os.path.exists(rules_p):
            return 0
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        import cascade_rules as cr

        # the just-fired reflex names arrive as argv or comma-separated on stdin.
        # A reflex name is [a-z0-9_] only; strip every other char (a BOM, whitespace, CR)
        # from each token. Some shells (PowerShell pipes) prepend a UTF-8 BOM, which would
        # silently corrupt the first name and make the antecedent never match.
        import re as _re
        def clean(x):
            return "".join(_re.findall(r"[a-z0-9_]+", x.lower()))
        fired = [clean(x) for x in (argv or [])]
        fired = [x for x in fired if x]
        if not fired and not sys.stdin.isatty():
            fired = [clean(x) for x in sys.stdin.read().split(",")]
            fired = [x for x in fired if x]
        if not fired:
            return 0

        now = time.time()
        state_p = os.path.join(d, "cascade_state.json")
        recent = _load(state_p, {})           # reflex -> last-fired epoch
        for r in fired:
            recent[r] = now
        # keep only reflexes fired within the window
        active = {r for r, t in recent.items() if now - t <= WINDOW_S}
        recent = {r: t for r, t in recent.items() if now - t <= WINDOW_S}
        json.dump(recent, io.open(state_p, "w", encoding="utf-8"))

        comp = cr.CascadeCompleter(cr.load_rules(rules_p), min_lift=MIN_LIFT)
        hits = comp.complete(active, top=2)
        if not hits:
            return 0

        with io.open(os.path.join(d, "cascade_shadow.tsv"), "a",
                     encoding="utf-8", newline="\n") as fh:
            for cons, conf, lift in hits:
                fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{cons}\t{conf:.2f}\t{lift:.1f}"
                         f"\t{','.join(sorted(active))}\n")
        if os.path.exists(os.path.join(d, "CASCADE_LIVE")):
            names = ", ".join(c for c, _, _ in hits)
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext":
                    "[cascade] the reflexes now active have historically completed to: "
                    + names + " - consider whether that reflex applies here too."}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
