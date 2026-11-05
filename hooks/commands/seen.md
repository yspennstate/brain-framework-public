---
description: Mark task-checklist entries as seen by the partner - /seen all, /seen today, or /seen T-20260918-001,T-20260918-002.
---

The partner has looked at entries on the task checklist and wants them marked seen. This checkmark is
THEIRS; an agent never marks it on its own.

1. Take the argument: `all` (every done entry not yet seen), `today` (done today), or a comma-separated
   list of ids. With no argument, ask which - do not guess.
2. Run `python <HOME>\.claude\dreaming\task_checklist_hook.py seen <arg>`.
3. Report one line: which ids moved to the Seen section.

The view: `<HOME>\.claude\task_checklist\CHECKLIST.md` (or the directory TASK_CHECKLIST_HOME names).
