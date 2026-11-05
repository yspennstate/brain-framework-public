#!/usr/bin/env python3
"""fire_surface.py - classify the SURFACE a tool call acts on, from its serialized input.

Shared by precision_audit.py (measurement), build_reflex_ranking.py (nightly context_damp
computation) and instinct_prime.py (applying the damp) so all three mean the same thing by
"brain-system surface". Built 2026-07-16 from the fire-precision measurement: the market
judgment reflexes (sim_flatters, risk_vs_measured_record, ib_marks, ...) fired 2.1-2.7x OVER
exposure on brain-system surfaces - the brain's own code/memos/tests carry their trigger
vocabulary - and at-or-below exposure on the market domain they exist for (sim_flatters:
56% of act/write fires on brain-sys vs 21% exposure; 8% on market vs 8% exposure).

Surface classes:
  brain_write  Edit/Write into the brain's canonical files (memory dir, dreaming dir,
               dreams, MISTAKES/MEMORY/INSTINCTS/..., scratchpad notes)
  brain_sys    the wider brain SYSTEM: cognitive-architecture tree, brain worktrees and
               framework clones, error_graph/task_memory/graph_fire code, mesh internals
  mesh_cmd     agent_mesh / nudge / brain.py remember|recall / dream plumbing commands
  market       the market/sim domain (tickers, sims, market data, sharpe, ...)
  prose_write  .md/.txt/.tex writes outside the brain (novel, reports, papers)
  code_write   source writes outside the brain
  sim_cmd      Bash running python work scripts outside the above
  other_cmd    remaining Bash acts
  unknown      unparseable/truncated

Self-referential surface = brain_write | brain_sys | mesh_cmd.

Classification looks only at the HEAD of the serialized tool_input (file_path for writes,
command for Bash) - cheap enough for the every-tool-call hook path, and honest: the
surface is where the action lands, not every word the content carries.
"""
import re

SELF_REFERENTIAL = ("brain_write", "brain_sys", "mesh_cmd")

_BRAIN_PATH = re.compile(
    r"projects[\\/]+{{BRAIN_SLUG}}[\\/]+memory|[\\/.]claude[\\/]+dreaming|"
    r"[\\/]dreams[\\/]|MISTAKES\.md|MEMORY\.md|INSTINCTS\.md|OPEN_QUESTIONS\.md|"
    r"CONSCIENCE\.md|CURIOSITY\.md|SALIENCE\.md|AUTOBIOGRAPHY\.md|MODEL_OF_PARTNER\.md|"
    r"DREAM_|scratchpad[\\/][^\"]*\.(md|jsonl)", re.I)
_BRAIN_SYS = re.compile(
    r"12_cognitive_architecture|brain-repo|brain[-_](framework|20x|"
    r"reliability|event|canonical|benefit|science)|math[-_](research[-_])?brain|"
    r"error_graph|task_memory|graph_fire|MemoryStore|ErrorGraphEngine|dream_|"
    r"codex-worktrees[\\/][^\"]*brain|instincts?\.(tsv|py)|"
    r"heed_|reflex_|mesh[\\/]", re.I)
_MESH_CMD = re.compile(r"agent_mesh\.py|nudge\.py|brain\.py\W+(remember|recall|dream|neighbors)|"
                       r"run_dream|dream_requests|append_journal|heed_audit", re.I)
_MARKET = re.compile(r"\babc\b|market[-_ ]?data|grid[-_ ]?sim|broker|leaps?|"
                     r"prod[-_ ]?stack|sharpe|backtest|vxn|equity|option[s]?[-_ ]?(pric|data)|"
                     r"straddle|delta[-_ ]?neutral", re.I)
_PROSE_EXT = re.compile(r"\.(md|txt|tex|rst|markdown)\b", re.I)
_CODE_EXT = re.compile(r"\.(py|ps1|psm1|js|ts|json|yaml|yml|toml|sh|vbs|ipynb|c|cpp|rs)\b", re.I)
_PY_RUN = re.compile(r"python[0-9.]*\s", re.I)

HEAD = 300  # inspect only the head: file_path/command shape lives here; content does not decide surface


def classify(proxy, head):
    """Surface class for one tool call. proxy: 'write' (Edit/Write/NotebookEdit) or 'act'
    (Bash and everything else). head: the first chars of the serialized tool_input
    ({"file_path": ...} / {"command": ...}); pass at least ~120, HEAD is plenty."""
    head = head[:HEAD]
    m = re.match(r'\{"file_path":\s*"([^"]*)', head)
    if proxy == "write":
        path = m.group(1) if m else head
        if _BRAIN_PATH.search(path):
            return "brain_write"
        if _BRAIN_SYS.search(head):
            return "brain_sys"
        if _MARKET.search(head):
            return "market"
        if _PROSE_EXT.search(path):
            return "prose_write"
        if _CODE_EXT.search(path):
            return "code_write"
        return "unknown"
    c = re.match(r'\{"command":\s*"(.*)', head)
    cmd = c.group(1) if c else head
    if _MESH_CMD.search(cmd):
        return "mesh_cmd"
    if _BRAIN_PATH.search(cmd) or _BRAIN_SYS.search(cmd):
        return "brain_sys"
    if _MARKET.search(cmd):
        return "market"
    if _PY_RUN.search(cmd):
        return "sim_cmd"
    return "other_cmd"


def is_self_referential(proxy, head):
    """True when the action lands on the brain's own surface (see module docstring)."""
    try:
        return classify(proxy, head) in SELF_REFERENTIAL
    except Exception:
        return False
