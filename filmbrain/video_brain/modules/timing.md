# Record first, then synchronize the picture

Bind all artifacts to content and configuration, and measure final playback rather than estimating from source length.

## Bind audio to text and complete synthesis configuration

Compute a recording key from the exact spoken text, provider, voice, language, rate, pronunciation-map revision and engine settings. A changed voice must invalidate the cache even when the words have not changed.

Text-only cache keys permit stale voice selection. A WAV filename says nothing about which configuration produced its contents.

**Check:** The selected audio entry matches the current recording key and its file hash; mismatches fail closed.

**Evidence:** CH16792, CH16800, memory:parallel_workers_each_write_their_own_manifest_2026_09_04.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Resolve all recording manifests by content

Read every expected worker shard. Merge by current recording identity; reject conflicting entries for one current key. Never silently choose by filename order or assume the single old durations.json remains authoritative.

Seven narrators wrote seven manifests while a reader loaded only one, projecting five hours as 24 minutes. A later filename-order merge selected stale durations.

**Check:** Expected beat IDs and manifest shards are complete. Sum actual durations independently and compare the projected total with the requested length.

**Evidence:** memory:parallel_workers_each_write_their_own_manifest_2026_09_04.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Cut the picture to the measured recording

Finish a beat's current narration before giving its visuals a final duration. Lay out animation holds and transitions against measured audio. If the script changes, invalidate dependent timing, scene render, chapter and final cut.

Replacing a WAV with a different duration after rendering makes later pictures drift. Stretching or truncating speech to fit an old scene hides the bug.

**Check:** The shot duration covers the current audio and intended transition; inspect synchronization at every beat boundary and at the end of each chapter.

**Evidence:** memory:lecture_pipeline_record_first_cut_the_picture_2026_09_03.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Derive all chapter labels from final playback order

Maintain one ordered chapter list. Derive spoken chapter numbers, title cards, filenames used for assembly and cross-references from it. Validate the final concatenation order explicitly.

Registration keys are not playback numbers. Earlier films announced chapter numbers that differed from the order in which viewers heard them.

**Check:** Opening and closing labels, chapter metadata and spoken cross-references agree with the actual export.

**Evidence:** memory:lecture_pipeline_record_first_cut_the_picture_2026_09_03.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Use one writer per beat and atomic outputs

Claim each beat before recording; refresh completed identities before work begins. Write media and manifests through temporary files and atomic replacement. Cap concurrency at the producer entry point across all passes.

Per-chapter claims prevent two workers sharing a chapter; they do not cap nine simultaneously launched render passes. A parent-child Python chain is not a duplicate job.

**Check:** There is one live producer per beat, a bounded global worker count, and no partially written media accepted as complete.

**Evidence:** memory:parallel_workers_each_write_their_own_manifest_2026_09_04, scar:MISTAKES_2026-09-03_python_parent_child.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.
