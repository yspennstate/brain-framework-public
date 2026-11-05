"""Iteration 8, the SAFE way: measure the acts-while-a-channel-is-stale rate RETROSPECTIVELY
from session transcripts, with no live-hook edit (so no window-flash / session-wake risk).

The conditioning-completeness guard (iteration 7, conditioning_guard.py) fires when a high-stakes
shell act (kill/delete/restart/deploy/override) meets a stale standing channel. Iteration 8 wants
the LIVE rate of that. Rather than wire infer_session_state() into a running hook, this walks
completed transcripts: for each tool-call in time order it (a) updates the freshness of the three
standing channels when it sees a read of one, and (b) on a high-stakes act, reconstructs the
session_state at that instant and runs the guard's own check(). The output is the fraction of
acts that happened while a required channel was stale -- the number the shadow stage exists to
produce -- plus example fires for eyeballing.

Scope + honesty: this sees only SHELL-command acts (the guard classifies command text), not
tool-level acts like TaskStop or a Write-delete -- those are out of the guard's shell scope and
are undercounted here; that undercount is stated, not hidden. Channel-read detection is heuristic
(below); a read is inferred from the command text, and freshness uses the guard's own 3600s window.
Read-only: writes nothing except an optional --report.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conditioning_guard as G

CHANNELS = list(G.CHANNELS) if hasattr(G, "CHANNELS") else ["mesh_inbox_read", "compute_state_read", "claims_asserted"]
WINDOW_S = 3600  # match the live guard's max_age_s

# Heuristic detectors: does this command text constitute READING a standing channel?
_READ_PATS = {
    "mesh_inbox_read": re.compile(r"agent_mesh\.py[^\n]*\b(inbox|digest)\b|\bmesh\b[^\n]*\b(inbox|digest)\b", re.I),
    "compute_state_read": re.compile(r"compute_state\.json|compute_health\.json", re.I),
    "claims_asserted": re.compile(r"agent_mesh\.py[^\n]*\b(claim|assert)\b", re.I),
}


# Target-scope: would a tightening of required_channels by act SPECIFICITY suppress this fire?
# A fire is "correctable" only if the command visibly names a BENIGN target. git push is NOT
# command-visible (scope needs the branch/remote), so it stays in the residual by construction.
_THROWAWAY_DEST = re.compile(r"/srv/aiwork|/tmp/|/scratch|scratchpad|[\\/]temp[\\/]", re.I)
_PROTECTED_UNIT = re.compile(r"prod-bot|\bprod\b|broker|grid|approval|market-data|observers", re.I)


def _correctable_fire(cmd, act_type):
    cmd = cmd or ""
    if act_type == "deploy":
        # a copy/sync to a scratch/research dir is not shared state; git push scope is undeterminable
        if re.search(r"\b(scp|rsync|cp)\b", cmd, re.I) and _THROWAWAY_DEST.search(cmd):
            return True
        return False
    if act_type == "restart":
        # restart of a NON-production (research) unit does not need compute_state
        if re.search(r"systemctl|service", cmd, re.I) and not _PROTECTED_UNIT.search(cmd):
            return True
        return False
    return False  # kill / delete / override keep their required channels


def _is_git_push(cmd):
    return bool(re.search(r"\bgit\s+push\b", cmd or "", re.I))


def _ts(row):
    t = row.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _tool_calls(path):
    """Yield (ts, tool_name, command_text) in file order. Defensive against schema drift."""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except Exception:
        return
    for ln in lines:
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except Exception:
            continue
        ts = _ts(row)
        msg = row.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name") or ""
            inp = b.get("input") or {}
            cmd = inp.get("command") if isinstance(inp, dict) else None
            if cmd is None and isinstance(inp, dict):
                # PowerShell/other tools also use 'command'; fall back to a joined blob
                cmd = inp.get("script") or ""
            yield ts, name, (cmd or "")


def analyze(paths):
    tot_acts = fired = 0
    correctable = git_push_fired = 0  # of the fires: command-visibly-benign (suppressible) and the git-push blind spot
    by_type = {}
    stale_channel = {c: 0 for c in CHANNELS}
    examples = []
    n_files = 0
    for path in paths:
        n_files += 1
        last_read = {c: None for c in CHANNELS}  # per-session freshness, reset each transcript
        for ts, tool, cmd in _tool_calls(path):
            # (a) update channel freshness if this call reads a channel
            for c, pat in _READ_PATS.items():
                if pat.search(cmd or ""):
                    last_read[c] = ts if ts is not None else last_read[c]
            # (b) classify the act and, if it is one, check against reconstructed state
            act = G.classify_act(cmd, tool=tool)
            if not act.get("is_act"):
                continue
            tot_acts += 1
            state = {}
            for c in CHANNELS:
                lr = last_read[c]
                state[c] = (lr is not None and ts is not None and (ts - lr) <= WINDOW_S)
            res = G.check(cmd, session_state=state, tool=tool)
            at = act.get("act_type") or "?"
            by_type.setdefault(at, [0, 0])
            by_type[at][0] += 1
            if res.get("fired"):
                fired += 1
                by_type[at][1] += 1
                if _correctable_fire(cmd, at):
                    correctable += 1       # benign target named IN the command -> act-specificity tightening suppresses it
                elif _is_git_push(cmd):
                    git_push_fired += 1     # deploy whose scope is NOT command-visible -> irreducible from text alone
                for c in res.get("stale", []):
                    if c in stale_channel:
                        stale_channel[c] += 1
                if len(examples) < 12:
                    examples.append((os.path.basename(path)[:12], at, list(res.get("stale", [])),
                                     (act.get("matched") or "")[:60]))
    return {"files": n_files, "acts": tot_acts, "fired": fired,
            "correctable": correctable, "git_push_fired": git_push_fired,
            "by_type": by_type, "stale_channel": stale_channel, "examples": examples}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Retrospective acts-while-stale shadow measurement.")
    ap.add_argument("--dir", default=r"<HOME>/.claude/projects/{{BRAIN_SLUG}}",
                    help="transcript dir")
    ap.add_argument("--n", type=int, default=25, help="most-recent N transcripts to scan")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    # POSITIVE CONTROL first: the guard must fire on a known kill-with-stale-inbox before any
    # empty/low rate is believed (a silently-broken classify_act would report 0 fires and look clean).
    pc = G.check("Get-CimInstance Win32_Process | Where CommandLine -match x | Stop-Process -Force",
                 session_state={"mesh_inbox_read": False, "compute_state_read": True, "claims_asserted": True},
                 tool="Bash")
    print(f"positive control (kill, inbox stale): fired={pc.get('fired')} act_type={pc.get('act_type')} "
          f"stale={pc.get('stale')}  -> {'OK' if pc.get('fired') else 'BROKEN - do not trust the rate below'}")
    if not pc.get("fired"):
        return 1

    files = sorted(glob.glob(os.path.join(args.dir, "*.jsonl")), key=os.path.getmtime)
    files = files[-args.n:]
    r = analyze(files)
    print(f"\nscanned {r['files']} transcripts; high-stakes SHELL acts classified: {r['acts']}")
    if r["acts"]:
        print(f"acts while a required channel was STALE (guard would have fired): {r['fired']}"
              f"  = {100.0*r['fired']/r['acts']:.1f}%")
        corr = r.get("correctable", 0); gpf = r.get("git_push_fired", 0)
        corrected = r["fired"] - corr
        genuine = corrected - gpf
        print(f"  -- of those fires: {corr} command-visibly benign (scp/cp to scratch, non-production restart)"
              f" -> suppressible by act-specificity tightening")
        print(f"  -- corrected fires (surviving the cheap tightening): {corrected}"
              f"  = {100.0*corrected/r['acts']:.1f}% of acts")
        print(f"  -- of the corrected residual: git-push {gpf} (scope NOT command-visible, the blind spot),"
              f" genuine non-git {genuine} (kill/delete/override/other -- the fires that might actually matter)")
        print("by act_type (acts / fired):", {k: f"{v[0]}/{v[1]}" for k, v in sorted(r["by_type"].items())})
        print("which channel was stale at a fire:", r["stale_channel"])
        print("examples (transcript, act, stale_channels, matched):")
        for e in r["examples"]:
            print("   ", e)
    print("\nScope: SHELL-command acts only (TaskStop / Write-delete are out of the guard's shell "
          "classifier and undercounted). Channel-read detection is heuristic; freshness window "
          f"{WINDOW_S}s. This is the shadow number, not a block rate -- do not wire blocking on it.")
    if args.report:
        json.dump(r, open(args.report, "w", encoding="utf-8"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
