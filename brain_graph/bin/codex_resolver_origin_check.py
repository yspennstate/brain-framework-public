"""codex_resolver_origin_check.py - how many of his Codex prompts does the rollout-origin rule move? (R30, scheduled audit
account02/B15, B15-R30-ORIGIN-TEXTKEY-COLLISION-20260928; 28 Sep 10:0x, claude-825b1305)

Resolves every Codex row of his in the prompt ledger twice with one Resolver: with the rollout check as it was (the
prompt's opening anywhere in a rollout line) and as it is (inside a message sent to that session). Reports how many rows
each resolution method takes, and every row whose session id or method changes. A synthetic control rides along: a
rollout that carries a prompt only in developer context, and one that carries it as a typed message.

    python codex_resolver_origin_check.py OUT_JSON
"""
import collections
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_verdicts as cv  # noqa: E402


def old_in_rollout(path, text):
    key = cv._rollout_key(text)
    if not key:
        return False
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return any(key in line for line in fh)
    except OSError:
        return False


def control():
    text = "please check the synthetic rollout control prompt number seven"
    d = tempfile.mkdtemp()
    echo = os.path.join(d, "echo.jsonl")
    typed = os.path.join(d, "typed.jsonl")
    with io.open(echo, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": "[owner-prompts] 09-27 " + text}]}}) + "\n")
    with io.open(typed, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": text}]}}}) + "\n")
    quoted = os.path.join(d, "quoted.jsonl")
    image = os.path.join(d, "image.jsonl")
    with io.open(quoted, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": "[headless-worker] His ask was: " + text}]}}}) + "\n")
    with io.open(image, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": text}, {"type": "input_image", "image_url": "data:"}]}}) + "\n")
    return {"old_echo": old_in_rollout(echo, text), "old_typed": old_in_rollout(typed, text),
            "old_quoted": old_in_rollout(quoted, text), "old_image": old_in_rollout(image, text),
            "new_echo": cv._in_rollout(echo, text), "new_typed": cv._in_rollout(typed, text),
            "new_quoted": cv._in_rollout(quoted, text), "new_image": cv._in_rollout(image, text)}


def main():
    out = sys.argv[1]
    ctl = control()
    print("control:", ctl)
    rows = list(cv.his_codex_rows())
    res = cv.Resolver()
    new_fn = cv._in_rollout
    got = []
    for r in rows:
        prefix = (r.get("session") or "")[:12]
        text = r.get("text")
        cv._in_rollout = new_fn
        n = res.full(prefix, text)
        cv._in_rollout = old_in_rollout
        o = res.full(prefix, text)
        got.append((o, n, r))
    cv._in_rollout = new_fn
    old_how = collections.Counter(o[1] for o, n, r in got)
    new_how = collections.Counter(n[1] for o, n, r in got)
    moved = [{"ts": r.get("ts"), "prefix": (r.get("session") or "")[:12], "old": list(o), "new": list(n),
              "has_verdict": bool(cv.verdicts_of(r.get("text")))} for o, n, r in got if o != n]
    resj = {"control": ctl, "rows": len(got), "old_methods": dict(old_how), "new_methods": dict(new_how),
            "moved": len(moved), "moved_with_a_verdict": sum(1 for m in moved if m["has_verdict"]), "moved_rows": moved}
    json.dump(resj, io.open(out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in resj.items() if k != "moved_rows"}, indent=1))
    for m in moved[:12]:
        print("  ", m["ts"], m["prefix"], m["old"], "->", m["new"], "verdict" if m["has_verdict"] else "")


if __name__ == "__main__":
    main()
