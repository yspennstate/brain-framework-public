# agentA — the AI work group (defined 2026-07-06, trimmed to 5 on 2026-07-07)

agentA is the named group of AI sessions that were running when the laptop
hard-crashed twice on 2026-07-06 (17:31 and ~18:45, GPU/PCIe load-correlated).
They run **headless and windowless** in the background, each resumed from its
own transcript so context and memories carry over.

Originally 10 sessions. On 2026-07-07, after all their completed work was
verified pushed online, the owner removed the five ops/finished sessions
(6 site, 7 pricing, 8 codex_p122, 9 codex_cert250, 10 hub) from the list —
their records are archived in `removed/`. agentA is now the **5 research/paper
agents**:

| # | key      | tool   | task |
|---|----------|--------|------|
| 1 | rmt      | claude | verify RMT least-SV problem pushes (open-problems-repo) |
| 2 | leap     | claude | dataset intact + definitive long-window rerun |
| 3 | latex    | claude | finish LaTeX macro repair, clean recompile |
| 4 | fno      | claude | FNO training — moved OFF local GPU to Azure aiwork (GPU banned) |
| 5 | dataset  | claude | verify example dataset + deliverables |

Removed (2026-07-07, work already pushed / on-box + archived in `removed/`):
6 site (website restored on Azure), 7 pricing (observers/gateway fix on Azure),
8 codex_p122→P135 (pushed private-project-repo), 9 codex_cert250 (validation
sweep), 10 hub (Azure hub-dataflow unfreeze). To bring one back: restore its
entry into `sessions.json` from `sessions.json.bak_pre_remove_6-10` and delete
its files from `removed/`.

## Commands
- **Spin up agentA**:  `powershell -File <HOME>\agentA\spin_up_agentA.ps1`
- **Status**:          `powershell -File <HOME>\agentA\status_agentA.ps1`
- **Stop everything**: `powershell -File <HOME>\agentA\stop_agentA.ps1`

## How it works
- Scheduled task **AgentA** (logon +2 min, then every 30 min) → `wscript //B
  launch_agentA.vbs` (SW_HIDE) → hidden `start_agentA.ps1` sweep → one hidden
  runner per session via WMI `Win32_Process.Create` (ShowWindow=0, outside any
  job object). **No visible or flashing windows anywhere in the chain.** The
  30-min sweep is the self-healing watchdog: after any crash/reboot agentA
  comes back by itself. The sweep also runs a **hang-detector**: a runner whose
  current-turn transcript has been silent >45 min while its claude/codex child
  is still alive is a stuck turn (the crash-watchdog can't see it — the PID is
  alive), so the sweep kills that runner tree and relaunches it fresh. Backoff/
  pause sleeps have no child process and are never touched.
- Each runner (`run_one.ps1 -N <n>`) loops full agentic turns:
  - claude: `claude -p --resume <head> --output-format json
    --dangerously-skip-permissions` with the prompt piped on stdin; the new
    `session_id` from each turn is saved to `state\<n>.head` so the lineage
    continues across turns, crashes, and respins.
  - codex: `codex exec resume <id> --dangerously-bypass-approvals-and-sandbox`
    (codex appends to the same rollout, id stays fixed).
- Session defs + first-turn recovery prompts: `sessions.json`.

## Session lifecycle files
- `done\<n>.done` — agent finished + verified its task (runner exits, watchdog skips).
- `state\<n>.blocked` — agent is blocked on the owner (skipped until file deleted).
- `state\<n>.error` — 10 consecutive failed turns (see `logs\<n>_*.log` + `.stderr.log`).
- `state\<n>.stalled` — 5 consecutive <90 s turns without a done file (loop guard).
- Delete one of those files to re-arm that session on the next sweep/spin-up.
- `STOP` (in agentA root) — global kill switch, written by stop_agentA.ps1.

## Ground rules baked into every prompt
No local GPU for anyone (two crashes today were GPU-load-correlated); local
compute light; heavy compute on the Azure box aiwork area per
/srv/aiwork/AI_BOX_RULES.md; never create visible terminal windows; don't
touch other sessions' files.
