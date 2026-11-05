"""Bind each row-level source decision to the exact row content it judged (28 Sep, claude-825b1305; scheduled audit
account03/B07 R40, B07-R40-SOURCE-VERDICT-NOT-ENFORCED-BY-APPLY-001).

The source check of the 10 Aug batch judged 75 rows by reading their text against their sources, but its decisions
named rows only by table and id, so nothing tied a decision to the content that was read. This adds row_sha256 to every
decision: the sha256 of the row as stored, taken from the live store and accepted only when the row is byte-identical to
its first record (the backup named in the decision, the one the first-record check found minutes after the write). A
decision whose row cannot be matched that way gets no hash, and the re-seal refuses to apply over it.

    python bind_source_decisions.py DECISIONS.json POPULATION_DIR OUT.json
Only reads the store and the backups (read-only connections). Prints OUT's sha256, the value the re-seal's
--decisions-sha256 must name.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import hashlib
import io
import json
import os
import sqlite3
import sys


def ro(p):
    c = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row
    return c


def row_sha256(conn, table, rid):
    """The row as stored, every column, in column order: the value the re-seal compares at apply time."""
    r = conn.execute("SELECT * FROM %s WHERE id = ?" % table, (rid,)).fetchone()
    if r is None:
        return None
    return hashlib.sha256(json.dumps([[k, r[k]] for k in r.keys()], ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def main():
    dec_path, pop, out = sys.argv[1:4]
    raw = io.open(dec_path, "rb").read()
    d = json.loads(raw.decode("utf-8"))
    live = ro(os.path.join(pop, "brain.sqlite3"))
    backups, bound, unbound = {}, 0, []
    for x in d["decisions"]:
        name = (x.get("first_record") or "").split(" (")[0].strip()
        path = os.path.join(pop, name) if name else ""
        now = row_sha256(live, x["table"], x["id"])
        first = None
        if path and os.path.exists(path):
            if path not in backups:
                backups[path] = ro(path)
            first = row_sha256(backups[path], x["table"], x["id"])
        if now is not None and now == first:
            x["row_sha256"] = now
            x["row_bound_to"] = "the live store and %s, byte-identical" % name
            bound += 1
        else:
            x["row_sha256"] = None
            x["row_bound_to"] = "not bound: %s" % ("row absent from the store" if now is None else
                                                    "no first-record backup" if first is None else
                                                    "the store differs from the first record")
            unbound.append([x["table"], x["id"]])
    for c in backups.values():
        c.close()
    live.close()
    d["bound"] = {"source_file": os.path.basename(dec_path), "source_sha256": hashlib.sha256(raw).hexdigest(),
                  "bound_rows": bound, "unbound_rows": unbound,
                  "hash": "sha256 of json.dumps([[column, value], ...] in column order, ensure_ascii=False, default=str)",
                  "made": "28 Sep 2026, claude-825b1305 (bind_source_decisions.py)"}
    blob = json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8")
    tmp = out + ".tmp"
    with io.open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, out)
    print("bound %d of %d decisions; unbound %d" % (bound, len(d["decisions"]), len(unbound)))
    print("sha256", hashlib.sha256(blob).hexdigest())


if __name__ == "__main__":
    main()
