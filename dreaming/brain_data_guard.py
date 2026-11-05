"""brain_data_guard.py - brain data is never deleted (a standing rule since 26 Sep 2026).

The rule: data about the brain's own activity and its improvement is never
deleted, because the brain is the most important research product on the machine. A PreToolUse gate
on Bash and PowerShell: a command whose command word deletes (rm, rmdir, del, erase, rd, unlink, shred, Remove-Item,
Clear-Content, find -delete or -exec rm, git rm, git clean, or python calling os.remove, os.unlink, shutil.rmtree,
Path.unlink) and that names brain data is refused, with the reason; so is mv of brain data to outside it, and
truncation (truncate, or a single > redirect onto it). Protected:

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
    r"math-store-build[\\/]+local[\\/]+population|math-store-main[\\/]+export|bank_recall_keys_quarantine|"
    r"codex_fleet[\\/]+movies[\\/]+out\b)")
# the film pictures (his 11 Oct order "no pictures should be deleted locally", 5 Oct "i want to make other videos out
# of these images"); a kit runner deleted 1,954 of them on 11 Oct, minutes after he said no, past an advisory reflex
PICTURES = re.compile(r"(?i)codex_fleet[\\/]+movies[\\/]+out\b")
PATHLIKE = re.compile(r"""[^\s'"`;|&()<>]*(?:\.claude|\.codex|brain-repo|math-store-build|"""
                      r"""math-store-main|bank_recall_keys_quarantine|codex_fleet)[^\s'"`;|&()<>]*""", re.I)
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


SANDBOX = re.compile(r"^(?:/private)?/tmp/|^/var/folders/")


def _resolve(path, cmd):
    """Expand $VAR and ${VAR} from VAR=value assignments in the same command: a test that builds a fake home
    (S=<scratchpad>; H=$S/fakehome; HOME=$H) writes '$H/.claude/dreaming/...' and that is not brain data."""
    env = dict(re.findall(r"(?:^|[\s;&(])([A-Za-z_]\w*)=([^\s;&|()<>'\"]+)", cmd))
    for _ in range(4):
        out = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: env.get(m.group(1), m.group(0)), path)
        if out == path:
            break
        path = out
    return path


def _lost(p, cmd):
    return bool(PROTECTED.search(p)) and not DERIVED.search(p) and not SANDBOX.search(_resolve(p, cmd))


# 5 Nov: PY_DELETE_ARG reads only a literal straight inside the call, so os.remove(os.path.expanduser('~/.claude/..')),
# os.remove(os.path.join(os.path.expanduser("~"), ".claude", "projects", ..)) and Path('~/..').expanduser().unlink()
# all passed (a framework test fixture found the first). Read the call's whole argument instead and join its string
# literals; a join with no home anchor (a test's own temp dir) stays allowed.
PY_DEL_CALL = re.compile(r"(?:os\.remove|os\.unlink|shutil\.rmtree|os\.rmdir|os\.removedirs|send2trash(?:\.send2trash)?)\(")
PY_DEL_METHOD = re.compile(r"\.(?:unlink|rmdir)\(")
PY_LIT = re.compile(r"""r?(['"])(.*?)(?<!\\)\1""")
PY_HOME = re.compile(r"expanduser|Path\.home\(\)|environ(?:\.get\(|\[)\s*\\?['\"](?:HOME|USERPROFILE)|getenv\(\s*\\?['\"](?:HOME|USERPROFILE)")


def _balanced(s, i):
    """Text from s[i] (just past an opening paren) to its closing paren, or to the end."""
    depth = 1
    for j in range(i, min(len(s), i + 600)):
        depth += {"(": 1, ")": -1}.get(s[j], 0)
        if depth == 0:
            return s[i:j]
    return s[i:i + 600]


def _py_delete_targets(cmd):
    spans = [_balanced(cmd, m.end()) for m in PY_DEL_CALL.finditer(cmd)]
    for m in PY_DEL_METHOD.finditer(cmd):
        head = cmd[max(0, m.start() - 400):m.start()]
        head = re.split(r";|\n", head)[-1]
        k = max(head.rfind("Path("), head.rfind("Path.home("))
        if k >= 0:
            spans.append(head[k:])
    out = []
    for sp in spans:
        lits = [x.group(2).strip("\\") for x in PY_LIT.finditer(sp)]
        lits = [x for x in lits if x and x not in ("~",)] if PY_HOME.search(sp) else [x for x in lits if x]
        if not lits:
            continue
        p = "/".join(x.strip("/\\") if i else x.rstrip("/\\") for i, x in enumerate(lits))
        if PY_HOME.search(sp) and not p.startswith(("~", "/")):
            p = "~/" + p
        if p.startswith(("~", "/")) or re.match(r"^[A-Za-z]:", p):
            out.append(p)
    return out


def verdict(cmd, cwd=""):
    """(deny, protected paths named) for one command string; cwd resolves a git path relative to the brain repo."""
    cmd = cmd or ""
    # python code is read whole (its own ';' are not shell separators): the path inside the delete call decides
    if re.search(r"(?i)\bpython[\w.]*\b|\bpy\s", cmd):
        for m in PY_DELETE_ARG.finditer(cmd):
            p = m.group(2) or m.group(4) or ""
            if _lost(p, cmd):
                return True, [p]
        hit = [p for p in _py_delete_targets(cmd) if _lost(p, cmd)]
        if hit:
            return True, hit
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
        # 31 Oct: moving a protected file out of the brain or truncating one loses it as surely as rm; a probe of the
        # live dispatcher found "mv <log> /tmp/" and ": > <log>" passed with only a warning. A move that stays inside
        # protected ground (a rename, a rotation into brain_graph) is allowed; ">>" appends and "2>" stay allowed.
        for w, s in zip(words, segs):
            toks = [t.strip("'\"") for t in s.split()]
            if w == "mv" and "mv" in [t.rsplit("/", 1)[-1] for t in toks]:
                args = toks[[t.rsplit("/", 1)[-1] for t in toks].index("mv") + 1:]
                args = [a for a in args if not a.startswith("-")]
                if len(args) >= 2 and not PROTECTED.search(args[-1]):
                    src_hit = [a for a in args[:-1] if _lost(a, cmd)]
                    if src_hit:
                        return True, src_hit
            elif w == "truncate":
                hit = [p for p in PATHLIKE.findall(s) if _lost(p, cmd)]
                if hit:
                    return True, hit
        # a '>' inside a quoted string (a grep pattern, an echo) is text; a quoted path right after '>' is still a target
        bare = re.sub(r"(?<!>)(?<!> )('[^']*'|\"[^\"]*\")", "Q", pl)
        for m in re.finditer(r"(?<![>&\d])>(?![>&])\s*([^\s;|&()<>]+)", bare):
            tgt = m.group(1).strip("'\"")
            if _lost(tgt, cmd):
                return True, [tgt]
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
    reason = ("BRAIN DATA IS NEVER DELETED (a standing rule since 26 Sep 2026: the brain is the most important research product on the "
              "machine). This command deletes, moves out or truncates %s. Keep it; if space is short, tell the owner and move it only "
              "with their word and a verified copy. Rebuildable files (.tmp .new .old .lock, the rebuilt store and "
              "sidecar) are not protected." % ", ".join(sorted(set(hit))[:3]))
    if any(PICTURES.search(h) for h in hit):
        reason = ("PICTURES ARE NEVER DELETED LOCALLY (a standing rule since 11 Oct 2026: the pictures are "
                  "source material for later videos). This command deletes, moves out or "
                  "truncates %s. That holds after an upload and after a sha256 check, and to free disk: free it "
                  "somewhere else, or tell the owner." % ", ".join(sorted(set(hit))[:3]))
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
