# -*- coding: utf-8 -*-
"""
The traps. Things that are true about these specific tools, cost an hour each
to discover, and are not in any tutorial because they are not features.

Every one of these was paid for - most of them on 2026-09-04.
"""

DOMAIN = ("craft.gotchas", "traps",
          "Tool-specific things that waste an hour and are not in the documentation.")

NODES = [
    # ---------------- ffmpeg ----------------
    ("gotcha.ffmpeg_filter_order", "filter order changes the result",
     "An -af or -vf chain runs left to right, and several pairs are not commutative. Loudness "
     "normalisation must come LAST or anything after it changes the level it just set. Additive EQ "
     "before a compressor makes the compressor chase what you boosted. Grain before a scale gets "
     "resampled into mush - grain goes last, on the delivery resolution.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.ffmpeg")], ["ffmpeg", "trap"]),

    ("gotcha.ffmpeg_loudnorm_two_pass", "the loudness is wrong - single-pass loudnorm guesses",
     "loudnorm in one pass estimates the programme loudness from what it has seen so far and gets it "
     "wrong on anything with a quiet opening. Measure first with print_format=json, then feed the "
     "measured values back with linear=true. Two commands, and the difference is audible.",
     [("part_of", "craft.gotchas"), ("applies_to", "craft.loudness_standard")], ["ffmpeg", "audio"]),

    ("gotcha.ffmpeg_concat_needs_same", "concat demuxer needs identical streams",
     "The concat demuxer with -c copy joins files without re-encoding only if codec, resolution, "
     "pixel format, frame rate and timebase all match. Mismatched, it either fails or produces a "
     "file that plays wrong after the first join. Either normalise everything first or accept a "
     "re-encode - and if you re-encode, do it once, at the end.",
     [("part_of", "craft.gotchas"), ("prevents", "failure.double_encode")], ["ffmpeg", "trap"]),

    ("gotcha.ffmpeg_yuv420p", "always set pix_fmt yuv420p for delivery",
     "libx264 will happily produce yuv444p or 10-bit output that several players and most browsers "
     "refuse. The file is fine; nobody can watch it. -pix_fmt yuv420p on every deliverable.",
     [("part_of", "craft.gotchas"), ("applies_to", "craft.delivery")], ["ffmpeg", "delivery"]),

    ("gotcha.ffmpeg_seek_before_input", "-ss before -i is fast, after is exact",
     "Put -ss before -i and ffmpeg seeks by keyframe, which is instant and can be off by up to a "
     "GOP. Put it after and it decodes from the start, which is exact and slow. For grabbing a "
     "thumbnail from a long file, before. For a precise cut, after - or both, coarse then fine.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.ffmpeg")], ["ffmpeg", "trap"]),

    ("gotcha.ffmpeg_windows_console", "every ffmpeg call needs a hidden console flag here",
     "On this machine a subprocess without CREATE_NO_WINDOW (0x08000000) pops a console. One call is "
     "a flash; a loop over 200 clips is a standing annoyance and a named owner rule. Every "
     "subprocess.run in our code carries creationflags.",
     [("part_of", "craft.gotchas"), ("prevents", "failure.flashing_window")], ["ffmpeg", "windows"]),

    # ---------------- Blender ----------------
    ("gotcha.blender_warmup", "the first render of a session is a warm-up",
     "Shader compilation and GPU context setup land entirely on the first frame, and if you are "
     "timing variants that cost lands on whichever ran first. Uncorrected it made EEVEE read 102 "
     "seconds a frame against its real 9, and made motion blur appear to SAVE ninety per cent.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.blender"),
      ("prevents", "failure.warmup_contamination")], ["blender", "measurement"]),

    ("gotcha.blender_engine_names", "the engine identifier changes between versions",
     "BLENDER_EEVEE_NEXT was right for one release and wrong for the next; on Blender 5.2 here the "
     "only identifiers are BLENDER_EEVEE and CYCLES. Setting an unknown one throws, and if you catch "
     "the exception the whole engine silently does not run. Read them from "
     "bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items rather than remembering.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.blender")], ["blender", "trap"]),

    ("gotcha.blender_factory_startup", "always --factory-startup for a headless render",
     "Without it the render inherits whatever preferences and add-ons happen to be enabled on this "
     "machine, so the same file produces different output elsewhere - or here, tomorrow. It also "
     "avoids a startup file with a leftover scene in it.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.blender")], ["blender", "pipeline"]),

    ("gotcha.blender_motion_blur_off", "motion blur is off by default in both engines",
     "And its absence is the single loudest computer-generated tell. Measured here it roughly "
     "doubles EEVEE, from 4.1 to 9.1 seconds a frame, which is a small price for the biggest "
     "available improvement.",
     [("part_of", "craft.gotchas"), ("applies_to", "craft.motion_blur")], ["blender", "look"]),

    # ---------------- MLT and NLEs ----------------
    ("gotcha.mlt_novalidate", "melt renders only a few frames of a long timeline",
     "It tells MLT not to probe the file, so a clip has no known duration and melt renders three "
     "frames of it - with no error, no warning, and a plausible-looking output file. Either use "
     "plain avformat or set an explicit length property. Symptom: a 0.128 second render from a 23 "
     "second timeline.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.kdenlive")], ["mlt", "trap"]),

    ("gotcha.mlt_frames_not_timecode", "MLT in and out points want frame numbers",
     "Timecode strings are accepted in some places and silently mis-parsed in others. Frame numbers "
     "are unambiguous everywhere. And an out point is the LAST FRAME, so it is length minus one - "
     "off-by-one here shortens every clip by a frame, which compounds across a long timeline.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.kdenlive")], ["mlt", "trap"]),

    ("gotcha.mlt_producer_attribute", "the mlt element's producer attribute names what renders",
     "Declaring producer=\"main_bin\" without defining a main_bin makes melt render nothing, quietly. "
     "Point it at the tractor that actually holds the tracks.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.kdenlive")], ["mlt", "trap"]),

    # ---------------- matplotlib as an animation engine ----------------
    ("gotcha.mpl_wrap_true", "my text did not appear at all - matplotlib wrap on a full-bleed axes",
     "matplotlib computes the wrap width from the axes and on a figure-sized axes it comes out as "
     "nothing, so the text silently does not appear. My title cards rendered as an orange rule on an "
     "empty frame. Fold the lines yourself.",
     [("part_of", "craft.gotchas"), ("prevents", "failure.unseen_overlap")], ["matplotlib", "trap"]),

    ("gotcha.mpl_data_vs_axes", "0.5 is not the middle of the frame unless you say so",
     "An axes without explicit set_xlim and set_ylim autoscales to whatever is plotted, so "
     "coordinates you meant as frame fractions become data coordinates. A rule drawn from 0.34 to "
     "0.66 filled the entire width. Set the limits, and use transform=ax.transAxes.",
     [("part_of", "craft.gotchas")], ["matplotlib", "trap"]),

    ("gotcha.mpl_annotation_alpha", "Annotation.set_alpha only touches the text",
     "An arrow drawn with annotate and an empty label: set_alpha succeeds, returns nothing, and the "
     "arrow stays invisible while its caption fades in. Set arrow_patch alpha explicitly - and note "
     "that a try/except around set_alpha will never fire, because it did not fail.",
     [("part_of", "craft.gotchas")], ["matplotlib", "trap"]),

    ("gotcha.mpl_stdin_pipe", "write frames into ffmpeg's stdin",
     "Writing PNGs to disk and encoding them afterwards is several times slower and fills a disk. A "
     "raw rgb24 pipe into an ffmpeg subprocess is the whole reason our engines run at usable speed.",
     [("part_of", "craft.gotchas"), ("applies_to", "tool.ffmpeg")], ["matplotlib", "speed"]),

    # ---------------- this machine ----------------
    ("gotcha.cuda_quarantine", "torch cannot see the GPU until you override the quarantine",
     "CUDA_VISIBLE_DEVICES=-1 is set at USER level from the July TDR crashes. nvidia-smi sees the "
     "card, torch reports 'No CUDA GPUs are available', and nothing explains why. Override per "
     "process with CUDA_VISIBLE_DEVICES=0; leave the user setting alone.",
     [("part_of", "craft.gotchas"), ("causes", "failure.cuda_quarantine")], ["windows", "gpu"]),

    ("gotcha.install_success_lies", "an installer saying it worked is not evidence",
     "winget reported success for two tools that never landed, and an MSI returned exit code 0 after "
     "rolling itself back on an access-denied write. Several installers need an interactive UAC "
     "prompt no agent can answer and report success when it is declined. Verify by finding and "
     "running the executable.",
     [("part_of", "craft.gotchas"), ("prevents", "failure.false_install")], ["windows", "tooling"]),

    ("gotcha.progress_bar_eats_output", "a progress bar on stdout swallows your result",
     "tqdm writes to the same stream as your JSON summary, and a grep filter takes both. Chatterbox "
     "appeared to fail twice when it had worked perfectly. Redirect stdout and stderr to separate "
     "files and read them whole before concluding anything.",
     [("part_of", "craft.gotchas")], ["python", "trap"]),

    ("gotcha.partial_file_reads_as_failure", "a file being written is not a failed file",
     "An mp4 gets its moov atom last, so a probe mid-write says 'moov atom not found' and a size "
     "check says 48 bytes. I diagnosed a broken assembly twice from a file that was simply still "
     "being written. Check whether the writer is alive before concluding, and wait on VALIDITY "
     "rather than on existence or size.",
     [("part_of", "craft.gotchas"), ("prevents", "failure.premature_diagnosis")],
     ["pipeline", "trap"]),

    ("gotcha.cache_needs_code_version", "cache by the data AND the code version",
     "A cache keyed only on the inputs means fixing the renderer changes nothing - the film rebuilds "
     "from the same broken frames and looks identical. A VERSION constant in the module, included in "
     "every cache key, is the whole fix.",
     [("part_of", "craft.gotchas"), ("requires", "principle.stamp_the_cache")], ["pipeline"]),

    ("gotcha.git_bash_paths", "a Windows python needs C:/ not /c/",
     "A glob passed from this shell as /c/Users/... matches nothing and the script reports an empty "
     "directory rather than an unresolvable path. Use the C:/ form, forward slashes, always.",
     [("part_of", "craft.gotchas")], ["windows", "trap"]),
]

SUPPORT = [
    ("failure.flashing_window", "failure", "a console window flashes",
     "A standing owner rule with zero tolerance. Any subprocess on Windows needs CREATE_NO_WINDOW, "
     "and any scheduled task needs a VBS launcher that hides at process creation - -WindowStyle "
     "Hidden still flashes for about 100 ms."),
    ("failure.premature_diagnosis", "failure", "diagnosing a failure from a partial artefact",
     "A file mid-write, a log not yet flushed, a count taken before the job finished. Check the "
     "writer is dead before you call the output broken."),
]
