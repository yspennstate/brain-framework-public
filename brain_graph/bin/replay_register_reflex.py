"""Replay the reflex recall_before_you_register over the real tool inputs of recent Claude sessions, as the hook sees
them (json.dumps(tool_input, ensure_ascii=False), matched with re.I), and print every fire with its context so each can
be judged by hand. Read-only.
    python replay_register_reflex.py SINCE_EPOCH
"""
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
import instinct_prime as IP  # noqa: E402

since = float(sys.argv[1])
only = [it for it in IP._load() if it["id"] == "recall_before_you_register"]
assert len(only) == 1
rx = re.compile(only[0]["trigger"], re.I)
n_inputs, fires, files = 0, [], 0
for path in glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", "*.jsonl")):
    if os.path.getmtime(path) < since:
        continue
    files += 1
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"tool_use"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            for b in (o.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("name") not in ("Bash", "PowerShell", "Write", "Edit"):
                    continue
                t = json.dumps(b.get("input") or {}, ensure_ascii=False)
                n_inputs += 1
                m = rx.search(t)
                if m:
                    whole = bool(IP._fire(t, only))
                    s = max(0, m.start() - 70)
                    fires.append((o.get("timestamp", "")[:16], os.path.basename(path)[:8], b.get("name"), whole,
                                  t[s:m.end() + 90].replace("\\n", " / ")))
print("files %d, tool inputs %d, fires %d" % (files, n_inputs, len(fires)))
for f in sorted(fires):
    print("%s %s %-5s hook=%s | %s" % f)
