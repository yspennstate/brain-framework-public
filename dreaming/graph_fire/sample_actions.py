#!/usr/bin/env python3
"""sample_actions.py - build a REAL 'typical action' population from the transcripts, so precision (the
nagging rate) is measured against ordinary tool calls, not the biased instinct_fires read-snippets.

The honest question the owner cares about: of every ordinary tool call, what fraction does the gut fire
on? Most tool calls are benign, so fire-rate here ~ the false-fire / nagging burden (an upper bound - a
few typical actions really are risky). Samples Bash/PowerShell/Edit/Write tool_use from the most recent
transcripts (excluding the session that BUILT this, which is scar-dense and unrepresentative), serialises
each exactly as instinct_prime does (json.dumps(tool_input)), tags read-only, dedups. Stdlib only.
"""
import os, io, json, glob, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = "~/.claude/projects/{{BRAIN_SLUG}}"
OUT = os.path.join(HERE, "typical_actions.jsonl")
EXCLUDE_SESSION = "89f84043"   # this build session - not a typical workload
N_FILES = 30           # overridable: --files=100 --max=2500 --out=typical_actions_big.jsonl
MAX_ACTIONS = 700

_READONLY = re.compile(r"^\s*(grep|rg|cat|less|head|tail|ls|find|wc|git (log|status|diff|show|blame)|"
                       r"python[0-9.]* (-c )?[\w./\\]*(recall|status|audit|eval|check|_test|probe)|echo|"
                       r"type|Get-Content|Select-String|Get-ChildItem|Get-Process|nvidia-smi|Get-Item)\b", re.I)


def is_read(tool, cmd):
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return False
    return bool(_READONLY.match(cmd or ""))


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    n_files, max_actions, out = N_FILES, MAX_ACTIONS, OUT
    for a in sys.argv[1:]:
        if a.startswith("--files="):
            n_files = int(a.split("=", 1)[1])
        elif a.startswith("--max="):
            max_actions = int(a.split("=", 1)[1])
        elif a.startswith("--out="):
            out = os.path.join(HERE, a.split("=", 1)[1]) if "/" not in a and "\\" not in a else a.split("=", 1)[1]
    fs = sorted(glob.glob(PROJ + "/*.jsonl"), key=lambda f: os.path.getmtime(f), reverse=True)
    fs = [f for f in fs if EXCLUDE_SESSION not in os.path.basename(f)][:n_files]
    seen = set()
    rows = []
    for f in fs:
        try:
            for l in io.open(f, encoding="utf-8"):
                try:
                    o = json.loads(l)
                except Exception:
                    continue
                m = o.get("message", {})
                if not (isinstance(m, dict) and m.get("role") == "assistant"):
                    continue
                for c in (m.get("content") or []):
                    if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                        continue
                    tool = c.get("name", "")
                    if tool not in ("Bash", "PowerShell", "Edit", "Write", "MultiEdit"):
                        continue
                    ti = c.get("input", {}) or {}
                    try:
                        text = json.dumps(ti, ensure_ascii=False)
                    except Exception:
                        continue
                    cmd = ti.get("command", "") or ""
                    key = text[:200]
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({"text": text, "tool": tool, "is_read": is_read(tool, cmd)})
        except Exception:
            continue
        if len(rows) >= max_actions * 3:
            break
    # spread the sample across files (already dedup'd); cap
    rows = rows[:max_actions]
    with io.open(out, "w", encoding="utf-8") as w:
        for r in rows:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")
    nread = sum(1 for r in rows if r["is_read"])
    print("wrote %d typical actions to %s  (read-only=%d, act/write=%d, from %d transcripts)"
          % (len(rows), os.path.basename(out), nread, len(rows) - nread, len(fs)))


if __name__ == "__main__":
    main()
