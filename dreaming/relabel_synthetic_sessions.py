"""Rows in the prompt ledger that are this fleet's own machinery, archived as his words (origin "owner"):

  - a synthetic session id: the dispatcher control gate (ctlA1-/ctlA2-/ctlB-), timing probes, delivery probes replay a
    prompt through every hook; "check the machine speed" became a standing ask "x33 on 4 days" in the hourly block on
    2026-09-20. The ledger keeps the first 12 characters of a real Claude or Codex session id (8 hex, a dash, 3 hex);
    anything else is a harness.
  - a harness-injected user turn: a mesh message relayed into a session (<cross-session-message>), the plugin list
    (<recommended_plugins>), a Codex hook's own prompt (<hook_prompt>), a relayed agent message (<agent-message>),
    notifications. The live stamper (prompt_origin.py) refuses both kinds since 2026-09-20; this tool relabels the past.

Usage: python relabel_synthetic_sessions.py [--apply]     (dry run without --apply; a backup is kept beside the ledger)
"""
import io, json, os, re, shutil, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


LEDGER = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory", "prompts", "PROMPTS.jsonl")
UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{3,4}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TAG_RX = re.compile(r"^\s*<(cross-session-message|recommended_plugins|task-notification|system-reminder|local-command-stdout"
                    r"|hook_prompt|agent-message|agent-notification)\b", re.I)


def is_synthetic_session(sess):
    s = str(sess or "")
    return bool(s) and not UUID_RX.match(s)


def main():
    apply = "--apply" in sys.argv[1:]
    with io.open(LEDGER, encoding="utf-8", newline="") as f:
        lines = f.read().split("\n")
    out, flipped, seen_prefix = [], 0, {}
    for ln in lines:
        if not ln.strip():
            out.append(ln)
            continue
        try:
            r = json.loads(ln)
        except Exception:
            out.append(ln)
            continue
        text = r.get("text") or r.get("prompt") or ""
        tag = TAG_RX.match(text)
        if (is_synthetic_session(r.get("session")) or tag) and (r.get("origin") or "owner") == "owner":
            r["origin"] = "automation"
            r.setdefault("origin_note", ("harness-injected turn" if tag else "synthetic session id (harness run)")
                         + ", relabelled %s" % time.strftime("%Y-%m-%d"))
            flipped += 1
            pre = ("tag:" + tag.group(1)) if tag else re.sub(r"[0-9a-f]{4,}.*$", "", str(r.get("session")))[:14]
            seen_prefix[pre] = seen_prefix.get(pre, 0) + 1
            out.append(json.dumps(r, ensure_ascii=False))
        else:
            out.append(ln)
    print("rows flipped owner -> automation: %d" % flipped)
    for k, v in sorted(seen_prefix.items(), key=lambda kv: -kv[1]):
        print("  %-28s %d" % (k, v))
    if not apply:
        print("dry run; add --apply to write (a backup is kept)")
        return 0
    bak = LEDGER + ".bak_synthetic_" + time.strftime("%Y%m%d_%H%M")
    shutil.copy2(LEDGER, bak)
    tmp = LEDGER + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(out))
    os.replace(tmp, LEDGER)
    print("written; backup %s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
