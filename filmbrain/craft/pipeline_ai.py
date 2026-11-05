# -*- coding: utf-8 -*-
"""
Post, delivery, VFX, and the AI-video specifics - including what this machine
will actually do, measured rather than assumed.
"""

DOMAIN = ("craft.post", "post-production", "Everything after the material exists.")

NODES = [
    # ---------------- pipeline ----------------
    ("craft.preproduction", "pre-production",
     "Every decision that is cheap now and expensive later. Aspect ratio, frame rate, palette, the "
     "structure, the shot list. An hour here routinely saves a day in the edit, which everyone knows "
     "and nobody does.",
     [("part_of", "craft.production")], ["pipeline", "planning"]),

    ("craft.production", "production",
     "Making the material. The only irreplaceable stage - you can regrade, recut and remix, but you "
     "cannot un-shoot a shot you did not take.",
     [("part_of", "craft.filmmaking")], ["pipeline"]),

    ("craft.storyboard", "storyboards",
     "Drawing the shots before you make them. The value is not the drawings; it is that drawing "
     "forces you to decide what each shot is FOR, and half of them turn out not to be for anything.",
     [("part_of", "craft.preproduction")], ["pipeline", "planning"]),

    ("craft.animatic", "animatic",
     "The storyboard cut to time with temp narration. This is the cheapest possible version of the "
     "finished film and it answers the only question that matters early: does the shape work. For a "
     "narrated piece the animatic is nearly free, because the narration already exists.",
     [("part_of", "craft.preproduction"), ("requires", "principle.record_first")],
     ["pipeline", "planning"]),

    ("craft.shot_list", "shot list",
     "Every shot, numbered, with size, move and purpose. Its real function on the day is to tell you "
     "what you can safely drop when you run out of time.",
     [("part_of", "craft.preproduction")], ["pipeline", "planning"]),

    ("craft.naming_convention", "naming and versioning",
     "Zero-padded shot numbers, a scheme that sorts correctly, and a version suffix that never "
     "reuses a number. It sounds like bureaucracy until an assemble script picks up clip 10 before "
     "clip 2 and nobody notices for an hour.",
     [("part_of", "craft.pipeline"), ("prevents", "failure.sort_order")], ["pipeline"]),

    ("craft.pipeline", "pipeline",
     "How material moves from stage to stage without loss. The two rules that matter: everything is "
     "regenerable from source plus a script, and every intermediate carries a stamp of the inputs "
     "that made it so a change invalidates exactly what it should.",
     [("part_of", "craft.post"), ("requires", "principle.stamp_the_cache")], ["pipeline"]),

    ("craft.proxy_workflow", "proxies",
     "Edit against small versions, conform against the originals at the end. On a laptop this is the "
     "difference between an edit that plays and one that stutters, and it costs one batch encode.",
     [("part_of", "craft.pipeline"), ("implemented_by", "tool.ffmpeg")], ["pipeline", "editing"]),

    ("craft.edl_interchange", "EDL, XML, AAF, OTIO",
     "Ways of describing a cut without the media. Useful because they let a program build an edit: "
     "an OpenTimelineIO or an MLT XML file written by a script is a full timeline that a human can "
     "then open in Kdenlive or Resolve and adjust.",
     [("part_of", "craft.pipeline")], ["pipeline", "editing"]),

    ("craft.render_farm", "distributing a render",
     "Frames are embarrassingly parallel; sequences are not. Split by frame range, write to a shared "
     "directory, and have the assemble step take whatever is complete. The hard part is never the "
     "distribution, it is making a partial result usable.",
     [("part_of", "craft.pipeline")], ["pipeline", "render"]),

    ("craft.review_loop", "watching it",
     "The step everyone skips. Sample a frame from every chapter and look at them side by side; watch "
     "the whole thing once at speed with no notes. Two genuine bugs in our lecture - a diagram drawn "
     "over another diagram - were invisible in code and obvious in a rendered frame.",
     [("part_of", "craft.pipeline"), ("prevents", "failure.unseen_overlap")], ["pipeline", "qa"]),

    ("craft.contact_sheet", "contact sheet",
     "One image holding a frame from every shot. The fastest quality control there is: mismatched "
     "grades, empty frames, wrong aspect ratios and duplicated shots all show up in a single glance.",
     [("part_of", "craft.review_loop"), ("implemented_by", "tool.ffmpeg")], ["pipeline", "qa"]),

    # ---------------- vfx ----------------
    ("craft.vfx", "visual effects",
     "Putting something in the frame that was not there. The whole discipline is integration, not "
     "creation: a perfect element that does not share the plate's grain, blur, black level and colour "
     "will look pasted on, and a rough element that shares all four will not.",
     [("part_of", "craft.post")], ["vfx"]),

    ("craft.compositing", "compositing",
     "Combining layers into one image. Work in linear light, keep alpha premultiplied and know it, "
     "and match the plate last: grain, defocus, chromatic aberration and a touch of the plate's own "
     "colour cast over the top of everything.",
     [("part_of", "craft.vfx"), ("requires", "craft.color_management")], ["vfx", "post"]),

    ("craft.keying", "keying",
     "Pulling a matte from a colour. Lit well it takes minutes; lit badly no software saves it. The "
     "two things that decide it are separation between subject and screen, and an evenly lit screen "
     "with no hotspots. Spill suppression afterwards is what stops the green edge.",
     [("part_of", "craft.compositing"), ("implemented_by", "tool.ffmpeg")], ["vfx"]),

    ("craft.roto", "rotoscoping a matte",
     "Hand-drawn masks over time when no key exists. Expensive, and the thing that makes it "
     "affordable is animating few points on a shape that follows the motion rather than many points "
     "redrawn per frame.",
     [("part_of", "craft.compositing")], ["vfx"]),

    ("craft.tracking", "tracking and matchmoving",
     "Recovering camera or object motion from the footage so an added element sticks. 2D tracking "
     "sticks a thing to a plane; 3D solve recovers the camera so you can put an object in the space. "
     "Blender does both, and its solver wants parallax and sharp high-contrast features.",
     [("part_of", "craft.vfx"), ("implemented_by", "tool.blender")], ["vfx"]),

    ("craft.plate_matching", "matching the plate",
     "The checklist that makes a composite invisible: black level, white level, colour cast, grain "
     "size and strength, motion blur, defocus, lens distortion, chromatic aberration, and a hint of "
     "atmospheric haze at distance. Work through it in that order.",
     [("part_of", "craft.compositing"), ("prevents", "failure.pasted_on")], ["vfx"]),

    ("craft.simulation", "simulation",
     "Fire, smoke, cloth, water, crowds. The expensive part is not the simulation but the iteration: "
     "cache aggressively, work at low resolution until the motion is right, then run once at "
     "quality. Changing anything upstream invalidates the cache, which is why the stamp matters.",
     [("part_of", "craft.vfx"), ("implemented_by", "tool.blender")], ["vfx", "3d"]),

    # ---------------- delivery ----------------
    ("craft.delivery", "delivery",
     "Getting the finished thing to a viewer without giving away what you made. The commonest "
     "avoidable losses: encoding twice, delivering untagged colour, delivering at a bitrate the "
     "motion cannot carry, and normalising loudness after the platform already did.",
     [("part_of", "craft.post")], ["delivery"]),

    ("craft.codec_choice", "codec choice",
     "H.264 for compatibility, H.265 or AV1 for the same quality at a lower bitrate where the player "
     "supports it, ProRes or DNxHR as an intermediate that survives being re-encoded. Never edit "
     "against long-GOP delivery codecs if you can avoid it - every seek decodes a whole group.",
     [("part_of", "craft.delivery"), ("implemented_by", "tool.ffmpeg")], ["delivery", "encode"]),

    ("craft.bitrate", "bitrate and CRF",
     "Constant-quality encoding (CRF) is right for delivery of a fixed file; two-pass bitrate is "
     "right when a size target is imposed. CRF 17 is visually lossless for most material, 20 to 23 is "
     "a good web delivery, and grain is the thing that eats bits fastest - which is the trade for the "
     "look it buys.",
     [("part_of", "craft.delivery"), ("conflicts_with", "craft.grain"),
      ("implemented_by", "tool.ffmpeg")], ["delivery", "encode"]),

    ("craft.generation_loss", "generation loss",
     "Every lossy re-encode costs something. Build the whole chain to encode ONCE at the end, and "
     "make intermediates either lossless or visually transparent. Our chalk board video was being "
     "encoded at preset slow, crf 17, and then re-encoded when the narration was muxed - paying "
     "twice for a quality nobody sees.",
     [("part_of", "craft.delivery"), ("prevents", "failure.double_encode")], ["delivery", "encode"]),

    ("craft.upscaling", "upscaling",
     "Making a small render big. Lanczos plus a small unsharp plus grain reads better than a naive "
     "bicubic; a learned upscaler reads better still on faces and worse on text and fine structure. "
     "The honest framing is that upscaling hides a resolution deficit, it does not remove one.",
     [("part_of", "craft.post"), ("requires", "craft.grain"),
      ("causes", "failure.plastic_upscale"), ("implemented_by", "tool.ffmpeg")],
     ["delivery", "ai", "post"]),

    ("craft.subtitles", "subtitles and captions",
     "Two lines maximum, about 37 characters a line, at least one second on screen and no more than "
     "seven, broken at clause boundaries. Burn them in only when you must; a sidecar file is "
     "searchable and switchable.",
     [("part_of", "craft.delivery"), ("implemented_by", "tool.ffmpeg")], ["delivery", "titles"]),

    ("craft.title_design", "titles",
     "Legibility first: enough contrast against the moving image beneath, a size that survives a "
     "phone, and a duration long enough to be read twice at a slow pace. Everything else is taste, "
     "and drop shadows are usually the wrong answer to a contrast problem - a subtle scrim is better.",
     [("part_of", "craft.post"), ("requires", "craft.safe_area")], ["titles", "graphics"]),

    ("craft.thumbnail", "thumbnail and first frame",
     "For anything published on a platform, the still is part of the film whether you designed it or "
     "not. One readable subject, high contrast, and legible at the size of a fingernail.",
     [("part_of", "craft.delivery")], ["delivery", "web"]),

    # ---------------- AI video ----------------
    ("craft.ai_video", "generative video",
     "Text or image to video with a diffusion model. What it is genuinely good at: texture, "
     "atmosphere, short atmospheric shots, and material no camera could get. What it is bad at, still: "
     "anything that must be consistent across shots, text in frame, hands, and anything where a "
     "specific thing must happen at a specific moment. Treat a generated clip as found footage - you "
     "get what you get and you edit around it.",
     [("part_of", "craft.production")], ["ai", "video"]),

    ("craft.ai_prompt_structure", "prompting a video model",
     "Subject, action, environment, camera, lighting, style, in roughly that order, with the camera "
     "and lighting stated in real film language because the training data was captioned that way. "
     "Negative or vague words waste tokens. One clear action per clip; two actions gives you neither.",
     [("part_of", "craft.ai_video"), ("requires", "craft.shot_size")], ["ai", "video", "prompting"]),

    ("craft.ai_seed", "seeds and takes",
     "The same prompt at a different seed is a different take. Once every shot exists, more compute "
     "only buys alternative takes - so vary the seed and pick the sharpest. Leaving the seed at its "
     "default means every re-render repeats itself exactly, which wasted hours here on 09-03.",
     [("part_of", "craft.ai_video"), ("prevents", "failure.identical_reruns")], ["ai", "video"]),

    ("craft.ai_temporal_coherence", "temporal coherence",
     "Whether a thing stays the same thing for the length of the clip. Current open models hold for a "
     "few seconds and then drift; faces drift first, then hands, then clothing detail. The practical "
     "consequence is that clips should be short and cut before the drift is visible.",
     [("part_of", "craft.ai_video"), ("causes", "failure.identity_drift")], ["ai", "video"]),

    ("craft.ai_shot_consistency", "keeping a character across shots",
     "The hard problem. Partial answers: generate from a single reference image, keep the same seed "
     "family, describe the character identically every time, and design the film so the character is "
     "rarely seen close and never seen twice in the same frame as themselves. Or accept it and cut "
     "in a way that makes the inconsistency read as style.",
     [("part_of", "craft.ai_video"), ("causes", "failure.identity_drift")], ["ai", "video"]),

    ("craft.img2vid", "image to video",
     "Start from a still you control and let the model animate it. Far more controllable than text to "
     "video because composition, palette and subject are already decided - and the still can come "
     "from anywhere, including a render or a photograph.",
     [("is_a", "craft.ai_video"), ("preferred_over", "craft.ai_video")], ["ai", "video"]),

    ("craft.ai_as_texture", "use generated video as texture, not as story",
     "The lesson from the Bastogne night: an hour of AI film on this card was 450 shots at 13 minutes "
     "of card time each, which is 97 GPU-hours. Generated clips are affordable as inserts, "
     "atmosphere and transitions inside a piece whose spine is something cheaper - a map engine, a "
     "board, stills with motion. Building the spine out of generated clips is what makes the budget "
     "impossible.",
     [("part_of", "craft.ai_video"), ("measured_by", "metric.gpu_minutes_per_clip")],
     ["ai", "video", "measured"]),
]

SUPPORT = [
    ("craft.filmmaking", "craft", "filmmaking", "The whole activity, root of the graph."),
    ("principle.stamp_the_cache", "principle", "stamp every cached artefact with its inputs",
     "Cache by a hash over the text, the parameter list and the files each piece resolved to. Then a "
     "changed sentence invalidates exactly its own segment and nothing else. Our film engines do "
     "this and it is why a rewritten line moves its own visuals with it."),
    ("metric.gpu_minutes_per_clip", "metric", "GPU minutes per finished clip",
     "Card time divided by clips that survived, INCLUDING the failures. Measured here on 09-03/04: "
     "about 13 minutes per finished LTX clip at a one-in-three success rate - 6.5 minutes for a "
     "success, 2.8 for a failure."),
    ("failure.sort_order", "failure", "lexical sort put clip 10 before clip 2",
     "Zero-pad every number in every filename."),
    ("failure.unseen_overlap", "failure", "two elements drawn over each other",
     "Invisible in code, obvious in a frame. Sample a frame per chapter and look."),
    ("failure.pasted_on", "failure", "the element looks pasted on",
     "Different grain, different black level, too sharp, no motion blur, no shared colour cast."),
    ("failure.double_encode", "failure", "encoding the same pixels twice",
     "An expensive intermediate encode that is immediately re-encoded. Make intermediates cheap or "
     "lossless."),
    ("failure.identical_reruns", "failure", "every re-render is the same",
     "The seed was left at its default, so extra compute bought nothing."),
    ("failure.identity_drift", "failure", "the subject changes identity mid-clip",
     "Faces first, then hands, then clothing. Cut before it shows."),
]
