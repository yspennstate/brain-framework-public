#!/usr/bin/env python
"""
Exercise every path through the film brain and report what is broken.

Run this after changing the store, the CLI, the craft loader or the linker. It
is deliberately picky about ANSWER QUALITY, not just exit codes: a recall that
returns nothing useful passes an exit-code check and fails a person.

  python selftest.py
"""
import os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fb_store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CNW = (0x08000000 if os.name == "nt" else 0)

# a natural-language question, and a node id the answer MUST contain.
# These are phrased the way a person asks, not the way the graph is indexed.
RECALL_CASES = [
    ("my video looks cheap", "symptom.looks_cheap"),
    ("it looks computer generated", "symptom.looks_cgi"),
    ("the narration sounds robotic", "symptom.narration_robotic"),
    ("people say it drags in the middle", "symptom.boring_middle"),
    ("how loud should the music be", "symptom.music_too_loud"),
    ("what frame rate should I use", "symptom.what_frame_rate"),
    ("the sound is echoey", "symptom.audio_bad"),
    ("the shots do not match", "symptom.colours_dont_match"),
    ("the render takes too long", "symptom.render_too_slow"),
    ("I do not know where to start", "symptom.where_do_i_start"),
    # any of these is a correct answer - the symptom node and the recipe are
    # both BETTER answers than the single craft node I first asserted, so the
    # test accepts a set rather than one id
    ("how do I stop a render looking fake",
     ("symptom.looks_cgi", "recipe.make_a_render_look_shot", "craft.motion_blur")),
    ("grain", "craft.grain"),
    ("how deep should the narrator voice be",
     ("symptom.how_deep_a_voice", "craft.deep_voice_hypothesis")),
]

PLAN_CASES = [
    ("a ten minute explainer with charts", ["the shape", "worked example", "record"]),
    ("a documentary from old photographs", ["archive", "still", "grade"]),
    ("record my screen showing the code", ["cursor", "crop", "resolution"]),
]


def run(args, timeout=180):
    r = subprocess.run([PY] + args, capture_output=True, text=True,
                       errors="replace", cwd=HERE, timeout=timeout,
                       creationflags=CNW)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    fails, checks = [], 0
    s = Store()

    # ---- store shape ----
    st = s.stats()
    checks += 1
    if st["nodes"] + st["edges"] < 20000:
        fails.append("store too small: {} nodes + {} edges".format(st["nodes"], st["edges"]))
    print("store        {} nodes, {} edges, {} lessons, {} observations, {} runs".format(
        st["nodes"], st["edges"], st["lessons"], st["obs"], st["runs"]))

    # ---- no dangling edges ----
    checks += 1
    dangling = list(s.db.execute(
        "SELECT e.src, e.rel, e.dst FROM edge e LEFT JOIN node n ON n.id = e.dst "
        "WHERE n.id IS NULL LIMIT 8"))
    if dangling:
        fails.append("{} dangling edges, e.g. {}".format(len(dangling), dangling[0]["dst"]))
    print("edges        {} dangling".format(len(dangling)))

    # ---- every authored craft node has a body worth reading ----
    checks += 1
    # An effect node is a one-line definition by design. What is wrong is an
    # EMPTY body: a person walking the graph arrives there and finds nothing.
    thin = list(s.db.execute(
        "SELECT id FROM node WHERE source='authored' AND (body IS NULL OR length(body) < 25) "
        "AND kind IN ('craft','principle','failure') LIMIT 12"))
    if thin:
        fails.append("{} authored nodes with almost no body, e.g. {}".format(
            len(thin), thin[0]["id"]))
    print("bodies       {} authored craft nodes too thin".format(len(thin)))

    # ---- lessons carry evidence ----
    checks += 1
    noev = list(s.db.execute(
        "SELECT id FROM lesson WHERE evidence IS NULL OR length(evidence) < 12"))
    if noev:
        fails.append("{} lessons with no evidence".format(len(noev)))
    print("lessons      {} without evidence".format(len(noev)))

    # ---- recall answers the way a person asks ----
    miss = []
    for q, want in RECALL_CASES:
        checks += 1
        rc, out = run(["filmbrain.py", "recall", q, "--limit", "4", "--brief"])
        wants = want if isinstance(want, tuple) else (want,)
        if rc != 0 or not any(w in out for w in wants):
            miss.append((q, wants[0]))
    if miss:
        fails.append("recall missed {} of {}: {}".format(
            len(miss), len(RECALL_CASES), "; ".join(q for q, _ in miss[:4])))
    print("recall       {} of {} questions answered".format(
        len(RECALL_CASES) - len(miss), len(RECALL_CASES)))

    # ---- plan produces a shape and real commands ----
    pmiss = []
    for want, terms in PLAN_CASES:
        checks += 1
        rc, out = run(["fbplan.py", want, "--minutes", "10"])
        low = out.lower()
        if rc != 0 or not all(t.lower() in low for t in terms):
            pmiss.append(want)
    if pmiss:
        fails.append("plan thin for: " + "; ".join(pmiss))
    print("plan         {} of {} briefs complete".format(
        len(PLAN_CASES) - len(pmiss), len(PLAN_CASES)))

    # ---- node view shows craft AND the command that implements it ----
    checks += 1
    rc, out = run(["filmbrain.py", "node", "craft.grain"])
    if rc != 0 or "noise" not in out.lower():
        fails.append("node craft.grain does not name the ffmpeg filter")

    # ---- the recorder round-trips ----
    checks += 1
    rc, out = run(["fbrun.py", "--kind", "test", "--tool", "selftest", "--node", "tool.ffmpeg",
                   "--quiet", "--", PY, "-c",
                   "print('FBMETRIC selftest_value=1.0')"])
    if rc != 0 or "selftest" not in out:
        fails.append("fbrun did not record a run")

    # ---- tools inventory is current ----
    checks += 1
    avail = list(s.db.execute(
        "SELECT node_id FROM obs WHERE metric='available' AND num=1.0 GROUP BY node_id"))
    if len(avail) < 10:
        fails.append("only {} tools verified available - run tools/verify_tools.py".format(
            len(avail)))
    print("tools        {} verified usable".format(len(avail)))

    print()
    if fails:
        print("FAILED {} of {} checks".format(len(fails), checks))
        for f in fails:
            print("   - " + f)
        return 1
    print("all {} checks pass".format(checks))
    return 0


if __name__ == "__main__":
    # Discovery is not execution: this script writes, so a help request must exit
    # before any work. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
