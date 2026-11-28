"""Receipt gate: a file is published only on a review receipt written by someone other than the publisher.

When several agents run a media pipeline, the agent that made a file is the worst one to judge it: it read its own
plan and sees what it meant, not what is on the frames. This PreToolUse hook (Claude Code, matcher "Bash") holds a
publishing command until a receipt exists that

  1. names the file by its CURRENT sha256 (a file re-rendered after review needs a new receipt),
  2. carries "verdict": "pass", and
  3. was written by a reviewer whose id differs from the session running the command.

A receipt is any *.json file in the receipt folder:

  {"sha256": "<hex of the file>", "verdict": "pass", "reviewer": "<session or agent id>",
   "method": "<what the reviewer read>", "written": "<ISO time>"}

Configuration (environment, or a JSON file named by RECEIPT_GATE_CONFIG with the same keys in lower case):
  RECEIPT_GATE_PATTERN   regex that recognises a publishing command; its named group "path" is the file.
                         Default: (?:^|\\s)\\S*publish\\S*\\.py\\s+(?P<path>\\S+)
  RECEIPT_GATE_DIR       folder of receipts. Default ~/.claude/receipts
  RECEIPT_GATE_AGENT     id of this publisher; default the hook's session_id. The reviewer field of a receipt must
                         use the same namespace (both session ids, or both agent names): a receipt signed with an
                         agent name can never match a session id, so a self-review would pass unseen.

Commands that do not match are passed through silently. A matched command is refused when the file cannot be read
or hashed (fail closed: a publish is hard to take back). Malformed hook input passes through (fail open: the gate
must never stop ordinary work).

A hook that times out is an allow. On a loaded machine a child-process hook can take longer to start than the host
waits, and the publish then goes through with no message. Give the hook a generous timeout, and keep the same check
inside the publishing script itself (call judge() before the upload); the hook is the early warning, the script is
the lock.

  python receipt_gate.py < hook.json        as a hook
  python receipt_gate.py --selftest         seven controls, both ways
"""
import glob, hashlib, io, json, os, re, sys, tempfile

DEFAULT_PATTERN = r"(?:^|\s)\S*publish\S*\.py\s+(?P<path>\S+)"


def _cfg():
    c = {}
    p = os.environ.get("RECEIPT_GATE_CONFIG")
    if p:
        try:
            c = {k.lower(): v for k, v in json.load(io.open(p, encoding="utf-8")).items()}
        except Exception:
            c = {}
    get = lambda k, d: os.environ.get(k) or c.get(k.lower()) or d
    return (get("RECEIPT_GATE_PATTERN", DEFAULT_PATTERN),
            os.path.expanduser(get("RECEIPT_GATE_DIR", "~/.claude/receipts")),
            get("RECEIPT_GATE_AGENT", ""))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def judge(command, publisher, pattern, receipt_dir, cwd=None):
    """Return None to allow, or the reason to refuse. Pure apart from reading files."""
    m = re.search(pattern, command or "")
    if not m:
        return None
    if not publisher:
        return "the publisher has no id (set RECEIPT_GATE_AGENT); without one, self-review cannot be told apart"
    path = m.group("path").strip("'\"")
    if cwd and not os.path.isabs(path):
        path = os.path.join(cwd, path)
    try:
        digest = sha256_of(path)
    except Exception as e:
        return "cannot hash %s (%s); a publish waits for a readable file" % (path, e.__class__.__name__)
    same_author = stale = 0
    for rp in glob.glob(os.path.join(receipt_dir, "*.json")):
        try:
            r = json.load(io.open(rp, encoding="utf-8"))
        except Exception:
            continue
        if str(r.get("verdict", "")).lower() != "pass":
            continue
        if r.get("sha256") != digest:
            stale += 1
            continue
        if not r.get("reviewer") or r.get("reviewer") == publisher:
            same_author += 1
            continue
        return None
    if same_author:
        return "the only receipt for this file was written by the publisher itself; ask another agent to review it"
    return ("no passing receipt for sha256 %s... (%d receipts are for other contents); "
            "a file changed after review needs a new receipt" % (digest[:12], stale))


def main():
    try:
        ev = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    if ev.get("tool_name") != "Bash":
        return 0
    pattern, receipt_dir, agent = _cfg()
    why = judge((ev.get("tool_input") or {}).get("command", ""), agent or ev.get("session_id", ""),
                pattern, receipt_dir, ev.get("cwd"))
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "[receipt-gate] " + why}}))
    return 0


def selftest():
    d = tempfile.mkdtemp()
    rec = os.path.join(d, "receipts"); os.makedirs(rec)
    f = os.path.join(d, "clip.mp4")
    io.open(f, "wb").write(b"frames v1")
    h = sha256_of(f)
    cmd = "python publish_video.py %s --public" % f
    put = lambda name, **kw: json.dump(kw, io.open(os.path.join(rec, name), "w", encoding="utf-8"))
    ok = []
    ok.append(("no receipt refused", judge(cmd, "A", DEFAULT_PATTERN, rec) is not None))
    put("self.json", sha256=h, verdict="pass", reviewer="A")
    ok.append(("publisher's own receipt refused", "publisher itself" in (judge(cmd, "A", DEFAULT_PATTERN, rec) or "")))
    put("other.json", sha256=h, verdict="pass", reviewer="B")
    ok.append(("another reviewer's receipt allowed", judge(cmd, "A", DEFAULT_PATTERN, rec) is None))
    io.open(f, "wb").write(b"frames v2, re-rendered after review")
    ok.append(("changed file refused", judge(cmd, "A", DEFAULT_PATTERN, rec) is not None))
    ok.append(("unrelated command silent", judge("ls -la && python render.py clip", "A", DEFAULT_PATTERN, rec) is None))
    ok.append(("empty publisher id refused", "no id" in (judge(cmd, "", DEFAULT_PATTERN, rec) or "")))
    put("fail.json", sha256=sha256_of(f), verdict="fail", reviewer="B")
    ok.append(("failing verdict refused", judge(cmd, "A", DEFAULT_PATTERN, rec) is not None))
    for name, good in ok:
        print("%s  %s" % ("PASS" if good else "FAIL", name))
    return 0 if all(g for _, g in ok) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
