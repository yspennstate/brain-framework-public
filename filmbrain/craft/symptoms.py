# -*- coding: utf-8 -*-
"""
Symptoms: what people actually say when something is wrong, mapped to the
causes worth checking, in the order worth checking them.

Nobody types "insufficient subject separation". They type "it looks cheap".
A brain that only indexes the correct term answers the question nobody asked.
Each symptom node carries the diagnosis in its body AND points at the causes,
so both the search and the graph get you there.
"""

DOMAIN = ("craft.symptoms", "symptoms",
          "What people say when it is wrong, and what to check first.")

NODES = [
    ("symptom.looks_cheap", "it looks cheap / amateur / like video not film",
     "Check in this order, because the first three are free. One: is there any light BEHIND the "
     "subject - the missing backlight is the commonest single cause. Two: is there anything in the "
     "foreground, because a frame with only a subject and a wall is flat whatever you do to it. "
     "Three: is the background busy or bright enough to steal the eye. Four: is there motion blur - "
     "in a render it is off by default and its absence is the loudest tell. Five: is everything "
     "sharp everywhere, which no lens does. Six: is the image perfectly clean, with no grain, which "
     "no camera produces.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.back_light"),
      ("see_also", "craft.depth_layers"), ("see_also", "craft.clean_background"),
      ("see_also", "craft.motion_blur"), ("see_also", "craft.shallow_focus"),
      ("see_also", "craft.grain"), ("causes", "failure.flat_image")],
     ["symptom", "look"]),

    ("symptom.looks_cgi", "it looks computer generated / fake / plastic",
     "Almost never a modelling problem. Motion blur off, no depth of field or a fake uniform blur, "
     "no contact shadow where things touch, perfect symmetry and perfect cleanliness, lighting with "
     "no bounce, and no grain to bind the layers together. Fix them in that order; the first two "
     "buy most of it.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.motion_blur"),
      ("see_also", "craft.depth_of_field"), ("see_also", "recipe.make_a_render_look_shot"),
      ("causes", "failure.cgi_look")], ["symptom", "3d", "render"]),

    ("symptom.boring_middle", "it drags / people stop watching in the middle",
     "Pace is not shot length, it is the rate at which NEW information arrives - so a slow middle is "
     "usually a middle where nothing changes rather than one that is cut too long. Check: does each "
     "section leave the viewer able to do something they could not before; is there a question open "
     "at every moment; have you signposted where they are so a wandering mind can rejoin; and is "
     "there any variation in texture, or is it forty minutes of the same shot type at the same "
     "rhythm. A colour script of the whole piece will show you the sameness in one glance.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.pacing"),
      ("see_also", "craft.signposting"), ("see_also", "craft.scene_structure"),
      ("see_also", "craft.color_script"), ("see_also", "craft.chapter_card")],
     ["symptom", "structure", "editing"]),

    ("symptom.confusing", "people say it is confusing",
     "Usually not too little explanation - too little SPACE. New information arriving before the "
     "last piece landed reads as confusion. The two cheapest fixes are two seconds of silence after "
     "anything hard, and introducing nothing before the question it answers. Then check that you are "
     "not changing three things at once in a single step.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.breathing_room"),
      ("see_also", "craft.pause_after_hard"), ("see_also", "craft.question_first"),
      ("see_also", "craft.one_new_thing"), ("causes", "failure.no_room_to_think")],
     ["symptom", "teaching"]),

    ("symptom.audio_bad", "the sound is bad / hollow / echoey",
     "The microphone was too far away, and no processing puts that back - the ratio of voice to room "
     "falls off with distance. Get it close, then treat the room cheaply: soft things behind the "
     "camera and behind you. Clap once in the space and listen for a ring. De-reverb tools trade the "
     "room for artefacts and are a last resort.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.dialogue_recording"),
      ("see_also", "craft.self_shoot_audio"), ("causes", "failure.roomy_dialogue")],
     ["symptom", "sound"]),

    ("symptom.music_too_loud", "how loud should the music be",
     "Mix dialogue first and alone, then effects around it, then music LAST and quieter than you "
     "want it. Platform targets: about -14 LUFS integrated for the web, -16 for podcasts, -19 for "
     "our audiobook chain, -23 for European broadcast, true peak under -1 dBTP. Then check it on a "
     "phone speaker and in a car, because that is where it will be heard. Music that STARTS is far "
     "more powerful than music that has been playing.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.mixing"),
      ("see_also", "craft.loudness_standard"), ("see_also", "craft.score")],
     ["symptom", "sound", "mix"]),

    ("symptom.what_frame_rate", "what frame rate should I use",
     "24 for film and drama - dreamlike, and it strobes on fast pans. 25 or 50 for European "
     "broadcast. 30 or 60 for the web, for screen recordings, and for anything with fast motion. It "
     "is a look before it is a number: 24 reads as cinema, 60 reads as video, sport or a game. "
     "Whatever you choose, choose once - mixing rates in one timeline costs you a conversion "
     "somewhere and it always shows on motion.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.frame_rate"),
      ("see_also", "craft.shutter_angle"), ("see_also", "craft.motion_blur")],
     ["symptom", "format"]),

    ("symptom.what_resolution", "what resolution should I shoot or render at",
     "Larger than delivery when you can afford it: it buys reframing, stabilisation and a clean "
     "downscale, which sharpens for free. But an upscaled small render is not a large one, and the "
     "difference shows in edges and fine texture. For screen recordings, record at delivery size or "
     "larger and never scale a 4K desktop down to 1080 - the text turns to mush.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.resolution_choice"),
      ("see_also", "craft.upscaling"), ("see_also", "craft.screen_demo")],
     ["symptom", "format"]),

    ("symptom.narration_robotic", "the narration sounds robotic / not human",
     "Decompose it before changing anything, because it is never one defect. In the order that has "
     "actually paid here: pauses all the same length; no breath anywhere; digital silence in the "
     "gaps; every sentence at the same speed; too clean a signal with no room; then, last, the "
     "voice itself. Most of the wins are in assembly and post, not in the model - our own voice "
     "measured close to human on pitch movement while its pause variation sat at the human floor.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.tts_naturalness"),
      ("see_also", "craft.pause_grammar"), ("see_also", "craft.breath"),
      ("see_also", "recipe.voice_humanise"), ("causes", "failure.robotic")],
     ["symptom", "narration", "tts"]),

    ("symptom.cut_feels_wrong", "the cut feels wrong but I cannot say why",
     "Work down Murch's list: is the emotion right, is the story served, is the rhythm right, does "
     "the eye land where the next shot needs it. Then the mechanical checks: did you cut on "
     "stillness instead of on movement, is the angle change under thirty degrees so it reads as a "
     "jump, did you cross the line, does the eyeline still agree with what the next shot shows.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.murch_six"),
      ("see_also", "craft.cut_on_action"), ("see_also", "craft.30_degree_rule"),
      ("see_also", "craft.180_rule"), ("see_also", "craft.eyeline_match")],
     ["symptom", "editing"]),

    ("symptom.colours_dont_match", "the shots do not match / the colour jumps",
     "Normalise every shot to a neutral base FIRST, then match shots to each other, then put a look "
     "on top of the matched sequence. Grading shot by shot to taste guarantees a sequence that "
     "flickers. If the mismatch is a white balance difference at the shoot, fix that before anything "
     "creative. Archive material never matches and should not be made to - put every source through "
     "one grade and one grain instead.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.color_correction"),
      ("see_also", "craft.white_balance"), ("see_also", "craft.archive_material")],
     ["symptom", "colour"]),

    ("symptom.text_unreadable", "the text is hard to read",
     "Bigger than looks right on your monitor, because it will be watched on a phone. Contrast comes "
     "from a scrim - a soft dark panel behind the text - not from a drop shadow, which muddies, or "
     "an outline, which reads as amateur. Keep it inside about ninety per cent of the frame because "
     "platforms overlay their own chrome. On screen long enough to be read twice by a slow reader.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.screen_typography"),
      ("see_also", "craft.type_contrast"), ("see_also", "craft.safe_area"),
      ("causes", "failure.unreadable_screen")], ["symptom", "titles"]),

    ("symptom.render_too_slow", "the render takes too long",
     "On this machine EEVEE is about sixteen times quicker than Cycles at 1080p - 4 seconds a frame "
     "against 67 - and most explainer work does not need path tracing. Then: render at the size you "
     "deliver, cache everything that is settled and composite only the live layer's bounding box, "
     "make intermediates cheap because they get re-encoded anyway, and split frame ranges across "
     "processes. And measure a warm frame, not the first one, which pays for shader compilation.",
     [("part_of", "craft.symptoms"), ("see_also", "machine.blender_speed"),
      ("see_also", "machine.blender_warmup"), ("see_also", "craft.generation_loss"),
      ("see_also", "craft.render_farm")], ["symptom", "render", "measured"]),

    ("symptom.ai_clips_inconsistent", "the AI clips do not match each other",
     "They will not. Current open models hold a subject for a few seconds and then drift - faces "
     "first, then hands, then clothing. The workable answers are to cut before the drift shows, to "
     "start from a single reference image rather than text, to describe the subject identically "
     "every time, and to design the piece so the character is rarely close and never twice in one "
     "frame. Or to accept it and cut so the inconsistency reads as style.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.ai_shot_consistency"),
      ("see_also", "craft.ai_temporal_coherence"), ("see_also", "craft.img2vid"),
      ("causes", "failure.identity_drift")], ["symptom", "ai"]),

    ("symptom.out_of_sync", "the sound drifts out of sync",
     "If it starts together and separates over minutes, it is a sample or frame rate mismatch, or "
     "one side was rebuilt and the other was not. The structural cure is never to assume a duration: "
     "measure what was actually produced and build the other side from that measurement. If it is "
     "off by a constant, remember human tolerance is asymmetric - late is forgiven to about 45 "
     "milliseconds, early is caught at 15.",
     [("part_of", "craft.symptoms"), ("see_also", "craft.drift"), ("see_also", "craft.sync"),
      ("see_also", "principle.record_first")], ["symptom", "sound", "editing"]),

    ("symptom.where_do_i_start", "I have an idea and do not know where to start",
     "Write the narration first, read it aloud, and time it - that gives you the length, the shape "
     "and the budget in one afternoon and costs nothing to throw away. Then a shot list of what each "
     "beat needs, then the cheapest thing that could carry each beat: a card, a still with a move, a "
     "chart, a clip. Build the whole thing ugly end to end before making any of it good.",
     [("part_of", "craft.symptoms"), ("see_also", "principle.record_first"),
      ("see_also", "craft.animatic"), ("see_also", "craft.assembly")],
     ["symptom", "planning"]),
]

SUPPORT = []
