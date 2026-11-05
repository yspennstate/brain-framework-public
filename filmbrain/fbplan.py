#!/usr/bin/env python
"""
Turn "I want to make X" into something you can act on: the shape, the craft
rules that apply, the failures to avoid, the recipes with their commands, and
what this machine will cost you.

  python fbplan.py "a ten minute explainer about the trading book"
  python fbplan.py "a documentary from old photographs" --minutes 20
  python fbplan.py --list

This is the difference between a reference and a brain. A reference makes you
know what to look up; this answers the question you actually asked.
"""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fb_store import Store

W = 98

# recognisable kinds of piece, the craft anchors each pulls in, and the
# shape it usually wants. Matching is by keyword because a person types a
# sentence, not a taxonomy.
KINDS = {
    "lecture": {
        "words": ["lecture", "teach", "course", "class", "maths", "math", "proof",
                  "explain", "explainer", "tutorial", "how it works"],
        "anchors": ["craft.lecture", "craft.educational_film", "craft.question_first",
                    "craft.worked_example", "craft.progressive_reveal", "craft.board_discipline",
                    "craft.narration", "principle.record_first", "craft.chapter_card",
                    "craft.pause_after_hard", "craft.notation_on_screen"],
        "recipes": ["recipe.narration_first_animation", "recipe.voice_humanise",
                    "recipe.check_a_cut", "recipe.loudness_deliver"],
        "shape": [
            "the question the piece answers, asked in the first thirty seconds",
            "the awkward case that makes the question necessary",
            "the idea, introduced as the answer to that question",
            "a worked example, done slowly",
            "the ways it goes wrong",
            "two or three harder examples",
            "where else this idea turns up",
            "the recipe card - what to do, in order",
        ],
        "note": "Record the narration first and cut every animation to its measured "
                "length. Chapter cards give a wandering mind somewhere to rejoin.",
    },
    "documentary": {
        "words": ["documentary", "history", "archive", "photographs", "photos", "stills",
                  "war", "biography", "story of"],
        "anchors": ["craft.documentary", "craft.archive_material", "craft.ken_burns",
                    "craft.cutaway", "craft.interview", "craft.ethics_of_the_cut",
                    "craft.map_sequence", "craft.grain", "craft.pillarbox_choice"],
        "recipes": ["recipe.stills_to_documentary", "recipe.voice_humanise",
                    "recipe.contact_sheet", "recipe.check_a_cut"],
        "shape": [
            "a specific small detail, not a grand vista",
            "the situation, established plainly",
            "the turn - what changed and when",
            "the consequence, through one person if you have one",
            "what it meant afterwards",
        ],
        "note": "Archive never matches. Do not try to make the sources agree - put every "
                "one through the same grade and the same grain so the mismatch is consistent.",
    },
    "ai_film": {
        "words": ["ai video", "generated", "generative", "ltx", "diffusion", "text to video",
                  "animation from prompts"],
        "anchors": ["craft.ai_video", "craft.ai_prompt_structure", "craft.ai_seed",
                    "craft.ai_temporal_coherence", "craft.ai_shot_consistency",
                    "craft.ai_as_texture", "craft.upscaling", "machine.ltx_budget"],
        "recipes": ["recipe.gpu_video_budget", "recipe.upscale_small_render",
                    "recipe.contact_sheet", "recipe.timeline_from_script"],
        "shape": [
            "decide the spine FIRST - stills, a map, a board, or real footage",
            "generated clips as inserts and atmosphere on that spine",
            "short clips, cut before the identity drifts",
            "one action per clip, camera and light stated in film language",
            "vary the seed for alternative takes, then pick the sharpest",
        ],
        "note": "On this card a finished clip costs about thirteen minutes including "
                "failures. Budget from that, not from the render time.",
    },
    "screen_demo": {
        "words": ["screen", "demo", "software", "walkthrough", "record my", "show how to use",
                  "code", "terminal"],
        "anchors": ["craft.screen_demo", "craft.cursor_discipline", "craft.zoom_to_detail",
                    "craft.demo_pace", "craft.jump_cut_technique", "craft.screen_typography"],
        "recipes": ["recipe.voice_humanise", "recipe.check_a_cut", "recipe.loudness_deliver"],
        "shape": [
            "the result first - what this will let you do",
            "where we are, established once at full screen",
            "the steps, each cropped to the region being discussed",
            "the thing that goes wrong, and the fix",
            "the result again, now that it means something",
        ],
        "note": "Record at delivery resolution or larger and raise the font size past "
                "what looks sensible. Cut out every wait.",
    },
    "talking_head": {
        "words": ["to camera", "piece to camera", "myself", "me talking", "interview me",
                  "vlog", "announcement"],
        "anchors": ["craft.piece_to_camera", "craft.self_shoot_setup", "craft.self_shoot_audio",
                    "craft.medium_closeup", "craft.soft_light", "craft.back_light",
                    "craft.headroom", "craft.two_takes_rule", "craft.teleprompter_tell"],
        "recipes": ["recipe.check_a_cut", "recipe.loudness_deliver"],
        "shape": [
            "the claim, in one sentence, first",
            "why it matters to whoever is watching",
            "the evidence or the story",
            "the qualification - what this does not show",
            "what you want them to do or think",
        ],
        "note": "Know the first and last sentence by heart and improvise the middle. "
                "Your room is the problem, not your microphone.",
    },
    "data_story": {
        "words": ["chart", "data", "results", "backtest", "returns", "graph", "numbers",
                  "study", "research", "paper"],
        "anchors": ["craft.chart_on_screen", "craft.chart_build", "craft.chart_annotation",
                    "craft.chart_axis_honesty", "craft.animated_transition_data",
                    "craft.chart_colour", "craft.number_on_screen", "craft.research_to_film",
                    "craft.result_first_or_last", "craft.honest_chart_in_film",
                    "craft.show_the_failure"],
        "recipes": ["recipe.narration_first_animation", "recipe.check_a_cut"],
        "shape": [
            "the number, said plainly, with what it is measured against",
            "what you expected instead",
            "how it was measured, honestly, including what it excludes",
            "the failure or the thing that nearly broke it",
            "what you would do differently",
        ],
        "note": "One message per chart, stated in the narration at the moment the chart "
                "shows it. If a scale is doing work, say so out loud.",
    },
}

UNIVERSAL = ["principle.record_first", "principle.stamp_the_cache", "craft.pacing",
             "craft.breathing_room", "craft.sound", "craft.loudness_standard",
             "craft.review_loop", "craft.contact_sheet", "craft.licence_hygiene",
             "craft.delivery", "craft.safe_area"]


def classify(text):
    t = text.lower()
    scores = {}
    for k, spec in KINDS.items():
        scores[k] = sum(3 if w in t else 0 for w in spec["words"])
    best = max(scores, key=lambda k: scores[k])
    return (best, scores[best]) if scores[best] > 0 else (None, 0)


def wrap(s, indent=6, width=W):
    import textwrap
    return textwrap.fill(s, width=width, initial_indent=" " * indent,
                         subsequent_indent=" " * indent)


def show(s, ids, header, body=True, limit=400):
    got = [(i, s.get(i)) for i in ids]
    got = [(i, n) for i, n in got if n]
    if not got:
        return
    print(header)
    print("  " + "-" * (W - 2))
    for i, n in got:
        print("  * " + n["name"])
        if body and n.get("body"):
            print(wrap(n["body"][:limit], indent=6))
    print()


def failures_for(s, anchors):
    """Everything these craft nodes prevent - the checklist of what goes wrong."""
    out = {}
    if not anchors:
        return out
    q = ",".join("?" * len(anchors))
    for r in s.db.execute(
            "SELECT src, dst, note FROM edge WHERE rel IN ('prevents','fixes') "
            "AND src IN (" + q + ")", anchors):
        f = s.get(r["dst"])
        srcn = s.get(r["src"])
        if f and f["kind"] == "failure":
            out.setdefault(f["id"], (f, []))[1].append(srcn["name"] if srcn else r["src"])
    return out


def commands_for(s, anchors):
    """The curated craft -> tool edges carry the actual command in the note."""
    rows = []
    if not anchors:
        return rows
    q = ",".join("?" * len(anchors))
    for r in s.db.execute(
            "SELECT src, dst, note FROM edge WHERE rel='implemented_by' AND src IN (" + q + ") "
            "AND note IS NOT NULL", anchors):
        srcn = s.get(r["src"])
        rows.append((srcn["name"] if srcn else r["src"], r["note"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("want", nargs="?", default=None)
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list or not a.want:
        print("kinds of piece this knows about:")
        for k, spec in KINDS.items():
            print("  {:<14} {}".format(k, ", ".join(spec["words"][:6])))
        return

    s = Store()
    kind, score = classify(a.want)
    print("=" * W)
    print("PLAN:  " + a.want)
    if kind:
        print("reads as: " + kind.replace("_", " "))
    else:
        print("no strong match - falling back to the universal rules")
    print("=" * W)
    print()

    spec = KINDS.get(kind, {})
    if spec.get("shape"):
        print("  the shape")
        print("  " + "-" * (W - 2))
        for i, step in enumerate(spec["shape"], 1):
            print("   {}. {}".format(i, step))
        print()
    if spec.get("note"):
        print(wrap(spec["note"], indent=3))
        print()

    if a.minutes:
        m = a.minutes
        print("  at {:.0f} minutes".format(m))
        print("  " + "-" * (W - 2))
        print("   narration    about {:.0f} words at 165 a minute".format(m * 165))
        print("   beats        about {:.0f} at 25 seconds each".format(m * 60 / 25))
        print("   narration    about {:.0f} min of kokoro on CPU, or {:.0f} min cloned on the GPU"
              .format(m * 2.05, m * 5.9))
        print()
        print("   These two are UPPER BOUNDS, not estimates - they scale a single measured")
        print("   unit and assume every frame is unique, which no real piece is:")
        print("     3D everywhere   {:.0f} GPU-hours at 9 s a frame, 30 fps, nothing reused"
              .format(m * 60 * 30 * 9 / 3600))
        print("     AI everywhere   {:.0f} GPU-hours at 13 min a finished 8-second clip"
              .format(m * 60 / 8 * 13 / 60))
        print("   The realistic case is far below both, because a card, a chart and a still")
        print("   with a move cost seconds a frame or nothing at all. Which is the argument")
        print("   for a cheap spine with generated clips as inserts on top of it.")
        print()

    anchors = spec.get("anchors", []) + UNIVERSAL
    show(s, spec.get("anchors", []), "  the craft that applies here")
    show(s, spec.get("recipes", []), "  recipes, with the commands", limit=900)

    fails = failures_for(s, anchors)
    if fails:
        print("  what goes wrong, and what prevents it")
        print("  " + "-" * (W - 2))
        for fid, (f, causes) in list(fails.items())[:14]:
            print("  [ ] " + f["name"])
            if f.get("body"):
                print(wrap(f["body"][:200], indent=8))
            print(wrap("prevented by: " + ", ".join(sorted(set(causes))[:4]), indent=8))
        print()

    cmds = commands_for(s, anchors)
    if cmds:
        print("  commands on this machine")
        print("  " + "-" * (W - 2))
        seen = set()
        for name, note in cmds:
            if note in seen:
                continue
            seen.add(note)
            print("  " + name)
            print(wrap(note, indent=6))
        print()

    les = []
    for term in (a.want, kind or ""):
        les.extend(s.search_lessons(term, limit=5))
    if les:
        print("  what we learned doing this before")
        print("  " + "-" * (W - 2))
        seen = set()
        for l in les:
            if l["id"] in seen:
                continue
            seen.add(l["id"])
            print(wrap("[" + l["confidence"] + "] " + l["text"][:420], indent=4))
            print()


if __name__ == "__main__":
    main()
