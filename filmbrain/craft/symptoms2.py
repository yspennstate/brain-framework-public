# -*- coding: utf-8 -*-
"""
More symptoms, found by asking the brain questions it was not designed around
and watching it answer the wrong thing. Every entry here is a probe that failed:
"how long should my video be" returned the node about music loudness, "should I
show my face" returned typeface pairing.

That is the honest way to grow a knowledge base - probe it with questions
nobody wrote it for, and fill what it gets wrong.
"""

DOMAIN = ("craft.symptoms_more", "more symptoms",
          "Questions the brain got wrong on a blind probe, and their answers.")

NODES = [
    ("symptom.how_long", "how long should my video be",
     "As long as it stays worth watching, which is a real answer rather than a dodge: the shape "
     "should be decided by the argument and then the length falls out of it. What the platforms "
     "actually reward is completion rate, so a tight six minutes beats a loose twelve. Practical "
     "starting points: an explainer of one idea, 4 to 8 minutes; a lecture chapter, 8 to 15; a "
     "documentary piece, as long as its story, but check that every minute changes something. "
     "Write the narration first and time it - that gives you the length in an afternoon, before "
     "you have made anything.",
     [("part_of", "craft.symptoms_more"), ("see_also", "principle.record_first"),
      ("see_also", "craft.pacing"), ("see_also", "symptom.boring_middle")],
     ["symptom", "planning", "delivery"]),

    # the name has to carry the words a person types - "should I show my face"
    # missed this node entirely when it was called only "should I be on camera"
    ("symptom.should_i_appear", "should I be on camera / show my face / appear myself",
     "It depends on whether YOU are part of the argument. If the piece rests on your judgement or "
     "your experience, appearing makes it credible in a way narration cannot. If the piece is about "
     "a thing - a proof, a method, a set of results - a face competes with the thing for attention "
     "and usually loses you clarity. A middle path that works well: appear at the open and the "
     "close, and narrate the middle over the material.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.piece_to_camera"),
      ("see_also", "craft.self_shoot_setup")], ["symptom", "production"]),

    ("symptom.what_microphone", "what microphone should I buy",
     "Distance beats price. A cheap microphone at twenty centimetres beats an expensive one at two "
     "metres, every time, because the ratio of voice to room falls off with distance and no "
     "processing puts it back. So the order to spend in is: get it close, treat the room with soft "
     "furnishings, and only then buy better. Practically: a lavalier on the chest for a talking "
     "head, a dynamic on a boom arm for a desk, and for narration anything that can be mounted six "
     "inches from your mouth with a pop shield. The room is the problem, not the microphone.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.dialogue_recording"),
      ("see_also", "craft.self_shoot_audio")], ["symptom", "production", "sound"]),

    ("symptom.how_many_shots", "how many shots do I need",
     "For a scene you will cut: a wide for geography, a medium, a single of each person, one insert "
     "and one cutaway. That is the honest minimum and it is about six setups, because the editor can "
     "only cut what exists. For an explainer built from generated material the question is different "
     "- there, count BEATS rather than shots, one visual per idea, and expect one visual to serve "
     "twenty to thirty seconds. A ten minute explainer is roughly twenty-four beats.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.coverage"),
      ("see_also", "craft.shot_list")], ["symptom", "production", "planning"]),

    ("symptom.what_aspect_for_platform", "what shape for which platform",
     "16:9 for YouTube and anything watched on a screen the viewer is facing. 9:16 vertical for "
     "phone-native feeds - and it is a different film, not a crop: a wide shot has nowhere to go in "
     "vertical, so the piece has to be composed for it from the start. 1:1 survives both badly. If "
     "one piece must serve two shapes, shoot or render 16:9 with the essential action inside a "
     "centre 9:16 column, and accept that neither version is quite right.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.aspect_ratio"),
      ("see_also", "craft.safe_area")], ["symptom", "format", "delivery"]),

    ("symptom.file_too_big", "the file is too big",
     "Bitrate is the dial, and grain and fast motion are what eat it. In order: encode with CRF "
     "rather than a bitrate target and raise the number until you can see it - 18 is visually "
     "lossless for most material, 20 to 23 is a good web delivery. Then check the resolution is not "
     "higher than the material deserves: an upscaled 640-wide render at 4K is spending bits on "
     "nothing. Then H.265 or AV1 if the destination supports it, for roughly half the size at the "
     "same quality. Do NOT solve it by encoding twice - every lossy pass costs something.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.bitrate"),
      ("see_also", "craft.codec_choice"), ("see_also", "craft.generation_loss")],
     ["symptom", "delivery", "encode"]),

    ("symptom.looks_different_elsewhere", "it looks different on my phone / their screen",
     "Almost always colour tagging. A file that does not declare its colour primaries, transfer and "
     "matrix is interpreted differently by different players, and the usual symptom is washed-out or "
     "over-saturated on one device. Tag the output explicitly - for ordinary HD that is bt709 on all "
     "three - and check on a second device before delivering. The other cause is grading on an "
     "uncalibrated screen at an unusual brightness, which no amount of tagging fixes.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.color_management"),
      ("see_also", "failure.wrong_gamma"), ("see_also", "craft.check_on_bad_speakers")],
     ["symptom", "colour", "delivery"]),

    ("symptom.no_time", "I do not have time to make it properly",
     "Then cut the scope, not the quality of what remains - a tight three minutes is worth more than "
     "a rough ten, and it is the version people finish. The order to protect when time is short: "
     "sound first, because an audience forgives a soft picture and not bad audio; then structure, "
     "because a clear argument survives poor visuals; then the picture. Polish is last and is the "
     "first thing to drop.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.sound"),
      ("see_also", "craft.assembly")], ["symptom", "planning"]),

    ("symptom.what_should_i_learn_first", "what should I learn first",
     "Sound, then structure, then light, then everything else. Sound because it is the thing an "
     "audience will not forgive and the cheapest to fix. Structure because it decides whether a "
     "piece works at all and costs nothing but thinking. Light because it is what makes an image "
     "look expensive and mostly needs moving a lamp rather than buying one. Camera and editing "
     "technique come after those three and are the parts most tutorials start with.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.sound"),
      ("see_also", "craft.story"), ("see_also", "craft.three_point")],
     ["symptom", "planning", "teaching"]),

    ("symptom.how_deep_a_voice", "how deep should the narrator voice be",
     "Deeper helps, for a reason that is not the obvious one. Measured across 79 human narrators, "
     "deep readers score WORSE on every voice-quality metric - harmonics to noise 8.2 against 12.1, "
     "jitter 2.78 against 2.04 - and still sound completely human, and the relationship is "
     "continuous wherever you cut it. So depth does not make a voice human; it widens the tolerance "
     "for the ways a synthetic one is not.\n"
     "What follows practically: prefer a natively deep voice to a lowered one. Lowering works to "
     "two semitones with formants preserved and fails at three, where shimmer and jitter both leave "
     "the human band. And the deepest voice available is not the cleanest - our deepest clone sits "
     "at F0 75 Hz with roughly the quality of what we already had, while the cleanest sits at 120.",
     [("part_of", "craft.symptoms_more"), ("see_also", "craft.deep_voice_hypothesis"),
      ("see_also", "craft.pitch_shifting"), ("see_also", "recipe.deepen_a_voice")],
     ["symptom", "narration", "voice", "measured"]),

    ("symptom.is_it_finished", "is it finished",
     "Four checks, and they are all cheap. Watch it once at speed with no notes and write down "
     "afterwards what you felt, not what you saw. Watch it on a phone with phone speakers. Read the "
     "transcript alone with no picture and see whether the argument survives. And look at a contact "
     "sheet of every shot at once. If all four hold, it is finished; if you are still adjusting "
     "frames, it was finished a while ago.",
     [("part_of", "craft.symptoms_more"), ("see_also", "recipe.check_a_cut"),
      ("see_also", "craft.contact_sheet"), ("see_also", "craft.review_loop")],
     ["symptom", "qa"]),
]

SUPPORT = []
