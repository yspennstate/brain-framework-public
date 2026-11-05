#!/usr/bin/env python3
"""apply_prompt_classes.py - fold model classifications into the prompt ledger.

The classifier (a Fable subagent or the nightly dream) writes a SIDECAR jsonl - one line per
verdict: {"sha1": <hash of normalized text>, "class_model": <taxonomy>, "confidence": 0-1,
"origin": "owner"|"automation" (optional fix), "correction_present": true|false (optional, and a
SEPARATE judgement from the class)}. It never touches the ledger itself (single-writer
discipline).

correction_present answers a different question from class_model and is carried independently: a
correction can sit inside a prompt whose action class is run, improve or prove, and the contract
(section 4.1) requires the two to be decided apart. A sidecar line may therefore carry only the
correction verdict, with no class at all. Before 2026-09-15 this script dropped such a line at
parse time and ignored the field even when supplied, which is why the flag was set on 12 of 34,373
ledger records and correction detection had collapsed onto class_model == 'mistake'. This script joins the sidecar onto memory/prompts/PROMPTS.jsonl by recomputing the
same normalized-text hash, and rewrites the ledger ONCE, atomically, carrying over any lines the
live hook appended while we worked (the tail-race guard).

Usage: python apply_prompt_classes.py <sidecar.jsonl>
"""
import os, io, sys, json, re, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prompt_archive as pa


def _key(text):
    return hashlib.sha1(re.sub(r"\s+", " ", (text or "").lower()).encode("utf-8", "replace")).hexdigest()


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    if len(sys.argv) < 2:
        print("usage: apply_prompt_classes.py <sidecar.jsonl>")
        return 1
    verdicts = {}
    for ln in io.open(sys.argv[1], encoding="utf-8", errors="replace"):
        try:
            v = json.loads(ln)
            # A verdict is usable if it carries a class OR an explicit correction judgement.
            # The second is the point of contract section 4.1: correction presence is a SEPARATE
            # question from the action class, so a sidecar line may answer only that one - "this
            # prompt is still 'run', and it is also a correction" - and must not be discarded for
            # lacking a class it was never asked for.
            has_class = v.get("class_model") in pa.TAXONOMY + ["automation"]
            has_correction = isinstance(v.get("correction_present"), bool)
            if v.get("sha1") and (has_class or has_correction):
                verdicts[v["sha1"]] = v
        except Exception:
            continue
    if not os.path.exists(pa.LEDGER):
        print("no ledger")
        return 1
    raw = io.open(pa.LEDGER, encoding="utf-8", errors="replace").read()
    n_before = raw.count("\n")
    out, merged, seen_keys, corrections = [], 0, 0, 0
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:
            out.append(ln)
            continue
        v = verdicts.get(_key(r.get("text", "")))
        if v:
            seen_keys += 1
            if v.get("class_model") and r.get("class_model") != v["class_model"]:
                r["class_model"] = v["class_model"]
                r["class_conf"] = v.get("confidence")
                if v.get("origin"):
                    r["origin"] = v["origin"]
                merged += 1
            # CORRECTION PRESENCE TRAVELS INDEPENDENTLY OF THE CLASS (2026-09-15, contract 4.1:
            # "Separate correction_present from a prompt's ordinary task classification. A
            # correction can appear inside a prompt classified as work, research, or improvement.")
            # Two reasons this sits outside the block above rather than inside it. First, nothing
            # carried this field at all: measured over the whole ledger it was set on 12 of 34,373
            # records, so `explicit_correction_candidate` fell through to class_model == 'mistake'
            # for 99.97 pct of the corpus - the exact coupling the contract forbids. Second, the
            # class comparison above SKIPS a verdict that agrees, so a sidecar saying "still run,
            # and it is a correction" would have been dropped for agreeing about the class.
            if isinstance(v.get("correction_present"), bool):
                if r.get("correction_present") != v["correction_present"]:
                    r["correction_present"] = v["correction_present"]
                    corrections += 1
        out.append(json.dumps(r, ensure_ascii=False))
    tmp = pa.LEDGER + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write("\n".join(out) + "\n")
    # tail-race guard: carry over lines the live hook appended while we rewrote
    raw2 = io.open(pa.LEDGER, encoding="utf-8", errors="replace").read()
    if raw2.count("\n") > n_before:
        tail = raw2.splitlines()[n_before:]
        with io.open(tmp, "a", encoding="utf-8") as f:
            for ln in tail:
                if ln.strip():
                    f.write(ln + "\n")
        print("tail-race guard: carried %d fresh hook lines" % len(tail))
    os.replace(tmp, pa.LEDGER)
    print("merged %d new classifications, %d correction flags (%d verdicts matched, %d supplied)"
          % (merged, corrections, seen_keys, len(verdicts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
