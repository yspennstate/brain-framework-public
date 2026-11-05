# -*- coding: utf-8 -*-
"""
Narration and synthetic voice.

His standing complaint, 2026-09-04: the narration is "still not totally human
sounding ... even having something sounding a little robotic is not good", and his
hypothesis is that a deeper voice is easier to make human.

So this file has to do two jobs. It has to hold what is actually known about
speech and reading aloud, and it has to name - precisely enough to measure -
every separate thing that makes synthetic speech sound synthetic. A single
"naturalness" score is useless; you cannot fix a score. You fix a cause.
"""

DOMAIN = ("craft.narration", "narration", "One voice carrying an argument for an hour without tiring anyone.")

NODES = [
    # ---------------- writing for the ear ----------------
    ("craft.narration", "narration",
     "A voice explaining something to someone who cannot ask a question. Everything follows from "
     "that: no sentence may need a second reading, because there is no second reading.",
     [("part_of", "craft.sound")], ["narration", "voice"]),

    ("craft.write_for_the_ear", "write for the ear, not the page",
     "Spoken prose is shorter, more concrete and more repetitive than written prose, and it puts the "
     "subject early. Subordinate clauses that a reader untangles at leisure are lost in speech. Read "
     "every line aloud before it is recorded; the ones you stumble on are the ones the listener will.",
     [("part_of", "craft.narration"), ("prevents", "failure.unspeakable_sentence")],
     ["narration", "writing"]),

    ("craft.one_idea_per_sentence", "one idea per sentence",
     "The listener has no scrollback. Two ideas in one sentence means the second one lands on a mind "
     "still holding the first, and the usual result is that neither is kept.",
     [("part_of", "craft.write_for_the_ear")], ["narration", "writing"]),

    ("craft.signposting", "signposting",
     "Tell the listener where they are: 'that is the definition; now the two ways it fails'. In a "
     "long lecture the signposts carry more of the comprehension than the explanations do, because "
     "they let a wandering mind rejoin.",
     [("part_of", "craft.narration"), ("used_in", "craft.lecture")], ["narration", "structure"]),

    ("craft.narration_pacing", "pacing a read",
     "Conversational English is 140 to 160 words a minute; audiobooks sit 150 to 160; explainer "
     "video sits 160 to 180. Above about 185 comprehension of NEW material falls off sharply even "
     "though the words stay perfectly intelligible - the listener has no spare capacity to think.\n"
     "THE DIAL DOES NOT SET THE RATE, the text does as much. kokoro bm_lewis at speed 0.95 measured "
     "174 words a minute on the calculus lecture, 167 on a different lecture paragraph and 179 on a "
     "documentary one - the same setting, a 12 wpm spread. So set the dial per piece against a "
     "MEASURED words-per-minute rather than fixing it once and assuming.",
     [("part_of", "craft.narration"), ("measured_by", "metric.words_per_minute"),
      ("requires", "craft.tts_speed_dial")], ["narration", "rhythm", "measured"]),

    ("craft.pause_grammar", "pauses are grammar",
     "In speech the pause does the work of punctuation, and it is not uniform: roughly 150 to 250 ms "
     "at a comma, 400 to 700 ms at a full stop, 800 to 1500 ms at a paragraph or a change of subject. "
     "A synthesiser that inserts the same gap everywhere sounds like a machine reading a list, which "
     "is precisely what it is doing.",
     [("part_of", "craft.narration"), ("causes", "craft.tts_naturalness"),
      ("measured_by", "metric.pause_distribution")], ["narration", "tts", "rhythm"]),

    ("craft.emphasis", "emphasis",
     "Which word carries the sentence. English marks it with pitch, length and loudness together. "
     "Get it wrong and the sentence is still intelligible but subtly false, and a listener corrects "
     "it internally on every line - which is exhausting and is a large part of why synthetic reads "
     "tire people.",
     [("part_of", "craft.narration"), ("causes", "failure.wrong_emphasis")], ["narration", "prosody"]),

    ("craft.phrasing", "phrasing",
     "Where a reader breathes and groups words. Human readers chunk by meaning; synthesisers chunk by "
     "punctuation and by whatever the text-splitter did. A long sentence broken at the wrong place is "
     "the single most common tell in long-form TTS.",
     [("part_of", "craft.narration"), ("causes", "failure.wrong_phrasing")], ["narration", "prosody"]),

    ("craft.declination", "declination",
     "Across a spoken sentence, pitch drifts downward and resets at the next sentence. A voice with "
     "no declination sounds like it is reading a list; a voice that resets too hard sounds like a "
     "newsreader. Measurable: fit a line to F0 over the utterance and look at the slope.",
     [("part_of", "craft.narration"), ("measured_by", "metric.f0_slope")], ["narration", "prosody"]),

    ("craft.final_lengthening", "phrase-final lengthening",
     "Humans stretch the last syllable before a boundary. Its absence is one of the quieter reasons "
     "a synthetic read feels clipped and hurried even at a correct words-per-minute.",
     [("part_of", "craft.narration"), ("measured_by", "metric.final_syllable_ratio")],
     ["narration", "prosody"]),

    ("craft.breath", "breath",
     "Real readers breathe, and the breath is audible. Most TTS removes it entirely, which is one of "
     "the strongest cues that nobody is there. Audiobook engineers reduce breaths; they do not delete "
     "them. Re-inserting quiet breaths at paragraph boundaries is one of the cheapest humanising "
     "moves available to us.",
     [("part_of", "craft.narration"), ("prevents", "failure.no_breath"),
      ("applies_to", "craft.tts_postprocess")], ["narration", "tts"]),

    ("craft.disfluency", "the tiny imperfections",
     "A small catch before a hard word, a slightly uneven pace, an intake of breath, a swallow. In "
     "moderation these are not defects - they are the evidence of a body. A read with none of them "
     "is uncanny in the exact way a face with no pores is.",
     [("part_of", "craft.narration"), ("applies_to", "craft.tts_naturalness")], ["narration", "tts"]),

    ("craft.pronunciation", "pronunciation and proper nouns",
     "Invented names, technical terms and abbreviations are where a synthetic read breaks trust. Fix "
     "them per-name with a phoneme override before rendering hours of audio, not after. Our own "
     "Atlas render has this exact debt outstanding.",
     [("part_of", "craft.narration"), ("prevents", "failure.mispronunciation")], ["narration", "tts"]),

    ("craft.narration_register", "register",
     "Who is speaking and to whom. A lecture is a person who knows something talking to a person who "
     "wants to. That is warmer and slower than an advertisement and more direct than an audiobook. "
     "Register is set in the writing, not by the voice.",
     [("part_of", "craft.narration")], ["narration", "writing"]),

    # ---------------- the voice itself ----------------
    ("craft.voice_pitch", "pitch of a voice",
     "Measured as fundamental frequency. Adult male speech typically sits 85 to 155 Hz, female 165 "
     "to 255. Our measured male Kokoro voices run from onyx at 83 to 86 Hz up to eric at 153. His "
     "chosen bm_lewis measures 94 Hz median with the lowest floor of the set at 70 Hz - it drops "
     "deepest on stressed syllables while sitting a little higher overall. That combination, not the "
     "lowest median, is what he picked by ear.",
     [("part_of", "craft.voice"), ("measured_by", "metric.f0_median"),
      ("measured_by", "metric.f0_floor")], ["voice", "tts"]),

    ("craft.deep_voice_hypothesis", "does a deeper voice sound more human?",
     "His hypothesis, 2026-09-04: 'the deeper the voice I think the easier it would be for us to make "
     "it sound human.' There are real reasons it might be true. Low F0 means the harmonics are "
     "closely spaced, so the vocal tract resonances are sampled more finely and the timbre carries "
     "more information; low voices also carry more of their energy in the first two harmonics, where "
     "codec and vocoder artefacts are least audible. And phase and jitter errors in a vocoder are "
     "proportionally smaller against a long pitch period. But there is a competing effect: most TTS "
     "models are trained on more mid-range speech, so an extremely low voice can sit at the edge of "
     "the training distribution and get buzzy. The test that settles it is a listening comparison at "
     "matched everything else, plus the artefact metrics - not an opinion.",
     [("part_of", "craft.voice"), ("measured_by", "metric.f0_median"),
      ("measured_by", "metric.hnr"), ("applies_to", "craft.tts_naturalness")],
     ["voice", "tts", "owner-hypothesis"]),

    ("craft.pitch_shifting", "shifting a voice down after the fact",
     "You can lower a synthesised voice, and there are two ways. Naive resampling lowers pitch AND "
     "formants together, which makes the speaker sound physically larger - past about three semitones "
     "it becomes a cartoon giant. A phase vocoder or PSOLA shift with formant preservation lowers "
     "only the pitch and keeps the speaker's identity, which is what we want; the cost is smearing on "
     "transients and a slightly hollow quality. Two semitones with formants preserved is usually "
     "free; four is audible.",
     [("part_of", "craft.tts_postprocess"), ("applies_to", "craft.deep_voice_hypothesis"),
      ("causes", "failure.chipmunk_formants"), ("implemented_by", "tool.ffmpeg")],
     ["voice", "tts", "postprocess"]),

    ("craft.formant", "formants",
     "The resonances of a vocal tract - they say how big the speaker's head and throat are, and they "
     "are what makes a vowel an 'ah' rather than an 'ee'. Pitch and formants are independent in a "
     "real voice and coupled in a naive resample. Every honest deepening keeps the formants where "
     "they were.",
     [("part_of", "craft.voice"), ("measured_by", "metric.formant_f1_f2")], ["voice", "acoustics"]),

    ("craft.vocal_fry", "vocal fry / creak",
     "The rattle at the very bottom of a voice, where the folds flap irregularly. Real deep readers "
     "drop into it at the ends of phrases, and its presence is a strong human cue. Almost no "
     "synthesiser produces it, and its absence is part of why a very low synthetic voice can sound "
     "artificially smooth.",
     [("part_of", "craft.voice"), ("applies_to", "craft.deep_voice_hypothesis")], ["voice", "tts"]),

    ("craft.timbre", "timbre",
     "What a voice sounds like once pitch and loudness are accounted for. Carried by the spectral "
     "envelope: how energy is distributed over frequency, and how fast the high end falls off. A dark "
     "timbre with fast high-frequency roll-off reads as warm and close; a bright one reads as "
     "present and, past a point, as thin.",
     [("part_of", "craft.voice"), ("measured_by", "metric.spectral_tilt")], ["voice", "acoustics"]),

    ("craft.voice_quality", "voice quality: breathy, creaky, modal",
     "Modal is the ordinary voiced setting. Breathy has audible airflow and reads as intimate. Creaky "
     "reads as authority or exhaustion depending on where it falls. A synthesiser that only ever "
     "produces modal phonation is producing one of the three things a real reader does.",
     [("part_of", "craft.voice"), ("measured_by", "metric.hnr")], ["voice", "acoustics"]),

    ("craft.voice", "the voice", "The instrument itself, separate from what it is asked to say.",
     [("part_of", "craft.narration")], ["voice"]),

    # ---------------- what makes TTS sound like TTS ----------------
    ("craft.tts_naturalness", "why synthetic speech sounds synthetic",
     "It is never one thing, and treating it as one thing is why 'try another voice' keeps failing. "
     "The separable causes, roughly in order of how much they cost us: prosody that is right on "
     "average and wrong on this sentence; pauses of uniform length; no breath; no variation in tempo "
     "across a paragraph; a spectrum that is too clean, with no room and no noise floor; vocoder "
     "artefacts on sibilants and plosives; wrong emphasis; wrong phrasing at splitter boundaries; "
     "and identical delivery of a repeated word. Each of these is separately measurable and "
     "separately fixable, and some of the biggest wins are post-processing rather than model choice.",
     [("part_of", "craft.tts"), ("causes", "failure.robotic")], ["tts", "voice", "diagnosis"]),

    ("craft.tts_prosody_flat", "flat prosody",
     "Pitch that moves within a small band and returns to the same place every sentence. Measure it: "
     "the standard deviation of F0 over voiced frames, in semitones, compared against a human read of "
     "the same text. Human narration typically runs 2.5 to 4 semitones of variation; a flat "
     "synthetic read runs under 2.",
     [("is_a", "craft.tts_naturalness"), ("measured_by", "metric.f0_sd_semitones")], ["tts", "prosody"]),

    ("craft.tts_uniform_timing", "uniform timing",
     "Every syllable the same length, every pause the same gap, no accelerando into a subordinate "
     "clause and no slowing into a conclusion. Measurable as the variance of inter-word intervals and "
     "of the pause-length distribution. This is the cue people describe as 'robotic' more often than "
     "pitch is.",
     [("is_a", "craft.tts_naturalness"), ("measured_by", "metric.pause_distribution")],
     ["tts", "prosody"]),

    ("craft.tts_too_clean", "too clean a signal",
     "No room, no breath, no noise floor. A tiny room reverb and a noise floor around -60 dBFS are "
     "two of the cheapest humanising steps and they are pure post-processing.\n"
     "One caveat, measured rather than assumed: DIGITAL SILENCE in the gaps is a weaker tell than "
     "it looks. 76 per cent of 79 public-domain human narrations contain frames below -90 dB and "
     "22 per cent contain more than a sixth of a second of it, because noise gates and lossy "
     "encoders put it there - one human reader has 9.14 seconds. Our kokoro output sat at the "
     "extreme of that distribution, 5.06 seconds in a 41.7 second take against a human median of "
     "0.02, which is worth fixing but is not on its own the reason a read sounds synthetic.",
     [("is_a", "craft.tts_naturalness"), ("fixes", "failure.robotic"),
      ("applies_to", "craft.tts_postprocess")], ["tts", "postprocess", "measured"]),

    ("craft.tts_vocoder_artifact", "vocoder artefacts",
     "Buzz on sustained vowels, smeared sibilants, a metallic ring on plosives. These are the "
     "artefacts that survive every prosody fix, and they are what limits the ceiling of a given "
     "model. Measure with harmonic-to-noise ratio and by looking at the 4 to 8 kHz band on a "
     "spectrogram of an 's'.",
     [("is_a", "craft.tts_naturalness"), ("measured_by", "metric.hnr")], ["tts", "artifacts"]),

    ("craft.tts_chunk_boundary", "the splitter is the enemy",
     "Long-form TTS synthesises in chunks and joins them. Every join risks a pitch discontinuity, a "
     "level jump and a wrong pause. Split on sentence boundaries with a comma fallback, never on a "
     "character count, and crossfade the joins by a few milliseconds. Our audiobook chain already "
     "does the first; the second is worth adding.",
     [("is_a", "craft.tts_naturalness"), ("causes", "failure.chunk_seam")], ["tts", "pipeline"]),

    ("craft.tts_postprocess", "post-processing a synthetic read",
     "The chain that has the best return per hour: trim per-utterance silence, then rebuild pauses to "
     "a graded scheme, then insert breaths at paragraph boundaries, then a very small room, then "
     "gentle compression, then a de-esser only if needed, then loudness normalisation last. Note the "
     "order - normalising before adding room changes the room's level.",
     [("part_of", "craft.tts"), ("fixes", "failure.robotic"),
      ("implemented_by", "tool.ffmpeg")], ["tts", "postprocess"]),

    ("craft.tts_reference_clone", "zero-shot voice cloning",
     "Modern models take a few seconds of reference audio and speak in that voice. This is the "
     "strongest lever we have on naturalness, because the model then borrows a real human's prosody "
     "and timbre rather than an average of thousands. The legal question is the whole question: the "
     "reference must be something we may use. Public-domain recordings are the clean answer.",
     [("part_of", "craft.tts"), ("requires", "asset.reference_voice"),
      ("applies_to", "craft.deep_voice_hypothesis")], ["tts", "voice", "cloning"]),

    ("craft.tts_speed_dial", "the speed dial, measured",
     "Measured on kokoro bm_lewis across 0.80 to 1.10, the dial does more than change tempo and it "
     "moves in the direction you would not guess. Slowing it IMPROVES pause structure: at 0.80 the "
     "pause length variation is 0.64 with three distinct clusters, against 0.54 and two clusters at "
     "0.95 and a single cluster by 1.05. Voice quality is flat across the whole range - harmonic to "
     "noise 6.7 to 7.4, jitter 3.11 to 3.37 - so speed costs nothing there. The words-per-minute "
     "depends on the TEXT as much as the dial: 0.95 gave 179 on a documentary paragraph and 167 on "
     "a lecture one.\n"
     "So for dense material 0.85 is the better default than 0.95, on the numbers. What the numbers "
     "cannot settle is whether a slower read SOUNDS deliberate or drugged - that is a listening "
     "question, and this node previously asserted 'drugged' without evidence, which was wrong to "
     "state as fact.",
     [("part_of", "craft.tts"), ("applies_to", "craft.narration_pacing"),
      ("measured_by", "metric.pause_distribution")], ["tts", "prosody", "measured"]),

    ("craft.tts", "text to speech", "Synthesising a read from text.", [("part_of", "craft.narration")],
     ["tts"]),

    ("craft.blind_ab", "blind A/B is the only honest test",
     "Whoever made a take believes it is better. Present takes unlabelled, in random order, with the "
     "same text and matched loudness, and only then ask which is more human. Announce the label after "
     "the choice. Our own voice audition did exactly this and it is why bm_lewis is trusted.",
     [("part_of", "craft.tts"), ("prevents", "failure.self_favouring_judgement")],
     ["tts", "method"]),

    ("craft.lecture", "the lecture form",
     "One voice, a board, an argument that builds. What makes a great one is not production: it is "
     "that every new thing is introduced by a question the listener already has. Our limits lecture "
     "follows that shape - the question a limit answers, then what it is, then how it fails.",
     [("part_of", "craft.narration"), ("used_in", "craft.explainer")], ["narration", "structure"]),

    ("craft.explainer", "explainer", "Showing how something works, with a voice and a picture that agree.",
     [("part_of", "craft.narration")], ["narration", "structure"]),
]

SUPPORT = [
    ("metric.words_per_minute", "metric", "words per minute",
     "Words spoken divided by minutes of speech, excluding leading and trailing silence."),
    ("metric.f0_median", "metric", "median F0",
     "Median fundamental frequency over voiced frames. The single best summary of how deep a voice is."),
    ("metric.f0_floor", "metric", "F0 floor",
     "The tenth percentile of F0 over voiced frames - how deep the voice goes on stressed syllables, "
     "which is what people hear as depth even when the median is higher."),
    ("metric.f0_sd_semitones", "metric", "pitch variation in semitones",
     "Standard deviation of F0 in semitones over an utterance. Human narration 2.5 to 4; flat "
     "synthetic reads under 2."),
    ("metric.f0_slope", "metric", "declination slope",
     "Semitones of pitch drop per second fitted across an utterance."),
    ("metric.pause_distribution", "metric", "pause length distribution",
     "The histogram of silences between words. A human read is multi-modal - short at commas, long "
     "at paragraphs; a robotic read is single-peaked."),
    ("metric.final_syllable_ratio", "metric", "phrase-final lengthening ratio",
     "Duration of the last voiced segment before a boundary over the mean segment duration."),
    ("metric.hnr", "metric", "harmonic to noise ratio",
     "How much of the signal is periodic. Low HNR on vowels means breathiness or vocoder noise."),
    ("metric.spectral_tilt", "metric", "spectral tilt",
     "Slope of the long-term average spectrum in dB per octave. Darker voices fall off faster."),
    ("metric.formant_f1_f2", "metric", "first and second formant",
     "The two resonances that identify a vowel and carry speaker size."),
    ("metric.jitter", "metric", "jitter",
     "Cycle-to-cycle variation in pitch period. Real voices have a little; synthetic ones often have "
     "almost none, which reads as unnaturally steady."),
    ("metric.shimmer", "metric", "shimmer", "Cycle-to-cycle variation in amplitude."),
    ("asset.reference_voice", "asset", "reference voice clip",
     "A few seconds of clean speech used to condition a cloning model. Must be legally usable; "
     "public-domain recordings are the safe source."),
    ("failure.robotic", "failure", "it sounds robotic",
     "Not one defect. Decompose it before trying to fix it: flat pitch, uniform pauses, no breath, "
     "too clean a signal, vocoder buzz, wrong emphasis, wrong phrasing, identical repeats."),
    ("failure.wrong_emphasis", "failure", "emphasis on the wrong word",
     "Intelligible but subtly false; the listener corrects it internally on every line and tires."),
    ("failure.wrong_phrasing", "failure", "a phrase broken in the wrong place",
     "Usually at a text-splitter boundary rather than a meaning boundary."),
    ("failure.no_breath", "failure", "no breaths at all",
     "Nobody is in the room. Reduce breaths, never delete them."),
    ("failure.chunk_seam", "failure", "an audible seam between synthesis chunks",
     "Pitch or level jumps at a join. Crossfade a few milliseconds and match levels per chunk."),
    ("failure.mispronunciation", "failure", "a name said wrong, consistently",
     "Worse than an occasional slip because it is systematic. Fix with phoneme overrides before "
     "rendering hours."),
    ("failure.chipmunk_formants", "failure", "formants moved with the pitch",
     "A naive resample makes the speaker sound like a different-sized person. Preserve formants."),
    ("failure.unspeakable_sentence", "failure", "a sentence no-one can say",
     "Too many clauses, or a garden path. Caught by reading aloud, never by reading."),
    ("failure.self_favouring_judgement", "failure", "judging your own take",
     "The maker hears what they intended. Blind, randomised, level-matched, or it is not a test."),
]
