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

A verdict may carry "scope": "heads" or "refires" (default "all"). The join is by text, and a loop
re-sends the owner's words unchanged, so one text can be both the prompt he typed (the head: a row
with no repeat_of) and hundreds of machine re-fires (rows with repeat_of). Without a scope a single
verdict labels every copy alike: measured 2026-09-23, 86 re-fired owner texts had the typed head
classed "automation" with its re-fires, which moved his loop orders out of the action-class views.
A text may carry one verdict per scope; a row takes the verdict for its own part first, then "all".

Usage: python apply_prompt_classes.py <sidecar.jsonl>
"""
import os, io, sys, json, re, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prompt_archive as pa


def _key(text):
    return hashlib.sha1(re.sub(r"\s+", " ", (text or "").lower()).encode("utf-8", "replace")).hexdigest()


SCOPES = ("all", "heads", "refires")


def _pick(scoped, r):
    """The verdict for this row: the one for its own part (head or re-fire) if the text has one, else "all"."""
    if not scoped:
        return None
    part = "refires" if r.get("repeat_of") else "heads"
    return scoped.get(part) or scoped.get("all")


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
    bad_scope = 0
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
            scope = v.get("scope") or "all"
            if scope not in SCOPES:
                bad_scope += 1
                continue
            if v.get("sha1") and (has_class or has_correction):
                verdicts.setdefault(v["sha1"], {})[scope] = v
        except Exception:
            continue
    if bad_scope:
        print("skipped %d verdicts with an unknown scope (allowed: %s)" % (bad_scope, ", ".join(SCOPES)))
    if not os.path.exists(pa.LEDGER):
        print("no ledger")
        return 1
    raw = io.open(pa.LEDGER, encoding="utf-8", errors="replace").read()
    n_before = raw.count("\n")
    out, merged, seen_keys, corrections, origins = [], 0, 0, 0, 0
    # Split on "\n" ONLY (text mode has already folded CRLF). splitlines() also breaks at U+2028,
    # U+2029 and U+0085, which json.dumps(ensure_ascii=False) writes raw inside a string: a run after
    # 09-23 00:12 cut one pasted prompt of 09-21 16:05, stored twice, into 58 fragments that this loop
    # kept verbatim and rejoined with newlines, so every reader skipped it (found and restored 09-23 13:27).
    for ln in raw.split("\n"):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:
            out.append(ln)
            continue
        v = _pick(verdicts.get(_key(r.get("text", ""))), r)
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
            # ORIGIN TRAVELS INDEPENDENTLY TOO (2026-09-23): the block above moves origin only when the class
            # changes, so a row the nightly pass had already classed "automation" kept origin "owner" - and
            # _is_owner reads origin first, so 7 headless picture-worker prompts still counted as his asks.
            if v.get("origin") in ("owner", "automation") and r.get("origin") != v["origin"]:
                r["origin"] = v["origin"]
                origins += 1
        out.append(pa.dumps_line(r))
    tmp = pa.LEDGER + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write("\n".join(out) + "\n")
    # tail-race guard: carry over lines the live hook appended while we rewrote. The slice index is a
    # count of "\n", so the split must be on "\n" too - splitlines() found more lines than that count
    # whenever a record held U+2028, and would have re-appended old lines as "fresh".
    raw2 = io.open(pa.LEDGER, encoding="utf-8", errors="replace").read()
    if raw2.count("\n") > n_before:
        tail = raw2.split("\n")[n_before:]
        with io.open(tmp, "a", encoding="utf-8") as f:
            for ln in tail:
                if ln.strip():
                    f.write(ln + "\n")
        print("tail-race guard: carried %d fresh hook lines" % len(tail))
    os.replace(tmp, pa.LEDGER)
    print("merged %d new classifications, %d correction flags, %d origin fixes (%d verdicts matched, %d supplied)"
          % (merged, corrections, origins, seen_keys, sum(len(s) for s in verdicts.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
