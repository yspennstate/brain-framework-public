"""Controls for _refusal_kwargs: the math hook refuses a whole block when the integrity read refuses any selected card.

 K1 over the installed math_brain (which lists "all" in ON_REFUSAL) it asks for on_refusal="all"
 K2 a package with only a strict parameter gets strict=True; a package with neither gets None
 K3 every consistent_hits call in math_sense passes the keyword, and a None refuses with integrity_api_incompatible
 K4 with on_refusal="all", a refused later card empties the block instead of serving the rest

    python test_refusal_kwargs.py      (a second; ALL PASS at the end)
"""
import importlib
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh():
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    m = fresh()
    check(m._refusal_kwargs() == {"on_refusal": "all"}, "K1 installed package: on_refusal='all'")

    import math_brain
    real = sys.modules["math_brain.selection_integrity"]
    try:
        old = types.ModuleType("math_brain.selection_integrity")
        def strict_form(hits, database, *, deadline, strict=False):
            return hits
        old.consistent_hits = strict_form
        sys.modules["math_brain.selection_integrity"] = old
        math_brain.selection_integrity = old
        check(m._refusal_kwargs() == {"strict": True}, "K2 strict-only package: strict=True")
        bare = types.ModuleType("math_brain.selection_integrity")
        def bare_form(hits, database, *, deadline):
            return hits
        bare.consistent_hits = bare_form
        sys.modules["math_brain.selection_integrity"] = bare
        math_brain.selection_integrity = bare
        check(m._refusal_kwargs() is None, "K2 package with neither form: None")
    finally:
        sys.modules["math_brain.selection_integrity"] = real
        math_brain.selection_integrity = real

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    calls = re.findall(r"consistent_hits\((?:[^()]|\([^()]*\))*\)", src)
    calls = [c for c in calls if "STORE_PATH" in c]
    check(len(calls) == 3 and all("**_rkw" in c for c in calls), "K3 all three integrity calls pass the keyword")
    check(src.count('_silence_note(data, "integrity_api_incompatible")') == 1, "K3 None refuses with a note")

    import math_brain.selection_integrity as si
    check(os.path.abspath(si.__file__).startswith(os.path.abspath(os.path.join(HERE, "..", "..", "tt"))) or
          "PUBLIC_PKG_ANY" in os.environ, "K4 the package under test is the public one: %s" % si.__file__)
    from types import SimpleNamespace as NS

    class Node:
        def __init__(self, i):
            self.id, self.kind, self.metadata = i, "claim", {}

        def digest(self):
            return "d-" + self.id

    hits = [NS(node=Node("b"))]          # one refused card: drop serves an empty list, all refuses

    def reader(db, nid, artifact_root=None):
        if nid == "b":
            raise si.NoteReadError("missing_artifact_binding")
        return {"current_id": nid, "computed_node_digest": "d-" + nid}

    def run(mode):
        try:
            return len(si.consistent_hits(hits, "store.sqlite", deadline=10 ** 12, reader=reader, on_refusal=mode))
        except si.SelectionUnavailable as exc:
            return str(exc)
    try:
        dropped, refused = run("drop"), run("all")
        check(isinstance(dropped, int) and refused == "selected_card_refused:missing_artifact_binding",
              "K4 'drop' serves the rest (%r); 'all' refuses the block (%r)" % (dropped, refused))
    except Exception as exc:
        check(False, "K4 could not drive consistent_hits: %r" % (exc,))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
