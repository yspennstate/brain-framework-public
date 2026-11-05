---
description: Show the partner's task checklist - what they asked every agent, what each is doing, what is done, what they have seen - and mark entries seen.
---

Show the task checklist for the partner.

1. Run `python <HOME>\.claude\dreaming\task_checklist_hook.py list --all` and read
   `<HOME>\.claude\task_checklist\CHECKLIST.md` (or the directory TASK_CHECKLIST_HOME names).
2. Answer in that order: what is OPEN (who is on it, what they are doing, since when), what is DONE and
   the partner has not yet seen (with the result line), then what they have seen. Their words per entry,
   dated.
3. If they say they have seen something: `python <HOME>\.claude\dreaming\task_checklist_hook.py seen <id|all>`.
   If they ask about one task: `python <HOME>\.claude\dreaming\task_checklist_hook.py show <id>`.

Every agent registers the partner's asks here automatically through the task checklist hook wired on
UserPromptSubmit, Stop and SessionStart (`dreaming/task_checklist_hook.py`); agents run `working` and
`done` themselves, and only the partner marks `seen`.
