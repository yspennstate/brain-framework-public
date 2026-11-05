# -*- coding: utf-8 -*-
"""Editing and sound. Where a film is actually made, and where half of it is heard."""

DOMAIN = ("craft.editing", "editing", "The only stage where the film exists as a whole.")

NODES = [
    # ---------------- the cut ----------------
    ("craft.cut", "the cut",
     "An instantaneous change of image. The audience accepts it without noticing because human "
     "attention already works in cuts - you do not pan from a face to a hand, you look. Everything "
     "else in editing is a special case of deciding when that jump is invisible and when it is felt.",
     [("part_of", "craft.editing")], ["editing"]),

    ("craft.murch_six", "Murch's six criteria",
     "In order of importance: emotion, story, rhythm, eye-trace, two-dimensional plane of the screen, "
     "three-dimensional space of the action. Emotion is worth more than the other five together, and "
     "continuity - the thing amateurs agonise over - is last. The practical use is as a tie-breaker: "
     "when two cuts both work, take the one higher on the list.",
     [("part_of", "craft.editing"), ("applies_to", "craft.cut")], ["editing", "theory"]),

    ("craft.cut_on_action", "cut on action",
     "Cut in the middle of a movement and the eye follows the movement across the join instead of "
     "noticing the join. The most reliable invisible cut there is. It works in animation and in "
     "AI-generated shots too - match the direction and the speed, not just the pose.",
     [("is_a", "craft.cut"), ("prevents", "failure.visible_cut")], ["editing"]),

    ("craft.match_cut", "match cut",
     "Two shots joined because their shapes, motions or ideas rhyme. It is the one cut that draws "
     "attention to itself and is still admired for it, because the rhyme carries an argument the "
     "narration would have had to state.",
     [("is_a", "craft.cut"), ("produces", "effect.association")], ["editing"]),

    ("craft.jump_cut_technique", "jump cut, used on purpose",
     "Cut out the middle of a take and let the subject snap. Once a mistake, now the native grammar "
     "of talking-head internet video: it removes every pause and doubles the density of a piece. The "
     "cost is that it destroys any sense of a real, continuous person, which is exactly what a "
     "documentary interview needs to keep.",
     [("is_a", "craft.cut"), ("conflicts_with", "craft.documentary")], ["editing", "web"]),

    ("craft.shot_reverse_shot", "shot / reverse shot",
     "The default grammar of a conversation. It works because of the 180-degree rule and the eyeline "
     "match; when a conversation feels wrong, one of those two is broken, not the pace.",
     [("part_of", "craft.editing"), ("requires", "craft.180_rule"),
      ("requires", "craft.eyeline_match")], ["editing"]),

    ("craft.j_cut", "J-cut",
     "The sound of the next shot arrives before its picture. The most useful transition in "
     "documentary and lecture work: the new voice pulls the audience across the cut, so the cut lands "
     "as an answer rather than an interruption.",
     [("is_a", "craft.sound_transition"), ("prevents", "failure.visible_cut")], ["editing", "sound"]),

    ("craft.l_cut", "L-cut",
     "The sound of the current shot continues under the next picture. Lets you show a reaction while "
     "someone keeps talking - which is usually the more interesting half of a conversation.",
     [("is_a", "craft.sound_transition")], ["editing", "sound"]),

    ("craft.cross_cutting", "cross-cutting / parallel action",
     "Alternate between two lines of action to imply they are simultaneous. It also builds tension "
     "for free, because every cut away from a question defers its answer.",
     [("part_of", "craft.editing"), ("produces", "effect.suspense")], ["editing", "structure"]),

    ("craft.montage", "montage",
     "A compressed sequence where meaning comes from the collision of images rather than from any one "
     "of them. Eisenstein's point, still true: two shots produce a third idea that is in neither. The "
     "modern lazy version is a series of pretty images under music, which produces no third idea at "
     "all.",
     [("part_of", "craft.editing"), ("produces", "effect.association")], ["editing", "theory"]),

    ("craft.kuleshov", "the Kuleshov effect",
     "The same neutral face cut against soup, a coffin, a child reads as hunger, grief, tenderness. "
     "Context, not performance, does the work. It is the licence for cutting an interview to a "
     "reaction shot, and the reason that practice is ethically loaded in documentary.",
     [("part_of", "craft.editing"), ("produces", "effect.association")], ["editing", "theory"]),

    ("craft.pacing", "pacing",
     "How fast the audience is asked to take things in. It is not shot length: a long take can race "
     "and a fast montage can drag. What actually sets pace is the rate at which new information "
     "arrives, and the strongest editing tool is deciding to withhold some.",
     [("part_of", "craft.editing")], ["editing", "rhythm"]),

    ("craft.rhythm", "rhythm",
     "The pattern of durations across a sequence. Cut to a regular beat and the audience relaxes into "
     "it; break the pattern and they sit up. In a lecture the equivalent is the length of the beats "
     "between ideas, and unbroken regularity there is what makes a good lecture feel like a metronome.",
     [("part_of", "craft.editing"), ("applies_to", "craft.narration_pacing")], ["editing", "rhythm"]),

    ("craft.breathing_room", "breathing room",
     "A moment with no new information after something important. Almost every first cut is missing "
     "them, because the editor knows the material and the audience does not. If a section feels "
     "confusing, the fix is often two seconds of silence rather than another sentence.",
     [("part_of", "craft.pacing"), ("prevents", "failure.no_room_to_think")], ["editing", "rhythm"]),

    ("craft.assembly", "assembly, rough, fine",
     "Assembly: every usable take in order, far too long. Rough: the shape of the thing, structure "
     "settled. Fine: frames. The discipline is to not polish before the structure holds - most wasted "
     "editing time is spent perfecting a scene that later comes out.",
     [("part_of", "craft.editing"), ("step_of", "craft.post")], ["editing", "process"]),

    ("craft.coverage", "coverage",
     "Every angle you shot of a scene. The editor can only cut what exists, so the real question at "
     "the shoot is not 'is this the shot' but 'what will I need to escape this shot'. Wide, mediums, "
     "singles, an insert and a cutaway is the honest minimum.",
     [("part_of", "craft.production")], ["editing", "production"]),

    ("craft.continuity", "continuity",
     "Whether the world stays consistent across cuts: screen direction, eyelines, props, light, "
     "energy. Real continuity errors that matter are the ones that break geography or emotion; a "
     "glass at a slightly different level is not one.",
     [("part_of", "craft.editing")], ["editing"]),

    ("craft.transition", "transitions",
     "A cut, a dissolve, a fade, a wipe. The plain cut carries no meaning and is therefore right "
     "almost always. A dissolve means time passed or these two things are connected. A fade to black "
     "is a full stop. A wipe is a period style. Anything else is a plugin and it shows.",
     [("part_of", "craft.editing"), ("implemented_by", "tool.ffmpeg")], ["editing"]),

    ("craft.dissolve", "dissolve",
     "One image fades through another. In documentary it is the standard way to move between "
     "archive stills without implying they are one continuous scene. Length matters: under half a "
     "second reads as a soft cut, over two seconds reads as dreaming.",
     [("is_a", "craft.transition")], ["editing"]),

    ("craft.cut_covering", "covering a cut",
     "Hiding a join you had to make - a fluffed line, a removed digression, a missing frame - "
     "behind a cutaway, an insert, a whip, a sound event or a graphic. In a narrated lecture this is "
     "how a rewritten sentence stays invisible.",
     [("part_of", "craft.editing"), ("requires", "craft.cutaway")], ["editing"]),

    ("craft.montage_of_attractions", "the six-second rule for web",
     "On a platform where the audience chose nothing, the first six seconds decide whether the rest "
     "is watched. That is a distribution fact, not an aesthetic one, and it deserves a deliberate "
     "answer rather than either ignoring it or letting it design the whole film.",
     [("part_of", "craft.editing"), ("applies_to", "craft.delivery")], ["editing", "web"]),

    # ---------------- sound ----------------
    ("craft.sound", "sound",
     "Half the picture, and the half an audience forgives least. People will watch a soft, "
     "badly-framed image with clean sound for an hour; they will not watch three minutes of sharp "
     "4K with a hollow room mic. If time is short, spend it here.",
     [("part_of", "craft.post")], ["sound"]),

    ("craft.dialogue_recording", "recording dialogue",
     "Get the microphone close. That single decision beats every choice of microphone, preamp and "
     "plugin, because the ratio of voice to room falls off with distance and no processing puts it "
     "back. A cheap mic at 20 cm beats an expensive one at two metres.",
     [("part_of", "craft.sound"), ("prevents", "failure.roomy_dialogue")], ["sound", "production"]),

    ("craft.room_tone", "room tone",
     "Thirty seconds of the room doing nothing, recorded at every location. It is the glue that "
     "lets you cut dialogue at all - without it every edit is a hole where the ambience stops. Nobody "
     "regrets recording it; everybody regrets not having it.",
     [("part_of", "craft.sound"), ("prevents", "failure.audio_hole")], ["sound", "production"]),

    ("craft.ambience", "ambience / atmos",
     "The continuous bed of a place. Its job is to make cuts within a scene feel like one space and "
     "to tell the audience where they are without a word. A quiet interior bed under a narrated "
     "sequence is the cheapest way to stop it sounding like a voice in a vacuum.",
     [("part_of", "craft.sound"), ("prevents", "failure.dead_air")], ["sound", "design"]),

    ("craft.foley", "foley",
     "Performed sound for what is on screen - footsteps, cloth, handling. It is not realism, it is "
     "presence: real recordings of the same actions usually sound thin, which is why foley is "
     "performed rather than sampled.",
     [("part_of", "craft.sound")], ["sound", "design"]),

    ("craft.sound_design", "sound design",
     "Everything that is neither dialogue nor score. Its most powerful move is offscreen sound: what "
     "the audience hears but does not see is built by their own imagination and is therefore always "
     "better than what you could have shown.",
     [("part_of", "craft.sound"), ("produces", "effect.offscreen_space")], ["sound", "design"]),

    ("craft.score", "score",
     "Music written or chosen for the piece. The hardest discipline is restraint - music under "
     "everything makes nothing feel like anything. Music that starts is far more powerful than music "
     "that has been playing.",
     [("part_of", "craft.sound")], ["sound", "music"]),

    ("craft.silence", "silence",
     "The loudest tool in the box, and it only works if the rest of the piece has been full. In a "
     "lecture, a two-second silence after a definition does more for retention than repeating it.",
     [("part_of", "craft.sound"), ("used_in", "craft.narration")], ["sound", "rhythm"]),

    ("craft.mixing", "the mix",
     "Balancing every element so the audience never works to hear the thing that matters. The "
     "practical order: dialogue first and alone, then effects around it, then music last and quieter "
     "than you want. Check on a phone speaker and in a car, not only on headphones.",
     [("part_of", "craft.sound"), ("requires", "craft.loudness_standard")], ["sound", "post"]),

    ("craft.loudness_standard", "loudness normalisation",
     "Platforms normalise to a target, so mixing louder gains nothing and costs dynamics. Practical "
     "targets: about -14 LUFS integrated for YouTube and Spotify, -16 for podcasts, -23 for European "
     "broadcast, -19 for the audiobook chain we already use. True peak should stay under -1 dBTP so "
     "lossy encoding does not clip.",
     [("part_of", "craft.mixing"), ("measured_by", "metric.lufs"),
      ("implemented_by", "tool.ffmpeg")], ["sound", "delivery"]),

    ("craft.eq_for_voice", "EQ for a voice",
     "A high-pass around 70 to 90 Hz to remove rumble that carries no voice; a gentle dip around "
     "200 to 400 Hz if the voice is boxy; a lift around 2 to 5 kHz for intelligibility; and a "
     "de-esser above 6 kHz if the sibilants hurt. Small moves. A voice that needs large moves was "
     "recorded wrong.",
     [("part_of", "craft.mixing"), ("applies_to", "craft.narration")], ["sound", "voice"]),

    ("craft.compression_audio", "compression",
     "Reduce the distance between loud and quiet so a voice sits steadily in a mix. For narration a "
     "gentle 2:1 to 3:1 with a slow-ish attack keeps consonants intact while levelling the "
     "sentence-to-sentence drift. Heavy compression is what makes a read sound like an advert.",
     [("part_of", "craft.mixing"), ("applies_to", "craft.narration"),
      ("causes", "failure.overcompressed_voice")], ["sound", "voice"]),

    ("craft.reverb", "reverb",
     "Puts a voice in a space. A completely dry narration sounds synthetic; a tiny room - under "
     "0.4 seconds decay, mixed low - reads as 'recorded somewhere' without reading as reverb. This "
     "is one of the most effective and least used fixes for synthetic-sounding speech.",
     [("part_of", "craft.mixing"), ("applies_to", "craft.tts_naturalness")], ["sound", "voice", "tts"]),

    ("craft.sound_transition", "sound transitions",
     "Sound crossing a picture cut, either before it or after it. The cheapest way to make an edit "
     "feel intentional.",
     [("part_of", "craft.sound")], ["sound", "editing"]),

    ("craft.sync", "sync",
     "Picture and sound landing together. Human tolerance is asymmetric - sound arriving up to about "
     "45 ms late is barely noticed, but sound arriving early is caught at around 15 ms, because "
     "nothing in nature does that. Err late, never early.",
     [("part_of", "craft.sound"), ("prevents", "failure.sync_drift")], ["sound", "editing"]),

    ("craft.drift", "drift",
     "Sound and picture that start together and separate over minutes. Caused by mismatched sample or "
     "frame rates, or by rebuilding one side and not the other. The structural cure is to never "
     "assume a duration: measure what was actually produced and build the other side from that "
     "measurement. That single rule is what makes our lecture pipeline hold together for an hour.",
     [("is_a", "failure.sync_drift"), ("fixes", "principle.record_first")],
     ["sound", "editing", "pipeline"]),
]

SUPPORT = [
    ("effect.association", "effect", "association", "Two things placed together produce a third idea that is in neither."),
    ("effect.suspense", "effect", "suspense", "The audience has a question and is made to wait."),
    ("effect.offscreen_space", "effect", "offscreen space", "The world continues beyond the frame because it can be heard."),
    ("metric.lufs", "metric", "LUFS", "Loudness units relative to full scale, integrated over a programme."),
    ("failure.visible_cut", "failure", "the cut is noticed",
     "The audience registers the edit rather than the content. Usually a cut on stillness, a "
     "mismatched energy, or a cut with no reason to be there."),
    ("failure.no_room_to_think", "failure", "no room to think",
     "New information arrives before the last piece landed. Symptom: viewers say a piece is "
     "confusing when it is merely relentless."),
    ("failure.roomy_dialogue", "failure", "hollow, roomy dialogue",
     "The microphone was too far away. Not fixable in post; de-reverb tools trade the room for "
     "artefacts."),
    ("failure.audio_hole", "failure", "a hole in the ambience at a cut"),
    ("failure.dead_air", "failure", "digital silence under a voice",
     "Absolute zero between sentences reads as synthetic. Real recordings always have a floor."),
    ("failure.sync_drift", "failure", "sound and picture drift apart"),
    ("failure.overcompressed_voice", "failure", "over-compressed voice",
     "Breaths as loud as words, no dynamic shape, an audible pumping under the sentence."),
    ("principle.record_first", "principle", "record first, cut the picture to the words",
     "For any narrated piece: synthesise or record the narration first, MEASURE its real duration "
     "beat by beat, and build the visuals from those measurements. Never write a duration into the "
     "animation and hope the read matches. Proven over an hour-long lecture - words and picture "
     "cannot drift even when an animation overruns."),
]
