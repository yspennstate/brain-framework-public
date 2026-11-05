# -*- coding: utf-8 -*-
"""
The kind of film he actually makes: a voice explaining something hard, with
pictures that carry the argument. Lecture, explainer, and the graphics that
serve them - diagrams, type and charts on screen, which follow different rules
from charts on paper.
"""

DOMAIN = ("craft.educational_film", "educational film",
          "A voice explaining something hard, with pictures that carry the argument.")

NODES = [
    # ---------------- teaching on screen ----------------
    ("craft.question_first", "introduce nothing before its question",
     "The rule that separates a lecture people finish from one they abandon. Every new object - a "
     "definition, a symbol, a technique - arrives as the answer to a question the listener already "
     "has. Present the awkward case first, let them feel the need, then give the tool. A definition "
     "delivered before its motivation is memorised and not understood.",
     [("part_of", "craft.educational_film"), ("used_in", "craft.lecture"),
      ("prevents", "failure.unmotivated_definition")], ["teaching", "structure"]),

    ("craft.worked_example", "the worked example does the teaching",
     "People learn a procedure by watching one done and then doing one. The explanation before it is "
     "orientation; the example is the lesson. Budget accordingly - if a chapter has one example and "
     "four minutes of explanation, it is the wrong way round.",
     [("part_of", "craft.educational_film"), ("used_in", "craft.lecture")], ["teaching"]),

    ("craft.one_new_thing", "one new thing at a time",
     "If a step introduces a new idea AND a new notation AND a new picture, the listener drops one. "
     "Change one variable per beat. This is the same discipline as an experiment and it fails the "
     "same way when ignored.",
     [("part_of", "craft.educational_film"), ("prevents", "failure.too_much_at_once")],
     ["teaching", "structure"]),

    ("craft.error_first_teaching", "show the wrong answer",
     "Demonstrating the natural mistake, then why it fails, sticks better than demonstrating the "
     "correct method alone - because the listener was going to make that mistake and now has a "
     "handle on it. Do it explicitly, and mark clearly when you return to the truth.",
     [("part_of", "craft.educational_film")], ["teaching"]),

    ("craft.callback", "return to the same example",
     "One example carried through a whole piece, revisited at each new level, beats ten fresh ones. "
     "The listener stops paying the cost of understanding a new situation and can spend all of it on "
     "the new idea.",
     [("part_of", "craft.educational_film"), ("requires", "craft.setup_payoff")], ["teaching"]),

    ("craft.pause_after_hard", "silence after the hard part",
     "Two seconds of nothing after a definition or a step that took effort. It feels like dead air "
     "when you are cutting and it is the difference between a listener who followed and one who is "
     "still on the previous sentence.",
     [("part_of", "craft.educational_film"), ("requires", "craft.silence"),
      ("requires", "craft.breathing_room")], ["teaching", "rhythm"]),

    ("craft.board_discipline", "clear the board",
     "Anything left on screen is still being read. Two of eleven beats in our own lecture had a "
     "genuine overlap bug - a tangent drawn over a car diagram, a table over a graph - and both were "
     "invisible in code and obvious in a rendered frame. Clear explicitly, and sample a frame per "
     "chapter and look at it.",
     [("part_of", "craft.educational_film"), ("prevents", "failure.unseen_overlap"),
      ("requires", "craft.review_loop")], ["teaching", "animation"]),

    ("craft.progressive_reveal", "build the picture in the order of the argument",
     "Do not draw the finished diagram and then talk about it. Draw it in the order the sentence "
     "needs, so the eye is always looking at the thing being said. Every element should appear at "
     "the moment the narration reaches it, which is trivially available if the narration was "
     "recorded first and measured.",
     [("part_of", "craft.educational_film"), ("requires", "principle.record_first")],
     ["teaching", "animation"]),

    ("craft.chapter_card", "chapter cards",
     "A named card between sections gives the listener a place to rejoin after their attention "
     "wandered, which it will. Two traps we have already hit: derive both the spoken number and the "
     "drawn number from the position in the running order in ONE place, or 23 of 28 chapters "
     "announce the wrong one; and move the cross-references in the narration when the order changes.",
     [("part_of", "craft.educational_film"), ("prevents", "failure.wrong_chapter_number")],
     ["teaching", "structure"]),

    ("craft.notation_on_screen", "notation must be readable at phone size",
     "Subscripts and fractions vanish on a small screen. Set mathematics larger than looks right on "
     "a monitor, and prefer a horizontal form to a stacked one where the meaning survives. On this "
     "machine LaTeX is broken, so mathematics is unicode text in Cambria Math, which renders clean "
     "and needs nothing installed.",
     [("part_of", "craft.educational_film"), ("causes", "failure.latex_broken_here")],
     ["teaching", "typography"]),

    # ---------------- typography ----------------
    ("craft.screen_typography", "type on screen",
     "Different from print in three ways that matter: it is read at a distance and at a size you do "
     "not control, it sits over a moving image, and it disappears on a schedule. So: larger than "
     "feels right, higher contrast than feels right, and on screen long enough to be read twice by a "
     "slow reader - about 0.3 seconds a word plus a second of settling.",
     [("part_of", "craft.title_design")], ["typography", "titles"]),

    ("craft.type_contrast", "contrast, not decoration",
     "Text over picture fails on contrast, and the usual answers make it worse. A drop shadow muddies; "
     "a hard outline reads as amateur. What works: a scrim - a soft dark gradient behind the text - "
     "or moving the text to a part of the frame that is already dark, which is a composition decision "
     "made at the shoot.",
     [("is_a", "craft.screen_typography"), ("requires", "craft.negative_space")],
     ["typography", "titles"]),

    ("craft.type_hierarchy", "hierarchy by size and weight, not by colour",
     "Colour is the first thing lost to compression, to a bad screen, and to colour-blind viewers. "
     "Build hierarchy with size, weight and position; use colour to confirm it, never to carry it.",
     [("is_a", "craft.screen_typography")], ["typography", "accessibility"]),

    ("craft.type_pairing", "two typefaces at most",
     "One for display, one for text, and preferably from families designed to sit together. A third "
     "face reads as indecision. For anything technical a face with real tabular figures and a "
     "distinguishable one, l and I is worth more than any amount of personality.",
     [("is_a", "craft.screen_typography")], ["typography"]),

    ("craft.lower_third", "lower thirds",
     "Name and role, in that order, one line each, on screen for four to six seconds, out of the "
     "action-safe margin. Animate it in from the direction it belongs and out the same way. The most "
     "common mistake is leaving it up for the whole answer, which turns it into furniture.",
     [("is_a", "craft.screen_typography"), ("used_in", "craft.interview")], ["typography", "titles"]),

    ("craft.title_sequence", "the title sequence",
     "Its real job is to set the terms: this is what kind of thing you are watching, this is the "
     "pace, this is the world. It is also the only place in a film where pure design is expected, so "
     "it is where a small budget can buy the most perceived quality.",
     [("is_a", "craft.screen_typography")], ["typography", "titles"]),

    # ---------------- charts on screen ----------------
    ("craft.chart_on_screen", "charts on screen are not charts on paper",
     "A reader of a printed chart can look as long as they like and go back. A viewer gets the "
     "seconds you give them and cannot return. So: one message per chart, the message stated in the "
     "narration at the moment the chart shows it, far fewer series than a paper figure, and labels "
     "on the lines rather than in a legend the eye has to shuttle to.",
     [("part_of", "craft.educational_film"), ("prevents", "failure.chart_too_dense")],
     ["dataviz", "teaching"]),

    ("craft.chart_build", "build a chart in the order of the argument",
     "Axes first, then the baseline series, then the comparison, then the annotation that says what "
     "to notice - each arriving as the narration reaches it. A chart that appears complete asks the "
     "viewer to search it while you are still talking, and they will lose the sentence.",
     [("is_a", "craft.chart_on_screen"), ("requires", "craft.progressive_reveal")],
     ["dataviz", "animation"]),

    ("craft.chart_annotation", "annotate the point, do not leave it to be found",
     "Put an arrow and four words on the thing you are talking about. Every second the viewer spends "
     "looking for what you mean is a second they are not listening. This single habit does more for "
     "comprehension than any styling.",
     [("is_a", "craft.chart_on_screen")], ["dataviz"]),

    ("craft.chart_axis_honesty", "say what the axis does",
     "A truncated axis, a log scale, a rebased index and a cumulative sum all change what a shape "
     "means, and on screen nobody reads the axis label. If the scale is doing work, the narration "
     "must say so out loud - a chart that misleads for two seconds has misled.",
     [("is_a", "craft.chart_on_screen"), ("prevents", "failure.misleading_axis")],
     ["dataviz", "ethics"]),

    ("craft.animated_transition_data", "animate between states, do not cut",
     "When a chart changes - a filter, a rebase, a new year - move the marks to their new positions "
     "over about half a second rather than cutting. The eye then tracks WHICH thing moved WHERE, "
     "which is the entire information content of the change. A cut throws that away.",
     [("is_a", "craft.chart_on_screen"), ("requires", "craft.easing")], ["dataviz", "animation"]),

    ("craft.chart_colour", "colour by meaning and keep it",
     "Once a series has a colour it keeps it for the whole film. Reusing a colour for a different "
     "thing three minutes later costs the viewer more than a new colour ever would. Reserve one high "
     "-saturation colour for the thing being discussed and let everything else sit grey.",
     [("is_a", "craft.chart_on_screen"), ("requires", "craft.color_palette")], ["dataviz"]),

    ("craft.number_on_screen", "numbers need units and a comparison",
     "A number alone is not information. On screen it needs its unit and something to be measured "
     "against - last year, the alternative, the total. The narration can carry the comparison, but "
     "one of the two must.",
     [("is_a", "craft.chart_on_screen")], ["dataviz", "teaching"]),

    # ---------------- documentary from archives ----------------
    ("craft.archive_material", "working with archive",
     "Mismatched everything: aspect ratios, grain, frame rates, colour, damage. The fix is not to "
     "make them match - it is to make the MISMATCH consistent. Pick one treatment and put every "
     "source through it: a common frame, a common grade, a common grain over the top.",
     [("part_of", "craft.documentary"), ("requires", "craft.grain")], ["documentary", "archive"]),

    ("craft.pillarbox_choice", "what to do with the wrong aspect ratio",
     "Three honest options and one dishonest one. Pillarbox it and let the black show. Fill the sides "
     "with a blurred, darkened copy of the frame. Crop into it and lose the edges. The dishonest one "
     "is stretching, which nobody consciously notices and everybody feels.",
     [("is_a", "craft.archive_material"), ("prevents", "failure.stretched_frame")],
     ["documentary", "archive"]),

    ("craft.map_sequence", "maps that explain movement",
     "Our own map engine's lessons, which generalise. Label placement is the whole look: try each "
     "side of a dot, take the first free slot, and DROP a label that fits nowhere - a wide shot with "
     "every town printed is unreadable. Rank places so only cities draw when zoomed out. Clamp "
     "callout boxes inside the frame. And never let the front line share a colour with the rivers.",
     [("part_of", "craft.documentary"), ("prevents", "failure.unreadable_map")],
     ["documentary", "graphics", "measured"]),

    ("craft.ethics_of_the_cut", "what an edit implies",
     "Cutting a reaction shot to a different moment, tightening an answer past its qualifications, "
     "scoring a neutral scene as sinister - all are invisible and all change what the audience "
     "believes happened. The test is simple: would the person in the frame recognise what they are "
     "shown as doing.",
     [("part_of", "craft.documentary"), ("requires", "craft.kuleshov")], ["documentary", "ethics"]),
]

SUPPORT = [
    ("failure.unmotivated_definition", "failure", "a definition with no question behind it",
     "Memorised, not understood, and forgotten by the next chapter."),
    ("failure.too_much_at_once", "failure", "a step that changes three things",
     "New idea plus new notation plus new picture in one beat. Something gets dropped and it is "
     "usually the idea."),
    ("failure.wrong_chapter_number", "failure", "the chapter announces the wrong number",
     "Labels used as registration keys while the running order is a separate list. Derive the spoken "
     "and drawn number from the ORDER, in one place, and move the cross-references with it."),
    ("failure.chart_too_dense", "failure", "a paper figure put on screen",
     "Six series, a legend, a caption and axis labels the viewer has three seconds to read."),
    ("failure.misleading_axis", "failure", "a scale doing unstated work",
     "Truncated, logged, rebased or cumulated without the narration saying so."),
    ("failure.stretched_frame", "failure", "archive stretched to fill the frame",
     "Nobody consciously notices; everybody feels it."),
    ("failure.unreadable_map", "failure", "a map with every label printed",
     "The wide shots become texture. Rank the places, drop what does not fit, clamp the callouts."),
]
