"""The exact-name trigger (owner, 6 Oct 2026 13:3x): controls both ways, in a sandbox that writes no live state.
Run: python test_exact_name.py"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
if __name__ != "__main__":
    # a script, not a pytest module: collection imports it, and its checks run only when it is executed
    import pytest
    pytest.skip("run as a script: python test_exact_name.py", allow_module_level=True)
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("exact_name_test", os.path.join(HERE, "exact_name.py"))
en = importlib.util.module_from_spec(spec)
spec.loader.exec_module(en)

LIVE_LOG = os.path.join(HERE, "exact_name_ab.tsv")
live_log = os.path.getsize(LIVE_LOG) if os.path.exists(LIVE_LOG) else -1
tmp = tempfile.mkdtemp()
en.MEM = os.path.join(tmp, "memory")
en.LIVE = os.path.join(tmp, "live")
en.INDEX = os.path.join(en.LIVE, "exact_names.json")
en.CACHE = os.path.join(en.LIVE, "exact_names_cache.json")
en.STATE_DIR = os.path.join(en.LIVE, "state")
en.LOG = os.path.join(tmp, "exact_name_ab.tsv")
en._rebuild_in_background = lambda: None
os.makedirs(en.MEM)


def card(slug, typ, desc, body, sup=""):
    fm = "---\nname: %s\ndescription: %s\nmetadata:\n  type: %s\n%s---\n" % (
        slug, desc, typ, ("superseded_by: %s\n" % sup) if sup else "")
    with open(os.path.join(en.MEM, slug + ".md"), "w", encoding="utf-8") as f:
        f.write(fm + body)


def cmd(c):
    return {"tool_input": {"command": c}}


card("scar_widget_tool_ate_the_queue_2026_09_01", "feedback", "widget_tool.py ate the queue",
     "Running `python widget_tool.py --all` deleted the queue.")
card("scar_widget_tool_newer_2026_10_01", "feedback", "widget_tool.py lost rows again", "See widget_tool.py.")
card("owner_order_in_passing_2026_10_05", "feedback", "his order on films, never math",
     "Uploads go through widget_tool.py, kaggle_ship.py, alpha_one.py, beta_two.py and gamma_three.py.")
card("owner_law_law_only_2026_08_01", "feedback", "never run law_only.py on Fridays", "law_only.py is his.")
card("plain_project_note_2026_10_01", "project", "a plain note", "It mentions plain_only.py and nothing went wrong.")
card("superseded_scar_2026_09_01", "feedback", "old_tool.py broke", "old_tool.py broke", sup="scar_widget_tool_newer_2026_10_01")
card("readme_scar_2026_10_01", "feedback", "README.md was wrong", "README.md was wrong.")
for i in range(9):
    card("busy_scar_%d_2026_10_0%d" % (i, i % 9 + 1), "feedback", "busy_file.py failed again", "busy_file.py failed")

fails = 0


def check(cond, what):
    global fails
    print(("PASS  " if cond else "FAIL  ") + what)
    fails += 0 if cond else 1


r = en.build()
idx = en._index()
check(idx.get("widget_tool.py", [""])[0] == "scar_widget_tool_newer_2026_10_01",
      "the newest card about the file wins: %s" % idx.get("widget_tool.py", ["-"])[0])
check(idx.get("widget_tool.py", [0, 0, 0])[2] == 2, "an order that only mentions the file among five does not count")
check("kaggle_ship.py" not in idx, "a file only mentioned in passing by an unfocused card indexes nothing")
check(idx.get("law_only.py", [""])[0] == "owner_law_law_only_2026_08_01", "his law about a file indexes it")
check("plain_only.py" not in idx, "a project card that reads as no mistake indexes nothing")
check("old_tool.py" not in idx, "a superseded card indexes nothing")
check("readme.md" not in idx, "a generic name indexes nothing")
check("busy_file.py" not in idx, "a name nine cards are about points at no one card")
r2 = en.build()
check(r2["read"] == 0 and r2["names"] == r["names"], "a second build rereads nothing (incremental by mtime)")

check(all(en.arm("s-%d" % i, "owner_law_law_only_2026_08_01") == "shown" for i in range(300)), "a law card is never held")
SL = "scar_widget_tool_newer_2026_10_01"
arms = [en.arm("s-%d" % i, SL) for i in range(400)]
check(60 <= arms.count("held") <= 140, "about one in four held for an ordinary card (%d of 400)" % arms.count("held"))
S = next("sess-%d" % i for i in range(100) if en.arm("sess-%d" % i, SL) == "shown")
l1 = en.line(dict(cmd("python C:/x/widget_tool.py --all"), session_id=S), "")
check(SL in l1 and l1.startswith("[exact-name]"), "a command naming the file gets the card")
check(en.line(dict(cmd("python widget_tool.py --again"), session_id=S), "") == "", "spent for the session after one push")
S2 = next("sess2-%d" % i for i in range(100) if en.arm("sess2-%d" % i, SL) == "shown")
check(en.line(dict(cmd("cat memory/%s.md widget_tool.py" % SL), session_id=S2), "") == "",
      "a command already reading the card does not get it pushed")
check(en.line(dict(cmd("python other_widget_tool.py"), session_id=S2), "") == "", "a longer name containing it does not match")
check(en.line({"session_id": S2, "tool_input": {"file_path": "C:/x/notes.txt", "content": "see widget_tool.py"}}, "") == "",
      "a name inside the content of a Write is not named by the agent")
check(SL in en.line({"session_id": S2, "tool_input": {"file_path": "C:/x/widget_tool.py", "content": "x"}}, ""),
      "the file a Write or Edit opens is named")
S3 = next("sess3-%d" % i for i in range(100) if en.arm("sess3-%d" % i, SL) == "shown")
check(SL in en.line({"session_id": S3, "prompt": "why did widget_tool.py fail"}, ""), "his prompt naming the file is named")
check(en.line(dict(cmd("ls plain_only.py README.md"), session_id="sess-D"), "") == "", "unindexed names push nothing")
check(en.line(cmd("python widget_tool.py"), "") == "", "no session: nothing")
os.environ["EXACT_NAME_OFF"] = "1"
check(en.line(dict(cmd("python widget_tool.py"), session_id="sess-E"), "") == "", "EXACT_NAME_OFF=1 pushes nothing")
os.environ.pop("EXACT_NAME_OFF")
check(all(en.line(dict(cmd("python law_only.py"), session_id="law-%d" % i), "") for i in range(200)),
      "his law card is shown in every session")
before = len(open(en.LOG, encoding="utf-8").read().splitlines())
outs = [en.line(dict(cmd("python widget_tool.py"), session_id="sess-N%d" % i), "") for i in range(400)]
rows = open(en.LOG, encoding="utf-8").read().splitlines()[before:]
held = sum(1 for o in outs if not o)
logged_held = sum(1 for x in rows if x.split("\t")[4] == "held")
check(60 <= held <= 140 and held == logged_held and len(rows) == 400,
      "held cards print nothing and both arms are logged (%d held, %d logged, %d rows)" % (held, logged_held, len(rows)))
now_live = os.path.getsize(LIVE_LOG) if os.path.exists(LIVE_LOG) else -1
check(now_live == live_log, "the live exact_name_ab.tsv was not written by this test")
print(("PASS" if not fails else "FAIL") + " - exact-name controls, %d failed" % fails)
sys.exit(1 if fails else 0)
