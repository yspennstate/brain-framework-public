"""Controls for lead cards on long prompts (_lead_filter, _key_titled).

 L1 a short prompt keeps every card
 L2 on a long prompt a lead card the prompt does not name is dropped, one it names is kept, a result card is kept
 L3 a pinned lead card is kept; MATHBRAIN_LEAD_NAMED=0 keeps everything
 L4 a title that is its entry key in words is a lead; an ordinary title is not; the populations come from the setting
 L5 _main_body runs it before the generic-only test and silences an emptied block

    python test_lead_filter.py      (a few seconds; ALL PASS at the end)
"""
import importlib
import os
import sys
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    m = importlib.import_module("math_sense")
    df = {w: 1 for w in "model theory sunflower bound lemma exponential audit verdict pass".split()}
    m._store_df = lambda: df
    m._store_n = lambda: 1000
    return m


def hit(i, title, bundle="population.house", key=""):
    return NS(node=NS(id=i, title=title, body="", metadata={"bundle_id": bundle, "bundle_entry_key": key}))


LONG = ("Here is the referee report on the manuscript. " * 6 + "The sunflower lemma gives an exponential bound, "
        "and the second section repeats the argument with care, checking every constant along the way. ") * 2


def main():
    m = fresh({})
    lead = hit("w", "Model theory", "population.wikipedia-harvest.v1")
    named = hit("s", "Sunflower lemma", "population.wikipedia-harvest.v1")
    res = hit("r", "Exponential bound for set systems")
    check(m._lead_filter("Is model theory relevant here?", [lead, res]) == [lead, res], "L1 short prompt keeps all")
    got = m._lead_filter(LONG, [lead, named, res])
    check(lead not in got and named in got and res in got, "L2 unnamed lead dropped, named lead and result kept")
    check(lead in m._lead_filter(LONG, [lead, res], pinned={"w"}), "L3 pinned lead kept")
    m0 = fresh({"MATHBRAIN_LEAD_NAMED": "0"})
    check(m0._lead_filter(LONG, [lead, res]) == [lead, res], "L3 switch off")

    m = fresh({})
    kt = hit("k", "audit verdict pass", key="xx-audit-verdict-pass")
    check(m._key_titled(kt.node) and not m._key_titled(res.node), "L4 entry-key title is a lead")
    check(kt not in m._lead_filter(LONG, [kt, res]), "L4 entry-key lead dropped on a long prompt")
    m = fresh({"MATHBRAIN_LEAD_POPULATIONS": "population.other"})
    check(lead in m._lead_filter(LONG, [lead, res]) and m.LEAD_POPULATIONS == ("population.other",),
          "L4 populations from the setting")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    i = src.index('_silence_note(data, "lead_unnamed:%d" % len(chosen))')
    check("_kept = _lead_filter(prompt, chosen, pinned=set(pinned_ids or ()))" in src[i - 300:i]
          and src.index('_silence_note(data, "generic_only_match")') > i, "L5 wired before the generic-only test")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
