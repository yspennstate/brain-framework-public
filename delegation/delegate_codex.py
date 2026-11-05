#!/usr/bin/env python3
"""Spawn a usage-frugal GPT-5.6 (Codex) worker, verify it wasn't downgraded, log it.

This is the cross-runtime half of the delegation methodology: a Claude (or any) parent
delegates a well-scoped unit of work to a fresh Codex worker via `codex exec`, which runs
ephemeral and lean (no brain wake, its own minimal context), returns a compact result, and
is then discarded. The parent verifies the served model (Codex hard-fails on a wrong model,
and warns "defaulting to fallback metadata" on a soft degrade), logs the outcome to the
ledger, then checks the work and implements/fixes it itself.

Account note (measured 2026-07-11): a ChatGPT-account Codex rejects `gpt-5.6` with HTTP 400;
the account's real model is `gpt-5.6-sol` (config.toml default, reasoning effort "ultra").
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_model as vm            # noqa: E402
from ledger import log_delegation    # noqa: E402

DEFAULT_MODEL = "gpt-5.6-sol"        # the account's real Codex model (not "gpt-5.6")

def run_codex_worker(prompt: str, model: str = DEFAULT_MODEL, task_type: str = "other",
                     cwd: str = None, timeout: int = 600, task_id: str = None,
                     raw_dir: str = None, log: bool = True):
    exe = shutil.which("codex")
    if not exe:
        raise RuntimeError("codex CLI not on PATH")
    # a clean, empty working root keeps the worker lean (no heavy AGENTS.md pulled in)
    cwd = cwd or tempfile.mkdtemp(prefix="codex_worker_")
    raw_dir = raw_dir or cwd
    task_id = task_id or f"cx{int(time.time())}"
    raw_path = os.path.join(raw_dir, f"{task_id}.jsonl")
    errf = os.path.join(raw_dir, f"{task_id}.err")

    cmd = [exe, "exec", "-m", model, "--json", "--skip-git-repo-check", "-C", cwd, "-"]
    t0 = time.time()
    with open(raw_path, "w", encoding="utf-8") as out, open(errf, "w", encoding="utf-8") as err:
        try:
            subprocess.run(cmd, input=prompt, stdout=out, stderr=err, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            with open(errf, "a", encoding="utf-8") as e:
                e.write(f"\n[delegate_codex] TIMEOUT after {timeout}s\n")
    dur_ms = int((time.time() - t0) * 1000)

    verdict = vm.verify(model, transcript=raw_path, source="codex")
    usage = verdict.get("usage") or {}
    rec = None
    if log:
        rec = log_delegation(
            task_type=task_type, worker="gpt-5.6", requested_model=model,
            served_model=(model if verdict["verdict"] == "OK" else None),
            downgraded=verdict["downgraded"], verdict=verdict["verdict"],
            tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0),
            duration_ms=dur_ms, notes="codex worker", task_id=task_id)
    return {
        "task_id": task_id, "ok": verdict["verdict"] == "OK",
        "verdict": verdict["verdict"], "downgraded": verdict["downgraded"],
        "result": verdict.get("result", ""), "usage": usage, "duration_ms": dur_ms,
        "evidence": verdict["evidence"], "raw_path": raw_path, "ledger": rec,
    }

def selftest():
    print("[delegate_codex] tiny live task on", DEFAULT_MODEL, "...")
    r = run_codex_worker("Reply with exactly the two words: WORKER READY",
                         task_type="other", timeout=120, log=False)
    print(json.dumps({k: r[k] for k in ("ok", "verdict", "downgraded", "result", "usage", "duration_ms")}, indent=2))
    ok = r["verdict"] in ("OK", "DOWNGRADED")   # both are *verified* outcomes; UNVERIFIABLE would be the bug
    print("SELFTEST:", "PASS (verifiable outcome)" if ok else "FAIL (unverifiable)")
    return ok

def main():
    ap = argparse.ArgumentParser(description="Spawn + verify a GPT-5.6 (Codex) worker.")
    ap.add_argument("--prompt"); ap.add_argument("--prompt-file")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--task-type", default="other")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    prompt = a.prompt or (open(a.prompt_file, encoding="utf-8").read() if a.prompt_file else None)
    if not prompt:
        ap.error("need --prompt, --prompt-file, or --selftest")
    r = run_codex_worker(prompt, model=a.model, task_type=a.task_type, timeout=a.timeout)
    print(json.dumps(r, indent=2))

if __name__ == "__main__":
    main()
