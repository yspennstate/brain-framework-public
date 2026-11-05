#!/usr/bin/env python3
"""owner_prompt_context.py - hand every agent the owner's last 100 prompts, so nobody works blind.

Owner order 2026-09-18 12:4x, after an agent answered a question about "the submatrix film" with the
state of the one already posted, when he had been asking all day for a NEW one:

    "make a hook that gives every agent i talk to my last 100 propts automatically, that way the
     don't just lose contacx of what we were talking about, put this in the brian, vinilla brain,
     a hook like that is importent, please be carful when you make it and test it."
    "... and have all agents get that information every hour in a hook"

WHAT IT DOES. On SessionStart every agent gets the owner's recent prompts in one block; during a long
session the block is re-served once an hour, because a session that has run for six hours has pushed
the wake context far out of view and that is exactly when the thread gets dropped. The text is what he
actually typed, newest last, so it reads as a conversation rather than a list.

THE SUBSTRATE is the perpetual ledger memory/prompts/PROMPTS.jsonl (written by prompt_archive.py on
every UserPromptSubmit). Three things about it decide this hook's design:

  1. IT IS 100 MB AND THIS BOX IS DISK-CONTENDED. Reading it whole on a hook fire is out of the
     question, so the view is built from a TAIL SEEK and cached; the hook itself reads a small JSON.
     The cache is rebuilt when it is older than an hour, which is also what makes the hourly re-serve
     mean something - a re-served block that could not have changed is just noise.
  2. MOST OWNER ROWS ARE LOOP REPLAYS. A /loop prompt is re-recorded on every tick: 1,750 of the 3,048
     owner rows in the last 4 MB carry `repeat_of`. Without dropping those, "his last 100 prompts"
     is ninety copies of one overnight instruction and none of the conversation - the precise failure
     this hook exists to prevent. The original of each loop prompt is kept; only the replays go.
  3. SECRETS ARE ALREADY STRIPPED. prompt_archive redacts credentials BEFORE writing the line and sets
     `redacted`; `text` is the safe form, and this hook never reads a prompt from anywhere else.

WHAT IT IS NOT. It does not judge, summarise or rank the prompts - an agent reading his own words is
the point, and a summary is where the meaning gets lost. It never blocks: any error exits 0 silently.

    python owner_prompt_context.py --selftest     both arms, on a synthetic ledger
    python owner_prompt_context.py --print        the block this would inject, to stdout
"""
import io
import json
import os
import sys
import time

DREAMING = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
BRAIN_DIR = os.environ.get("BRAIN_DIR") or os.path.join(os.path.expanduser("~"), ".brain")
LEDGER = os.environ.get("BRAIN_PROMPT_LEDGER") or os.path.join(BRAIN_DIR, "prompts", "PROMPTS.jsonl")
STATE_DIR = os.path.join(DREAMING, "owner_prompt_context_state")
# The cache and the log live in the state directory, not beside the module: this file is imported by
# owner_prompts_hook.py, and an import must not drop files into the package directory of whoever
# imports it (a peer's import test left a stray log in a repo checkout on 2026-09-18).
CACHE = os.path.join(STATE_DIR, "owner_prompt_context_cache.json")
LOG = os.path.join(STATE_DIR, "owner_prompt_context.log")

N_PROMPTS = int(os.environ.get("BRAIN_PROMPT_N") or 100)
CACHE_TTL_S = int(os.environ.get("BRAIN_PROMPT_EVERY_S") or 3600)
RESERVE_EVERY_S = 60 * 60      # and re-serve it to a live session hourly (his "every hour")
TOTAL_BUDGET = 12000           # characters of prompt text, so the block cannot crowd out the turn
# Adaptive tail: start small because the common case needs very little of a 100 MB file.
TAIL_STEPS = (2 * 1024 * 1024, 8 * 1024 * 1024, 32 * 1024 * 1024)


def log(msg):
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _rows_from_tail(path, nbytes):
    """Parse whole JSON lines out of the last nbytes of the ledger. The first line is dropped when we
    did not start at byte zero, because a seek lands mid-line and half a row is not a row."""
    size = os.path.getsize(path)
    with io.open(path, "rb") as fh:
        start = max(0, size - nbytes)
        fh.seek(start)
        raw = fh.read()
    if start > 0:
        cut = raw.find(b"\n")
        raw = raw[cut + 1:] if cut >= 0 else b""
    out = []
    for ln in raw.split(b"\n"):
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln.decode("utf-8", "replace")))
        except Exception:
            continue
    return out


def select(rows, n=N_PROMPTS):
    """His own prompts, newest last, loop replays and consecutive repeats removed."""
    keep = []
    for r in rows:
        if r.get("origin") != "owner":
            continue
        text = (r.get("text") or "").strip()
        if not text:
            continue
        # A /loop tick re-records the same instruction every few minutes. Keep the original, drop
        # the replays, or the window fills with one prompt and the conversation disappears.
        if r.get("repeat_of"):
            continue
        if keep and keep[-1][1] == text:
            continue
        keep.append((r.get("ts") or "", text))
    return keep[-n:]


def build_view(path=None, n=N_PROMPTS):
    """(prompts, meta). Widens the tail read until it has enough, so the usual case reads 2 MB."""
    path = path or LEDGER
    if not os.path.exists(path):
        return [], {"error": "no ledger"}
    picked, used = [], 0
    for nbytes in TAIL_STEPS:
        used = nbytes
        picked = select(_rows_from_tail(path, nbytes), n)
        if len(picked) >= n:
            break
        if nbytes >= os.path.getsize(path):
            break
    return picked, {"built_at": time.time(), "tail_bytes": used, "count": len(picked)}


def load_view(cache=None, ttl=CACHE_TTL_S, now=None, path=None):
    """The cached view, rebuilt when stale. The cache is what the hook actually reads."""
    cache = cache or CACHE
    now = time.time() if now is None else now
    try:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    except Exception:
        pass
    try:
        d = json.load(io.open(cache, encoding="utf-8"))
        if (now - float(d.get("built_at") or 0)) < ttl and d.get("prompts"):
            return d
    except Exception:
        pass
    prompts, meta = build_view(path=path, n=N_PROMPTS)
    d = {"prompts": prompts, "built_at": meta.get("built_at") or now,
         "tail_bytes": meta.get("tail_bytes"), "count": len(prompts)}
    _atomic_write(cache, json.dumps(d, ensure_ascii=False))
    return d


def _atomic_write(path, body):
    """Temp + replace, with a retry: os.replace over a file another session holds open raises
    WinError 5 on Windows, and every concurrent session runs this hook."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return True
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        return False
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return False


def render(prompts, budget=TOTAL_BUDGET):
    """One block, newest last. The recent end is given more room because that is the live thread."""
    if not prompts:
        return ""
    # Spend the budget from the newest backwards, so a long tail of old prompts cannot starve today's.
    per_new, per_old = 400, 130
    recent_n = 20
    chosen = []
    spent = 0
    for i in range(len(prompts) - 1, -1, -1):
        ts, text = prompts[i]
        cap = per_new if (len(prompts) - 1 - i) < recent_n else per_old
        t = " ".join(text.split())
        if len(t) > cap:
            t = t[:cap - 1].rstrip() + "…"
        if spent + len(t) > budget:
            break
        chosen.append((ts, t))
        spent += len(t)
    chosen.reverse()
    head = ("[owner-prompts] the last %d things the owner actually typed, oldest first - this is the thread "
            "you are in, not a task list. Loop replays are collapsed. If your current task seems to "
            "contradict a more recent line here, HIS LATER WORDS WIN: re-read them before you answer.\n"
            % len(chosen))
    body = "\n".join("  %s  %s" % ((ts or "")[:16].replace("T", " "), t) for ts, t in chosen)
    return head + body


def decide(payload, now=None, state_dir=None, cache=None, path=None):
    """The hook output for one event. SessionStart always; UserPromptSubmit once an hour per session."""
    now = time.time() if now is None else now
    state_dir = state_dir or STATE_DIR
    event = str(payload.get("hook_event_name") or "")
    if event not in ("SessionStart", "UserPromptSubmit"):
        return {}
    session = str(payload.get("session_id") or "nosession")
    session = "".join(ch for ch in session if ch.isalnum() or ch in "-_")[:80] or "nosession"
    os.makedirs(state_dir, exist_ok=True)
    spath = os.path.join(state_dir, session + ".json")
    st = {}
    try:
        st = json.load(io.open(spath, encoding="utf-8"))
    except Exception:
        st = {}
    last = float(st.get("last_fired") or 0.0)
    # SessionStart is the wake: a fresh agent has no thread at all, so it always gets the block.
    # CODEX HAS NO SessionStart EVENT - its hooks.json carries only PostToolUse, PreToolUse, Stop and
    # UserPromptSubmit. So "fire on UserPromptSubmit only once an hour has passed since the last fire"
    # would never fire there at all: last stays 0 forever and the hook is silently dead on half the
    # fleet, which is the opposite of "every agent i talk to". An unfired session gets the block on
    # its first prompt; after that both runtimes are on the same hourly clock.
    if event == "SessionStart":
        fire = True
    elif not last:
        fire = True
    else:
        fire = (now - last) >= RESERVE_EVERY_S
    if not fire:
        st.setdefault("first_seen", now)
        _atomic_write(spath, json.dumps(st))
        return {}
    view = load_view(cache=cache, now=now, path=path)
    block = render(view.get("prompts") or [])
    st["first_seen"] = st.get("first_seen") or now
    st["last_fired"] = now
    st["fires"] = int(st.get("fires") or 0) + 1
    _atomic_write(spath, json.dumps(st))
    if not block:
        return {}
    log("fire session=%s event=%s n=%s prompts=%d" % (session, event, st.get("fires"), view.get("count") or 0))
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": block}}


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="owner_prompt_ctx_")
    led = os.path.join(d, "PROMPTS.jsonl")
    cache = os.path.join(d, "cache.json")
    sd = os.path.join(d, "state")
    # THE LOOP REPLAYS ARE DELIBERATELY NOT ADJACENT. With them consecutive, the duplicate-collapse
    # removes them on its own and the repeat_of filter can be deleted with the test still passing -
    # measured, that mutation survived. Interleaving another prompt makes repeat_of the only thing
    # that can remove them, which is what gives this assertion teeth.
    rows = [
        {"ts": "2026-09-18T01:00", "origin": "owner", "text": "first real prompt"},
        {"ts": "2026-09-18T01:01", "origin": "automation", "text": "AUTOMATION MUST NOT APPEAR"},
        {"ts": "2026-09-18T01:02", "origin": "owner", "text": "loop me", "repeat_of": None},
        {"ts": "2026-09-18T01:03", "origin": "owner", "text": "a prompt between the replays"},
        {"ts": "2026-09-18T01:04", "origin": "owner", "text": "loop me", "repeat_of": "x"},
        {"ts": "2026-09-18T01:05", "origin": "owner", "text": "another between the replays"},
        {"ts": "2026-09-18T01:06", "origin": "owner", "text": "loop me", "repeat_of": "x"},
        {"ts": "2026-09-18T01:07", "origin": "owner", "text": "second real prompt"},
        {"ts": "2026-09-18T01:08", "origin": "owner", "text": "second real prompt"},
        {"ts": "2026-09-18T01:09", "origin": "owner", "text": "the newest thing he said"},
    ]
    with io.open(led, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    picked, _ = build_view(path=led)
    texts = [t for _, t in picked]
    assert "AUTOMATION MUST NOT APPEAR" not in texts, "automation rows must be filtered out"
    assert texts.count("loop me") == 1, "loop replays collapse to the original, got %r" % (texts,)
    assert texts.count("second real prompt") == 1, "consecutive duplicates collapse"
    assert texts[-1] == "the newest thing he said", "newest prompt is last, got %r" % (texts[-1],)
    assert texts[0] == "first real prompt", "oldest prompt is first"

    # THE RENDERED BLOCK IS ORDERED OLDEST FIRST. Asserting this on select() alone is not enough:
    # the reversal lives in render(), so deleting it there left every select() assertion green while
    # the block came out newest-first - that mutation survived until this check existed.
    blk_order = render(picked)
    assert blk_order.index("first real prompt") < blk_order.index("the newest thing he said"), \
        "the block must read oldest first, newest last"

    # POSITIVE ARM: SessionStart serves the block, and his newest words are in it.
    out = decide({"hook_event_name": "SessionStart", "session_id": "t1"},
                 now=1000.0, state_dir=sd, cache=cache, path=led)
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "the newest thing he said" in ctx, "SessionStart must serve the newest prompt"
    assert "AUTOMATION MUST NOT APPEAR" not in ctx, "automation must never reach the block"
    assert ctx.index("first real prompt") < ctx.index("the newest thing he said"), \
        "the served block must read oldest first, newest last"

    # NEGATIVE ARM: a prompt right after the wake is silent; an hour later it serves again.
    q = {"hook_event_name": "UserPromptSubmit", "session_id": "t1"}
    assert decide(q, now=1001.0, state_dir=sd, cache=cache, path=led) == {}, "silent right after a fire"
    assert decide(q, now=1000.0 + 59 * 60, state_dir=sd, cache=cache, path=led) == {}, "silent before the hour"
    out2 = decide(q, now=1000.0 + 61 * 60, state_dir=sd, cache=cache, path=led)
    assert "additionalContext" in out2.get("hookSpecificOutput", {}), "serves again after an hour"
    assert decide({"hook_event_name": "Stop", "session_id": "t1"},
                  now=1000.0 + 999 * 60, state_dir=sd, cache=cache, path=led) == {}, "never on Stop"

    # CODEX ARM: a runtime with no SessionStart (Codex has none) must still get the block, on its
    # first prompt. Without this the hourly clock never starts there and the hook is dead on half
    # the fleet while every Claude-side test stays green.
    out_cx = decide({"hook_event_name": "UserPromptSubmit", "session_id": "codex-1"},
                    now=5000.0, state_dir=sd, cache=cache, path=led)
    assert "additionalContext" in out_cx.get("hookSpecificOutput", {}), \
        "a session that never saw SessionStart must fire on its first prompt (Codex)"
    assert decide({"hook_event_name": "UserPromptSubmit", "session_id": "codex-1"},
                  now=5060.0, state_dir=sd, cache=cache, path=led) == {}, \
        "and then go quiet until the hour"

    # FAIL-SAFE: a missing ledger is silence, not a crash that blocks the session.
    gone = os.path.join(d, "nope.jsonl")
    p2, meta = build_view(path=gone)
    assert p2 == [] and meta.get("error"), "missing ledger returns empty, not an exception"
    assert decide({"hook_event_name": "SessionStart", "session_id": "t2"},
                  now=2000.0, state_dir=sd, cache=os.path.join(d, "c2.json"), path=gone) == {}, \
        "missing ledger must not emit a block"

    # BUDGET: a pathological ledger cannot blow the context window.
    big = os.path.join(d, "big.jsonl")
    with io.open(big, "w", encoding="utf-8", newline="\n") as fh:
        for i in range(400):
            fh.write(json.dumps({"ts": "2026-09-18T02:%02d" % (i % 60), "origin": "owner",
                                 "text": "x" * 5000 + " n%d" % i}) + "\n")
    pb, _ = build_view(path=big)
    blk = render(pb)
    assert len(blk) < TOTAL_BUDGET + 2000, "block must stay inside its budget, got %d" % len(blk)
    assert len(pb) == N_PROMPTS, "caps at %d prompts, got %d" % (N_PROMPTS, len(pb))

    print("selftest ok:")
    print("  automation filtered, loop replays collapsed, duplicates collapsed, newest last")
    print("  SessionStart always serves; UserPromptSubmit silent until the hour, then serves")
    print("  never on Stop; missing ledger is silent; block stays under %d chars" % TOTAL_BUDGET)


def main():
    if "--selftest" in sys.argv:
        selftest()
        return 0
    if "--print" in sys.argv:
        v = load_view()
        sys.stdout.write(render(v.get("prompts") or []) + "\n")
        sys.stderr.write("\n[%d prompts, tail %s bytes, built %s]\n"
                         % (v.get("count") or 0, v.get("tail_bytes"),
                            time.strftime("%H:%M:%S", time.localtime(v.get("built_at") or 0))))
        return 0
    try:
        raw = sys.stdin.read(512 * 1024)
        payload = json.loads(raw) if raw.strip() else {}
        out = decide(payload)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception as exc:      # fail-safe: a hook never blocks a session
        log("error: %r" % (exc,))
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: owner_prompt_context.py [--selftest|--print]   (hook: event JSON on stdin)")
        raise SystemExit(0)
    sys.exit(main())
