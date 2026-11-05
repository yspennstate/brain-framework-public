# -*- coding: utf-8 -*-
"""
Recipes: procedures short enough to follow, specific enough to run.

A craft node says what a thing is and why. A recipe says do this, then this,
with the actual command. These are the nodes that will earn their keep at two
in the morning when something has to be finished.

Every command here is written for the tools on THIS machine - ffmpeg 8.1.2
gyan full, Blender 5.2, python 3.14 - and paths use forward slashes because
this shell eats backslashes.
"""

DOMAIN = ("craft.recipes", "recipes", "Procedures, with the commands.")

NODES = [
    ("recipe.stills_to_documentary", "turn a folder of stills into a documentary sequence",
     "1. Order the stills and name them zero-padded. 2. Record the narration FIRST and measure each "
     "beat's real duration. 3. Give each still a slice of that measured time, never a guessed one. "
     "4. Move each one slowly - a push or a drift under three per cent of frame width a second, eased "
     "at both ends. 5. Cross-dissolve between them at 0.5 to 0.8 seconds so nobody reads them as one "
     "continuous scene. 6. Grade them to a single look, because archive material never matches. "
     "7. Grain over the whole thing at the end, which glues the mismatched sources together.\n"
     "  ffmpeg -loop 1 -t 6 -i img.png -vf \"zoompan=z='min(zoom+0.0006,1.10)':d=150:"
     "s=1920x1080:fps=25,format=yuv420p\" -c:v libx264 -crf 18 out.mp4",
     [("part_of", "craft.recipes"), ("requires", "principle.record_first"),
      ("requires", "craft.ken_burns"), ("requires", "craft.grain"),
      ("used_in", "craft.documentary")], ["recipe", "documentary", "stills"]),

    ("recipe.contact_sheet", "look at every shot at once",
     "The fastest quality control there is. One frame from every clip, tiled into one image; "
     "mismatched grades, black frames, wrong aspect ratios and duplicate shots all show up in a "
     "glance and none of them show up in a log.\n"
     "  ffmpeg -i in.mp4 -vf \"fps=1/10,scale=320:-1,tile=6x5\" -frames:v 1 sheet.png\n"
     "For a folder of clips, take frame 12 of each and montage them with ImageMagick.",
     [("part_of", "craft.recipes"), ("implemented_by", "tool.ffmpeg"),
      ("prevents", "failure.unseen_overlap")], ["recipe", "qa"]),

    ("recipe.narration_first_animation", "cut the picture to the words",
     "The rule that makes an hour-long narrated piece hold together. 1. Write the beats as data, "
     "each carrying its own text and a written pause. 2. Synthesise every beat and MEASURE what came "
     "out. 3. Render the animation so each beat waits until it has lasted exactly narration plus "
     "pause, and have the scene WRITE what it actually spent to a timing file. 4. Build the audio "
     "track from those measured seconds. Words and picture then cannot drift even when an animation "
     "overruns.\n"
     "Two traps we have already paid for: a chapter must refuse to render until every beat's wav "
     "matches the CURRENT text (compare a hash), or an edited line renders against the old audio; "
     "and a chapter must announce the number it is PLAYED at, derived from the running order in one "
     "place, or 23 of 28 chapters announce the wrong one.",
     [("part_of", "craft.recipes"), ("requires", "principle.record_first"),
      ("prevents", "failure.sync_drift"), ("used_in", "craft.lecture")],
     ["recipe", "narration", "animation"]),

    ("recipe.make_a_render_look_shot", "stop a render looking computer-generated",
     "In the order that buys the most per minute of work. 1. Turn motion blur ON - it is off by "
     "default in every engine and its absence is the loudest tell. 2. Give the camera a real depth "
     "of field with an aperture shape, not a background blur. 3. Add a contact shadow wherever "
     "something touches something. 4. Break the perfect symmetry - nothing in the world is aligned "
     "or clean. 5. Put a light behind the subject. 6. In the comp: a touch of chromatic aberration "
     "at the edges, a highlight bloom, a lens vignette. 7. Grain last, over everything, so all the "
     "layers share one texture.\n"
     "  ffmpeg -i r.mp4 -vf \"unsharp=5:5:0.3,vignette=PI/5,noise=alls=4:allf=t+u\" out.mp4",
     [("part_of", "craft.recipes"), ("prevents", "failure.cgi_look"),
      ("requires", "craft.motion_blur"), ("requires", "craft.grain"),
      ("requires", "craft.plate_matching")], ["recipe", "3d", "vfx"]),

    ("recipe.upscale_small_render", "make a small render deliverable",
     "What we do to every 640x352 LTX clip. Lanczos to the target, a modest unsharp, a small "
     "contrast lift, then grain - and the grain is not decoration, it is what stops the upscale "
     "reading as plastic.\n"
     "  ffmpeg -i in.mp4 -vf \"scale=1920:1080:flags=lanczos,unsharp=5:5:0.6:5:5:0.0,"
     "eq=contrast=1.04,noise=alls=4:allf=t+u\" -c:v libx264 -crf 17 out.mp4\n"
     "Do not reach for a learned upscaler on text or fine structure - it invents.",
     [("part_of", "craft.recipes"), ("requires", "craft.upscaling"),
      ("prevents", "failure.plastic_upscale")], ["recipe", "ai", "post"]),

    ("recipe.voice_humanise", "make a synthetic read less obviously synthetic",
     "In this order, because each step changes what the next one acts on. 1. Synthesise SENTENCE BY "
     "SENTENCE, not paragraph by paragraph, so the gaps are ours to choose. 2. Trim each sentence's "
     "own silence. 3. Reassemble with pause lengths drawn per boundary type - roughly 0.2 s at a "
     "comma, 0.5 at a full stop, 1.0 at a paragraph - sampled from a lognormal so they are never "
     "identical. 4. Vary the reading rate a couple of per cent per sentence. 5. Put a real breath "
     "at the end of the long gaps. 6. A tiny room, well under audibility. 7. Gentle compression. "
     "8. A noise floor around -60 dBFS, because nothing recorded is digitally silent. "
     "9. Loudness normalise LAST.\n"
     "  narration_lab/narrate_natural.py texts/x.txt out.wav --voice bm_lewis",
     [("part_of", "craft.recipes"), ("fixes", "failure.robotic"),
      ("requires", "craft.pause_grammar"), ("requires", "craft.breath")],
     ["recipe", "narration", "tts"]),

    ("recipe.blind_listening_test", "find out which take is actually better",
     "1. Same text, same length, matched loudness - normalise every take to the same LUFS or the "
     "louder one wins. 2. Randomise the order and strip the labels from the filenames. 3. Announce "
     "each take by a NUMBER in a clearly different voice, so the label and the sample never blur. "
     "4. Ask one question at a time: which sounds more like a person. 5. Reveal afterwards. "
     "Anything less and you are measuring what the maker hoped for.",
     [("part_of", "craft.recipes"), ("requires", "craft.blind_ab"),
      ("prevents", "failure.self_favouring_judgement")], ["recipe", "method", "tts"]),

    ("recipe.timeline_from_script", "write a timeline a human can then edit",
     "Generate an MLT XML or an OpenTimelineIO file rather than a flat concat, and Kdenlive or "
     "Shotcut will open it with every clip on its own track at its own in and out. That turns a "
     "generated assembly into a starting point instead of a finished object, which is the difference "
     "between a tool and a toy.",
     [("part_of", "craft.recipes"), ("implemented_by", "tool.kdenlive"),
      ("requires", "craft.edl_interchange")], ["recipe", "editing", "pipeline"]),

    ("recipe.blender_headless", "drive Blender from a script",
     "  blender --background --factory-startup --python job.py -- args\n"
     "--factory-startup matters: it ignores whatever preferences and add-ons happen to be enabled, "
     "so the render is the same on any machine. Set the engine, samples, resolution, frame range and "
     "output path in the script, never in the file, so one .blend can produce every variant. Frames "
     "are embarrassingly parallel - split the range across processes and let the assembler take "
     "whatever is complete.",
     [("part_of", "craft.recipes"), ("implemented_by", "tool.blender"),
      ("requires", "craft.render_farm")], ["recipe", "3d", "pipeline"]),

    ("recipe.check_a_cut", "watch it properly before calling it done",
     "1. A contact sheet of every shot. 2. Watch the whole thing once at speed with no notes and "
     "only afterwards write down what you felt. 3. Watch it on a phone with phone speakers. "
     "4. Watch the first ten seconds five times. 5. Read the transcript alone, with no picture - if "
     "the argument does not survive that, no amount of picture will save it.",
     [("part_of", "craft.recipes"), ("requires", "craft.review_loop")], ["recipe", "qa"]),

    ("recipe.gpu_video_budget", "decide whether a generated sequence is affordable",
     "Measure ONE clip end to end on the machine that will do the work, including the failures, then "
     "multiply. On this card, LTX-2.5 costs about 13 minutes per finished clip at a one-in-three "
     "success rate, so eight seconds of usable film is 13 minutes and an hour of film is roughly 97 "
     "GPU-hours. Say that out loud before promising an hour, and design the piece so generated clips "
     "are inserts on a cheaper spine.",
     [("part_of", "craft.recipes"), ("requires", "craft.ai_as_texture"),
      ("measured_by", "metric.gpu_minutes_per_clip")], ["recipe", "ai", "planning"]),

    ("recipe.loudness_deliver", "deliver at the right loudness",
     "Two-pass loudnorm, because the single-pass version guesses.\n"
     "  ffmpeg -i in.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null -\n"
     "then feed the measured values back:\n"
     "  ffmpeg -i in.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=..:measured_TP=..:"
     "measured_LRA=..:measured_thresh=..:linear=true out.wav\n"
     "-14 LUFS for YouTube, -16 for podcast, -19 for our audiobook chain, -23 for EBU broadcast.",
     [("part_of", "craft.recipes"), ("requires", "craft.loudness_standard")],
     ["recipe", "audio", "delivery"]),

    ("recipe.deepen_a_voice", "lower a synthetic voice without wrecking it",
     "Two ways, and they fail differently. rubberband with formant=preserved keeps the speaker the "
     "same size and only drops the pitch - measured here, it costs shimmer, rising from 13.4 per "
     "cent at source to 20 per cent at five semitones down. asetrate plus atempo moves the formants "
     "with the pitch, holds shimmer steady, and makes the speaker sound physically larger, which "
     "past about three semitones stops being the same person.\n"
     "  ffmpeg -i in.wav -af \"rubberband=pitch=0.891:formant=preserved\" out.wav   # 2 st down\n"
     "Neither improves the vocoder noise. Depth relocates the artefacts, it does not remove them - "
     "so prefer a natively deep voice to a shifted one.",
     [("part_of", "craft.recipes"), ("requires", "craft.pitch_shifting"),
      ("applies_to", "craft.deep_voice_hypothesis")], ["recipe", "tts", "measured"]),
]

SUPPORT = []
