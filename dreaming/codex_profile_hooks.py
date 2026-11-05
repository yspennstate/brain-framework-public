"""codex_profile_hooks.py - every Codex agent runs with the full hook set; only a picture call goes without.

The rule (15 Oct 2026, when the Codex film workers were found running hookless on the account profiles): an
agent that does real work needs the hooks; only a bare picture call may run without them.

What was wrong: the account profiles (~/.codex-img-acc2..acc5) were made bare on 24 Sep for picture drawing (86k
tokens a picture against 272k-531k through ~/.codex), and since then the film series workers, the remakes, the solvers
and the test-bank workers were all launched on them with CODEX_HOME pointing there. Each got one hook (and that one
untrusted) against 44 in ~/.codex: no owner prompts, no checklist, no reflexes, no film hooks, no compaction hook,
no inbox, no Stop summary.

The fix (15 Oct 00:0x): each account profile carries ~/.codex/hooks.json byte for byte, plus the trust entries he
already gave those same hooks in ~/.codex/config.toml, re-keyed to the profile's own path (copied, never computed).
A picture call opts out with `--disable hooks` (codex_lean.OFF and codex_img.draw).

This hook keeps it so:
  UserPromptSubmit (and --sync by hand): when ~/.codex/hooks.json or its trust entries change, copy them to every
      account profile again; prints one line only when it re-synced or found a profile it could not fix.
  PreToolUse (Bash, PowerShell, Write, Edit): refuses a codex launch that turns hooks off (--disable hooks,
      features.hooks=false) unless the command is a picture job (codex_img / codex_lean / lean_worker), and refuses a
      write that strips a profile's hooks.json or sets features.hooks=false in a Codex config.
  --check: prints the state of every profile and exits 1 on drift.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time

H = os.path.expanduser("~")
MAIN = os.path.join(H, ".codex")
PROFILES = ["%s/.codex-img-acc%d" % (H, n) for n in (2, 3, 4, 5)]
LOG = os.path.join(H, ".claude", "brain_graph", "logs", "codex_profile_hooks.log")

LAUNCH = re.compile(r"(?i)\bcodex(?:\.exe|\.cmd)?\b[^\n|;&]*\bexec\b|\bcodex(?:\.exe|\.cmd)?[\"']?\s*,\s*[\"']exec[\"']")
HOOKS_OFF = re.compile(r"(?i)--disable[\"']?\s*,?\s*[\"']?hooks\b|features\.hooks\s*=\s*false")
PICTURE = re.compile(r"(?i)codex_img|codex_lean|lean_worker|picture_fleet|draw_batch|imagegen|image_gen")
CFG_OFF = re.compile(r"(?im)^\s*hooks\s*=\s*false|features\.hooks\s*=\s*false")


def _md5(p):
    try:
        return hashlib.md5(open(p, "rb").read()).hexdigest()
    except OSError:
        return None


def _state_blocks(text):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("["):
            if cur is not None:
                out.append(cur)
                cur = None
            if line.startswith("[hooks.state."):
                cur = [line]
            continue
        if cur is not None:
            cur.append(line)
    if cur is not None:
        out.append(cur)
    return ["\n".join(b).rstrip() for b in out]


def _wanted_blocks(prof):
    main_text = io.open(os.path.join(MAIN, "config.toml"), encoding="utf-8").read()
    src = os.path.join(MAIN, "hooks.json").replace("/", os.sep)
    dst = os.path.join(prof, "hooks.json").replace("/", os.sep)
    return [b.replace(src, dst) for b in _state_blocks(main_text)]


# 17 Oct: the profiles' AGENTS.md was left bare too (3.7 KB of picture faults), so the 15-17 Oct Codex math workers on
# acc3/acc4 never saw the math-recall guidance written into ~/AGENTS.md on 15 Oct (0 of 123 rollouts carried it).
# Each profile now carries that one section, copied from ~/AGENTS.md between these markers, plus a pointer to the
# machine rules. About 2.4 KB, so a picture call pays a few hundred tokens for it.
RB_BEGIN = "<!-- MATH-BRAIN-RECALL BEGIN (copied from C:\\Users\\me\\AGENTS.md by codex_profile_hooks.py; edit that file, not this copy) -->"
RB_END = "<!-- MATH-BRAIN-RECALL END -->"
RB_HEAD = "## RECALLING FROM THE MATH BRAIN"


def _recall_block():
    try:
        t = io.open(os.path.join(H, "AGENTS.md"), encoding="utf-8").read().replace("\r\n", "\n")
    except OSError:
        return None
    i = t.find("\n" + RB_HEAD)
    if i < 0:
        return None
    j = t.find("\n## ", i + 1)
    sec = t[i + 1:j if j > 0 else len(t)].rstrip()
    return "\n".join([RB_BEGIN, "This account profile loads only this file. The machine-wide rules for every agent are in "
                      "C:\\Users\\me\\AGENTS.md; read it before shared work or math work.", "", sec, RB_END])


def _split_recall(text):
    a = text.find(RB_BEGIN)
    b = text.find(RB_END, a + 1) if a >= 0 else -1
    if a < 0 or b < 0:
        return text, None
    return text[:a].rstrip() + text[b + len(RB_END):], text[a:b + len(RB_END)]


def agents_state(prof):
    want = _recall_block()
    try:
        have = _split_recall(io.open(os.path.join(prof, "AGENTS.md"), encoding="utf-8").read().replace("\r\n", "\n"))[1]
    except OSError:
        have = None
    return want is None or have == want


def agents_install(prof):
    want = _recall_block()
    if want is None:
        return
    p = os.path.join(prof, "AGENTS.md")
    raw = io.open(p, encoding="utf-8", newline="").read() if os.path.exists(p) else ""
    if raw:
        shutil.copy2(p, p + ".bak_recallsync_" + time.strftime("%Y%m%d_%H%M%S"))
    nl = "\r\n" if "\r\n" in raw else "\n"
    rest = _split_recall(raw.replace("\r\n", "\n"))[0].rstrip()
    t = (rest + "\n\n" if rest else "") + want + "\n"
    tmp = p + ".tmp_recallsync"
    io.open(tmp, "w", encoding="utf-8", newline="").write(t.replace("\n", nl))
    os.replace(tmp, p)


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def state(prof):
    """(hooks_same, trust_same, n_trust) for one profile."""
    hj = os.path.join(prof, "hooks.json")
    same = _md5(hj) == _md5(os.path.join(MAIN, "hooks.json"))
    try:
        have = _state_blocks(io.open(os.path.join(prof, "config.toml"), encoding="utf-8").read())
    except OSError:
        have = []
    want = _wanted_blocks(prof)
    return same, have == want, len(have), len(want)


def install(prof):
    hj, cfg = os.path.join(prof, "hooks.json"), os.path.join(prof, "config.toml")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for f in (hj, cfg):
        if os.path.exists(f):
            shutil.copy2(f, f + ".bak_hooksync_" + stamp)
    shutil.copy2(os.path.join(MAIN, "hooks.json"), hj)
    t = io.open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else ""
    keep, skip = [], False
    for line in t.splitlines():
        if line.startswith("["):
            skip = line.startswith("[hooks.state")
        if not skip:
            keep.append(line)
    t = "\n".join(keep).rstrip() + "\n\n[hooks.state]\n\n" + "\n\n".join(_wanted_blocks(prof)) + "\n"
    tmp = cfg + ".tmp_hooksync"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(t)
    os.replace(tmp, cfg)


def sync(quiet=True):
    msgs, bad = [], 0
    for prof in PROFILES:
        if not os.path.isdir(prof):
            continue
        try:
            if not agents_state(prof):
                agents_install(prof)
                msgs.append("%s AGENTS.md recall section %s" % (os.path.basename(prof),
                                                                 "synced" if agents_state(prof) else "could NOT be synced"))
                bad += 0 if agents_state(prof) else 1
            same, trust, n, w = state(prof)
            if same and trust:
                continue
            install(prof)
            same2, trust2, n2, w2 = state(prof)
            name = os.path.basename(prof)
            if same2 and trust2:
                msgs.append("%s re-synced (%d trusted hooks)" % (name, n2))
            else:
                bad += 1
                msgs.append("%s could NOT be synced (hooks %s, trust %d/%d)" % (name, same2, n2, w2))
        except Exception as e:
            bad += 1
            msgs.append("%s sync error %r" % (os.path.basename(prof), e))
    if msgs:
        _log("; ".join(msgs))
        print("[codex-hooks] Codex account profiles: " + "; ".join(msgs) + (" - FIX BY HAND" if bad else "")
              + " (his 15 Oct order: Codex film agents run with every hook; only picture calls go without)")
    elif not quiet:
        print("[codex-hooks] all %d account profiles carry the main hook set and its trust" % len(PROFILES))
    return bad


def check():
    rc = 0
    for prof in PROFILES:
        if not os.path.isdir(prof):
            print(os.path.basename(prof), "missing")
            continue
        same, trust, n, w = state(prof)
        ag = agents_state(prof)
        ok = same and trust and ag
        rc |= 0 if ok else 1
        print("%-16s hooks.json %s  trust %d/%d  AGENTS.md recall %s  %s" % (
            os.path.basename(prof), "same" if same else "DIFFERENT", n, w, "same" if ag else "MISSING", "ok" if ok else "DRIFT"))
    return rc


def pretool(data):
    name = data.get("tool_name")
    ti = data.get("tool_input") or {}
    reason = None
    if name in ("Bash", "PowerShell"):
        cmd = str(ti.get("command") or "")
        if LAUNCH.search(cmd) and HOOKS_OFF.search(cmd) and not PICTURE.search(cmd):
            reason = "launches a Codex agent with its hooks turned off"
    elif name in ("Write", "Edit", "MultiEdit"):
        path = str(ti.get("file_path") or "").replace("\\", "/").lower()
        if name == "Write":
            text = str(ti.get("content") or "")
        elif name == "Edit":
            text = str(ti.get("new_string") or "")
        else:
            text = "\n".join(str(e.get("new_string") or "") for e in (ti.get("edits") or []))
        if re.search(r"/\.codex[^/]*/config\.toml$", path) and CFG_OFF.search(text):
            reason = "sets features.hooks=false in a Codex profile"
        elif re.search(r"/\.codex-img-acc\d/hooks\.json$", path) and name == "Write":
            try:
                n = sum(len(g.get("hooks", [])) for v in json.loads(text).get("hooks", {}).values() for g in v)
                main = json.load(open(os.path.join(MAIN, "hooks.json"), encoding="utf-8"))
                m = sum(len(g.get("hooks", [])) for v in main.get("hooks", {}).values() for g in v)
                if n < m:
                    reason = "writes a Codex account profile's hooks.json with %d hooks where ~/.codex has %d" % (n, m)
            except Exception:
                pass
    if not reason:
        return
    _log("refused: %s" % reason)
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "CODEX AGENTS RUN WITH EVERY HOOK (a standing rule: an agent doing real work needs "
        "the hooks). This %s. Only a picture call (codex_img / "
        "codex_lean) may run with --disable hooks. Launch the agent on its account profile as it is; every profile "
        "carries ~/.codex's hook set and trust (codex_profile_hooks.py --check)." % reason}}))


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    if "--sync" in sys.argv:
        sys.exit(1 if sync(quiet=False) else 0)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    ev = data.get("hook_event_name") or ""
    if ev == "PreToolUse" or data.get("tool_name"):
        return pretool(data)
    sync(quiet=True)


if __name__ == "__main__":
    main()
