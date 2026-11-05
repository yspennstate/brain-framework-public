#!/usr/bin/env python3
"""repair_ledger_mojibake.py - un-mangle cp1252 double-encoding already stored in PROMPTS.jsonl.

775 of 18,629 records (4.16%) carried it on 2026-08-22 - 685 through the live hook, 592 in
2026-08. Confirmed on three independent readers before repair: raw bytes, Python explicit utf-8,
and PowerShell -Encoding UTF8 all show U+00E2 U+20AC U+201D where a single U+2014 belongs.
prompt_archive.py is not the cause (fed a real em-dash it stores a real em-dash) and now repairs
on the way in; this fixes what is already on disk.

Only provably-lossless repairs are applied (see mojibake.repair): the fixed text must re-mangle
back to exactly the stored text, and must contain strictly fewer markers. Records that gain
nothing are re-emitted byte-identical. Repaired records are stamped `mojibake_repaired: true` so
the edit is auditable rather than silent.

Usage: python repair_ledger_mojibake.py [--apply]   (default: dry run)
"""
import sys, os, io, json, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mojibake as mj

LED = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory/prompts/PROMPTS.jsonl")
APPLY = "--apply" in sys.argv


def main():
    size0 = os.path.getsize(LED)
    with io.open(LED, "rb") as f:
        raw = f.read(size0)
    parts = raw.decode("utf-8", "strict").split("\n")

    out, fixed, skipped = [], 0, 0
    bymonth = collections.Counter()
    samples = []
    for p in parts:
        body = p[:-1] if p.endswith("\r") else p
        if not body.strip():
            out.append(p); continue
        try:
            r = json.loads(body)
        except Exception:
            out.append(p); continue
        t = r.get("text") or ""
        if not mj.looks_mangled(t):
            out.append(p); continue
        n = mj.repair_deep(t)
        if n == t:
            skipped += 1; out.append(p); continue
        if len(samples) < 5:
            i = max(0, mj._MARKERS.search(t).start() - 22)
            samples.append((r.get("ts", "?")[:16], t[i:i + 40], n[i:i + 40]))
        r["text"] = n
        r["mojibake_repaired"] = True
        fixed += 1
        bymonth[r.get("ts", "?")[:7]] += 1
        out.append(json.dumps(r, ensure_ascii=False) + ("\r" if p.endswith("\r") else ""))

    print("records repaired      : %d" % fixed)
    print("mangled but UNSAFE    : %d (left exactly as found)" % skipped)
    print("by month              :", sorted(bymonth.items()))
    for ts, a, b in samples:
        print("  %s  %r -> %r" % (ts, a, b))
    if not APPLY:
        print("\nDRY RUN - rerun with --apply to write")
        return

    body = "\n".join(out)
    tail = b""
    if os.path.getsize(LED) > size0:
        with io.open(LED, "rb") as f:
            f.seek(size0); tail = f.read()
        print("appended during run   : %d bytes - carried over verbatim" % len(tail))
    tmp = LED + ".tmp"
    with io.open(tmp, "wb") as f:
        f.write(body.encode("utf-8"))
        if tail:
            f.write(tail)
    os.replace(tmp, LED)
    print("wrote %s (%d bytes, was %d)" % (LED, os.path.getsize(LED), size0))


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__); raise SystemExit(0)
    main()
