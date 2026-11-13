"""codex_orders_hook.py - his standing orders for Codex sessions.

Codex sessions run no membrane (~/.codex/hooks.json carries the reflexes, his prompts, the checklist and the math notes),
so the part of the brain that holds his orders never reached them. This is the standing-orders channel of
live_membrane.py for Codex: on his prompt it keeps his words, on the tool calls it keeps the words of what the session
touches (the command and paths in the tool input, ten minutes back), and every fourth call or two minutes it runs the
word index over his words plus those, over his stored orders only (owner_* memories and memories marked owner-stated).
A line is served only when the same order leads two evaluations running, clears the bar, and has not been served to
this session in two hours: policy.json serve_orders_codex, orders_min, orders_stable. Every evaluation is one line of
live/codex_orders.jsonl, served or not. Never raises; prints nothing unless it serves.

Output (Codex takes JSON only): {"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import json
import os
import sys
import time

BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
LIVE = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "live")
STATE_DIR = os.path.join(LIVE, "codex_orders")
LOG = os.path.join(LIVE, "codex_orders.jsonl")
ERRLOG = os.path.join(LIVE, "codex_orders_errors.jsonl")   # every swallowed failure, so a broken hook is not read as silence
WINDOW = 600.0
EVERY_CALLS, EVERY_S, REFRACTORY = 4, 120.0, 7200.0


def _strings(x, out, depth=0):
    if depth > 4:
        return
    if isinstance(x, str):
        out.append(x[:4000])
    elif isinstance(x, dict):
        for v in x.values():
            _strings(v, out, depth + 1)
    elif isinstance(x, (list, tuple)):
        for v in x[:50]:
            _strings(v, out, depth + 1)


def _state_path(sid):
    return os.path.join(STATE_DIR, "%s.json" % "".join(ch for ch in sid if ch.isalnum() or ch in "-_")[:80])


IA_VERSION = 2          # the receiver rule below; a stored answer from another version is read again once
SCAN_BYTES = 4 << 20    # rollout bytes one evaluation reads before it stops with "scan_limit" (it always ends a line)
# Codex's own rules (openai/codex e07e58c8, rollout/src/list.rs read_session_meta_line and rollout/src/lib.rs
# INTERACTIVE_SESSION_SOURCES): before its session_meta a rollout may carry any of these kinds, and a response or an
# inter-agent message before it means the rollout has no usable header; a blank or unreadable line is skipped.
STOP_KINDS = ("response_item", "inter_agent_communication")
INTERACTIVE_SOURCES = ("cli", "vscode")
INTERACTIVE_CUSTOM = ("atlas", "chatgpt")
FINAL = ("session_meta", "response_before_meta")


def _interactive_meta(meta):
    """Codex's interactive sources: "cli", "vscode", and the custom sources atlas and chatgpt. A header with no
    source reads as vscode there (the field's default). `codex exec` writes "exec", a spawned thread a dict under
    "subagent"; an originator of codex_exec is never counted as his."""
    src = meta.get("source", "vscode")
    if isinstance(src, str):
        ok = src in INTERACTIVE_SOURCES
    else:
        ok = isinstance(src, dict) and list(src) == ["custom"] and src.get("custom") in INTERACTIVE_CUSTOM
    return ok and str(meta.get("originator") or "") != "codex_exec"


def _classify(payload, scan=None):
    """(value, reason, scan). True only for a session he types into, False only for another kind, both read from the
    rollout's session_meta; None while that is not known, with the reason: no transcript path, the file missing or
    unreadable, "no_session_meta" (none in what is written so far), "scan_limit" (SCAN_BYTES read this time, the rest
    next time) or "response_before_meta" (a response came first, which Codex itself refuses as a rollout header).
    `scan` is the previous call's record for the file (path, offset and line count reached, size and mtime, status):
    a file that has only grown is read on from where the last call stopped, and one unchanged since a call that found
    nothing is not read at all."""
    path = str(payload.get("transcript_path") or "")
    if not path:
        return None, "no_transcript_path", None
    try:
        st = os.stat(path)
    except OSError:
        return None, ("transcript_unreadable" if os.path.exists(path) else "transcript_missing"), None
    prev = scan if isinstance(scan, dict) and scan.get("path") == path else {}
    same = prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns
    if same and prev.get("status") == "no_session_meta":
        return None, "no_session_meta", dict(prev, read=0)
    grown = prev.get("status") in ("no_session_meta", "scan_limit") and 0 <= prev.get("offset", -1) <= st.st_size \
        and prev.get("size", st.st_size + 1) <= st.st_size
    off, lines = (prev["offset"], prev.get("lines", 0)) if grown else (0, 0)
    rec = {"path": path, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    read, value, status = 0, None, "no_session_meta"
    try:
        with open(path, "rb") as fh:
            fh.seek(off)
            while True:
                if read >= SCAN_BYTES:
                    status = "scan_limit"
                    break
                raw = fh.readline()
                if not raw:
                    break
                read += len(raw)
                whole = raw.endswith(b"\n")
                try:
                    row = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
                except ValueError:
                    row = None
                kind = row.get("type") if isinstance(row, dict) else None
                if kind == "session_meta" and isinstance(row.get("payload"), dict):
                    value, status = _interactive_meta(row["payload"]), "session_meta"
                    rec.update(meta_line=lines + 1, meta_at=off)
                    break
                if kind in STOP_KINDS:
                    status = "response_before_meta"
                    rec.update(stop_line=lines + 1, stop_at=off, stop_kind=kind)
                    break
                if not whole:
                    break                  # a line still being written: read it again, whole, next time
                off, lines = off + len(raw), lines + 1
    except OSError:
        return None, "transcript_unreadable", None
    rec.update(offset=off, lines=lines, status=status, read=read)
    return value, status, rec


def _interactive(payload):
    """True only when the rollout's session_meta says he types into the session."""
    return _classify(payload)[0] is True


def _log_error(kind, exc, payload):
    """One line per failure (the hook still exits 0 so Codex is never broken). Past 2 MB the file is set aside whole
    under a dated name and a fresh one begins, so no failure is dropped.
    Nothing here is ever deleted."""
    try:
        if os.path.exists(ERRLOG) and os.path.getsize(ERRLOG) > 2 * 1024 * 1024:
            base, ext = os.path.splitext(ERRLOG)
            try:
                os.rename(ERRLOG, "%s.%s.%d%s" % (base, time.strftime("%Y%m%dT%H%M%S"), os.getpid(), ext))
            except OSError:
                pass            # another call set it aside first, or holds it open: append now, set aside next time
        import traceback
        pl = payload if isinstance(payload, dict) else {}
        with open(ERRLOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(time.time(), 1), "kind": kind, "ev": str(pl.get("hook_event_name") or ""),
                                 "sid": str(pl.get("session_id") or "")[:40],
                                 "err": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                                 "tb": traceback.format_exc().strip().splitlines()[-4:]},
                                separators=(",", ":")) + "\n")
    except Exception:
        pass


def run(payload, now=None):
    now = now or time.time()
    event = str(payload.get("hook_event_name") or "")
    sid = str(payload.get("session_id") or "")
    if event not in ("UserPromptSubmit", "PreToolUse") or not sid or os.path.exists(os.path.join(LIVE, "STOP")):
        return None
    sys.path.insert(0, BIN)
    import live_membrane as LM
    if not os.path.exists(LM.SIDECAR):
        return None                     # a fresh install: no graph is built yet, so there is nothing to serve
    pol = LM.policy()
    os.makedirs(STATE_DIR, exist_ok=True)
    p = _state_path(sid)
    try:
        st = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        if os.path.exists(p):
            _log_error("state_reset", e, payload)
        st = {"ask": [], "recent": [], "calls": 0, "calls_at_eval": 0, "last_eval": 0.0, "run": [None, 0], "served": {}}
    due = False
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        words = [w for w in LM._ORDER_TOKEN.findall(prompt.lower()) if w not in LM.ORDER_NOISE][:40]
        if words:
            st["ask"] = words
        due = True
    else:
        st["calls"] = st.get("calls", 0) + 1
        texts = []
        _strings(payload.get("tool_input"), texts)
        toks = []
        for t in texts:
            toks += [w for w in LM._ORDER_TOKEN.findall(t.lower()) if w not in LM.ORDER_NOISE]
        if toks:
            st.setdefault("recent", []).append([round(now, 1), toks[:60]])
        due = (st["calls"] - st.get("calls_at_eval", 0) >= EVERY_CALLS) or (now - st.get("last_eval", 0.0) >= EVERY_S)
    st["recent"] = [r for r in st.get("recent", []) if now - r[0] <= WINDOW][-40:]
    served, st_oc = None, None
    if due:
        st["last_eval"], st["calls_at_eval"] = now, st.get("calls", 0)
        items = []
        for t, toks in reversed(st["recent"]):
            items += [("said:" + w, now - t) for w in toks]
        query = LM.order_query(items, st.get("ask") or [])
        lv = LM.Live(delta=None)
        oc = LM.order_candidates(query, set(), lv.db)
        lead = oc[0]["slug"] if oc else None
        run_ = st.get("run") or [None, 0]
        run_ = [lead, run_[1] + 1 if lead and run_[0] == lead else (1 if lead else 0)]
        st["run"] = run_
        ia_read, tpath = 0, str(payload.get("transcript_path") or "")
        scan = st.get("ia_scan") if isinstance(st.get("ia_scan"), dict) else None
        if not (st.get("ia_status") in FINAL and st.get("ia_v") == IA_VERSION and scan is not None
                and (not tpath or scan.get("path") == tpath)):
            # an answer is kept only when this version read it from the rollout now in use (its header, or a response
            # before any header); anything else, including a state an older version wrote, is asked again, and the
            # scan record lets the next evaluation read on from where this one stopped
            value, status, rec = _classify(payload, scan)
            st["interactive"], st["ia_status"], st["ia_v"] = value, status, IA_VERSION
            if rec is not None:
                st["ia_scan"], ia_read = rec, rec.get("read", 0)
        held = None
        if pol.get("serve_orders_codex", False) and oc and st.get("interactive"):
            c = oc[0]
            t0 = st.get("served", {}).get(c["slug"])
            if c["score"] >= pol.get("orders_min", 30.0) and run_[1] >= pol.get("orders_stable", 2) and \
                    (t0 is None or now - t0 > REFRACTORY):
                st.setdefault("served", {})[c["slug"]] = now
                if hasattr(LM, "held_back") and LM.held_back({"sid": sid}, "order:" + c["slug"], now, pol):
                    held = c["slug"]                  # show versus hold (see live_membrane.held_back): logged, not shown
                else:
                    served, st_oc = c, oc
        try:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(now, 1), "sid": sid[:40], "ev": event, "lead": lead,
                                     "score": oc[0]["score"] if oc else None, "run": run_[1],
                                     "top": [[c["slug"], c["score"]] for c in oc[:3]],
                                     "served": served["slug"] if served else None, "held": held,
                                     "ia": st.get("interactive"), "ia_status": st.get("ia_status"),
                                     "ia_read": ia_read},
                                    separators=(",", ":")) + "\n")
        except OSError as e:
            _log_error("log_write", e, payload)
    tmp = p + ".%d.tmp" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, p)
    except OSError as e:
        _log_error("state_write", e, payload)
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not served:
        return None
    line = ("[his standing order] %s (what this session is doing matches it; he has had to repeat orders like this one "
            "when agents missed them - check your work against it before you report; his later words win)" % (
                LM.order_line(st_oc)))
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": line}}


def main():
    payload = None
    try:
        raw = sys.stdin.buffer.read(512 * 1024)
        payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        out = run(payload)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        _log_error("run", e, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
