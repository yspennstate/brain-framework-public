# -*- coding: utf-8 -*-
"""
The forms he is most likely to actually make, and what happens after the film
is finished. Recording yourself, demonstrating something on a screen, turning
your own research into a piece, and publishing it.
"""

DOMAIN = ("craft.making", "making and publishing",
          "The forms we will actually make, and what happens after the cut.")

NODES = [
    # ---------------- recording yourself ----------------
    ("craft.piece_to_camera", "piece to camera",
     "You, talking to the lens. It is the hardest easy thing in film: nothing to hide behind and "
     "every hesitation visible. What makes it work is not performance, it is preparation - know the "
     "first sentence and the last sentence by heart and improvise the middle, because the two places "
     "people stumble are starting and landing.",
     [("part_of", "craft.making"), ("requires", "craft.medium_closeup")], ["production", "self"]),

    ("craft.self_shoot_setup", "the one setup that always works",
     "A window on one side of you at 45 degrees, a white wall or board on the other to bounce fill, "
     "the camera at eye level about two metres away on a long-ish lens, and a lamp behind you off to "
     "one side. Microphone as close as it can be without entering frame - a lav on the chest or a "
     "boom just out of the top of frame. That is the whole thing, and it beats any amount of "
     "equipment used badly.",
     [("is_a", "craft.piece_to_camera"), ("requires", "craft.soft_light"),
      ("requires", "craft.back_light"), ("requires", "craft.dialogue_recording")],
     ["production", "self"]),

    ("craft.self_shoot_audio", "your room is the problem, not your microphone",
     "Almost every bad home recording is a room problem: a hard-walled space with parallel surfaces "
     "and nothing soft in it. Get the microphone close, then treat the room cheaply - a duvet on a "
     "stand behind the camera, a bookshelf behind you, curtains, a rug. Clap once and listen: if you "
     "hear a ring, the room will be in every word you say.",
     [("is_a", "craft.piece_to_camera"), ("prevents", "failure.roomy_dialogue")],
     ["production", "audio", "self"]),

    ("craft.teleprompter_tell", "reading is visible",
     "Eyes tracking across a line, a rhythm that never varies, and stress landing on the wrong words "
     "- the audience does not identify the cause, they just stop trusting you. If you must read, "
     "read a sentence, look away, then say it. It costs takes and buys credibility.",
     [("is_a", "craft.piece_to_camera"), ("prevents", "failure.reading_aloud")],
     ["production", "self"]),

    ("craft.two_takes_rule", "take two is usually the one",
     "The first take is warming up and the fifth is tired. If a line is not right by take four, the "
     "line is wrong, not the delivery - rewrite it rather than performing it again.",
     [("is_a", "craft.piece_to_camera")], ["production", "directing"]),

    # ---------------- demonstrating on a screen ----------------
    ("craft.screen_demo", "the screen recording",
     "Showing software or a process. The single biggest improvement is to record at the resolution "
     "you will DELIVER, or larger, and never scale a 4K desktop down to 1080 - text turns to mush "
     "and no sharpening brings it back. Second biggest: increase the font size before you record, "
     "past what looks sensible.",
     [("part_of", "craft.making"), ("implemented_by", "tool.obs"),
      ("prevents", "failure.unreadable_screen")], ["production", "screen"]),

    ("craft.cursor_discipline", "the cursor is an actor",
     "Move it deliberately, park it out of the way when it is not doing anything, and never wiggle "
     "it while you think. A cursor drifting around the screen is the visual equivalent of saying "
     "'um'. Consider hiding it entirely and cutting to the result.",
     [("is_a", "craft.screen_demo")], ["production", "screen"]),

    ("craft.zoom_to_detail", "zoom in on what matters",
     "Nobody can read a full desktop on a phone. Crop into the region being discussed - a scale and "
     "crop in post, or a zoom in the recorder - and hold it there while it is being talked about. "
     "The whole screen is only needed to establish where you are.",
     [("is_a", "craft.screen_demo"), ("requires", "craft.establishing_sequence")],
     ["production", "screen"]),

    ("craft.demo_pace", "cut out every wait",
     "Loading, compiling, thinking, typing a long path - all of it goes. A screen demo is the one "
     "form where jump cuts are not merely acceptable but expected, because the alternative is real "
     "time and real time is unwatchable.",
     [("is_a", "craft.screen_demo"), ("requires", "craft.jump_cut_technique")],
     ["production", "screen", "editing"]),

    # ---------------- turning your own work into a film ----------------
    ("craft.research_to_film", "making a film out of your own research",
     "The trap is that you know it too well: you will start where you currently stand rather than "
     "where the audience does. Write the version you would have needed a year ago. The second trap "
     "is that the interesting thing to you - the method - is rarely the interesting thing to them, "
     "which is what changed.",
     [("part_of", "craft.making"), ("requires", "craft.question_first")],
     ["explainer", "structure"]),

    ("craft.result_first_or_last", "where to put the number",
     "Two honest structures. Result first, then how - respects the viewer's time and suits an "
     "audience that already trusts you. Question first, then the walk, then the result - builds an "
     "argument and suits an audience that does not. Choose deliberately; drifting between them is "
     "what makes a piece feel shapeless.",
     [("is_a", "craft.research_to_film")], ["explainer", "structure"]),

    ("craft.show_the_failure", "show what did not work",
     "The failed attempt is usually more instructive and always more credible than the polished "
     "result. It is also the part nobody else publishes, which makes it the part worth having.",
     [("is_a", "craft.research_to_film")], ["explainer", "documentary"]),

    ("craft.honest_chart_in_film", "do not let the film flatter the data",
     "Every device that makes a chart more filmic - a truncated axis, an animated build that reveals "
     "only the good half, a dramatic music cue on a favourable point - is also a device that "
     "misleads. If a scale is doing work, say so out loud in the narration.",
     [("is_a", "craft.research_to_film"), ("requires", "craft.chart_axis_honesty"),
      ("prevents", "failure.misleading_axis")], ["explainer", "ethics", "dataviz"]),

    # ---------------- publishing ----------------
    ("craft.publishing", "publishing",
     "The film is not finished when the cut is. Title, thumbnail, description, chapters, captions "
     "and the first fifteen seconds are all part of whether it is watched at all - and unlike the "
     "film itself, they can be changed after release.",
     [("part_of", "craft.making")], ["delivery", "web"]),

    ("craft.title_that_says", "a title is a promise",
     "It should say what the viewer will know afterwards. Curiosity gaps get clicks and lose trust; "
     "a specific claim gets fewer clicks from better-matched people, which is the trade worth making "
     "for anything technical.",
     [("is_a", "craft.publishing")], ["delivery", "web"]),

    ("craft.chapters_and_captions", "chapters and captions are not optional",
     "Chapters let someone find the part they came for and are the difference between a reference "
     "and a one-time watch. Captions serve people who cannot hear, people watching silently, and "
     "search - and an auto-caption of technical vocabulary is always wrong, so correct the terms.",
     [("is_a", "craft.publishing"), ("requires", "craft.subtitles")], ["delivery", "accessibility"]),

    ("craft.archive_your_project", "keep the project, not just the film",
     "Keep the sources, the script, the timing data and the scripts that built it, and keep them "
     "such that the film can be REBUILT. A film you cannot regenerate is a film you cannot correct, "
     "and everything of ours is generated from text plus a build script for exactly that reason.",
     [("is_a", "craft.publishing"), ("requires", "craft.pipeline")], ["delivery", "pipeline"]),

    ("craft.licence_hygiene", "know what you are allowed to sell",
     "Every asset carries a licence and the ones that bite are audio and models. Kokoro is Apache "
     "2.0 and Chatterbox is MIT including weights, so both are safe to sell; XTTS-v2 and several "
     "cloning models are non-commercial; Microsoft's neural voices through edge-tts are a service "
     "and their terms do not cover selling the output. LibriVox recordings are public domain, which "
     "is why our reference voices come from there. Record the licence beside the asset, not in your "
     "memory.",
     [("is_a", "craft.publishing"), ("applies_to", "craft.tts_reference_clone")],
     ["delivery", "legal"]),
]

SUPPORT = [
    ("failure.reading_aloud", "failure", "it is obvious you are reading",
     "Eyes tracking, unvarying rhythm, stress on the wrong words. The audience stops trusting you "
     "without knowing why."),
    ("failure.unreadable_screen", "failure", "screen text that cannot be read",
     "A full desktop scaled down, or a default font size. Record larger than delivery and crop in."),
]
