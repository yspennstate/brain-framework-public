"""Lecture craft from the owner's September 4--5 corrections.

Owner preferences are not measurements of learning gains. Original prompt locators
are private in experiments/nmkc_lecture_20260905; no raw prompts are copied here.
Historical no-LaTeX and kokoro defaults are superseded within this lecture scope.
"""

DOMAIN = ("craft.geometric_lecture", "Mathematics through pictures and geometry",
          "Owner-stated: explain mathematical papers through concrete pictures, geometric "
          "constructions, worked examples and complete proofs. A picture carries an argument; "
          "its presence alone is not evidence that the explanation works.")

NODES = [
    ("principle.picture_carries_proof", "A meaningful geometric picture on every lecture board",
     "Owner-stated, September 4--5: each board with words needs a picture or two explaining "
     "those words. Start from a concrete object, show what changes, name its variables, then "
     "derive the formula. Link arrows, distances, projections, areas and uncertainty regions "
     "to the same symbols in the proof. A decorative icon or unrelated stock image does not "
     "satisfy this request. A low-dimensional analogy must be labelled as an analogy and its "
     "invariant carried explicitly into the general proof. The criterion is semantic, so a "
     "nonempty image field is only a structural check, never a comprehension test.",
     [("part_of", "craft.geometric_lecture"), ("requires", "craft.complete_proof_sequence"),
      ("prevents", "failure.decorative_proof_picture")],
     ["lecture", "geometry", "pictures", "proof", "owner-stated"]),
    ("craft.complete_proof_sequence", "Show every load-bearing step of the main proof",
     "For a paper lecture, state the hypotheses, construct the geometric object, work a "
     "small example, derive each equality and inequality, and show the equality or failure "
     "case. Define every symbol before using it. Distinguish an illustrative simulation "
     "from a proof, a conditional theorem from a measured certificate, and a finite-n "
     "statement from a limit. Slow down for the proof; do not slow every introduction or "
     "fill the requested duration with silence. Owner asked explicitly for the main proof "
     "in the NMKC lecture on September 5.",
     [("part_of", "craft.geometric_lecture"), ("requires", "machine.latex_pdf_route")],
     ["proof", "theorem", "finite sample", "example", "geometry"]),
    ("machine.latex_pdf_route", "Real LaTeX: pdflatex, then dvisvgm PDF to SVG",
     "Verified reference implementation: ~/lecture_relu/tex.py sets Manim "
     "TexTemplate.tex_compiler='pdflatex' and output_format='.pdf'; Manim converts with "
     "dvisvgm --pdf. Its preamble uses amsmath, amssymb, newtxtext and newtxmath. The old "
     "DVI backend failure is specific to that route. For a new lecture, author explicit "
     "LaTeX rather than guessing math spans from prose, use a separate TeX cache per process, "
     "and stop a final build on a failed equation. A Unicode fallback is not verified "
     "mathematical typesetting. Visually inspect superscripts, norms and matrix indices.",
     [("part_of", "craft.geometric_lecture"), ("fixes", "failure.latex_broken_here"),
      ("implemented_by", "tool.manim")],
     ["LaTeX", "mathematics", "pdflatex", "SVG", "lecture", "typesetting"]),
    ("voice.lecture_andrew_20260905", "Natural human narrator for a paper lecture: Microsoft Andrew",
     "Owner-stated September 5: use the most natural Microsoft neural voice; he accepted "
     "sending narration text to Microsoft's service and liked the current sample. The "
     "active reference is edge-tts en-US-AndrewMultilingualNeural at rate +10%. Brian, Ryan "
     "and Guy are audition alternatives, not permission to mix voices randomly within a "
     "lecture. The latest owner instruction requires Microsoft voices only; the earlier "
     "Kokoro offline fallback is superseded. Judge the "
     "actual spoken passage, including pronunciation and pauses; pitch or jitter alone "
     "cannot establish that a voice sounds human. Later preference for voice samples and "
     "possible rotation does not specify a replacement voice for NMKC.",
     [("part_of", "craft.geometric_lecture"), ("applies_to", "craft.narration")],
     ["voice", "narrator", "narration", "natural", "human", "paper video", "Microsoft", "Andrew", "owner-stated"]),
    ("craft.lecture_pacing_20260905", "Brisk introduction, patient mathematics, direct prose",
     "Owner's latest correction: the narrator stopped too often; speed up the introduction "
     "and the speaking voice. Preserve time for unfamiliar examples and proof steps. Write "
     "as a lecturer explaining the object, without self-commentary about the process. Use "
     "ordinary connected sentences and a capital letter when a prose line begins with a "
     "word. Record first and measure actual durations. Short purposeful pauses are useful; "
     "uniform long gaps and repeated footage are not a way to reach two hours.",
     [("part_of", "craft.geometric_lecture"), ("requires", "principle.record_first")],
     ["lecture", "pace", "pauses", "human voice", "prose", "owner-stated"]),
    ("craft.lecture_data_provenance", "Show the actual fields and numerical evidence",
     "Use images of the paper's data, labelled inputs, reference outputs, predictions and "
     "residuals on aligned axes and honest colour scales. Every reported numerical figure "
     "needs a source file, hash, split, seed set, metric, units and transformation. Mark toy "
     "examples as illustrative. A geometric sketch must not masquerade as observed data. "
     "Keep mean error and RMS error distinct, and show unsuccessful cases or limitations "
     "where they change the conclusion. Make graphs legible at video viewing size.",
     [("part_of", "craft.geometric_lecture"), ("requires", "craft.complete_proof_sequence")],
     ["lecture", "data", "numerics", "graph", "field", "provenance"]),
    ("principle.narration_identity", "Check that a recording matches the current script and voice",
     "A recording is current only if the actual audio hash is bound to the current "
     "normalized narration text, voice, rate and synthesis settings. Old manifest entries "
     "cannot prove that a file overwritten by another narrator has that content. Validate "
     "all chapter beats before rendering, time pictures from the bound WAV durations, "
     "and regenerate changed beats. Test a wrong voice and replaced WAV as negative "
     "controls. This is a proposed stricter contract derived from source inspection; "
     "it does not claim a failure occurred in the existing finished lecture.",
     [("part_of", "craft.geometric_lecture"), ("requires", "principle.record_first")],
     ["lecture", "audio", "hash", "voice", "recording", "provisional"]),
]

SUPPORT = [
    ("failure.decorative_proof_picture", "failure", "A picture is present but explains nothing",
     "Counting images can pass a board whose visual has no relation to its mathematical "
     "claim. Require a written visual-to-symbol correspondence and inspect the rendered board."),
]
