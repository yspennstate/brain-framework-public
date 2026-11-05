# -*- coding: utf-8 -*-
"""Animation craft and story structure - the two things that make a sequence hold together."""

DOMAIN = ("craft.animation", "animation", "Making movement that reads as alive rather than as interpolation.")

NODES = [
    # ---------------- the twelve principles ----------------
    ("craft.12_principles", "the twelve principles",
     "Thomas and Johnston's list from Disney. They are not a style; they are a description of what "
     "the eye needs in order to believe a movement. Every one of them applies to a graph animating "
     "on a lecture board just as much as to a bouncing character - a curve that appears with no "
     "anticipation and stops dead is exactly as wrong as a character that does.",
     [("part_of", "craft.animation")], ["animation", "theory"]),

    ("craft.squash_stretch", "squash and stretch",
     "Deform on impact and on acceleration while keeping volume. It communicates weight and material "
     "in a single frame. A completely rigid object that never deforms reads as weightless, which is "
     "why unmodified 3D primitives look like nothing.",
     [("is_a", "craft.12_principles"), ("produces", "effect.weight")], ["animation"]),

    ("craft.anticipation", "anticipation",
     "A small movement in the opposite direction before the main one. The audience needs it to track "
     "what is about to happen. Removing it is what makes fast action unreadable, and it is the single "
     "most common thing missing from procedurally generated motion.",
     [("is_a", "craft.12_principles"), ("prevents", "failure.unreadable_action")], ["animation"]),

    ("craft.staging_anim", "staging",
     "Present one idea at a time, clearly, in silhouette. If the pose does not read as a black shape, "
     "it does not read. This is the animation version of composition and it is stricter, because the "
     "audience has a fraction of a second.",
     [("is_a", "craft.12_principles"), ("applies_to", "craft.composition")], ["animation"]),

    ("craft.straight_ahead_vs_pose", "straight ahead and pose to pose",
     "Draw every frame in order, or set the key poses and fill between. Straight ahead gives "
     "spontaneity and drifts; pose to pose gives control and can feel mechanical. Most real work does "
     "poses first, then straight-ahead passes on the parts that must feel loose - hair, cloth, fire.",
     [("is_a", "craft.12_principles")], ["animation", "process"]),

    ("craft.follow_through", "follow through and overlapping action",
     "Parts of a body do not stop at once, and they do not all move at once. A hand arrives after the "
     "arm; hair keeps going after the head stops. This is what separates animation that looks alive "
     "from animation that looks keyframed.",
     [("is_a", "craft.12_principles"), ("prevents", "failure.robotic_motion")], ["animation"]),

    ("craft.easing", "slow in and slow out (easing)",
     "Real things accelerate and decelerate. Linear interpolation between two keys is the loudest "
     "tell of computer animation there is, and it takes one curve setting to fix. For camera moves "
     "over stills, ease both ends; for a UI or a diagram element, ease out hard and in gently.",
     [("is_a", "craft.12_principles"), ("prevents", "failure.linear_motion"),
      ("used_in", "craft.ken_burns"), ("implemented_by", "tool.ffmpeg")], ["animation", "motion"]),

    ("craft.arcs", "arcs",
     "Living things move on curves, not on straight lines. A hand travelling in a straight line from "
     "A to B is mechanical. In motion graphics the same rule makes a shape entering on a slight arc "
     "feel designed rather than computed.",
     [("is_a", "craft.12_principles")], ["animation"]),

    ("craft.secondary_action", "secondary action",
     "A smaller movement supporting the main one - a glance while walking, a breath while listening. "
     "It must not compete: if the audience watches the secondary action, it has become the primary "
     "one.",
     [("is_a", "craft.12_principles")], ["animation"]),

    ("craft.timing_spacing", "timing and spacing",
     "Timing is how many frames; spacing is where the object is in each of them. Spacing is where the "
     "life is - the same number of frames with even spacing reads as machinery and with clustered "
     "spacing reads as intention.",
     [("is_a", "craft.12_principles"), ("measured_by", "metric.frames_per_action")], ["animation"]),

    ("craft.exaggeration", "exaggeration",
     "Push the pose past the literal. The camera and the frame flatten everything, so a realistic "
     "gesture reads as no gesture. This applies to graphs and diagrams too: an emphasis that looks "
     "excessive in the editor is usually right on screen.",
     [("is_a", "craft.12_principles")], ["animation"]),

    ("craft.solid_drawing", "solid drawing",
     "Weight, volume and balance - the figure occupies space and could stand up. In 3D the equivalent "
     "is silhouette plus contact: if a foot does not visibly meet the floor, nothing else will save "
     "the shot.",
     [("is_a", "craft.12_principles"), ("prevents", "failure.floating_contact")], ["animation"]),

    ("craft.appeal", "appeal",
     "The design is interesting to look at. Not prettiness - clarity, distinctness, and something the "
     "eye wants to follow. A confusing design cannot be animated into a good one.",
     [("is_a", "craft.12_principles")], ["animation"]),

    # ---------------- animation practice ----------------
    ("craft.on_ones_twos", "on ones, twos and threes",
     "How many frames each drawing is held. Twos at 24fps is the traditional feature standard and it "
     "reads as animation; ones reads smoother and more expensive; threes reads as limited animation "
     "and as television. Mixing them deliberately - ones for a fast action inside a scene on twos - is "
     "normal.",
     [("part_of", "craft.animation"), ("applies_to", "craft.frame_rate")], ["animation", "2d"]),

    ("craft.keys_breakdowns_inbetweens", "keys, breakdowns, inbetweens",
     "The key poses carry the acting; the breakdown between two keys decides the arc and the "
     "character of the move; the inbetweens are labour. The breakdown is where a junior animator "
     "becomes a good one.",
     [("part_of", "craft.animation")], ["animation", "2d"]),

    ("craft.walk_cycle", "walk cycle",
     "Contact, down, passing, up, contact. The down pose carries the weight, the up pose carries the "
     "energy, and the hips and shoulders counter-rotate. A cycle with no vertical travel is the "
     "classic amateur walk - it slides.",
     [("part_of", "craft.animation"), ("prevents", "failure.foot_slide")], ["animation", "character"]),

    ("craft.motion_graphics", "motion graphics",
     "Type, shapes and data moving with intent. The rules that matter: things should enter from where "
     "they belong, they should ease, they should not all move at once, and text must be readable for "
     "long enough to be read aloud twice by a slow reader.",
     [("part_of", "craft.animation"), ("requires", "craft.easing"),
      ("applies_to", "craft.title_design")], ["animation", "graphics"]),

    ("craft.kinetic_typography", "kinetic typography",
     "Words that move in time with a voice. It works when the movement is doing the emphasis the "
     "voice is doing, and it is unbearable when every word gets its own animation. Anchor the "
     "movement to the stressed syllables, which you can extract from the narration timing you already "
     "measured.",
     [("is_a", "craft.motion_graphics"), ("requires", "craft.emphasis")], ["animation", "graphics"]),

    ("craft.2d_animation", "2D animation",
     "Drawn or vector movement. Cheaper per second than 3D at low quality and more expensive at high "
     "quality, because every frame is authored rather than computed. Blender's grease pencil puts "
     "drawn 2D inside a 3D camera, which is the best of both for explainer work.",
     [("part_of", "craft.animation"), ("implemented_by", "tool.blender")], ["animation", "2d"]),

    ("craft.cutout_animation", "cutout animation",
     "Rigged flat pieces moved and rotated rather than redrawn. Fast, cheap, and instantly "
     "recognisable. The right choice when the content is the point and the animation is carrying it, "
     "which is most explainer work.",
     [("is_a", "craft.2d_animation")], ["animation", "2d"]),

    ("craft.rotoscoping", "rotoscoping",
     "Tracing over filmed motion. Gives real weight for free and takes the life out if traced "
     "literally - the reason rotoscoped work often looks eerie is that it has human timing and no "
     "exaggeration.",
     [("is_a", "craft.2d_animation"), ("part_of", "craft.vfx")], ["animation", "vfx"]),

    ("craft.procedural_animation", "procedural animation",
     "Movement generated by rule rather than by hand: noise, physics, expressions, simulation. It "
     "scales, and it has no taste - it will happily give you perfectly uniform, perfectly wrong "
     "motion. The fix is to drive procedural systems with curves that have anticipation and easing "
     "built in.",
     [("part_of", "craft.animation"), ("causes", "failure.robotic_motion"),
      ("implemented_by", "tool.blender")], ["animation", "technical"]),

    ("craft.frame_interpolation", "frame interpolation",
     "Generating intermediate frames to raise a frame rate or slow footage down. Optical-flow methods "
     "break on occlusion and on fast rotation - the tell is warping around edges. Useful for "
     "smoothing a low-frame-rate AI render; dangerous on anything with text or fine structure.",
     [("part_of", "craft.animation"), ("causes", "failure.interpolation_warp"),
      ("implemented_by", "tool.ffmpeg")], ["animation", "post", "ai"]),

    # ---------------- story ----------------
    ("craft.story", "story structure",
     "The order in which an audience is given things. Even a lecture has one: what they think they "
     "know, what breaks it, what replaces it. Structure is not decoration on top of content; it IS "
     "how content is understood.",
     [("part_of", "craft.preproduction")], ["story", "structure"]),

    ("craft.three_act", "three acts",
     "Setup, confrontation, resolution, with a turn at each join. It is a description rather than a "
     "recipe - most things that work can be read this way afterwards. Its real use is diagnostic: if "
     "you cannot say what changes at the end of the first quarter, nothing does.",
     [("is_a", "craft.story")], ["story", "structure"]),

    ("craft.sequence_approach", "the sequence approach",
     "Eight sequences of eight to fifteen minutes, each with its own small question and answer. Far "
     "more useful than three acts for anything long, because it gives you a unit you can actually "
     "hold in your head - and it maps directly onto chapters of a lecture.",
     [("is_a", "craft.story"), ("used_in", "craft.lecture")], ["story", "structure"]),

    ("craft.scene_structure", "scene structure",
     "Someone wants something, something is in the way, the situation changes. If a scene ends where "
     "it started, it is exposition wearing a scene's clothes. The lecture equivalent: each chapter "
     "should leave the listener able to do something they could not do at its start.",
     [("is_a", "craft.story")], ["story", "structure"]),

    ("craft.setup_payoff", "setups and payoffs",
     "Plant it before you need it, far enough back that the audience has stopped holding it. Nothing "
     "makes an audience feel a piece is well-made like a payoff they half-remember. In explanation, "
     "the setup is the example you introduce casually and return to as the proof.",
     [("is_a", "craft.story"), ("produces", "effect.satisfaction")], ["story", "structure"]),

    ("craft.exposition", "exposition",
     "Necessary information the audience has no appetite for. Deliver it while something else is "
     "happening, or in answer to a question they already asked. Exposition in a vacuum is where "
     "attention goes to die - and in an educational film it is most of the danger.",
     [("is_a", "craft.story"), ("causes", "failure.exposition_dump")], ["story", "structure"]),

    ("craft.documentary", "documentary form",
     "Assembling a truthful argument out of real material. Its central craft problem is that reality "
     "did not shoot itself in coverage: you build continuity out of cutaways, ambience and narration, "
     "and every one of those choices is an ethical one as well as a technical one.",
     [("is_a", "craft.story"), ("requires", "craft.cutaway")], ["story", "documentary"]),

    ("craft.essay_film", "the essay film",
     "A voice thinking on screen, with images as evidence and counterpoint rather than illustration. "
     "The interesting version never shows what the voice is saying; it shows what the voice is "
     "thinking about.",
     [("is_a", "craft.documentary")], ["story", "documentary"]),

    ("craft.interview", "the interview",
     "The whole job is asked before the camera rolls: what is the one thing only this person can say. "
     "Technically: eye level, a soft key close and slightly off axis, a long lens, the subject looking "
     "just off lens, and a clean background at some distance behind them.",
     [("part_of", "craft.documentary"), ("requires", "craft.soft_light"),
      ("requires", "craft.long_lens")], ["documentary", "production"]),

    ("craft.opening_shot", "the opening",
     "The audience decides in seconds what kind of thing this is and how much attention it deserves. "
     "An opening does not have to be spectacular; it has to be specific. A precise small detail beats "
     "a generic grand vista every time.",
     [("part_of", "craft.story")], ["story", "editing"]),

    ("craft.establishing_sequence", "establishing",
     "Telling the audience where they are before asking them to care what happens there. It can be "
     "one wide shot or one sound, and it can be skipped deliberately for disorientation - but skipping "
     "it by accident is the commonest structural fault in first films.",
     [("part_of", "craft.story"), ("requires", "craft.wide_shot")], ["story", "editing"]),

    ("craft.dialogue_scene", "the dialogue scene",
     "Two people and a disagreement. Shoot a wide for geography, singles for the argument, and a "
     "two-shot for the moment they connect or fail to. The editing question is only ever whose face "
     "the line lands on - often the listener's.",
     [("part_of", "craft.story")], ["story", "editing"]),

    ("craft.blocking", "blocking",
     "Where people move and stand. Good blocking gives the camera a reason to move and gives the "
     "editor a change of composition without a cut. Bad blocking is two people standing still facing "
     "each other, which is why so much amateur dialogue is dull before a single shot is chosen.",
     [("part_of", "craft.production"), ("applies_to", "craft.camera_move")], ["directing", "production"]),

    ("craft.geography", "screen geography",
     "The audience's mental map of where everyone and everything is. Built from wides, eyelines and "
     "consistent screen direction; destroyed by crossing the line or by never establishing.",
     [("part_of", "craft.continuity")], ["editing", "continuity"]),
]

SUPPORT = [
    ("effect.weight", "effect", "weight", "The object has mass and the world has gravity."),
    ("effect.satisfaction", "effect", "satisfaction", "Something planted earlier pays off and the audience feels the shape of the thing."),
    ("metric.frames_per_action", "metric", "frames per action",
     "How many frames a movement takes. At 24fps: a blink is 3 to 5, a head turn 8 to 12, a "
     "considered gesture 16 to 24. Anything under 3 frames reads as a pop rather than a move."),
    ("failure.unreadable_action", "failure", "the action cannot be read",
     "It happens too fast with no anticipation, so the eye has nothing to track."),
    ("failure.robotic_motion", "failure", "motion that reads as computed",
     "Uniform velocity, no anticipation, no overlap, everything starting and stopping together."),
    ("failure.linear_motion", "failure", "linear interpolation",
     "Constant velocity between keys. The single loudest tell of unfinished animation, and one "
     "curve setting away from fixed."),
    ("failure.foot_slide", "failure", "feet sliding",
     "Contact point moves while it should be planted. Ruins weight completely."),
    ("failure.floating_contact", "failure", "no visible contact",
     "Nothing touches anything: shadows missing at the contact point, a foot hovering, an object "
     "resting on nothing. Contact shadows are the cheapest fix in a render."),
    ("failure.interpolation_warp", "failure", "warping around edges after interpolation",
     "Optical flow guessed wrong at an occlusion. Visible on hands crossing bodies and on text."),
    ("failure.exposition_dump", "failure", "an exposition dump",
     "A block of necessary information with nothing happening. Attention leaves and does not "
     "reliably come back."),
]
