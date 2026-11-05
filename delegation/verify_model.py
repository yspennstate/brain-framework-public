#!/usr/bin/env python3
"""Downgrade detector for delegated workers.

The problem this exists for (measured 2026-07-11): when a Claude parent spawns a
subagent with model=fable, the worker's SYSTEM PROMPT is written with the requested
model, so the worker self-reports "I am claude-fable-5" even when the API actually
served it on claude-opus-4-8 (a subscription-cap fallback). Four of five workers in
one session lied this way. The self-report cannot see the downgrade; only the
transcript's per-message `model` field (set from the API response, not the prompt)
tells the truth. This module reads that ground truth and reports whether a worker ran
on the model we asked for.

Signals, in order of trust:
  1. GROUND TRUTH - `message.model` on each assistant record in the worker's transcript
     JSONL (Claude) or the model on codex json events (GPT). Cannot be faked by the model.
  2. SELF-REPORT - what the worker said its model was. Weak: reflects the prompt, not
     the serving model. Its DISAGREEMENT with (1) is itself the downgrade signature.
  3. WARNING SCAN - fallback/limit phrases in the transcript. Belt-and-suspenders;
     absent from subagent JSONL in practice (the notice is interactive-only).

CLI:
  python verify_model.py --requested fable --transcript <path.jsonl>
  python verify_model.py --requested fable --agent-id <id>        # locate then verify
  python verify_model.py --selftest                               # validate on real data
"""
from __future__ import annotations
import argparse, glob, json, os, sys

PROJECT_DIR = r"{{USERPROFILE}}\.claude\projects\{{BRAIN_SLUG}}"

# --- model identity: normalize an alias/id to a canonical (family, id, tier) ---------
# tier is "capability rank as the owner ranks them": Fable is the top Claude model and
# Opus is the model it falls back to, so fable > opus. Higher tier = more capable.
_CLAUDE = [
    ("claude-fable-5", 5, ("fable", "claude-fable-5")),
    ("claude-opus-4",  4, ("opus", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5")),
    ("claude-sonnet",  3, ("sonnet", "claude-sonnet-5", "claude-sonnet-4")),
    ("claude-haiku",   2, ("haiku", "claude-haiku-4-5", "claude-haiku")),
]
_OPENAI = [
    ("gpt-5.6", 6, ("gpt-5.6", "gpt-5-6", "gpt5.6")),
    ("gpt-5",   5, ("gpt-5", "gpt-5.1", "gpt-5.2")),
]

def normalize(model: str):
    """Return (family, canonical, tier) for a model alias or full id, or (None,raw,None)."""
    if not model:
        return (None, "", None)
    m = model.strip().lower()
    for canon, tier, aliases in _CLAUDE:
        if m == canon or any(m == a or m.startswith(a) for a in aliases):
            return ("claude", canon, tier)
    for canon, tier, aliases in _OPENAI:
        if any(m == a or m.startswith(a) for a in aliases):
            return ("openai", canon, tier)
    # family-prefix fallback so a NEW id (e.g. a future claude-opus-4-9) still maps to its
    # tier and a genuine fable->opus downgrade is caught, not softened to DIFFERENT_MODEL.
    if m.startswith("claude-fable"):
        return ("claude", "claude-fable-5", 5)
    if m.startswith("claude-opus"):
        return ("claude", "claude-opus-4", 4)
    if m.startswith("claude-sonnet"):
        return ("claude", "claude-sonnet", 3)
    if m.startswith("claude-haiku"):
        return ("claude", "claude-haiku", 2)
    if m.startswith("gpt-5"):
        return ("openai", "gpt-5", 5)
    return (None, model, None)

# --- source-specific extractors of the served model(s) -------------------------------
def models_from_claude_transcript(path: str):
    """Distinct REAL `message.model` values across a Claude Code subagent/session JSONL.
    Skips `<synthetic>` placeholder records (API errors, usage-limit notices, connection
    drops all use model `<synthetic>` + isApiErrorMessage) - those are not a served model,
    they are the error/rate-limit signal, read separately by _claude_error_blob. Without this
    skip, one API-error record made a clean Fable run look like DIFFERENT_MODEL, and a
    rate-limited run looked model-verified (review finding #1, 2026-07-11)."""
    served = {}
    if not path or not os.path.exists(path):
        return served
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        model = msg.get("model")
        if not isinstance(model, str) or not model or model == "<synthetic>":
            continue
        if rec.get("isApiErrorMessage") or msg.get("isApiErrorMessage"):
            continue
        served[model] = served.get(model, 0) + 1
    return served

def _claude_error_blob(path: str):
    """Lowercased text of ONLY the error/system records - API errors, `<synthetic>` notices,
    system messages - where a real fallback/limit notice actually appears. Scanning the whole
    transcript (worker prompts here literally discuss 'downgrade'/'rate limit') produced false
    positives (review finding #3, 2026-07-11), so the scan is scoped to these records."""
    if not path or not os.path.exists(path):
        return ""
    parts = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        if (rec.get("isApiErrorMessage") or msg.get("isApiErrorMessage")
                or msg.get("model") == "<synthetic>" or rec.get("type") == "system"):
            parts.append(line.lower())
    return " ".join(parts)

def models_from_codex_json(path: str):
    """Distinct model values across a `codex exec --json` JSONL event stream."""
    served = {}
    if not path or not os.path.exists(path):
        return served
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        # codex events carry the model in a few shapes across versions; check the common ones
        for key in ("model",):
            v = rec.get(key)
            if isinstance(v, str) and v:
                served[v] = served.get(v, 0) + 1
        msg = rec.get("msg") if isinstance(rec.get("msg"), dict) else None
        if msg and isinstance(msg.get("model"), str):
            served[msg["model"]] = served.get(msg["model"], 0) + 1
    return served

_WARN = ("fell back", "falling back", "fallback to", "reached your", "you've reached",
         "downgrad", "model unavailable", "temporarily unavailable", "limit reached",
         "defaulting to fallback metadata", "is not supported", "model metadata for")

def scan_warnings(path: str):
    hits = set()
    if not path or not os.path.exists(path):
        return sorted(hits)
    for line in open(path, encoding="utf-8", errors="replace"):
        low = line.lower()
        for kw in _WARN:
            if kw in low:
                hits.add(kw)
    return sorted(hits)

def codex_outcome(path: str):
    """Parse a `codex exec --json` event stream. Codex fails HARD on an unsupported model
    (a 400 error / turn.failed) rather than silently serving another, so the outcome is:
    got an agent_message with no error/fallback = requested model served OK."""
    had_error, had_agent_msg, usage, result = False, False, {}, ""
    err_texts = []
    if not path or not os.path.exists(path):
        return {"error": False, "agent_message": False, "error_text": "", "usage": {}, "result": ""}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        t = rec.get("type", "")
        item = rec.get("item") if isinstance(rec.get("item"), dict) else {}
        if t in ("error", "turn.failed") or item.get("type") == "error":
            had_error = True
            # collect the message from ERROR records only - scanning the agent's answer text
            # for phrases like "is not supported" caused false verdicts (review finding #3).
            m = (rec.get("message") or item.get("message")
                 or (rec.get("error") or {}).get("message") or "")
            if isinstance(m, str):
                err_texts.append(m.lower())
        if item.get("type") == "agent_message":
            had_agent_msg = True
            result = item.get("text", "") or result
        if t == "turn.completed" and isinstance(rec.get("usage"), dict):
            usage = rec["usage"]
    return {"error": had_error, "agent_message": had_agent_msg,
            "error_text": " ".join(err_texts), "usage": usage, "result": result}

def _verify_codex(requested, path, self_report=None):
    o = codex_outcome(path)
    et = o["error_text"]          # scanned from ERROR records only, not the agent's answer (finding #3)
    # a rate limit is NOT a downgrade (dogfood 2026-07-11: a "you've hit your usage limit"
    # turn.failed was mislabeled DOWNGRADED).
    rate_limited = any(p in et for p in ("usage limit", "hit your usage", "rate limit",
                                         "try again at", "purchase more credits"))
    model_rejected = any(p in et for p in ("is not supported", "model metadata for",
                                           "defaulting to fallback metadata"))
    evidence = []
    if model_rejected:            # BEFORE the OK branch: a soft fallback can still complete (finding #2)
        verdict, downgraded = "DOWNGRADED", True
        evidence.append(f"codex rejected/degraded requested {requested} (not supported / fallback metadata)")
    elif o["agent_message"]:       # completed - OK even if a transient error event fired mid-run (finding #7)
        verdict, downgraded = "OK", False
        _note = " (after a transient error event)" if o["error"] else ""
        evidence.append(f"codex completed on requested {requested}{_note}; hard-fails on wrong model, so success == served")
    elif rate_limited:
        verdict, downgraded = "RATE_LIMITED", False
        evidence.append("codex hit a usage/rate limit - the worker could not run (retry later or use another "
                        "worker); this is NOT a model downgrade")
    elif o["error"]:
        verdict, downgraded = "FAILED", False
        evidence.append("codex turn failed for a non-model reason (not a downgrade)")
    else:
        verdict, downgraded = "UNVERIFIABLE", False
        evidence.append("codex stream had neither a clean agent_message nor a clear error")
    return {
        "requested": requested, "requested_canonical": normalize(requested)[1],
        "served": [requested] if verdict == "OK" else [],
        "served_counts": {}, "downgraded": downgraded, "served_differs": model_rejected,
        "self_report": self_report, "self_report_lie": False,
        "warnings": (["fallback/limit in error records"] if (model_rejected or rate_limited) else []),
        "ground_truth_available": bool(o["agent_message"] or o["error"]),
        "confidence": "certain" if (o["agent_message"] or o["error"]) else "unknown",
        "verdict": verdict, "evidence": evidence, "transcript": path,
        "usage": o["usage"], "result": o["result"],
    }

def locate_transcript(agent_id: str, project_dir: str = PROJECT_DIR):
    """Find a subagent transcript JSONL by its agentId (from the Task tool result)."""
    pats = [os.path.join(project_dir, "**", "subagents", f"agent-{agent_id}.jsonl")]
    for pat in pats:
        hits = [f for f in glob.glob(pat, recursive=True) if os.path.getsize(f) > 0]
        if hits:
            return max(hits, key=os.path.getmtime)
    return None

# --- the verdict ---------------------------------------------------------------------
def verify(requested: str, transcript: str = None, source: str = "claude",
           self_report: str = None, agent_id: str = None):
    if agent_id and not transcript:
        transcript = locate_transcript(agent_id)
    if source == "codex":
        return _verify_codex(requested, transcript, self_report)
    req_family, req_canon, req_tier = normalize(requested)

    served_counts = models_from_claude_transcript(transcript)
    served_ids = sorted(served_counts)
    served_norm = [normalize(m) for m in served_ids]

    evidence, downgraded, served_differs = [], False, False
    ground_truth = bool(served_ids)

    for raw, (fam, canon, tier) in zip(served_ids, served_norm):
        if req_canon and canon != req_canon:
            served_differs = True
            if req_tier is not None and tier is not None and tier < req_tier:
                downgraded = True
                evidence.append(f"served {raw} (tier {tier}) < requested {requested} (tier {req_tier}) = DOWNGRADE")
            elif fam and req_family and fam != req_family:
                evidence.append(f"served {raw} is a different family than requested {requested}")
            else:
                evidence.append(f"served {raw} differs from requested {requested}")

    sr_family, sr_canon, sr_tier = normalize(self_report) if self_report else (None, None, None)
    self_report_lie = bool(self_report and served_ids and sr_canon and
                           all(sr_canon != normalize(m)[1] for m in served_ids))
    if self_report_lie:
        evidence.append(f"self-report '{self_report}' contradicts served {served_ids} "
                        f"(the classic prompt-says-Fable / API-served-Opus lie)")

    err_blob = _claude_error_blob(transcript)
    warnings = sorted({kw for kw in _WARN if kw in err_blob})
    if warnings:
        evidence.append(f"fallback/limit phrases in error records: {warnings}")

    rate_limited = any(p in err_blob for p in ("reached your", "usage limit", "hit your usage",
                                               "rate limit", "try again at"))
    if not ground_truth:
        confidence = "unknown"     # no transcript model field -> cannot confirm either way
        if rate_limited:
            evidence.append("worker hit a usage/rate limit and produced no assistant turn - RATE_LIMITED, not a downgrade")
        else:
            evidence.append("no message.model found in transcript - cannot ground-truth; do NOT trust self-report")
    else:
        confidence = "certain"     # message.model is authoritative

    return {
        "requested": requested,
        "requested_canonical": req_canon,
        "served": served_ids,
        "served_counts": served_counts,
        "downgraded": downgraded,
        "served_differs": served_differs,
        "self_report": self_report,
        "self_report_lie": self_report_lie,
        "warnings": warnings,
        "ground_truth_available": ground_truth,
        "confidence": confidence,
        "verdict": ("DOWNGRADED" if downgraded else
                    "DIFFERENT_MODEL" if served_differs else
                    "OK" if ground_truth else
                    "RATE_LIMITED" if rate_limited else "UNVERIFIABLE"),
        "evidence": evidence,
        "transcript": transcript,
    }

# --- self-test on synthetic + this environment's real transcripts --------------------
def selftest():
    ok = True
    # synthetic: a transcript that served opus when fable was asked
    sp = os.path.join(os.environ.get("TEMP", "."), "_vm_synth.jsonl")
    with open(sp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8", "role": "assistant"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8", "role": "assistant"}}) + "\n")
    v = verify("fable", transcript=sp, self_report="claude-fable-5")
    assert v["downgraded"] and v["self_report_lie"] and v["confidence"] == "certain", v
    print("[selftest] synthetic downgrade + self-report-lie: PASS")

    v2 = verify("fable", transcript=sp.replace("_vm_synth", "_nope"))
    assert v2["verdict"] == "UNVERIFIABLE", v2
    print("[selftest] missing transcript -> UNVERIFIABLE: PASS")

    tmp = os.environ.get("TEMP", ".")
    cp = os.path.join(tmp, "_vm_codex_rl.jsonl")
    with open(cp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "error", "message": "You've hit your usage limit. try again at 2:02 PM."}) + "\n")
        f.write(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}) + "\n")
    vc = verify("gpt-5.6-sol", transcript=cp, source="codex")
    assert vc["verdict"] == "RATE_LIMITED" and not vc["downgraded"], vc
    print("[selftest] codex rate-limit -> RATE_LIMITED (not downgrade): PASS")

    cp2 = os.path.join(tmp, "_vm_codex_ns.jsonl")
    with open(cp2, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "item.completed", "item": {"type": "error",
                "message": "The 'gpt-5.6' model is not supported when using Codex with a ChatGPT account."}}) + "\n")
        f.write(json.dumps({"type": "turn.failed", "error": {"message": "not supported"}}) + "\n")
    vn = verify("gpt-5.6", transcript=cp2, source="codex")
    assert vn["verdict"] == "DOWNGRADED", vn
    print("[selftest] codex model-not-supported -> DOWNGRADED: PASS")

    # finding #1: a <synthetic> API-error record must NOT count as a served model
    sp3 = os.path.join(tmp, "_vm_synth_mixed.jsonl")
    with open(sp3, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-fable-5", "role": "assistant"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "isApiErrorMessage": True,
                            "message": {"model": "<synthetic>", "role": "assistant"}}) + "\n")
    vs = verify("fable", transcript=sp3)
    assert vs["verdict"] == "OK" and vs["served"] == ["claude-fable-5"], vs
    print("[selftest] synthetic API-error record ignored -> OK (finding #1): PASS")

    # finding #3: 'downgrade'/'rate limit' in the PROMPT/ANSWER text must not false-trigger
    sp4 = os.path.join(tmp, "_vm_contam.jsonl")
    with open(sp4, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user",
                "content": "discuss the downgrade and rate limit; try again at 5pm"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-fable-5", "role": "assistant",
                "content": [{"type": "text", "text": "a run served as opus is a downgrade"}]}}) + "\n")
    vp = verify("fable", transcript=sp4)
    assert vp["verdict"] == "OK" and not vp["warnings"], vp
    print("[selftest] 'downgrade/rate limit' in prompt/answer does not false-trigger (finding #3): PASS")

    # finding #2: codex completed on fallback metadata must be DOWNGRADED, not OK
    cp3 = os.path.join(tmp, "_vm_codex_fb.jsonl")
    with open(cp3, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "item.completed", "item": {"type": "error",
                "message": "Model metadata for X not found. Defaulting to fallback metadata."}}) + "\n")
        f.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n")
    vf = verify("gpt-5.6-sol", transcript=cp3, source="codex")
    assert vf["verdict"] == "DOWNGRADED", vf
    print("[selftest] codex completed-on-fallback -> DOWNGRADED (finding #2): PASS")

    # finding #7: a transient error followed by a successful completion must be OK
    cp4 = os.path.join(tmp, "_vm_codex_transient.jsonl")
    with open(cp4, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "error", "message": "stream hiccup, retrying"}) + "\n")
        f.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n")
    vt = verify("gpt-5.6-sol", transcript=cp4, source="codex")
    assert vt["verdict"] == "OK", vt
    print("[selftest] codex transient-error-then-complete -> OK (finding #7): PASS")

    # real: this environment's known cases (skip gracefully if absent)
    known = {
        "a4c7ec51e91c9eb28": ("fable", "DOWNGRADED"),   # story bible -> opus
        "afdcea20d030f6c50": ("fable", "DOWNGRADED"),   # build.py    -> opus
        "a883a20c958943b9b": ("fable", "OK"),           # explore     -> fable
    }
    for aid, (req, want) in known.items():
        t = locate_transcript(aid)
        if not t:
            print(f"[selftest] real {aid}: transcript absent, skipped")
            continue
        v = verify(req, transcript=t)
        status = "PASS" if v["verdict"] == want else "FAIL"
        if v["verdict"] != want:
            ok = False
        print(f"[selftest] real {aid}: requested {req} -> served {v['served']} -> {v['verdict']} (want {want}) {status}")
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return ok

def main():
    ap = argparse.ArgumentParser(description="Verify a delegated worker ran on the requested model.")
    ap.add_argument("--requested", help="requested model (alias ok: fable/opus/gpt-5.6)")
    ap.add_argument("--transcript", help="path to worker transcript JSONL")
    ap.add_argument("--agent-id", help="subagent agentId (locate transcript automatically)")
    ap.add_argument("--source", default="claude", choices=["claude", "codex"])
    ap.add_argument("--self-report", help="what the worker claimed its model was (optional)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not a.requested or (not a.transcript and not a.agent_id):
        ap.error("need --requested and (--transcript or --agent-id), or --selftest")
    v = verify(a.requested, transcript=a.transcript, source=a.source,
               self_report=a.self_report, agent_id=a.agent_id)
    print(json.dumps(v, indent=2))
    sys.exit(2 if v["downgraded"] else 0)

if __name__ == "__main__":
    main()
