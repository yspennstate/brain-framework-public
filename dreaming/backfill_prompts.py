#!/usr/bin/env python3
"""backfill_prompts.py - harvest every prompt the owner ever gave an agent into the perpetual ledger.

Owner directive (2026-07-09): every single prompt, saved in perpetuity, classified. prompt_archive.py
captures the stream from now on (UserPromptSubmit hook); this script recovers the history:

  sources: ~/.claude/projects/<proj>/*.jsonl          (Claude Code session transcripts, all projects)
           ~/.codex/sessions/**/rollout-*.jsonl       (Codex CLI sessions)
           <repo>/11_raw_session_transcripts/**       (older copies of both; dedupe absorbs overlap)

  filters: tool_result turns, harness noise (task notifications, system reminders, command stdout),
           environment/instruction injections, continuation summaries - none of that is the owner
           speaking. /command wrappers are unwrapped to "/name args" (those ARE his words).
           Known machine-authored preambles (dream prompts, fleet launchers) are kept but tagged
           origin=automation so views can separate them; everything else is origin=owner.

  dedupe: normalized-text hash; earliest timestamp wins, occurrence count + session/project lists kept.
          Existing ledger lines (hook-captured) are respected - no twins appended.

Append-only into memory/prompts/PROMPTS.jsonl (same file the live hook writes; PROMPT_ARCHIVE_DIR
seam honored for tests). Classification here stays PROVISIONAL (keyword tier) - the model pass
(classify_prompts workflow / the nightly dream) writes class_model afterward.

Usage: python backfill_prompts.py [--dry]
"""
import os, io, sys, json, glob, re, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prompt_archive as pa

HOME = os.path.expanduser("~")
CLAUDE_PROJ = os.path.join(HOME, ".claude", "projects")
CODEX_SESS = os.path.join(HOME, ".codex", "sessions")
REPO_RAW = os.path.join(HOME, "brain-repo", "11_raw_session_transcripts")

SKIP_PROJ = re.compile(r"subagents|workflows-wf", re.I)
AUTOMATION = ("You are the brain", "You are dreaming", "You are the dream", "Tonight ",
              "<<autonomous-loop", "[work-session]", "You are agentA", "You are a research agent",
              "You are Tad", "Run one dream cycle", "Wake up and continue",
              # codex goal-loop wrappers + scripted iteration templates (caught by the 07-09 model
              # classification pass: 564 of these were riding in the owner stratum)
              "<codex_internal_context", "<goal_context>", "You are in the FINAL")
DROP_PREFIX = ("<environment_context>", "<user_instructions", "<ENVIRONMENT", "# AGENTS.md",
               "[Request interrupted", "<turn_aborted", "This session is being continued from a previous",
               "<subagent_notification")  # injected notification of a subagent finishing, not the owner


def _clean(text):
    t = (text or "").strip()
    if not t or any(t.startswith(p) for p in DROP_PREFIX):
        return ""
    if t.startswith("Caveat: The messages below"):
        # command wrappers ride inside this caveat; keep only the unwrapped command (if any)
        t = t.split("</local-command-caveat>")[-1] if "</local-command-caveat>" in t else t
        if "<command-name>" not in t:
            return ""
    return pa.human_part(t)


def _origin(text):
    return "automation" if any(text.startswith(m) for m in AUTOMATION) else "owner"


def _claude_files():
    for proj in sorted(glob.glob(os.path.join(CLAUDE_PROJ, "*"))):
        if not os.path.isdir(proj) or SKIP_PROJ.search(os.path.basename(proj)):
            continue
        for f in sorted(glob.glob(os.path.join(proj, "*.jsonl"))):
            yield f, os.path.basename(proj), "claude"
    if os.path.isdir(REPO_RAW):
        for f in sorted(glob.glob(os.path.join(REPO_RAW, "claude_projects", "**", "*.jsonl"), recursive=True)):
            rel = os.path.relpath(f, REPO_RAW).replace("\\", "/")
            # any subagents/workflows path segment = agent-authored transcript, not the owner speaking
            if re.search(r"/(subagents|workflows)/", rel) or SKIP_PROJ.search(rel):
                continue
            yield f, os.path.basename(os.path.dirname(f)), "claude"


def _codex_files():
    for root in (CODEX_SESS, os.path.join(REPO_RAW, "codex_sessions")):
        if os.path.isdir(root):
            for f in sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
                yield f, "codex", "codex"


def _iter_claude(f):
    sid = os.path.splitext(os.path.basename(f))[0]
    for ln in io.open(f, encoding="utf-8", errors="replace"):
        if '"type":"user"' not in ln and '"type": "user"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("type") != "user" or d.get("isMeta"):
            continue
        msg = d.get("message") or {}
        c = msg.get("content")
        if isinstance(c, list):
            if any((isinstance(i, dict) and i.get("type") == "tool_result") for i in c):
                continue
            text = "\n".join(i.get("text", "") for i in c if isinstance(i, dict) and i.get("type") == "text")
        else:
            text = c or ""
        yield text, (d.get("sessionId") or sid), (d.get("timestamp") or "")[:19], d.get("cwd") or ""


def _iter_codex(f):
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27,})", os.path.basename(f))
    sid = m.group(1) if m else os.path.basename(f)[:20]
    cwd = ""
    for ln in io.open(f, encoding="utf-8", errors="replace"):
        if '"session_meta"' in ln and not cwd:
            try:
                meta = json.loads(ln).get("payload") or {}
                cwd = meta.get("cwd", "")
                # interactive sessions have source="cli" (a string); spawned subagent threads
                # carry a dict with subagent/thread_spawn - those prompts are agent-authored
                if not isinstance(meta.get("source", "cli"), str):
                    return
            except Exception:
                pass
        if '"role":"user"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        p = d.get("payload") or d
        c = p.get("content")
        if not isinstance(c, list):
            continue
        text = "\n".join(i.get("text", "") for i in c if isinstance(i, dict) and i.get("type") in ("input_text", "text"))
        yield text, sid, (d.get("timestamp") or "")[:19], cwd


def harvest():
    seen = {}
    order = []
    files = 0
    for f, proj, src in list(_claude_files()) + list(_codex_files()):
        files += 1
        it = _iter_claude(f) if src == "claude" else _iter_codex(f)
        try:
            for raw, sid, ts, cwd in it:
                t = _clean(raw)
                if len(t) < 2:
                    continue
                key = hashlib.sha1(re.sub(r"\s+", " ", t.lower()).encode("utf-8", "replace")).hexdigest()
                r = seen.get(key)
                if r:
                    r["count"] += 1
                    if ts and (not r["ts"] or ts < r["ts"]):
                        r["ts"] = ts
                    if sid[:12] not in r["sessions"] and len(r["sessions"]) < 5:
                        r["sessions"].append(sid[:12])
                    continue
                text, red = pa.redact(t)
                seen[key] = {"ts": ts, "session": sid[:12], "sessions": [sid[:12]], "cwd": cwd,
                             "source": "backfill-" + src, "origin": _origin(text),
                             "class_provisional": ("automation" if _origin(text) == "automation"
                                                   else pa.provisional_class(text)),
                             "redacted": red, "count": 1, "project": proj, "text": text}
                order.append(key)
        except Exception as e:
            print("  ! %s: %s" % (os.path.basename(f), e))
    return seen, order, files


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    dry = "--dry" in sys.argv
    existing = set()
    if os.path.exists(pa.LEDGER):
        for ln in io.open(pa.LEDGER, encoding="utf-8", errors="replace"):
            try:
                t = json.loads(ln).get("text", "")
                existing.add(hashlib.sha1(re.sub(r"\s+", " ", t.lower()).encode("utf-8", "replace")).hexdigest())
            except Exception:
                pass
    seen, order, files = harvest()
    new = [k for k in order if k not in existing]
    new.sort(key=lambda k: seen[k]["ts"] or "9999")
    kinds = {}
    for k in new:
        kinds[seen[k]["origin"]] = kinds.get(seen[k]["origin"], 0) + 1
    print("scanned %d transcript files; %d unique prompts (%d already in ledger); new by origin: %s"
          % (files, len(order), len(order) - len(new), kinds))
    if dry:
        for k in new[:12]:
            r = seen[k]
            print("  [%s|%s|%s] %s" % (r["ts"], r["origin"], r["class_provisional"], r["text"][:110].replace("\n", " ")))
        print("  ... (--dry: nothing written)")
        return
    os.makedirs(pa.ARCHIVE_DIR, exist_ok=True)
    with io.open(pa.LEDGER, "a", encoding="utf-8") as f:
        for k in new:
            r = {kk: vv for kk, vv in seen[k].items() if kk != "sessions" or len(vv) > 1}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("appended %d prompts to %s" % (len(new), pa.LEDGER))


if __name__ == "__main__":
    main()
