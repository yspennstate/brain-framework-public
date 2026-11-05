# -*- coding: utf-8 -*-
"""
Sound design and music in the detail that actually gets used. Half the picture,
and the half an audience forgives least.
"""

DOMAIN = ("craft.sound_design_detail", "sound design in detail",
          "The specific moves, not the principle.")

NODES = [
    # ---------------- the spine of a mix ----------------
    ("craft.mix_order", "the order of a mix",
     "Dialogue alone first, until it is intelligible and steady with nothing else playing. Then "
     "effects placed around it. Then ambience under both. Then music last and quieter than you want. "
     "Working in any other order means every later element forces you to redo the earlier ones, and "
     "it is why amateur mixes have music fighting the voice.",
     [("part_of", "craft.mixing")], ["sound", "mix"]),

    ("craft.headroom_audio", "leave headroom",
     "Mix with peaks around -6 dBFS and let the loudness normalisation at the end bring it up. A mix "
     "that is already touching zero has nowhere to put a moment that should be loud, and the "
     "platform will turn it down anyway.",
     [("part_of", "craft.mixing"), ("requires", "craft.loudness_standard")], ["sound", "mix"]),

    ("craft.ducking", "ducking",
     "Automatically lowering the music when the voice speaks. Done with a sidechain compressor it is "
     "invisible; done crudely it pumps and everybody hears it. Better than either for a narrated "
     "piece: write the music level by hand, because the places it should drop are decided by meaning "
     "rather than by amplitude.",
     [("part_of", "craft.mixing"), ("causes", "failure.pumping_music")], ["sound", "mix"]),

    ("craft.music_entry", "where music starts is the whole decision",
     "Music that starts is powerful; music that has been playing is wallpaper. Start it on a turn - "
     "a revelation, a change of subject, the moment a question is answered - and let it run out "
     "rather than fading it, if the shape allows. The commonest fault in an explainer is a bed that "
     "runs from the first frame to the last and therefore means nothing.",
     [("part_of", "craft.score"), ("prevents", "failure.wallpaper_music")], ["sound", "music"]),

    ("craft.music_no_lyrics", "no words under words",
     "Lyrics compete directly with narration for the same processing. Any music under speech must be "
     "instrumental, and preferably without a strong melodic hook, because a memorable tune is also a "
     "distracting one.",
     [("part_of", "craft.score")], ["sound", "music"]),

    ("craft.music_licensing", "where music can come from",
     "Public-domain recordings, Creative Commons work with attribution honoured, libraries you have "
     "actually licensed, or something you make. A track lifted from anywhere else will be found by "
     "an automated system, and the penalty is usually the whole video's revenue rather than a "
     "takedown. Record the licence beside the file.",
     [("part_of", "craft.score"), ("requires", "craft.licence_hygiene")], ["sound", "legal"]),

    ("craft.sound_transition_detail", "sound across a cut",
     "Three moves worth having. Carry the ambience across so the cut is a change of view rather than "
     "a change of world. Bring the next scene's sound in early - a J-cut - so the audience is pulled "
     "over. Or cut the sound clean with the picture for a hard break, which is the loudest edit "
     "available and should be spent rarely.",
     [("part_of", "craft.sound_transition")], ["sound", "editing"]),

    ("craft.silence_as_punctuation", "cut everything for one second",
     "A complete drop to near-silence for about a second before something important is the strongest "
     "attention device in sound, and it costs nothing. It only works if the piece has been full - in "
     "a sparse mix it reads as a fault.",
     [("part_of", "craft.silence")], ["sound", "rhythm"]),

    # ---------------- voice in a mix ----------------
    ("craft.voice_seat", "seating a voice in a mix",
     "The order that works: high-pass first to remove what carries no voice, then subtractive EQ to "
     "take out what is boxy, then compression to level it, then a small amount of additive EQ for "
     "presence, then de-essing only if it hurts, then the room. Additive EQ before compression makes "
     "the compressor chase what you just boosted.",
     [("part_of", "craft.mixing"), ("requires", "craft.eq_for_voice")], ["sound", "voice"]),

    ("craft.two_voices", "two voices in one piece",
     "If a piece has a narrator and an interviewee, they must sit differently: different level, "
     "different room, different EQ. Matching them makes the audience work out who is speaking. The "
     "narrator is usually drier, closer and slightly quieter.",
     [("part_of", "craft.mixing")], ["sound", "voice"]),

    ("craft.plosive", "plosives and sibilance",
     "A p or b hitting the microphone makes a low thump no EQ removes cleanly - the cure is a pop "
     "shield and speaking across the microphone rather than into it. Sibilance is the opposite "
     "problem, an excess around 6 to 9 kHz, and a de-esser handles it; but a voice that needs heavy "
     "de-essing was recorded too close or too bright.",
     [("part_of", "craft.dialogue_recording"), ("prevents", "failure.plosive_thump")],
     ["sound", "voice"]),

    ("craft.noise_reduction", "noise reduction is a trade",
     "Every de-noiser removes signal along with noise, and the artefacts it leaves - a watery, "
     "swirling quality on the residue - are more distracting than the hiss was. Take out a few dB "
     "and stop. A better answer is usually to raise the voice rather than lower the noise.",
     [("part_of", "craft.mixing"), ("causes", "failure.denoise_artefacts")], ["sound", "repair"]),

    # ---------------- design ----------------
    ("craft.offscreen_sound", "the most powerful sound is the one you do not show",
     "What is heard and not seen is built by the audience's own imagination and is therefore always "
     "better than what you could have shown. It is also free. In documentary and explainer work this "
     "is how you cover material you do not have footage of.",
     [("part_of", "craft.sound_design"), ("produces", "effect.offscreen_space")], ["sound", "design"]),

    ("craft.sound_scale", "sound sets scale",
     "How big something is comes from its low end and its reverberation, not from the picture. The "
     "same shot of a door reads as a cupboard or a cathedral depending on the tail on the sound. "
     "This is the cheapest way to make a small set feel large.",
     [("part_of", "craft.sound_design")], ["sound", "design"]),

    ("craft.layered_effects", "one sound is never one sound",
     "A convincing effect is three or four layers doing different jobs: a low element for weight, a "
     "mid for body, a high for detail, and often something organic that has nothing to do with the "
     "object. A single recording of the real thing almost always sounds thin.",
     [("part_of", "craft.sound_design")], ["sound", "design"]),

    ("craft.sound_before_picture", "sound can arrive before its picture",
     "Putting a sound a few frames before the image it belongs to makes the cut feel motivated and "
     "the moment feel anticipated. Late is forgiven to about 45 milliseconds, early is caught at 15 "
     "- so this is a deliberate placement of a whole event, not a sync error.",
     [("part_of", "craft.sound_design"), ("requires", "craft.sync")], ["sound", "editing"]),

    ("craft.ui_sound", "sounds for graphics",
     "Motion graphics want sound, and the trap is a library whoosh on every element. Use one soft, "
     "short, low-mid element for an appearance and let most things arrive silently. If every "
     "movement makes a noise, none of them mean anything.",
     [("part_of", "craft.sound_design"), ("applies_to", "craft.motion_graphics")],
     ["sound", "graphics"]),

    ("craft.stereo_field", "stereo for a talking piece",
     "Narration goes in the centre, mono, always. Ambience and music can be wide. Anything panned "
     "hard in a piece someone might watch on a phone speaker will simply vanish, so nothing "
     "essential goes to one side.",
     [("part_of", "craft.mixing")], ["sound", "mix"]),

    ("craft.check_on_bad_speakers", "check it where it will be heard",
     "A phone speaker reproduces almost nothing below 500 Hz. A mix that depends on low end for its "
     "weight will sound thin and possibly incoherent there, and that is where most of your audience "
     "is. Check on a phone, in a car, and on cheap earbuds before anything else.",
     [("part_of", "craft.mixing"), ("prevents", "failure.mix_only_works_on_headphones")],
     ["sound", "mix", "delivery"]),
]

SUPPORT = [
    ("failure.pumping_music", "failure", "the music pumps under the voice",
     "A sidechain compressor set too fast or too deep. The audience hears the mechanism."),
    ("failure.wallpaper_music", "failure", "music that runs from first frame to last",
     "It stops meaning anything, and it removes the possibility of music meaning something later."),
    ("failure.plosive_thump", "failure", "plosive thumps",
     "A p or b hitting the capsule. No EQ removes it cleanly; a shield and an off-axis angle prevent it."),
    ("failure.denoise_artefacts", "failure", "watery de-noising",
     "The residue swirls and is more distracting than the noise. A few dB and stop."),
    ("failure.mix_only_works_on_headphones", "failure", "a mix that only works on headphones",
     "It depends on low end a phone speaker cannot reproduce, and most of the audience is on one."),
]
