# Use natural Microsoft narration with brisk delivery

The latest owner rule is Microsoft natural voices only for videos, with slightly faster speech and fewer artificial pauses.

## Microsoft natural voices are the video rule

Use a Microsoft natural/neural voice. The current working baseline is en-US-AndrewMultilingualNeural at +10%. If that service is unavailable, preserve the work and retry or pause narration; do not silently substitute Kokoro, a clone or another engine.

The latest direct instruction says only Microsoft natural voices. It supersedes the older fallback permission for videos, while unrelated audiobook projects retain their own explicit contracts.

**Check:** The recording metadata names Microsoft, the exact approved voice and rate. A new render contains no old engine recordings.

**Evidence:** CH16792, CX3572.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Audition alternatives and keep changes intentional

Use the same short representative text to compare Microsoft voices, including a natural paragraph, proper names and one spoken formula. Andrew, Brian, Ryan and Guy were the recent sample set. Rotate at an intentional project or chapter boundary after listening.

The owner liked the voice and asked to hear alternatives. A different voice every few seconds is not implied by that request; naturalness is a listening judgment, not a vendor ranking.

**Check:** A sample manifest records voice, exact text, rate, file hash and listening notes. Voice changes invalidate old recordings and mix reviews.

**Evidence:** CH16800, CH16801, CH16794.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Speak a little faster without rushing the idea

Start from the working +10% setting. Remove unnecessary paragraph gaps and shorten introductory setup. Keep pauses that support an actual new diagram or difficult inference, and let the picture hold when needed.

A speech-rate setting is relative to that voice; it is not a guaranteed words-per-minute value. Measure representative recordings and judge comprehensibility in the combined cut.

**Check:** Record actual spoken duration and pause distribution, then listen at 1x playback. Do not use the player speed control to conceal a slow final export.

**Evidence:** CH16671, CH16774, CH16784, CX3572.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Write sentences that sound like a person explaining

Use direct concrete phrasing, varied sentence lengths and clear referents. Say the mathematical or historical point. Remove repeated promises, inflated importance, unnecessary recaps and remarks about what the agent is doing.

The owner objected to both synthetic delivery and AI-sounding wording. Changing the voice cannot repair a script full of self-commentary.

**Check:** Read the script aloud, then listen to the synthesis. Flag awkward emphasis, repetitive cadence and phrases whose object is unclear.

**Evidence:** CH16690, CH16776, CH16777, CH16793.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Make formulas and names unambiguous aloud

Maintain a pronunciation and spoken-math map. Name the numerator and denominator clearly, distinguish logarithm-of-half from half-of-logarithm, expand abbreviations appropriately and audition proper names in a sentence.

A formula can be correct on the board and wrong in the listener's interpretation. Do not rely on punctuation to create an audible mathematical distinction.

**Check:** An audio-only review reconstructs each load-bearing operation, boundary case and number; fixes change the recording identity and timing.

**Evidence:** CH16717, memory:the_board_can_be_right_while_the_voice_is_wrong_2026_09_04.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Keep service capability separate from wrapper capability

The existing narrate_edge.py reaches Microsoft through edge-tts. Use its tested voice/rate path. Check the current official Microsoft voice and SSML documentation before relying on advanced styles, roles or pronunciation support in a particular endpoint.

Azure Speech capabilities are not automatically available in the unofficial Edge wrapper. The owner accepted sending narration text to Microsoft, not arbitrary private archives or credentials.

**Check:** Record the actual endpoint/tool version and supported settings; submit only the intended narration text and leave secrets out of scripts and logs.

**Evidence:** CH16792, official:microsoft_voice.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.
