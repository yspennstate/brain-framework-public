# Using the delivery gate

Copy a template from `templates/` into the video project's owned directory.
It is deliberately incomplete and fails the checker. Fill it from the producer's
actual files and measurements; never copy the synthetic passing fixture from
the tests into a production report.

```powershell
python -B <HOME>/filmbrain/video_brain/video_brain.py check delivery.json
```

The optional `--artifact-root C:/path/to/owned/output` checks local file bytes.
It can read a large final movie, so use current compute policy. Without that
option the result is explicitly limited to manifest/receipt consistency.

| Field | Producer and meaning |
| --- | --- |
| `schema` | Exactly `video_brain.delivery.v1`. |
| `project`, `kind` | Separate project identity; `lecture`, `documentary`, `fiction`, or `explainer`. |
| `policy` | `visual_passes`, `audio_passes`, `facts_passes`, `av_passes`, each at least 2. Retain stronger project-specific counts. |
| `expected_shot_ids` | The ordered producer inventory, established from the storyboard before export. |
| `shots` | Actual disjoint timeline slots with ID, start/end seconds, visual plan, source evidence, visual/audio artifact IDs, and narration. Account for transitions within an owning slot. |
| `assets` | ID, original `source_asset_id`, actual SHA-256 and provenance. Derivative crops retain the original source identity. |
| `artifacts` | ID, actual SHA-256, and portable relative path. Include native visual renders, each recording, and the final export. |
| `recordings` | Unique row ID, shot ID, complete recording key, audio artifact ID and measured duration. Combine all expected shards. |
| `reviews` | Actual observation events, described below. Empty means unreviewed. |
| `final` | Current final artifact ID, measured duration and independent media-probe evidence including audio/video stream presence. |

Each shot's `visual` has `kind`, `asset_ids`, and `explanation`: what the picture
helps the viewer understand. For explanatory lecture shots the supported kinds
are geometry, plot, diagram, map, worked_example and data_image. A nonempty
description is structural evidence only; the reviewer must judge relevance.

The `narration` has exact `text` and a `configuration` object with provider,
voice, language, rate, pronunciation_revision and engine_settings_revision.
Use `recording_key(text, configuration)` from `video_brain.py` to create its
identity. A rate other than the working `+10%` also requires
`rate_listening_evidence`. This does not declare +10% universally optimal.
Audition a new Microsoft voice and verify its catalogue identity before changing
the approved set; do not silently add another engine.

Review events require a unique `id`, `scope` (shot ID or `final`), `stage`, `pass`
(numbered from 1), `reviewer`, `observed_at`, `evidence`, `result`, `artifact_id`
and `artifact_sha256`. Use these methods only for acts actually performed:

- `visual` / `view_native_render`: inspect the native rendered slide or scene.
- `audio` / `listen_actual_audio`: listen to the current recording.
- `facts` / `independent_source_check`: verify the claim by source or an
  independent derivation; bind the receipt to the displayed explanation.
- `av` / `watch_actual_export`: watch the actual final export. Supply
  `intervals`, e.g. `[[0, 420], [420, 840]]`. The union must cover the whole movie
  for each pass; overlapping watches cannot conceal an unwatched end.

Use different evidence for distinct passes: observations and corrections, not
the phrase "all good" generated from the program's return code. Each corrected
artifact invalidates its own reviews and the reviews of dependent final cuts.

`BLOCKED` means missing/inconsistent evidence or malformed input; it is not an
aesthetic judgment. `RECEIPTS_COMPLETE` means the records meet the gate. It is
not a certificate that someone actually watched, understood, liked or fact-
checked the video. Retain reviewer accountability and owner feedback separately.

The generic two-pass minimum does not replace the ReLU request for over ten
watches and over one hundred audits. Put that project's stronger whole-video
pass count in `av_passes`; track its additional audit obligations in the project
ledger. Do not relabel code runs, frames or checksum checks as whole-film watches.

Before delivery, also check pronunciation, important spoken constants, real
LaTeX glyphs, near-duplicate images, asset/source accuracy, encoding defects,
the exact file copied to Downloads, and compliance with the requested subject
and length. Several of these require judgment that this checker cannot supply.
