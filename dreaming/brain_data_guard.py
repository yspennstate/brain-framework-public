"""brain_data_guard.py - brain data is never deleted (a standing rule since 26 Sep 2026).

The rule: data about the brain's own activity and its improvement is never
deleted, because the brain is the most important research product on the machine. A PreToolUse gate
on Bash and PowerShell: a command whose command word deletes (rm, rmdir, del, erase, rd, unlink, shred, Remove-Item,
Clear-Content, find -delete or -exec rm, git rm, git clean, or python calling os.remove, os.unlink, shutil.rmtree,
Path.unlink) and that names brain data is refused, with the reason. Protected:

  ~/.claude/projects (session transcripts and the memories)       ~/.codex/sessions (Codex rollouts)
  ~/.claude/brain_graph (events, live logs, learned edges, study)  ~/.claude/dreaming logs (reflex fires, served notes,
  outcomes, memory access, retrievals, judgments, math serves and fetches) and .math_sense_sessions
  brain-repo: 11_raw_session_transcripts, 01_ai_consciousness, and the brain_graph,
  brain_improvement, audits and dreaming trees of 12_cognitive_architecture
  math-store-build/local/population, math-store-main/export, bank_recall_keys_quarantine
Things rebuilt from those sources stay deletable: *.tmp, *.new, *.old, *.lock, __pycache__, *.pyc, the rebuilt store
and sidecar, the study's cut stores, the hook's per-session state. A word inside an echo or a grep is not a command word
and does not trip it. Every refusal is logged to ~/.claude/brain_graph/logs/guard.log. There is no override: only his
words in a live session change the law.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import json
import os
import re
import sys
import time

LOG = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "logs", "guard.log")
PROTECTED = re.compile(
    r"(?i)(?:\.claude[\\/]+projects\b|\.codex[\\/]+sessions\b|\.claude[\\/]+brain_graph\b|"
    r"\.claude[\\/]+dreaming[\\/]+(?:instinct_fires|instinct_suppressed|surfaced|outcomes|memory_access|retrievals|"
    r"judged_nightly|shadow_fires|consciousness|salience|eligibility_credit|reality_log|mathbrain_fetches|"
    r"mathbrain_silence|coactivation|spread_edges|keyword_graph|keyword_nodes|\.math_sense_sessions)|"
    r"brain-repo[\\/]+(?:11_raw_session_transcripts|01_ai_consciousness|"
    r"12_cognitive_architecture[\\/]+(?:brain_graph|brain_improvement|audits|dreaming))|"
    r"math-store-build[\\/]+local[\\/]+population|math-store-main[\\/]+export|bank_recall_keys_quarantine)")
PATHLIKE = re.compile(r"""[^\s'"`;|&()<>]*(?:\.claude|\.codex|brain-repo|math-store-build|"""
                      r"""math-store-main|bank_recall_keys_quarantine)[^\s'"`;|&()<>]*""", re.I)
DERIVED = re.compile(r"(?i)(?:\.tmp\b|\.new\b|\.old\b|\.lock\b|\.pyc\b|__pycache__|[\\/]store\.(?:new|old)\b|"
                     r"sidecar\.sqlite\.new|[\\/]study[\\/]+(?:store_wf|sidecar_wf|cut)|[\\/]live[\\/]+sessions\b|"
                     r"\.tmp\.\d+|(?:^|[\\/_.-])cache(?:[\\/_.-]|$))")
DELETE_WORDS = {"rm", "rmdir", "del", "erase", "rd", "unlink", "shred", "remove-item", "ri", "clear-content",
                "remove-itemproperty"}
PY_DELETE = re.compile(r"os\.remove|os\.unlink|shutil\.rmtree|\.unlink\(|os\.rmdir|send2trash|os\.removedirs")
LEAD_SKIP = {"sudo", "command", "builtin", "exec", "nohup", "time", "xargs", "&", "call", "cmd", "/c", "powershell",
             "powershell.exe", "pwsh", "-command", "-c", "-noprofile", "timeout"}


def pipelines(cmd):
    """Split a command into pipelines at ; && || and newlines (quotes are not parsed: an approximation that errs
    toward seeing more pipelines, never fewer)."""
    return [p for p in re.split(r"\|\||&&|;|\n", cmd) if p.strip()]


def first_word(seg):
    toks = seg.strip().split()
    i = 0
    while i < len(toks) and (toks[i].lower() in LEAD_SKIP or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i])
                             or toks[i].isdigit()):
        i += 1
    if i >= len(toks):
        return ""
    w = toks[i].strip("'\"`(").lower().replace("\\", "/").rsplit("/", 1)[-1]
    return w[:-4] if w.endswith(".exe") else w


PY_DELETE_ARG = re.compile(r"""(?:os\.remove|os\.unlink|shutil\.rmtree|os\.rmdir|os\.removedirs|send2trash(?:\.send2trash)?)"""
                           r"""\(\s*r?(['"])(.+?)\1|Path\(\s*r?(['"])(.+?)\3\s*\)\.unlink""")
REPO_REL = re.compile(r"(?i)(?:^|[\s'\"])(?:\./)?((?:11_raw_session_transcripts|01_ai_consciousness|"
                      r"12_cognitive_architecture[\\/]+(?:brain_graph|brain_improvement|audits|dreaming))"
                      r"[^\s'\"`;|&()<>]*)")


def verdict(cmd, cwd=""):
    """(deny, protected paths named) for one command string; cwd resolves a git path relative to the brain repo."""
    cmd = cmd or ""
    # python code is read whole (its own ';' are not shell separators): the path inside the delete call decides
    if re.search(r"(?i)\bpython[\w.]*\b|\bpy\s", cmd):
        for m in PY_DELETE_ARG.finditer(cmd):
            p = m.group(2) or m.group(4) or ""
            if PROTECTED.search(p) and not DERIVED.search(p):
                return True, [p]
    in_repo = "brain-repo" in (cwd or "").replace("\\", "/").lower()
    for pl in pipelines(cmd):
        segs = pl.split("|")
        words = [first_word(s) for s in segs]
        deletes = any(w in DELETE_WORDS for w in words)
        if not deletes:
            for w, s in zip(words, segs):
                if w == "find" and (re.search(r"\s-delete\b", s) or re.search(r"-exec(?:dir)?\s+(?:rm|shred|unlink)\b", s)):
                    deletes = True
                elif w == "git":
                    g = re.search(r"\bgit\b(?:\s+-C\s+(\S+))?(?:\s+-c\s+\S+)*\s+(rm|clean)\b(.*)", s)
                    if g:
                        repo = (g.group(1) or "").replace("\\", "/").lower()
                        if "brain-repo" in repo or (not g.group(1) and (in_repo or
                                                                                          "brain-repo" in cmd.lower())):
                            rel = [m.group(1) for m in REPO_REL.finditer(g.group(3)) if not DERIVED.search(m.group(1))]
                            if g.group(2) == "clean" or rel:
                                return True, ["brain-repo/" + (rel[0] if rel else "(git clean)")]
                        deletes = True
        if not deletes:
            continue
        named = [m.group(0) for m in PATHLIKE.finditer(pl)]
        hit = [p for p in named if PROTECTED.search(p) and not DERIVED.search(p)]
        if in_repo:
            hit += ["brain-repo/" + m.group(1) for m in REPO_REL.finditer(pl)
                    if not DERIVED.search(m.group(1))]
        if hit:
            return True, hit
    return False, []


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return
    cmd = str((data.get("tool_input") or {}).get("command") or "")
    deny, hit = verdict(cmd, str(data.get("cwd") or ""))
    if not deny:
        return
    reason = ("BRAIN DATA IS NEVER DELETED (his law, 26 Sep 2026: 'the most important research product on this whole "
              "computer is the brain'). This command deletes %s. Keep it; if space is short, tell him and move it only "
              "with his word and a verified copy. Rebuildable files (.tmp .new .old .lock, the rebuilt store and "
              "sidecar) are not protected." % ", ".join(sorted(set(hit))[:3]))
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s refused (%s): %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), str(data.get("session_id"))[:8],
                                                cmd[:300].replace("\n", " ")))
    except Exception:
        pass
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                        "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
