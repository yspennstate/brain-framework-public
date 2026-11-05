"""Controls for brain_data_guard.py: what must be refused and what must pass (26 Sep 2026)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
import brain_data_guard as G  # noqa: E402

DENY = [
    r"rm -rf ~/.claude/projects/{{BRAIN_SLUG}}/abc.jsonl",
    r"rm -f {{USERPROFILE}}/.claude/brain_graph/events_touch.jsonl",
    r"cd /tmp && rm -r ~/.codex/sessions/2026/07",
    r"Remove-Item ~\.codex\sessions\2026\09 -Recurse -Force",
    r"Get-ChildItem ~\.claude\projects -Filter *.jsonl | Remove-Item",
    r"""python -c "import os; os.remove(os.path.expanduser(r'~/.claude/brain_graph/live/fires.jsonl'))" """,
    r"del ~\.claude\dreaming\instinct_fires.2026-07.tsv",
    r"git -C ~/ai-memories-and-functionality rm -r 11_raw_session_transcripts/claude_projects",
    r"find ~/.claude/projects -name '*.jsonl' -mtime +30 -delete",
    r"find {{USERPROFILE}}/.claude/dreaming/.math_sense_sessions -type f -exec rm {} +",
    r"sudo rm ~/ai-memories-and-functionality/12_cognitive_architecture/brain_graph/results/LIVE_MEMBRANE_STUDY_x.json",
    r"rm ~/bank_recall_keys_quarantine/recall_keys.jsonl",
]
ALLOW = [
    r"rm -f /tmp/x.json",
    r"ls -la ~/.claude/projects | head",
    r"cat ~/.claude/brain_graph/events_touch.jsonl | head -5",
    r"rm -rf ~/.claude/brain_graph/store.old",
    r"rm ~/.claude/brain_graph/live/sidecar.sqlite.new",
    r"rm -f ~/.claude/brain_graph/live/sessions/demo-1.json",
    r"""echo "never rm the transcripts in ~/.claude/projects" """,
    r"""grep -rn "rm -rf" ~/.claude/dreaming/brain.py""",
    r"rm -rf ~/movie_projects/x/renders",
    r"git -C ~/ai-memories-and-functionality status --short",
    r"""python -c "print(open(os.path.expanduser(r'~/.claude/brain_graph/STATUS.json')).read())" """,
    r"mv ~/.claude/brain_graph/logs/old.log ~/.claude/brain_graph/logs/archive/old.log",
    r"rm -rf ~/.claude/brain_graph/study/store_wfB_hist",
    r"""python -c "import os; d=open(os.path.expanduser(r'~/.claude/projects/x.jsonl')).read(); os.remove('/tmp/y')" """,
    r"git -C ~/ai-memories-and-functionality rm --cached 05_other/file.txt",
    r"rm -f {{USERPROFILE}}/.claude/brain_graph/repo_roots_cache.json",
]
DENY_IN_REPO = [r"git rm -r 11_raw_session_transcripts/codex_sessions", r"rm -rf 12_cognitive_architecture/brain_graph/results"]
ALLOW_IN_REPO = [r"git rm --cached 05_other/file.txt", r"rm -f 12_cognitive_architecture/brain_graph/x.tmp"]
REPO = os.path.expanduser("~/ai-memories-and-functionality")


def hook(cmd, tool="Bash"):
    p = subprocess.run([sys.executable, "-s", os.path.join(os.path.expanduser("~"), ".claude", "dreaming",
                                                           "brain_data_guard.py")],
                       input=json.dumps({"tool_name": tool, "tool_input": {"command": cmd}, "session_id": "guard-test"}).encode(),
                       capture_output=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
    out = p.stdout.decode("utf-8", "replace")
    return p.returncode, ("deny" in out and "permissionDecision" in out)


def main():
    bad = 0
    for c in DENY:
        d, _ = G.verdict(c)
        rc, denied = hook(c, "PowerShell" if "Remove-Item" in c or c.startswith("del ") else "Bash")
        ok = d and denied and rc == 0
        bad += not ok
        print("%s  must refuse: %s" % ("PASS" if ok else "FAIL", c[:90]))
    for c in ALLOW:
        d, hit = G.verdict(c)
        rc, denied = hook(c)
        ok = not d and not denied and rc == 0
        bad += not ok
        print("%s  must pass:   %s%s" % ("PASS" if ok else "FAIL", c[:90], "" if ok else "  (named %s)" % hit))
    for c in DENY_IN_REPO:
        d, _ = G.verdict(c, REPO)
        bad += not d
        print("%s  must refuse in the repo: %s" % ("PASS" if d else "FAIL", c))
    for c in ALLOW_IN_REPO:
        d, hit = G.verdict(c, REPO)
        bad += d
        print("%s  must pass in the repo:   %s%s" % ("PASS" if not d else "FAIL", c, "" if not d else "  (%s)" % hit))
    n = len(DENY) + len(ALLOW) + len(DENY_IN_REPO) + len(ALLOW_IN_REPO)
    print("%d checks, %d failed" % (n, bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
