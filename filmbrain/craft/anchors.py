# -*- coding: utf-8 -*-
"""
Bodies for the anchor nodes.

These ids were created by the harvesters and the cross-linker as places to hang
things from, and were left empty. An empty node is a dead end in a graph a
person walks: they arrive at "rendering" from a Blender operator and find
nothing. The self-test flags them, and this file answers it.
"""

DOMAIN = ("craft.filmmaking", "filmmaking",
          "The whole activity: deciding what to show, making it, and putting it in order.")

NODES = [
    ("craft.rendering", "rendering",
     "Turning a scene into pixels. The two questions that decide everything: how the light is "
     "computed - path traced like Cycles, which is slow and behaves like light, or rasterised like "
     "EEVEE, which is fast and needs its bounce faked - and how many samples before the noise is "
     "acceptable. On this machine that difference is sixteen to one, 4 seconds a frame against 67, "
     "and most explainer work does not need path tracing.",
     [("part_of", "craft.post"), ("see_also", "machine.blender_speed")], ["3d", "render"]),

    ("craft.modelling", "modelling",
     "Building the geometry. For film work the only thing that matters is the silhouette and the "
     "contact: a shape that does not read as a black outline will not read at all, and an object "
     "that does not visibly touch what it stands on floats no matter how good the material is.",
     [("part_of", "craft.3d")], ["3d"]),

    ("craft.materials_shading", "materials and shading",
     "What a surface does to light. Roughness carries more information than colour - it is how you "
     "tell metal from plastic from skin - and the commonest error in a first render is everything "
     "at the same roughness, which reads as one substance painted different colours.",
     [("part_of", "craft.3d")], ["3d", "look"]),

    ("craft.texturing", "texturing",
     "Putting detail on a surface that the geometry does not have. Its real job in film is breaking "
     "uniformity: real things are dirty, worn unevenly and marked where they are touched, and a "
     "perfectly even surface is one of the loudest computer-generated tells.",
     [("part_of", "craft.3d"), ("prevents", "failure.cgi_look")], ["3d", "look"]),

    ("craft.rigging", "rigging",
     "Giving a model controls an animator can actually use. The measure of a rig is not what it can "
     "do but how fast a pose can be found: a rig with two hundred controls and no sensible defaults "
     "is slower than no rig at all.",
     [("part_of", "craft.animation")], ["3d", "animation"]),

    ("craft.scene_layout", "scene layout",
     "Where everything is in the space, and where the camera stands relative to it. This is blocking "
     "for a render, and the same rule applies: three layers of depth - something near, the subject, "
     "something behind - is what stops a frame reading flat.",
     [("part_of", "craft.3d"), ("requires", "craft.depth_layers")], ["3d", "composition"]),

    ("craft.procedural_geometry", "procedural geometry",
     "Building shapes by rule rather than by hand - scattering, arraying, growing, deforming along a "
     "curve. It scales to a thousand instances at no extra authoring cost, which is exactly what a "
     "crowd, a forest or a city needs, and it has no taste: without a hand-placed variation pass it "
     "produces a thousand identical things and reads as wallpaper.",
     [("part_of", "craft.3d"), ("implemented_by", "tool.blender")], ["3d", "technical"]),

    ("craft.animation_curves", "animation curves",
     "The graph editor, where the actual timing lives. Keyframes say where; the curve between them "
     "says how, and that is the whole difference between mechanical and alive. If a move looks "
     "wrong, the fix is almost always in the curve rather than in another keyframe.",
     [("part_of", "craft.animation"), ("requires", "craft.easing")], ["animation"]),

    ("craft.motion_tracking", "motion tracking",
     "Recovering camera or object motion from footage so something added sticks to it. The solve "
     "wants parallax, sharp high-contrast features spread across the frame, and a known focal "
     "length; it fails on locked-off shots, on motion blur and on featureless sky.",
     [("part_of", "craft.vfx"), ("implemented_by", "tool.blender")], ["vfx"]),

    ("craft.digital_painting", "digital painting",
     "Making images by hand rather than by camera or render: matte paintings, textures, backgrounds, "
     "title art, chalk strokes. The thing it buys that no generator does is EXACTLY what you "
     "intended, which matters most in the frames an audience looks at longest.",
     [("part_of", "craft.making"), ("implemented_by", "tool.krita")], ["2d", "art"]),

    ("craft.typography", "typography",
     "Setting type. On screen it differs from print in three ways that matter: it is read at an "
     "uncontrolled size, it sits over a moving image, and it disappears on a schedule - so larger, "
     "higher contrast, and on long enough to be read twice.",
     [("part_of", "craft.title_design"), ("see_also", "craft.screen_typography")],
     ["titles", "design"]),

    ("craft.viewport", "the viewport",
     "What you see while working, which is not what the render will look like. Every viewport lies "
     "in a specific direction - wrong colour transform, no motion blur, fewer samples, simplified "
     "shading - so judgements about a look must be made on a rendered frame, never on the viewport.",
     [("part_of", "craft.3d"), ("prevents", "failure.judged_on_viewport")], ["3d", "workflow"]),

    ("craft.timeline_editing", "timeline editing",
     "Working with clips laid out in time rather than one at a time. It is what an NLE gives you "
     "that a command line does not: seeing the shape of the whole thing, and being able to move a "
     "cut by four frames because it feels wrong, which is not a decision that can be scripted.",
     [("part_of", "craft.editing"), ("implemented_by", "tool.kdenlive")], ["editing"]),

    ("craft.parallel_processing", "working in parallel",
     "Frames are embarrassingly parallel; sequences are not. Split by frame range across processes, "
     "write to one directory, and let the assembler take whatever is complete - the hard part is "
     "never the splitting, it is making a partial result usable.",
     [("part_of", "craft.pipeline"), ("see_also", "craft.render_farm")], ["pipeline"]),

    ("craft.tts", "text to speech",
     "Synthesising a read from text. Four things decide whether it passes: the model, the voice, "
     "the ASSEMBLY - how the sentences are cut and spaced - and the post-processing. Measured here, "
     "the last two carry more of the remaining gap than the first two do.",
     [("part_of", "craft.narration")], ["tts"]),

    ("craft.3d", "3D",
     "Building and lighting a scene that never existed, and photographing it with a camera that has "
     "no physical constraints. That freedom is the trap: every real camera and every real light "
     "imposes limits an audience has learned to read as truth, so the work is mostly putting those "
     "limits back.",
     [("part_of", "craft.making"), ("prevents", "failure.cgi_look")], ["3d"]),

    # failures whose bodies were left empty
    ("failure.audio_hole", "a hole in the ambience at a cut",
     "The room tone stops dead at an edit and starts again a frame later. The audience does not "
     "hear a missing sound, they hear the cut. Cured by a continuous ambience bed under the whole "
     "scene, which is why thirty seconds of room tone is recorded at every location.",
     [("part_of", "craft.sound"), ("prevents", "craft.room_tone")], ["sound", "editing"]),

    ("failure.banding", "banding in gradients",
     "Visible steps where a gradient should be smooth - skies, soft shadows, a vignette. Caused by "
     "too few bits for the range being covered, usually an 8-bit output of something graded hard. "
     "Cure with more bit depth through the pipeline, a dither, or a little grain.",
     [("part_of", "craft.color_grading"), ("prevents", "craft.grain")], ["colour", "delivery"]),

    ("failure.cramped_frame", "the subject is pressed against the frame edge",
     "A face in profile with no space in front of it, or a moving subject with nothing ahead. The "
     "viewer feels the frame as a wall. Lead the look and lead the movement.",
     [("part_of", "craft.composition")], ["camera"]),

    ("failure.sync_drift", "sound and picture drift apart",
     "They start together and separate over minutes. Almost always a sample-rate or frame-rate "
     "mismatch, or one side rebuilt while the other was not. The structural cure is to measure what "
     "was actually produced and build the other side from that measurement rather than from an "
     "assumed duration.",
     [("part_of", "craft.sound"), ("prevents", "principle.record_first")], ["sound", "editing"]),
]

SUPPORT = [
    ("failure.judged_on_viewport", "failure", "a look judged on the viewport",
     "The viewport applies a different colour transform, no motion blur and simplified shading. "
     "Every decision about how something looks must be made on a rendered frame."),
]
