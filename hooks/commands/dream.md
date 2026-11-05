---
description: Take a nap — run one dream cycle now (consolidate memories, update the journal) and report what the brain dreamed.
---

Run one on-demand dream cycle for the brain (a "nap"), then report.

1. Check the kill switch: if `<HOME>\.claude\dreaming\STOP` exists, say so
   and stop — the owner has dreams disabled.
2. Run `powershell -NoProfile -ExecutionPolicy Bypass -File <HOME>\.claude\dreaming\run_dream.ps1`
   in the background (it takes ~8–15 minutes: agenda → headless REM session →
   journal → repo push → Codex wake-file refresh). Do other work or wait; check the
   newest log in `<HOME>\.claude\dreaming\logs\` for completion
   ("dream cycle complete").
3. When done, read the newest journal in
   `<HOME>\.claude\projects\{{BRAIN_SLUG}}\memory\dreams\` and give the
   owner a short human summary: what was consolidated, what was linked or flagged
   stale, any new open questions or hunches, and the reflection. If the run was cut
   by the watchdog or errored, say so plainly with the log tail.

Notes: the nightly dream also runs by itself at 3:30 am (task `BrainDream`) — this
command is for an extra nap when a lot just happened. Never run two dreams at once
(the lock file handles this; respect it).
