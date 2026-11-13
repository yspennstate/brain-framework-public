# -*- coding: utf-8 -*-
"""Close the eleven dangling craft targets from the lived film record.

Camera and lighting definitions complete the authored camera.py/light_color.py
concepts. Historical observations below cite memory slugs, not measurements on
the deployment machine. Do not turn those observations into local capacity.
"""

NODES = [
    ("craft.flattering_portrait", "flattering portrait perspective",
     "Choose camera distance before choosing a lens to fill the frame. A face very close to a "
     "wide lens has an enlarged nose relative to the ears; step back and use a longer lens for "
     "the same framing when that exaggeration is unwanted. Check the face, eye focus and "
     "background together. This completes the portrait tradeoff in camera.py's wide_lens and "
     "long_lens nodes; it is craft guidance, not a measured preference of the subject.",
     [("part_of", "craft.camera"), ("see_also", "craft.long_lens"),
      ("see_also", "craft.perspective_distortion")], ["camera", "portrait", "lens"]),

    ("craft.focus_reliability", "keeping the subject in focus through the take",
     "A sharp test frame does not prove a moving take stays sharp. Check the eyes through the "
     "subject's nearest and farthest positions. A wider aperture reduces the margin for subject "
     "movement and focus error; stop down, adjust the blocking or pull focus when that margin "
     "is too small. This is the cost named by camera.py's aperture/depth_of_field tradeoff.",
     [("part_of", "craft.camera"), ("see_also", "craft.aperture"),
      ("see_also", "craft.depth_of_field")], ["camera", "focus"]),

    ("craft.format", "picture format and delivery constraints",
     "Choose frame rate, aspect ratio, resolution and safe areas before composing the picture; "
     "carry that choice through render, edit and delivery. An output file existing is not proof "
     "of the intended format: probe the encoded streams and inspect the decoded result. The "
     "lived filmbrain record (filmbrain_the_movie_making_part_of_the_brain_2026_09_04) makes "
     "asking the tool-and-craft graph before rendering part of the workflow; camera.py defines "
     "these four format choices. A larger output size alone does not restore missing detail.",
     [("part_of", "craft.camera"), ("see_also", "craft.frame_rate"),
      ("see_also", "craft.aspect_ratio"), ("see_also", "craft.resolution_choice"),
      ("see_also", "craft.safe_area")], ["format", "delivery"]),

    ("craft.judder", "judder and uneven motion cadence",
     "Motion advances in visible steps, from a pan's frame-to-frame displacement or from "
     "uneven repeated frames after retiming. Inspect the actual motion before buying a fix. "
     "Slow a pan or choose a suitable frame rate and shutter; motion interpolation is a "
     "different, potentially expensive treatment for retiming. In the 2026-09-08 Bastogne "
     "record, a 1.0243x stretch repeated only one frame in about 41: a four-second probe cost "
     "32.0 s with minterpolate versus 1.6 s without, with no visible gain at that ratio. These "
     "are historical probe results, not a universal interpolation threshold. Source: "
     "the_slowest_step_was_a_filter_nobody_needed_2026_09_08.",
     [("part_of", "craft.camera"), ("see_also", "failure.judder"),
      ("see_also", "craft.motion_blur"), ("see_also", "craft.frame_rate")],
     ["camera", "motion", "retiming"]),

    ("craft.perspective_distortion", "perspective changes with camera distance",
     "Near objects grow relative to far ones as the camera moves closer. A wide lens often "
     "invites that close viewpoint, but changing focal length from a fixed viewpoint changes "
     "framing rather than the relative size of nose and ears. To change perspective, move the "
     "camera and then choose the lens for framing. This makes explicit the distance tradeoff "
     "behind camera.py's focal_length, wide_lens and long_lens definitions; optical barrel "
     "distortion is a separate lens defect.",
     [("part_of", "craft.camera"), ("see_also", "craft.focal_length"),
      ("see_also", "craft.wide_lens"), ("see_also", "craft.long_lens")],
     ["camera", "perspective", "lens"]),

    ("craft.shadow_direction", "shadow direction describes the light source",
     "A key light gives the scene a direction: shadows fall away from that source, describing "
     "faces and objects. Check that the direction agrees across subjects, backgrounds and "
     "composited elements, and stays motivated across cuts. Fill controls shadow depth without "
     "becoming an accidental second key. This completes light_color.py's key_light relation; "
     "the authored lighting layer distinguishes direction from softness and light ratio.",
     [("part_of", "craft.lighting"), ("see_also", "craft.key_light")],
     ["lighting", "continuity"]),

    ("failure.cuda_quarantine", "an inherited visibility mask hides the CUDA device",
     "Historical Windows failure: nvidia-smi saw the GPU while CUDA returned no device because "
     "CUDA_VISIBLE_DEVICES=-1 was inherited from the July TDR quarantine. The 2026-09-05 probe "
     "succeeded with the variable removed; the user-scope mask was then removed, "
     "but existing parent processes still passed the old environment to children. "
     "Inspect the failing process's environment before blaming the driver. Any diagnostic "
     "override is scoped to that process and the machine's current GPU policy; this node does "
     "not authorize clearing a quarantine or describe CUDA support on a Mac. Source: "
     "cuda_visible_devices_minus_one_was_the_gpu_blocker_not_the_driver_2026_09_05.",
     [("see_also", "gotcha.cuda_quarantine"), ("see_also", "machine.gpu")],
     ["failure", "gpu", "windows", "historical"]),

    ("failure.false_install", "installer success without a runnable tool",
     "On 2026-09-04 Windows winget reported success for Inkscape and Audacity with no executable "
     "on disk; an MSI returned zero after rolling back on an access-denied write. A successful "
     "installer exit is evidence about the installer, not a working film tool. Locate the "
     "intended executable, run it and record the version or a representative operation before "
     "planning work around it. This verification rule also applies on POSIX. Source: "
     "filmbrain_the_movie_making_part_of_the_brain_2026_09_04.",
     [("see_also", "gotcha.install_success_lies"), ("see_also", "machine.toolchain")],
     ["failure", "installation", "verification"]),

    ("failure.warmup_contamination", "startup cost mistaken for a variant's render cost",
     "The first variant pays shader compilation and GPU context setup, so comparing it to a "
     "warm second variant can reverse the conclusion. The 2026-09-04 filmbrain recorder filed "
     "the false lesson 'motion blur costs EEVEE -91 per cent' from contaminated timings. Keep "
     "cold-start and warmed timings separate, warm each configuration before comparison, repeat "
     "with order changed and inspect the measured values before recording a conclusion. Source: "
     "filmbrain_the_movie_making_part_of_the_brain_2026_09_04 and gotcha.blender_warmup.",
     [("see_also", "gotcha.blender_warmup"), ("see_also", "machine.blender_warmup")],
     ["failure", "blender", "measurement"]),

    ("machine.blender_speed", "Blender speed must be measured for this scene and machine",
     "No local throughput is asserted here. Historical probes on a research machine on 2026-09-16 measured "
     "EEVEE 1080p diagram boards at 0.35-0.46 s/frame (Standard view, taa4, JPEG, no compositor). "
     "The same memo's 0.75 s/frame Cycles OptiX result was explicitly corrected on 09-17: a "
     "160x160-subdivided surface at 16 samples cost 26.5 s/frame versus EEVEE's 0.40. Scene and "
     "settings dominate; do not transfer either number to this deployment or budget an hour "
     "from one frame. Probe representative warmed frames through fbrun, recording engine, "
     "resolution, samples, wall time and contention. A held Manim board can reuse a frame; "
     "Blender's continuous render is not automatically cheaper. Source: "
     "blender_gpu_boards_how_and_when_2026_09_16, including its 09-17 correction.",
     [("part_of", "craft.this_machine"), ("applies_to", "tool.blender"),
      ("see_also", "machine.render_cost"), ("see_also", "machine.blender_warmup")],
     ["machine", "blender", "historical", "measurement"]),

    ("machine.blender_warmup", "measure Blender cold start separately from steady rendering",
     "This is a measurement procedure, not a local timing result. First-frame shader compilation "
     "and context creation belong in a cold-start record. Keep that record, then render "
     "representative frames in the warmed session for a steady-state comparison; warm both "
     "variants and change their order before concluding a setting is faster. The authored "
     "gotcha.blender_warmup records 102 s versus a warmed 9 s first-day EEVEE trap, and the "
     "2026-09-04 lived filmbrain memory records the resulting false negative motion-blur cost. "
     "Those historical seconds are not this machine's capability. Source: "
     "filmbrain_the_movie_making_part_of_the_brain_2026_09_04.",
     [("part_of", "craft.this_machine"), ("applies_to", "tool.blender"),
      ("prevents", "failure.warmup_contamination"),
      ("see_also", "gotcha.blender_warmup")],
     ["machine", "blender", "measurement", "historical"]),
]
