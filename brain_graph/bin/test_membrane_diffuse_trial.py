"""The diffuse-line trial (owner, 6 Oct 2026): memory and place lines are shown or held by held_back's hash, half and half
(hold_every_diffuse 2); a held line takes the slot a shown one would, prints nothing, is spent for the session, and his laws are
never held. Run: python test_membrane_diffuse_trial.py"""
import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_membrane as lm  # noqa: E402

fails = 0


def check(cond, what):
    global fails
    print(("PASS  " if cond else "FAIL  ") + what)
    fails += 0 if cond else 1


pol = dict(lm.DEFAULT_POLICY) if hasattr(lm, "DEFAULT_POLICY") else {}
pol.update(lm.load_policy() if hasattr(lm, "load_policy") else {})
pol.update({"serve_mem": True, "serve_place": True, "serve_err": False, "serve_attr": False, "serve_word": False,
            "serve_verdict": False, "serve_orders": False, "serve_successor": False, "hold_every": 4,
            "hold_every_diffuse": 2,
            "max_lines": 1, "max_fires_per_hour": 99, "refractory_s": 600, "mem_size_min": 0.0, "mem_hebb_min": 0.0,
            "mem_reach_min": 0.0, "place_hebb_min": 0.0, "mem_max_sessions": 99, "tired": []})
lm.served_elsewhere = lambda now: {}                 # no other sessions: the cross-session quota is not under test here
lm.precision_line = lambda kind: "unjudged"


def ev_mem(slug):
    return {"mem_all": [{"slug": slug, "size": 9.0, "hebb": 0.0, "reach": 0.0, "why": ["file:~/x.py", "dir:~/proj"],
                         "descr": "d"}], "place": []}


def ev_place(nm):
    return {"mem_all": [], "place": [[nm, 99.0, None, [(1.0, "file:~/x.py"), (1.0, "dir:~/proj")]]]}


now = time.time()
for label, make, prefix in (("memory", ev_mem, "mem:"), ("place", ev_place, "place:")):
    held = shown = 0
    for i in range(400):
        st = {"sid": "trial-%04d" % i}
        key = "some_memory_2026_10_01" if label == "memory" else "dir:~/somewhere/else"
        lines, served = lm.decide(make(key), now, st, pol)
        kinds = [s[0] for s in served]
        if "held" in kinds:
            held += 1
            check(not lines and served[0][1] == prefix + key, "%s: a held line prints nothing and logs %s" % (label, prefix + key)) if held == 1 else None
            check(st.get("served", {}).get(prefix + key) == now, "%s: a held line is spent (refractory) like a shown one" % label) if held == 1 else None
        elif lines:
            shown += 1
    check(150 <= held <= 250 and held + shown == 400, "%s: about half held (%d held, %d shown of 400)" % (label, held, shown))

# a held memory is added to the session's seen set, so the hold arm cannot show it later in the same session
seen = set()
lm.spend_served(seen, [("held", "mem:some_memory_2026_10_01", [])], {})
check("some_memory_2026_10_01" in seen, "a held memory is spent for the session (seen)")

# his laws are never held
law = "owner_law_something_2026_10_01"
never = all(lm.decide(ev_mem(law), now, {"sid": "law-%04d" % i}, pol)[0] for i in range(200))
check(never, "a memory line naming one of his laws is shown in every session, never held")

# switched off, nothing is held
off = dict(pol, hold_mem=False, hold_place=False)
none_held = all("held" not in [s[0] for s in lm.decide(ev_mem("some_memory_2026_10_01"), now, {"sid": "off-%04d" % i}, off)[1]]
                for i in range(200))
check(none_held, "hold_mem / hold_place False: no line is held")

print(("PASS" if not fails else "FAIL") + " - diffuse-line trial controls, %d failed" % fails)
sys.exit(1 if fails else 0)
