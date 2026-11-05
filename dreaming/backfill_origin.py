#!/usr/bin/env python3
"""backfill_origin.py - add origin / repeat_of / repeat_n to PROMPTS.jsonl, additively.

The live hook now stamps these (prompt_archive.py -> prompt_origin.stamp). This fills in history:
7,001 records - every live-hook record from 2026-07 onward - carried no `origin` at all, so
build_prompt_views.py's `origin == "automation"` branch was dead across the whole recent period,
and nothing could separate an ask he typed from a /loop cron re-fire of it.

Rules, deliberately conservative:
  - never change an existing field value; only ADD absent ones
  - never touch ts / session / cwd / source / text
  - a record that gains nothing is re-emitted BYTE-IDENTICAL (the ledger is CRLF and any
    re-serialisation of 18k records is 18k chances to corrupt one)
  - decode strict: errors='replace' would bake U+FFFD into records permanently
  - `count` (backfill-era: occurrences collapsed INTO one record) is a different quantity from
    repeat_n (separate records of the same ask). Do not add them together.
  - the ledger is live and append-only, so re-read the tail immediately before replacing and carry
    over anything a hook appended while this ran

Usage: python backfill_origin.py [--apply]   (default: dry run)
"""
import sys, os, io, json

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_origin as po

LED = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory/prompts/PROMPTS.jsonl")
APPLY = "--apply" in sys.argv


def main():
    size0 = os.path.getsize(LED)
    with io.open(LED, "rb") as f:
        raw = f.read(size0)
    text = raw.decode("utf-8", "strict")          # strict on purpose - see docstring
    parts = text.split("\n")                       # ONLY \n; keep any trailing \r per line

    recs = []                                      # (index, parsed_or_None, original_part)
    for i, p in enumerate(parts):
        body = p[:-1] if p.endswith("\r") else p
        if not body.strip():
            recs.append([i, None, p]); continue
        try:
            recs.append([i, json.loads(body), p])
        except Exception:
            recs.append([i, None, p])

    order = sorted((i for i, r, _ in recs if r and r.get("text")),
                   key=lambda i: recs[i][1].get("ts") or "9999")

    seen = {}
    add_origin = add_repeat = 0
    origin_added = {}
    changed = set()
    for i in order:
        r = recs[i][1]
        t = r.get("text", "")
        k = po.normkey(t)
        hit = seen.get(k)
        if "origin" not in r:
            o = "automation" if po.is_agent_authored(t) else "owner"
            r["origin"] = o
            origin_added[o] = origin_added.get(o, 0) + 1
            add_origin += 1
            changed.add(i)
        if hit and "repeat_of" not in r:
            r["repeat_of"] = hit[0]
            r["repeat_n"] = hit[1] + 1
            add_repeat += 1
            changed.add(i)
        if hit:
            hit[1] += 1
        else:
            seen[k] = [r.get("ts") or "", 1]

    print("lines                 : %d" % len(parts))
    print("json records          : %d" % sum(1 for _, r, _ in recs if r))
    print("origin added          : %d  %s" % (add_origin, origin_added))
    print("repeat_of added       : %d" % add_repeat)
    print("records rewritten     : %d (the rest stay byte-identical)" % len(changed))
    print("distinct asks (by key): %d" % len(seen))
    if not APPLY:
        print("\nDRY RUN - rerun with --apply to write")
        return

    out = []
    for i, r, p in recs:
        if i in changed:
            out.append(json.dumps(r, ensure_ascii=False) + ("\r" if p.endswith("\r") else ""))
        else:
            out.append(p)
    body = "\n".join(out)

    tail = b""
    size1 = os.path.getsize(LED)
    if size1 > size0:
        with io.open(LED, "rb") as f:
            f.seek(size0)
            tail = f.read()
        print("appended during run   : %d bytes - carried over verbatim" % len(tail))

    tmp = LED + ".tmp"
    with io.open(tmp, "wb") as f:
        f.write(body.encode("utf-8"))
        if tail:
            f.write(tail)
    os.replace(tmp, LED)
    print("wrote %s (%d bytes, was %d)" % (LED, os.path.getsize(LED), size0))


if __name__ == "__main__":
    main()
