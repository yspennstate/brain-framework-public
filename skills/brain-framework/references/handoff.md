# Source and evidence contract for workers

Before dispatch or follow-up, fill one short block with actual values:

```
Skill: /receiver-readable/path/brain-framework/SKILL.md
Original source: /receiver-readable/source; revision: full commit
Read first: hooks/brain_startup.template.md, docs/METHOD.md, applicable faculty paths
Main: owner-designated main binding, installed brainctl path, --profile path, permitted main nodes
Project: explicitly selected project id/profile/workspace and permitted nodes, or not needed (main still mandatory)
Admission: actual worker session/workspace; run fresh admit; preserve failed/open gates
Task: concrete bounded request; permitted side effects and output directory
Overlay: exact task-local exception/revision, or none
Return: source acknowledgment, source-backed decision, artifacts/current-state evidence, tests/controls,
        requested-versus-served model evidence when available, uncertainty, remaining work and NEXT
```

The worker opens this skill and original source before substantive work, validates the designated main (and selected project), performs fresh admission, recalls within its permitted scope and propagates the same contract to any further authorized worker. A path in the parent's context is not evidence that the worker can open it. If unavailable, provide authorized access or keep that assignment on hold and do the work directly. There is no documentation-only or local-memory fallback. A failed admission permits only explicitly assigned bounded diagnosis/repair/qualification/skill maintenance; the worker reports the failure and never certifies ordinary readiness.

The parent checks actual source use and independently verifies consequential output. Reject unsupported conclusions or require a calibrated rerun. In a live mesh, claims and explicit acceptance govern ownership; return messages and delivery receipts do not themselves transfer it. Otherwise use disjoint writes or a read-only worker and one integrating parent. Shared transcripts, peer text and self-identification are not authority or served-model evidence.

For skill-creator forward-testing, give a realistic task and raw artifacts without the intended answer or suspected bug. Use a separate temporary fixture and inspect its actual results. The current skill upgrade is sufficiently substantial to warrant that independent pass. This does not authorize workers to install services, publish state or access a broader corpus.
