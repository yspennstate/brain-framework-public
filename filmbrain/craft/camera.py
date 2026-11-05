# -*- coding: utf-8 -*-
"""
Camera craft: what the lens and the frame do to an audience.

Each entry is written the way I would say it to someone about to shoot, not the
way a glossary would define it. The body is the part that has to earn its place:
if it only restates the name, it should not be a node.

Format: (id, name, body, [(rel, target), ...], [tags])
Targets that do not exist yet are created as stubs by the loader, so I can point
at a concept before I have written it.
"""

DOMAIN = ("craft.camera", "camera", "Everything the lens decides before an edit exists.")

NODES = [
    # ---------------- shot size ----------------
    ("craft.shot_size", "shot size",
     "How much of the subject the frame holds. It is the single strongest lever on how close the "
     "audience feels to a person, and it works whether or not anyone notices it. The rule of thumb "
     "that survives contact with real work: the tighter you go, the more the audience is asked to "
     "read a face rather than a situation. Going tight too early leaves you nowhere to escalate.",
     [("part_of", "craft.camera"), ("applies_to", "craft.coverage")], ["camera", "framing"]),

    ("craft.extreme_wide", "extreme wide shot",
     "The subject is small or absent; the place is the subject. Use it to establish geography or to "
     "make a person insignificant. In documentary it buys the viewer a breath between arguments. It "
     "fails when it has no readable point of interest - a wide shot without a focal element is just "
     "a texture, and the eye leaves it in under two seconds.",
     [("is_a", "craft.shot_size"), ("causes", "effect.scale"), ("causes", "effect.isolation"),
      ("used_in", "craft.establishing_sequence")], ["camera", "framing", "wide"]),

    ("craft.wide_shot", "wide shot",
     "Full body plus the space it stands in. The workhorse of blocking: it tells the audience where "
     "everyone is, which is the debt every tighter shot borrows against. If you never pay it, the "
     "close-ups float.",
     [("is_a", "craft.shot_size"), ("requires", "craft.blocking"),
      ("prevents", "failure.geography_confusion")], ["camera", "framing"]),

    ("craft.medium_shot", "medium shot",
     "Waist up. Close enough for gesture, wide enough for two people to share the frame and act on "
     "each other. Most dialogue lives here; the close-up is the exception you spend, not the default.",
     [("is_a", "craft.shot_size"), ("used_in", "craft.dialogue_scene")], ["camera", "framing"]),

    ("craft.medium_closeup", "medium close-up",
     "Chest up - the interview shot, and the one most talking-head videos should use instead of the "
     "close-up they reach for. Leaves room for a hand to come into frame, which is where half of a "
     "speaker's expressiveness lives.",
     [("is_a", "craft.shot_size"), ("used_in", "craft.interview"),
      ("preferred_over", "craft.closeup", )], ["camera", "framing", "interview"]),

    ("craft.closeup", "close-up",
     "The face fills the frame. It is an escalation, and it only reads as one if you have not been "
     "sitting in it. The classic mistake in cheap video is cutting between two close-ups for six "
     "minutes: the audience has nothing left to be given.",
     [("is_a", "craft.shot_size"), ("causes", "effect.intimacy"),
      ("conflicts_with", "craft.geography"), ("requires", "craft.wide_shot")],
     ["camera", "framing"]),

    ("craft.extreme_closeup", "extreme close-up",
     "An eye, a hand, a switch. Almost always an insert doing a specific job: proving a detail, "
     "carrying a beat of tension, or covering a cut you could not otherwise make. Held too long it "
     "reads as style rather than information.",
     [("is_a", "craft.shot_size"), ("used_in", "craft.insert"),
      ("used_in", "craft.cut_covering")], ["camera", "framing"]),

    ("craft.two_shot", "two shot",
     "Two people in one frame. The relationship is the content: their distance apart, who is nearer "
     "the lens, whether the frame gives them equal weight. A two shot answers a question a pair of "
     "singles cannot.",
     [("is_a", "craft.shot_size"), ("used_in", "craft.dialogue_scene"),
      ("alternative_to", "craft.shot_reverse_shot")], ["camera", "framing"]),

    ("craft.over_the_shoulder", "over the shoulder",
     "Shoot past one head at the other. It binds the two people into one space and keeps the eyeline "
     "honest. Cheap when both sides are shot flat; good when the foreground shoulder is soft, large "
     "and dark, which gives the frame depth for free.",
     [("is_a", "craft.shot_size"), ("requires", "craft.eyeline_match"),
      ("produces", "effect.depth"), ("used_in", "craft.shot_reverse_shot")], ["camera", "framing"]),

    ("craft.insert", "insert",
     "A shot of a thing, cut into a scene. Its real job is often structural rather than "
     "informational: it lets the editor jump time, fix a fluffed line, or change the rhythm. Shoot "
     "more of them than you think you need; you will use all of them.",
     [("part_of", "craft.coverage"), ("used_in", "craft.cut_covering")], ["camera", "editing"]),

    ("craft.cutaway", "cutaway",
     "A shot away from the main action that stays in the same world. Same structural rescue as an "
     "insert, and the reason interview footage can be cut at all. B-roll is cutaway material "
     "gathered in advance.",
     [("part_of", "craft.coverage"), ("used_in", "craft.interview"),
      ("prevents", "failure.jump_cut")], ["camera", "editing", "documentary"]),

    # ---------------- angle and height ----------------
    ("craft.camera_height", "camera height",
     "Where the lens sits relative to the subject's eyes. Eye level is neutral and almost always "
     "right for an interview. Below eye level makes a subject loom; above makes them small. The "
     "effect is strong at small angles - two hand-widths of height change is visible.",
     [("part_of", "craft.camera")], ["camera", "angle"]),

    ("craft.low_angle", "low angle",
     "Looking up. Power, threat, or simply a cleaner background because you are shooting into sky or "
     "ceiling. Directors reach for it for the first reason; working shooters reach for it for the "
     "third at least as often.",
     [("is_a", "craft.camera_height"), ("causes", "effect.dominance"),
      ("produces", "craft.clean_background")], ["camera", "angle"]),

    ("craft.high_angle", "high angle",
     "Looking down. Diminishes the subject and opens up the floor as background. In documentary it "
     "reads as observation rather than judgement, which is why overhead shots feel forensic.",
     [("is_a", "craft.camera_height"), ("causes", "effect.vulnerability")], ["camera", "angle"]),

    ("craft.dutch_angle", "dutch angle",
     "Roll the camera off horizontal. One of the few purely expressive choices with no practical "
     "excuse, which is why it is so easy to overuse. A three-degree tilt unsettles without announcing "
     "itself; fifteen degrees announces itself and had better be earned.",
     [("is_a", "craft.camera_height"), ("causes", "effect.unease")], ["camera", "angle"]),

    ("craft.overhead_shot", "overhead / top shot",
     "Straight down. Turns a scene into a diagram - people become pieces on a board. Excellent for "
     "explaining a process or a spatial relationship, and the reason cooking and how-to video lives "
     "there.",
     [("is_a", "craft.camera_height"), ("used_in", "craft.explainer"),
      ("produces", "effect.diagrammatic")], ["camera", "angle", "explainer"]),

    # ---------------- lens ----------------
    ("craft.focal_length", "focal length",
     "The lens's angle of view, and with it how depth behaves. Short lenses stretch depth: things "
     "near the camera get big fast, and a step towards the lens is dramatic. Long lenses compress it: "
     "background and foreground stack, and movement towards the camera goes nowhere. This is a "
     "choice about the shape of space, not about how close you can stand.",
     [("part_of", "craft.camera"), ("causes", "craft.perspective_distortion")], ["camera", "lens"]),

    ("craft.wide_lens", "wide lens (under 35mm equivalent)",
     "Exaggerated depth, curved edges, everything sharp. Puts the audience inside a space. Hard on "
     "faces - noses grow, and a face at close range on an 18mm reads as a caricature. Use it for "
     "rooms, not portraits, unless the distortion is the point.",
     [("is_a", "craft.focal_length"), ("causes", "effect.immersion"),
      ("conflicts_with", "craft.flattering_portrait")], ["camera", "lens"]),

    ("craft.normal_lens", "normal lens (~50mm equivalent)",
     "Roughly the perspective of human attention. Nothing about the space is being argued. That is "
     "its virtue: it disappears, which is exactly what you want when the content is the argument.",
     [("is_a", "craft.focal_length"), ("used_in", "craft.documentary")], ["camera", "lens"]),

    ("craft.long_lens", "long lens (85mm and up)",
     "Compressed, isolating, flattering to faces. Separates a subject from a busy background better "
     "than any amount of lighting. The cost is that you must stand far away, which changes what the "
     "subject does in front of you - people relax when the camera is not in their face.",
     [("is_a", "craft.focal_length"), ("produces", "craft.subject_separation"),
      ("used_in", "craft.interview"), ("causes", "effect.compression")], ["camera", "lens"]),

    ("craft.aperture", "aperture",
     "How wide the iris opens: exposure on one axis, depth of field on the other. Wide open is the "
     "look everyone wants and the one that costs you focus. On a moving subject with a wide aperture, "
     "you are trading sharpness for shallowness on every take.",
     [("part_of", "craft.camera"), ("causes", "craft.depth_of_field"),
      ("conflicts_with", "craft.focus_reliability")], ["camera", "lens", "exposure"]),

    ("craft.depth_of_field", "depth of field",
     "The band of distance that is acceptably sharp. Shallow depth of field directs the eye more "
     "reliably than any composition trick because it removes the alternative. Deep focus asks the "
     "audience to choose where to look, which is a legitimate and underused choice.",
     [("part_of", "craft.camera"), ("produces", "craft.subject_separation"),
      ("alternative_to", "craft.deep_focus")], ["camera", "lens"]),

    ("craft.deep_focus", "deep focus",
     "Everything sharp from foreground to background. Lets you stage action in depth rather than in "
     "cuts - two things happening at two distances in one unbroken frame. Requires light, a small "
     "aperture, and blocking that is worth looking at in layers.",
     [("is_a", "craft.depth_of_field"), ("requires", "craft.blocking"),
      ("alternative_to", "craft.shallow_focus")], ["camera", "lens"]),

    ("craft.shallow_focus", "shallow focus",
     "One plane sharp, the rest soft. The default modern look, and a crutch: it hides bad backgrounds "
     "and bad set dressing. In animation and 3D it must be added deliberately, and adding it badly - "
     "a uniform blur with no depth cue - is worse than leaving everything sharp.",
     [("is_a", "craft.depth_of_field"), ("prevents", "failure.busy_background"),
      ("causes", "failure.fake_looking_dof")], ["camera", "lens"]),

    ("craft.bokeh", "bokeh",
     "The character of the out-of-focus blur, not the amount of it. Round, even highlights read as "
     "expensive; harsh-edged or onion-ringed ones read as cheap. In a render, the honest way to get "
     "it is a real defocus with a bladed aperture shape, not a gaussian blur, which has no highlight "
     "bloom and reads as a smear.",
     [("part_of", "craft.depth_of_field"), ("causes", "failure.gaussian_fake_bokeh")],
     ["camera", "lens", "vfx"]),

    ("craft.rack_focus", "rack focus",
     "Move focus from one plane to another inside a shot. It is a cut you do not have to make, and it "
     "forces attention with no ambiguity. Needs the two subjects at genuinely different distances or "
     "it looks like a focus error.",
     [("is_a", "craft.camera_move"), ("requires", "craft.shallow_focus"),
      ("alternative_to", "craft.cut")], ["camera", "focus"]),

    # ---------------- movement ----------------
    ("craft.camera_move", "camera movement",
     "The rule that saves the most footage: a move should start because something in the scene "
     "started it, and stop when that thing stops. Motivated movement is invisible; unmotivated "
     "movement is 'the camera is moving now', and the audience watches the camera instead of the "
     "film. This is the single most common failure in first films and in AI video prompts alike.",
     [("part_of", "craft.camera"), ("prevents", "failure.unmotivated_move")], ["camera", "movement"]),

    ("craft.pan", "pan",
     "Rotate horizontally on a fixed point. Cheap, and it changes what is in frame without changing "
     "the audience's position, which is why it feels like looking rather than moving. Fast pans strobe "
     "badly at 24fps - a whip pan works only because you cannot resolve it.",
     [("is_a", "craft.camera_move"), ("causes", "failure.judder")], ["camera", "movement"]),

    ("craft.tilt", "tilt",
     "The vertical pan. Reveals scale from the bottom up or drops from a face to a hand. Tilting up "
     "to a building is such a cliche that it now reads as a genre marker.",
     [("is_a", "craft.camera_move")], ["camera", "movement"]),

    ("craft.dolly", "dolly / track",
     "The whole camera travels. Unlike a zoom it changes perspective, so foreground and background "
     "shift against each other - parallax - and the audience feels themselves move. This is the only "
     "move that genuinely reads as three-dimensional, and the reason a dolly-in beats a zoom-in "
     "almost every time.",
     [("is_a", "craft.camera_move"), ("produces", "effect.parallax"),
      ("preferred_over", "craft.zoom")], ["camera", "movement"]),

    ("craft.push_in", "push in",
     "A slow dolly towards a face during a line. It intensifies without any cut, and it works even "
     "when it is too slow to notice consciously. Around two to four per cent of frame width over ten "
     "seconds is the range where it does its job invisibly - and it is trivially cheap in a render or "
     "on a still image.",
     [("is_a", "craft.dolly"), ("causes", "effect.intensification"),
      ("used_in", "craft.ken_burns")], ["camera", "movement", "editing"]),

    ("craft.zoom", "zoom",
     "Change focal length inside the shot. No parallax, so the space flattens as you go and the "
     "audience is pushed rather than carried. Reads as documentary or as television; used "
     "deliberately fast it reads as shock.",
     [("is_a", "craft.camera_move"), ("alternative_to", "craft.dolly")], ["camera", "movement"]),

    ("craft.dolly_zoom", "dolly zoom (vertigo shot)",
     "Dolly one way while zooming the other so the subject stays the same size and the background "
     "changes shape behind them. Space itself appears to warp. Almost impossible to use without "
     "quoting Hitchcock, so use it when quoting is fine.",
     [("is_a", "craft.camera_move"), ("requires", "craft.dolly"), ("requires", "craft.zoom"),
      ("causes", "effect.unease")], ["camera", "movement"]),

    ("craft.crane", "crane / jib",
     "Vertical travel with the camera free in space. Reads as authorial - the film stepping back to "
     "comment. That is why it opens and closes so many pictures.",
     [("is_a", "craft.camera_move"), ("used_in", "craft.opening_shot")], ["camera", "movement"]),

    ("craft.steadicam", "stabilised handheld / steadicam",
     "Walking with the subject without the shake. It puts the audience alongside a person, which is "
     "different from watching them. The classic use is a long unbroken walk-and-talk that would take "
     "eight cuts to cover otherwise.",
     [("is_a", "craft.camera_move"), ("used_in", "craft.oner")], ["camera", "movement"]),

    ("craft.handheld", "handheld",
     "Shake as a texture. Reads as urgency, immediacy, or documentary truth depending on how much "
     "you allow. The failure mode is uniform, mechanical wobble - the tell of a fake handheld added "
     "in post is that the motion has no weight and no anticipation.",
     [("is_a", "craft.camera_move"), ("causes", "effect.immediacy"),
      ("causes", "failure.fake_handheld")], ["camera", "movement"]),

    ("craft.oner", "oner / long take",
     "One shot doing the work of a scene. It buys real time and continuous space, and it costs you "
     "every option in the edit. Worth it when the content is duration itself - a wait, an argument "
     "that has to be endured, a walk whose length is the point.",
     [("part_of", "craft.camera"), ("requires", "craft.blocking"),
      ("conflicts_with", "craft.coverage")], ["camera", "directing"]),

    ("craft.locked_off", "locked-off shot",
     "Camera does not move at all. Underrated. Stillness is the strongest contrast you have available "
     "against a moving sequence, and in an explainer or lecture it lets the audience read a diagram "
     "without fighting the frame.",
     [("is_a", "craft.camera_move"), ("used_in", "craft.explainer"),
      ("opposite_of", "craft.handheld")], ["camera", "movement"]),

    ("craft.ken_burns", "Ken Burns move",
     "A slow push or drift across a still image so a photograph reads as motion picture. Two rules "
     "make the difference between elegant and cheap: keep the speed under about three per cent of "
     "frame width a second, and move toward or across the subject's meaning, not arbitrarily. "
     "Ease in and out; a linear move starting and stopping abruptly is the tell.",
     [("is_a", "craft.camera_move"), ("used_in", "craft.documentary"),
      ("requires", "craft.easing"), ("implemented_by", "tool.ffmpeg")],
     ["camera", "movement", "documentary", "stills"]),

    # ---------------- composition ----------------
    ("craft.composition", "composition",
     "Where things sit in the frame and what that does to the eye. The useful frame of mind is not "
     "'follow the rules' but 'the eye enters here, travels here, rests here' - and then check that "
     "the path you built is the one the shot needs.",
     [("part_of", "craft.camera")], ["camera", "composition"]),

    ("craft.rule_of_thirds", "rule of thirds",
     "Put the subject a third of the way in rather than dead centre. It works because it leaves the "
     "frame asymmetric, and asymmetry has direction. It is a starting position, not a law; centring "
     "is powerful precisely because it breaks it.",
     [("is_a", "craft.composition")], ["camera", "composition"]),

    ("craft.headroom", "headroom",
     "Space above the head. Too much makes the subject sink; too little traps them. In an interview "
     "the eyes should sit roughly on the upper third line, and that single adjustment fixes most "
     "amateur framing.",
     [("is_a", "craft.composition"), ("used_in", "craft.interview"),
      ("prevents", "failure.bad_headroom")], ["camera", "composition"]),

    ("craft.lookroom", "looking room / nose room",
     "Space in front of a face turned in profile. Without it the subject presses against the frame "
     "edge and the shot feels wrong for reasons the viewer cannot name. Same principle for a moving "
     "subject: lead the movement.",
     [("is_a", "craft.composition"), ("prevents", "failure.cramped_frame")], ["camera", "composition"]),

    ("craft.leading_lines", "leading lines",
     "Lines in the scene that carry the eye to the subject. Roads, rails, shadows, an arm. The most "
     "reliable compositional tool because it works before the viewer is conscious of the frame.",
     [("is_a", "craft.composition")], ["camera", "composition"]),

    ("craft.framing_within_frame", "frame within a frame",
     "Shoot through a doorway, a window, branches. Adds depth, focuses attention, and implies "
     "watching or confinement depending on how tight it is.",
     [("is_a", "craft.composition"), ("produces", "effect.depth")], ["camera", "composition"]),

    ("craft.negative_space", "negative space",
     "Empty area deliberately left in the frame. Isolation, calm, or dread. In titles and explainers "
     "it is also the practical thing: it is where text goes, and planning it at the shoot is cheaper "
     "than fighting for it in the edit.",
     [("is_a", "craft.composition"), ("used_in", "craft.title_design")], ["camera", "composition"]),

    ("craft.symmetry", "symmetry",
     "Centred, balanced, formal. Reads as order, control, or artifice - which is why it belongs to "
     "institutions and to Wes Anderson. It is also the easiest composition to get right on a static "
     "diagram shot.",
     [("is_a", "craft.composition"), ("causes", "effect.formality")], ["camera", "composition"]),

    ("craft.depth_layers", "staging in depth",
     "Something in the foreground, the subject in the middle, something behind. Three layers is "
     "enough to make a flat sensor read as a room. This is the cheapest fix for a shot that looks "
     "flat, and it costs nothing but moving one object.",
     [("is_a", "craft.composition"), ("produces", "effect.depth"),
      ("prevents", "failure.flat_image")], ["camera", "composition"]),

    ("craft.clean_background", "clean background",
     "No pole growing out of a head, no bright object stealing the eye, no text you did not intend to "
     "publish. Check the background before the subject; the subject will be fine, the background is "
     "where the mistakes live.",
     [("is_a", "craft.composition"), ("prevents", "failure.busy_background")],
     ["camera", "composition"]),

    ("craft.180_rule", "the 180-degree rule",
     "Keep the camera on one side of the line between two subjects, and their screen directions stay "
     "consistent across cuts. Break it and the audience feels the geography flip even if they cannot "
     "say why. You may cross deliberately, on a move or on a cutaway - the rule is about not doing it "
     "by accident.",
     [("part_of", "craft.continuity"), ("prevents", "failure.geography_confusion"),
      ("applies_to", "craft.shot_reverse_shot")], ["camera", "editing", "continuity"]),

    ("craft.30_degree_rule", "the 30-degree rule",
     "Between two shots of the same subject, change the angle by at least thirty degrees or the cut "
     "reads as a jump. The underlying principle is that a cut must give the eye a genuinely new view; "
     "a small change is neither a match nor a move.",
     [("part_of", "craft.continuity"), ("prevents", "failure.jump_cut")],
     ["camera", "editing", "continuity"]),

    ("craft.eyeline_match", "eyeline match",
     "Where a person looks in one shot must agree with what the next shot shows. The audience "
     "reconstructs the room from eyelines faster than from anything else, which is why a wrong "
     "eyeline is instantly disorienting even in animation.",
     [("part_of", "craft.continuity"), ("requires", "craft.180_rule")],
     ["camera", "editing", "continuity"]),

    # ---------------- exposure and format ----------------
    ("craft.exposure_triangle", "exposure",
     "Aperture, shutter and sensitivity trade against each other. In film work two of them are "
     "usually locked by other decisions - shutter by the motion-blur convention, aperture by the depth "
     "you want - so sensitivity and light are what you actually move.",
     [("part_of", "craft.camera")], ["camera", "exposure"]),

    ("craft.shutter_angle", "shutter angle / 180-degree shutter",
     "Expose for half the frame interval and you get the motion blur cinema has trained everyone to "
     "expect: at 24fps, a 1/48 second shutter. Faster and motion becomes staccato and harsh, which is "
     "why the Saving Private Ryan look is a shutter choice. In a render this is the motion-blur "
     "shutter setting, and leaving it off is the loudest tell that a shot is computer-generated.",
     [("is_a", "craft.exposure_triangle"), ("causes", "craft.motion_blur"),
      ("prevents", "failure.cgi_look")], ["camera", "exposure", "render"]),

    ("craft.motion_blur", "motion blur",
     "Smear within a frame caused by movement during the exposure. Its absence is uncanny; its "
     "presence is what makes 24 frames a second acceptable at all. Every render engine has it, most "
     "default it off because it costs samples, and turning it on is usually the single biggest "
     "improvement available to an animated shot.",
     [("part_of", "craft.camera"), ("prevents", "failure.strobing"),
      ("implemented_by", "tool.blender")], ["camera", "render", "animation"]),

    ("craft.frame_rate", "frame rate",
     "24 for film, 25 or 50 for European broadcast, 30 or 60 for the web and for anything with fast "
     "motion or screen recording. The choice is a look, not just a number: 24 is dreamlike and "
     "strobes on pans, 60 is immediate and reads as video, live sport or a game.",
     [("part_of", "craft.format"), ("causes", "craft.judder")], ["camera", "format", "delivery"]),

    ("craft.aspect_ratio", "aspect ratio",
     "16:9 for the web, 2.39:1 for scope, 1.85:1 for flat, 4:3 for archive and for deliberate "
     "constriction, 9:16 for phones. It is a compositional constraint before it is a delivery "
     "decision - a wide frame wants two things in it, a tall one wants a person.",
     [("part_of", "craft.format")], ["camera", "format", "delivery"]),

    ("craft.resolution_choice", "resolution",
     "Shoot or render larger than delivery when you can: it buys reframing, stabilisation and clean "
     "downscale sharpening. But an upscaled small render is not the same as a large one, and the "
     "difference shows in edges and in fine texture, not in the size of the file.",
     [("part_of", "craft.format"), ("applies_to", "craft.upscaling")], ["format", "delivery"]),

    ("craft.safe_area", "title safe and action safe",
     "Keep essential text inside about 90 per cent of the frame and important action inside 93. It "
     "matters less than it did with tube televisions and more than people think now, because "
     "platforms overlay their own chrome on the bottom and right of a player.",
     [("part_of", "craft.format"), ("applies_to", "craft.title_design")], ["format", "titles"]),
]

# effects and failures this file points at, defined here so the graph is closed
SUPPORT = [
    ("effect.intimacy", "effect", "intimacy", "The audience is brought close enough to read a face rather than a situation."),
    ("effect.scale", "effect", "scale", "The subject is measured against something much larger."),
    ("effect.isolation", "effect", "isolation", "The subject is alone in a frame that could hold more."),
    ("effect.dominance", "effect", "dominance", "The subject has power in the frame."),
    ("effect.vulnerability", "effect", "vulnerability", "The subject is diminished by the frame."),
    ("effect.unease", "effect", "unease", "Something is wrong and the audience cannot name it."),
    ("effect.depth", "effect", "depth", "A two-dimensional image reads as a three-dimensional space."),
    ("effect.parallax", "effect", "parallax", "Foreground and background move against each other, proving the space is real."),
    ("effect.immersion", "effect", "immersion", "The audience is inside the space rather than watching it."),
    ("effect.compression", "effect", "compression", "Distances stack; the far becomes as present as the near."),
    ("effect.intensification", "effect", "intensification", "The pressure of a moment rises without a cut."),
    ("effect.immediacy", "effect", "immediacy", "It is happening now and nobody arranged it."),
    ("effect.formality", "effect", "formality", "The frame is composed, controlled, deliberate."),
    ("effect.diagrammatic", "effect", "diagrammatic", "The image explains a relationship rather than showing an event."),

    ("failure.geography_confusion", "failure", "the audience loses the room",
     "They cannot say who is where, so every subsequent cut costs them attention. Caused by "
     "crossing the line, by too few wides, or by close-ups that never established a space."),
    ("failure.jump_cut", "failure", "jump cut",
     "Two shots too similar to be a cut and too different to be continuous. The subject appears to "
     "teleport slightly. Fixable with a cutaway, a 30-degree angle change, or by embracing it."),
    ("failure.unmotivated_move", "failure", "the camera moves for no reason",
     "The audience notices the camera. The fix is not a smoother move; it is a reason - someone "
     "stands, something enters, a line lands."),
    ("failure.busy_background", "failure", "the background steals the shot",
     "Bright, high-contrast or moving elements behind the subject win the eye. Fix by moving the "
     "subject, changing the angle, going longer on the lens, or killing the light behind them."),
    ("failure.flat_image", "failure", "the image reads flat",
     "No foreground, no separation, even light. Reads as amateur video regardless of resolution. "
     "The cheapest fixes are a foreground object and a backlight."),
    ("failure.bad_headroom", "failure", "wrong headroom",
     "Head floating in the middle of frame or cropped at the crown by accident."),
    ("failure.cramped_frame", "failure", "the subject is pressed against the frame edge"),
    ("failure.judder", "failure", "judder on a pan",
     "At 24fps a horizontal pan faster than roughly seven seconds per frame width strobes. Slow the "
     "pan, raise the frame rate, or add motion blur."),
    ("failure.strobing", "failure", "strobing motion",
     "Discrete steps instead of movement, because there is no motion blur and the step per frame is "
     "larger than the eye tolerates."),
    ("failure.cgi_look", "failure", "it looks computer-generated",
     "Usually not a modelling problem. It is: no motion blur, no lens imperfection, no grain, "
     "perfect focus everywhere, and lighting with no bounce."),
    ("failure.fake_looking_dof", "failure", "fake depth of field",
     "A uniform blur applied to a whole background rather than a depth-varying defocus, with no "
     "highlight bloom and hard edges where the matte was cut."),
    ("failure.gaussian_fake_bokeh", "failure", "gaussian blur used as bokeh",
     "Gaussian blur has no aperture shape and does not bloom highlights, so bright points smear "
     "instead of becoming discs. Use a real defocus or a lens-blur filter."),
    ("failure.fake_handheld", "failure", "synthetic handheld with no weight",
     "Uniform sinusoidal wobble. Real handheld has drift, correction, anticipation before a move, "
     "and settles after it. Drive it with filtered noise plus occasional correction, not a sine."),
]
