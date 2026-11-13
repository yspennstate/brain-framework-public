"""lemma_fidelity_check.py - does each 10 Aug campaign lemma say what its cited source line says? (account01/B07,
B07-R60-R40-SOURCE-CHECK-CONFLATES-CORRECTNESS-AND-FIDELITY; 28 Sep, claude-825b1305)

Source fidelity and mathematical truth are separate questions; this answers only the first, by machine, where it can.
For every lemma of the batch, each equation or inequality in its statement (a window of 14 characters either side of each
"=", "<=", ">=", normalised: whitespace removed, the argument lists of Var(...) and Cov(...) dropped, unicode folded) must
occur in the normalised text of the source lines the lemma is cited to. A window found = that equation is in the source;
none found = no machine match, read by hand. It says nothing about whether the source is right: the l^p lemma is faithful
to a log line that carries the same error.

    python lemma_fidelity_check.py STORE OUT_JSON
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import io
import json
import os
import re
import sqlite3
import sys
import unicodedata

CAMP = os.path.expanduser("~/brain-repo/06_math_research/probability_open_problem_campaign_2026_08_09")
SOURCES = {  # lemma id prefix -> [(file, first line, last line)], as the source lookup found them (LEMMA_SOURCES_LOOKUP.md)
    "claim_32cba36e9ea9": [("LOG.md", 1076, 1076), ("LOG.md", 1091, 1091)],
    "claim_8a7b59a80537": [("LOG.md", 1070, 1070)],
    "claim_a12c2b183a67": [("LOG.md", 1060, 1060)],
    "claim_a20ea460b938": [("LOG.md", 730, 730), ("LOG.md", 954, 954), (r"problem\localization_tail_draft_iter106.md", 105, 118)],
    "claim_c87bc326d20b": [(r"problem\REFEREE2_walk_report.md", 187, 212)],
    "claim_c9d0bb8354eb": [("LOG.md", 954, 954)],
    "claim_d1dfdac81ad6": [("LOG.md", 1038, 1040)],
    "claim_d955f566019b": [(r"problem\REFEREE2_walk_report.md", 72, 95), ("LOG.md", 1033, 1033)],
    "claim_edd981dfa102": [(r"problem\MG2B_framework_lemma_draft.md", 73, 75), ("LOG.md", 954, 954), ("LOG.md", 960, 960)],
}


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("\u2212", "-").replace("\u2264", "<=").replace("\u2265", ">=")
    s = re.sub(r"\s+", "", s)
    return re.sub(r"(Var|Cov)\([^()]*\)", r"\1", s)


def windows(stmt, w=14):
    s = norm(stmt)
    out = []
    for m in re.finditer(r"<=|>=|=", s):
        a, b = max(0, m.start() - w), min(len(s), m.end() + w)
        out.append(s[a:b])
    return out


def source_text(prefix):
    parts = []
    for f, a, b in SOURCES.get(prefix, []):
        lines = io.open(os.path.join(CAMP, f), encoding="utf-8").read().splitlines()
        parts.append(" ".join(lines[a - 1:b]))
    return norm(" ".join(parts))


def check(stmt, prefix):
    src = source_text(prefix)
    ws = windows(stmt)
    # a window counts when some 10-character core of it (around the relation) occurs in the source
    found = []
    for w in ws:
        k = w.find("=")
        core = w[max(0, k - 8):k + 9]
        found.append(bool(core) and core in src)
    return {"equations": len(ws), "found_in_source": sum(found), "windows": ws, "found": found}


def main():
    store, out = sys.argv[1], sys.argv[2]
    db = sqlite3.connect("file:%s?mode=ro" % store.replace("\\", "/"), uri=True)
    res = []
    for pre in SOURCES:
        row = db.execute("SELECT id, payload_json FROM claims WHERE id LIKE ?", (pre + "%",)).fetchone()
        st = json.loads(row[1])["statement"]
        r = check(st, pre)
        r.update({"id": row[0]})
        res.append(r)
        print("%s  equations %d, in the source %d" % (pre, r["equations"], r["found_in_source"]))
    json.dump({"store": store, "rows": res}, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
