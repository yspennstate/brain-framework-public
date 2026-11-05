"""test_orders_channel.py - controls for the membrane's standing-orders channel (27 Sep, claude-825b1305).

    python test_orders_channel.py [PATH_TO_live_membrane.py]      (default: the installed one in bin)

Against the live sidecar and his memory files (read only; nothing is written outside a temp dir):
 1 a superseded order resolves to the end of its chain (his GPU orders are chained two deep)
 2 no candidate the channel returns is itself superseded, whatever the query
 3 order_facts reads the order's own date and the parts later overtaken
 4 the served text names the date, the overtaken parts, and a newer order on the same ground when one ranks close
 5 decide(): nothing is served with serve_orders off
 6 decide(): with it on, a leader that has led once is not served; twice, above the bar, it is (kind 'order')
 7 decide(): below the bar, or in a headless session, nothing is served
 8 decide(): the same order is not served again inside the refractory, once when it has passed, and a memory the
   session has read is not a candidate
"""
import collections
import importlib.util
import os
import sys

BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BIN, "live_membrane.py")
sys.path.insert(0, BIN)
spec = importlib.util.spec_from_file_location("live_membrane", PATH)
LM = importlib.util.module_from_spec(spec)
sys.modules["live_membrane"] = LM
spec.loader.exec_module(LM)

FAILS = []


def run(name, fn):
    try:
        ok = bool(fn())
    except Exception as e:  # a control that raises fails
        ok = False
        name += "  (%s: %s)" % (type(e).__name__, e)
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        FAILS.append(name)


lv = LM.Live(delta=None)
GPU_Q = "gpu not being used render movies blender nvenc ffmpeg"
CHAINED = "owner_allows_gpu_movies_before_restart_2026_09_05"


def chain_end():
    oc = LM.order_candidates(CHAINED.replace("_", " "), set(), lv.db, topn=12)
    got = [c for c in oc if c.get("via") == CHAINED]
    if not got:
        return False
    row = lv.db.execute("SELECT superseded_by FROM memory WHERE slug=?", (got[0]["slug"],)).fetchone()
    return got[0]["slug"] != "owner_order_use_80_percent_of_the_gpu_everyone_2026_09_05" and row and not row[0]


def none_superseded():
    for q in (GPU_Q, "pictures stock footage codex slides film", "voice narration calm speechify benjamin",
              "youtube thumbnail upload playlist", "mesh claim git push brain memory", "box simulation law engine"):
        for c in LM.order_candidates(q, set(), lv.db, topn=6):
            row = lv.db.execute("SELECT superseded_by FROM memory WHERE slug=?", (c["slug"],)).fetchone()
            if not row or row[0]:
                print("   superseded candidate:", c["slug"])
                return False
    return True


def facts():
    created, parts = LM.order_facts("owner_law_no_dnn_training_on_this_laptop_gpu_for_movies_only_2026_09_06")
    return created == "2026-09-06" and any("gpu_rendering_stopped" in p for p in parts)


def text():
    oc = [{"slug": "owner_law_no_dnn_training_on_this_laptop_gpu_for_movies_only_2026_09_06", "score": 40.0,
           "descr": "x " * 300, "via": ""},
          {"slug": "owner_law_render_everything_locally_2026_09_23", "score": 30.0, "descr": "y", "via": ""}]
    t = LM.order_line(oc)
    far = LM.order_line([oc[0], dict(oc[1], score=10.0)])
    return ("(2026-09-06)" in t and "parts later overtaken" in t and "gpu_rendering_stopped" in t
            and "newer on the same ground (2026-09-23)" in t and "newer on the same ground" not in far
            and " ..." in t)


NOW = 1790530000.0
OC = [{"slug": "owner_law_no_dnn_training_on_this_laptop_gpu_for_movies_only_2026_09_06", "score": 40.0,
       "descr": "no DNN training on this laptop's GPU", "via": ""}]


def pol(on=True, bar=30.0):
    p = LM.policy()
    p.update({"serve_attr": False, "serve_err": False, "serve_place": False, "serve_word": False, "serve_mem": False,
              "serve_verdict": False, "tired": [], "serve_orders": on, "orders_min": bar, "orders_stable": 2,
              "max_fires_per_hour": 99, "hold_every": 0})
    return p


def fresh_state():
    return {"sid": "test-orders", "seen": [], "served": {}, "fire_times": []}


def evs(n, ep="cli", oc=OC):
    return [{"orders": [dict(c) for c in oc], "ep": ep} for _ in range(n)]


def served_kinds(ev_list, st, p, t0=NOW):
    out = []
    for i, ev in enumerate(ev_list):
        _lines, served = LM.decide(ev, t0 + 60 * i, st, p)
        out.append([s[0] for s in served])
    return out


def off():
    return served_kinds(evs(3), fresh_state(), pol(on=False)) == [[], [], []]


def stable():
    k = served_kinds(evs(2), fresh_state(), pol())
    return k[0] == [] and k[1] == ["order"]


def bar_and_headless():
    a = served_kinds(evs(3), fresh_state(), pol(bar=50.0))
    b = served_kinds(evs(3, ep="sdk-cli"), fresh_state(), pol())
    return a == [[], [], []] and b == [[], [], []]


def refractory_and_seen():
    st = fresh_state()
    k = served_kinds(evs(4), st, pol())
    again = served_kinds(evs(2), st, pol(), t0=NOW + 7200.0 + 400)
    st2 = fresh_state()
    st2["seen"] = [OC[0]["slug"]]
    oc_seen = LM.order_candidates(GPU_Q, set(st2["seen"]), lv.db)
    return (k == [[], ["order"], [], []] and again == [["order"], []]
            and all(c["slug"] != OC[0]["slug"] for c in oc_seen))


def hold():
    """One order line in four is held back per session and two-hour block (logged as 'held', no text); his laws are never
    held. Since 28 Sep (account03/B08 R12, B08-R12-HOLD-PARITY-010) a held line spends the hourly budget a shown line
    would, so both arms leave the same fire times; before that this control asserted the opposite."""
    if not hasattr(LM, "held_back"):
        return False
    p = pol()
    p["hold_every"] = 4
    oc = [{"slug": "owner_order_more_codex_pictures_twenty_films_a_day_2026_09_26", "score": 40.0, "descr": "x",
           "via": ""}]
    kinds = collections.Counter()
    budget_ok = True
    for i in range(200):
        st = {"sid": "hold-test-%d" % i, "seen": [], "served": {}, "fire_times": []}
        for k in range(2):
            lines, served = LM.decide({"orders": [dict(c) for c in oc], "ep": "cli"}, NOW + 60 * k, st, p)
        kinds[tuple(sv[0] for sv in served)] += 1
        if any(sv[0] == "held" for sv in served) and (lines or not st.get("fire_times")):
            budget_ok = False
    held = kinds[("held",)]
    law = [{"slug": "owner_law_no_dnn_training_on_this_laptop_gpu_for_movies_only_2026_09_06", "score": 40.0,
            "descr": "x", "via": ""}]
    law_held = 0
    for i in range(200):
        st = {"sid": "law-test-%d" % i, "seen": [], "served": {}, "fire_times": []}
        for k in range(2):
            _l, served = LM.decide({"orders": [dict(c) for c in law], "ep": "cli"}, NOW + 60 * k, st, p)
        law_held += any(sv[0] == "held" for sv in served)
    print("   held %d of 200 (shown %d); laws held %d of 200" % (held, kinds[("order",)], law_held))
    return 30 <= held <= 70 and kinds[("order",)] + held == 200 and law_held == 0 and budget_ok


def in_force():
    """A superseded order the session just read or recalled: one line naming the order in force, once per old order in
    the refractory, interactive sessions only, never held."""
    if not hasattr(LM, "stale_orders"):
        return False
    LM._LV_DB["db"] = lv.db
    items = [("mem:owner_order_use_80_percent_of_the_gpu_everyone_2026_09_05", 30.0), ("file:~/x.py", 5.0),
             ("mem:owner_law_render_everything_locally_2026_09_23", 10.0)]
    st = LM.stale_orders(items, lv.db)
    p = pol(on=False)
    p["hold_every"] = 4
    state = fresh_state()
    lines, served = LM.decide({"stale": st, "ep": "cli", "orders": []}, NOW, state, p)
    again, _ = LM.decide({"stale": st, "ep": "cli", "orders": []}, NOW + 60, state, p)
    headless, _ = LM.decide({"stale": st, "ep": "sdk-cli", "orders": []}, NOW, fresh_state(), p)
    none_, _ = LM.decide({"stale": LM.stale_orders(items[1:], lv.db), "ep": "cli", "orders": []}, NOW, fresh_state(), p)
    return (len(st) == 1 and st[0][1] == "owner_law_no_dnn_training_on_this_laptop_gpu_for_movies_only_2026_09_06"
            and len(lines) == 1 and "the order in force is" in lines[0] and "gpu_rendering_stopped" in lines[0]
            and served[0][0] == "successor" and again == [] and headless == [] and none_ == [])


run("1 a superseded order resolves to the end of its chain", chain_end)
run("2 no candidate is itself superseded", none_superseded)
run("3 order_facts reads the date and the parts overtaken", facts)
run("4 the served text: date, overtaken parts, a newer order when close, cut at a word", text)
run("5 decide(): nothing served with serve_orders off", off)
run("6 decide(): served on the second evaluation led, not the first", stable)
run("7 decide(): nothing below the bar or in a headless session", bar_and_headless)
run("8 decide(): refractory holds, and a read order is not a candidate", refractory_and_seen)
run("9 decide(): one order line in four held back (no text, the budget a shown line spends), laws never", hold)
run("10 decide(): a superseded order just read gets the order in force, once, interactive only", in_force)
print("%d of 10 controls pass (%s)" % (10 - len(FAILS), PATH))
sys.exit(1 if FAILS else 0)
