# -*- coding: utf-8 -*-
"""
The reference layer: named looks and the people who made them.

This exists so that "make it look like X" can be answered with what X actually
consists of, rather than with a LUT called X. Every entry says what the look is
MADE of - lens, light, colour, movement, cutting - because that is what can be
reproduced. The names are here as shorthand for a set of decisions, not as
things to imitate wholesale.
"""

DOMAIN = ("craft.looks", "looks and references",
          "Named styles broken into the decisions that make them.")

NODES = [
    ("craft.look_teal_orange", "teal and orange",
     "Skin pushed warm, everything else pushed cool. It became universal because it works on the "
     "one thing an audience judges absolutely - faces separate from any background without a light "
     "being moved. It is a cliche now, and the interesting version is the restrained one: warm skin "
     "against a background that is merely NOT warm, rather than actively cyan.",
     [("part_of", "craft.looks"), ("requires", "craft.lift_gamma_gain"),
      ("requires", "craft.complementary_color")], ["look", "grade"]),

    ("craft.look_bleach_bypass", "bleach bypass",
     "High contrast, desaturated, silver retained in the shadows so blacks go hard and skin goes "
     "grey. Chemically it was skipping the bleach step; digitally it is a heavy contrast curve with "
     "saturation pulled down and the shadows crushed. Reads as brutal, documentary, or war.",
     [("part_of", "craft.looks"), ("requires", "craft.contrast_curve"),
      ("requires", "craft.desaturation")], ["look", "grade"]),

    ("craft.look_day_for_night", "moonlight",
     "Cool, underexposed but not muddy, a hard key standing in for the moon, and practicals left warm "
     "so the frame has two colour temperatures. The mistake is making it dark; real moonlit "
     "photography is normally exposed and cool, and the darkness lives in what is NOT lit.",
     [("part_of", "craft.looks"), ("requires", "craft.day_for_night"),
      ("requires", "craft.low_key")], ["look", "grade"]),

    ("craft.look_16mm", "16mm",
     "Visible grain that moves, soft corners, slightly low contrast, colours that drift rather than "
     "sit. Digitally: real temporal grain at strength, a gentle vignette, a small softening at the "
     "edges of frame only, and a black point lifted off zero. The grain must MOVE - static grain "
     "reads as dirt on the lens.",
     [("part_of", "craft.looks"), ("requires", "craft.grain"),
      ("requires", "craft.film_emulation")], ["look", "grade", "film"]),

    ("craft.look_technicolor", "three-strip Technicolor",
     "Saturated primaries, especially red, with skin that stays plausible and blacks that stay dense. "
     "The modern imitation usually oversaturates everything; the real thing is selective - it is the "
     "reds and the greens that sing while the neutrals stay neutral.",
     [("part_of", "craft.looks"), ("requires", "craft.color_palette")], ["look", "grade", "film"]),

    ("craft.look_natural_light_realism", "available light realism",
     "Windows as the key, practicals in shot, deep shadows accepted, sometimes a face lost. Reads as "
     "honest because it declines to arrange anything. Expensive in a different currency: it needs "
     "locations that already have good light and the discipline to shoot at the hour when they do.",
     [("part_of", "craft.looks"), ("requires", "craft.motivated_light"),
      ("requires", "craft.golden_hour")], ["look", "lighting"]),

    ("craft.look_flat_symmetrical", "flat and symmetrical",
     "Centred subjects, planimetric staging - everything at ninety degrees to the lens - a restricted "
     "palette, and a camera that only ever whip-pans or dollies on an axis. Reads as designed, "
     "wry, artificial on purpose. It is also very forgiving to execute, which is why it is a good "
     "choice for a small production with control over its set.",
     [("part_of", "craft.looks"), ("requires", "craft.symmetry")], ["look", "composition"]),

    ("craft.look_observational_doc", "observational documentary",
     "Long lens, handheld but settled, available light, subjects unaddressed, cutting that lets "
     "scenes run past their point. Its grammar is about NOT intervening, so every stylish choice "
     "reads as a lie.",
     [("part_of", "craft.looks"), ("requires", "craft.long_lens"),
      ("requires", "craft.handheld")], ["look", "documentary"]),

    ("craft.look_ken_burns_doc", "the archival documentary",
     "Stills given slow motion, a warm even grade over mismatched sources, one voice, a spare score, "
     "and interviews shot flat against dark backgrounds. It is the most reproducible high-quality "
     "look available to a small team, which is exactly why it is the right default for us.",
     [("part_of", "craft.looks"), ("requires", "craft.ken_burns"),
      ("requires", "craft.archive_material"), ("used_in", "craft.documentary")],
     ["look", "documentary"]),

    ("craft.look_blackboard", "the blackboard lecture",
     "A dark ground, a bright stroke, handwriting rather than type, and mathematics that appears as "
     "it is spoken. Our own chalk engine's lessons: strokes are a white layer multiplied by a grain "
     "mask with a soft halo; bake what is finished into one cached image and composite only the live "
     "layer's bounding box, or grain-multiplying a full frame 24 times a second costs four times the "
     "total render.",
     [("part_of", "craft.looks"), ("used_in", "craft.lecture")], ["look", "lecture", "measured"]),

    ("craft.look_animated_map", "the animated documentary map",
     "A restrained base map, ranked labels, arrows with mid-labels, a front line that morphs, and a "
     "camera that eases between extents. What makes or breaks it is label placement, not draughting: "
     "try each side of a dot, take the first free slot, and drop a label that fits nowhere.",
     [("part_of", "craft.looks"), ("requires", "craft.map_sequence"),
      ("used_in", "craft.documentary")], ["look", "documentary", "measured"]),

    # ---- people, as shorthand for decisions ----
    ("person.deakins", "Roger Deakins",
     "Shorthand for: one strong motivated source, deep and unfussy blocking, very little camera "
     "movement unless the scene demands it, and a willingness to let large parts of the frame go "
     "black. The lesson worth stealing is subtraction - he removes lights rather than adding them.",
     [("part_of", "craft.looks"), ("applies_to", "craft.negative_fill")], ["person", "dp"]),

    ("person.lubezki", "Emmanuel Lubezki",
     "Shorthand for: natural and available light, wide lenses close to faces, long unbroken takes "
     "that move continuously, and shooting at the hour the light is right rather than lighting to "
     "match a schedule. Expensive in time, cheap in equipment.",
     [("part_of", "craft.looks"), ("applies_to", "craft.oner"),
      ("applies_to", "craft.natural_light")], ["person", "dp"]),

    ("person.murch", "Walter Murch",
     "Shorthand for the six criteria and for the idea that a cut is emotional before it is spatial. "
     "Also for sound: he treats the mix as a composition with a limited number of simultaneous "
     "voices, and cuts elements out rather than balancing everything.",
     [("part_of", "craft.looks"), ("applies_to", "craft.murch_six"),
      ("applies_to", "craft.mixing")], ["person", "editor"]),

    ("person.eisenstein", "Sergei Eisenstein",
     "Shorthand for montage as argument: meaning made by collision rather than by continuity. Worth "
     "having in mind whenever a sequence needs to make a POINT rather than tell a sequence of "
     "events - which in an explainer is often.",
     [("part_of", "craft.looks"), ("applies_to", "craft.montage")], ["person", "director"]),

    ("person.burns", "Ken Burns",
     "Shorthand for the archival grammar: motion on stills, a single authoritative voice, letters "
     "read by actors, a spare recurring musical figure, and interviews that carry emotion while the "
     "narration carries fact. Nearly all of it is reproducible by two people and a scanner.",
     [("part_of", "craft.looks"), ("applies_to", "craft.ken_burns"),
      ("applies_to", "craft.documentary")], ["person", "director"]),

    ("person.3blue1brown", "Grant Sanderson",
     "Shorthand for the modern mathematics explainer: an object on screen that IS the idea rather "
     "than an illustration of it, transformations animated so the eye tracks what changed, colour "
     "used as consistent notation, and a voice that asks the question before it gives the tool. Manim "
     "exists because of this and we already use it.",
     [("part_of", "craft.looks"), ("applies_to", "craft.educational_film"),
      ("implemented_by", "tool.manim")], ["person", "explainer"]),
]

SUPPORT = []
