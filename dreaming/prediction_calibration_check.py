#!/usr/bin/env python3
"""prediction_calibration_check.py - tell a bet what its own track record is.

WHY. Measured 2026-08-17 over 140 scored bets in PREDICTIONS.md, the brain's overconfidence is not
general. It is concentrated in OWNER-scope bets BELOW p=0.70:

    scope    band          n   stated  actual     gap
    owner    <0.50         7    0.429   0.000   -0.429
    owner    0.50-0.60    20    0.528   0.400   -0.128
    owner    0.60-0.70    11    0.618   0.364   -0.255
    owner    >=0.70       12    0.717   0.750   +0.033
    infra    0.50-0.60    22    0.536   0.545   +0.009
    infra    0.60-0.70    14    0.607   0.500   -0.107
    infra    >=0.70       13    0.758   0.769   +0.012
    result   all bands    37    0.541   0.514   -0.027

When the brain is CONFIDENT about the owner it is right. When it HEDGES about him it is close to
fantasising, and seven sub-0.50 owner bets went 0 for 7.

WHY A LINTER AND NOT A PLACEMENT HOOK. There is no code path that writes a bet. foresight.py only
READS the ledger; dream.py only mentions it inside a prompt telling the dreaming model to edit the
file; the scripts that have actually appended bets are one-off dream scratch files. The author is a
MODEL following prose, so the two things that can work are (a) this linter, run over open bets, and
(b) putting the table above into the instructions the bet-writer reads. A placement-time guard has
no host to live in.

FIRE WHEN   scope is owner and p < 0.70          (every sub-band measured at gap <= -0.128)
DO NOT FIRE scope is owner and p >= 0.70          (measured +0.033, the best-calibrated cell)
DO NOT FIRE scope is infra at any band            (overall +0.009)
DO NOT FIRE scope is result at any band           (overall -0.027, inside noise for n=37)
DO NOT FIRE a cell with fewer than MIN_N resolved bets - an unmeasured cell gets UNKNOWN, never a
            warning invented from a neighbouring cell.

Recomputes the table from the live ledger every run rather than trusting the constants above, so it
cannot keep warning from a record that has since changed. Usage:
    python prediction_calibration_check.py                 lint the OPEN bets
    python prediction_calibration_check.py --table         print the current calibration table
    python prediction_calibration_check.py --selftest      controls
Read-only.
"""
from __future__ import annotations

import io
import os
import re
import sys
from collections import defaultdict

LEDGER = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}",
                      "memory", "PREDICTIONS.md")

# BOTH resolution vocabularies. The ledger uses outcome:hit and also "RESOLVED HIT"; reading only
# the first silently drops entries, which is how a calibration loses part of its own denominator.
VALID = re.compile(r"\boutcome:\s*(hit|miss|void)\b", re.I)
ALT = re.compile(r"\bRESOLVED\s+(HIT|MISS|VOID)\b", re.I)
P_RE = re.compile(r"\bp:\s*([0-9.]+)")
SCOPE_RE = re.compile(r"\bscope:\s*([A-Za-z_]+)")
ID_RE = re.compile(r"- \[[ xX]\]\s*(\S+)")

BANDS = [(0.0, 0.50, "<0.50"), (0.50, 0.60, "0.50-0.60"),
         (0.60, 0.70, "0.60-0.70"), (0.70, 1.01, ">=0.70")]
MIN_N = 8          # below this a cell is UNKNOWN, not a verdict
GAP_WARN = -0.10   # stated minus actual worse than this is worth saying out loud


def band_of(p):
    for lo, hi, name in BANDS:
        if lo <= p < hi:
            return name
    return "?"


def parse(path=LEDGER):
    """Return (scored, open_bets, unscoreable)."""
    scored, open_bets, unscoreable = [], [], []
    for line in io.open(path, encoding="utf-8", errors="replace"):
        if not line.startswith("- ["):
            continue
        checked = line.startswith("- [x]") or line.startswith("- [X]")
        mp, ms, mid = P_RE.search(line), SCOPE_RE.search(line), ID_RE.match(line)
        m = VALID.search(line) or ALT.search(line)
        lab = m.group(1).lower() if m else None
        rec = {"id": mid.group(1) if mid else "?",
               "p": float(mp.group(1)) if mp else None,
               "scope": (ms.group(1).lower() if ms else "UNKNOWN")}
        if checked:
            if lab in ("hit", "miss") and rec["p"] is not None:
                scored.append((rec, 1 if lab == "hit" else 0))
            elif lab == "void":
                pass
            else:
                unscoreable.append(rec)          # resolved but not machine-readable
        elif rec["p"] is not None:
            open_bets.append(rec)
    return scored, open_bets, unscoreable


def table(scored):
    cells = defaultdict(list)
    for rec, y in scored:
        cells[(rec["scope"], band_of(rec["p"]))].append((rec["p"], y))
    out = {}
    for key, v in cells.items():
        mp = sum(p for p, _ in v) / len(v)
        hr = sum(y for _, y in v) / len(v)
        out[key] = {"n": len(v), "stated": mp, "actual": hr, "gap": hr - mp}
    return out


def verdict(cell):
    if cell is None or cell["n"] < MIN_N:
        return "UNKNOWN", ("no measured track record for this scope and confidence band "
                           "(fewer than %d resolved bets) - not a warning, an absence" % MIN_N)
    if cell["gap"] <= GAP_WARN:
        return "OVERCONFIDENT", ("this cell states %.3f and lands %.3f over %d resolved bets "
                                 "(gap %+.3f)" % (cell["stated"], cell["actual"],
                                                  cell["n"], cell["gap"]))
    return "OK", ("stated %.3f against %.3f over %d resolved bets (gap %+.3f)"
                  % (cell["stated"], cell["actual"], cell["n"], cell["gap"]))


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    scored, open_bets, unscoreable = parse()
    t = table(scored)

    if "--table" in args:
        print("%-10s %-11s %5s %8s %8s %8s" % ("scope", "band", "n", "stated", "actual", "gap"))
        for (sc, bd), c in sorted(t.items()):
            print("%-10s %-11s %5d %8.3f %8.3f %+8.3f" % (sc, bd, c["n"], c["stated"],
                                                          c["actual"], c["gap"]))
        return 0

    print("scored bets %d | open bets %d | RESOLVED BUT UNSCOREABLE %d"
          % (len(scored), len(open_bets), len(unscoreable)))
    if unscoreable:
        print("  UNKNOWN outcomes (excluded from every calibration, kept visible here): %s"
              % ", ".join(r["id"] for r in unscoreable))
    print()
    warned = 0
    for rec in sorted(open_bets, key=lambda r: r["id"]):
        v, why = verdict(t.get((rec["scope"], band_of(rec["p"]))))
        if v == "OK":
            continue
        warned += 1
        print("  %-6s scope=%-7s p=%.2f  %s" % (rec["id"], rec["scope"], rec["p"], v))
        print("         %s" % why)
    if not warned:
        print("  no open bet sits in a cell measured as overconfident.")
    return 0


def selftest():
    """Controls: the check must FIRE on the measured-bad cell and STAY SILENT on the good ones."""
    scored, _, _ = parse()
    t = table(scored)
    fails = []

    def show(label, key, expect):
        cell = t.get(key)
        v, why = verdict(cell)
        ok = (v == expect)
        if not ok:
            fails.append(label)
        print("  %-5s %-34s -> %-14s (%s)" % ("PASS" if ok else "FAIL", label, v, why[:70]))

    print("POSITIVE CONTROL - must fire:")
    show("owner p=0.65", ("owner", "0.60-0.70"), "OVERCONFIDENT")
    show("owner p=0.55", ("owner", "0.50-0.60"), "OVERCONFIDENT")
    print()
    print("NEGATIVE CONTROLS - must stay silent:")
    show("owner p=0.75 (best cell)", ("owner", ">=0.70"), "OK")
    show("infra p=0.55", ("infra", "0.50-0.60"), "OK")
    show("infra p=0.75", ("infra", ">=0.70"), "OK")
    print()
    print("UNKNOWN CONTROL - an unmeasured cell must not invent a verdict:")
    show("scope=nonsense p=0.55", ("nonsense", "0.50-0.60"), "UNKNOWN")
    print()
    if fails:
        print("SELFTEST FAILED: %s" % ", ".join(fails))
        return 1
    print("SELFTEST PASSED - fires on the measured cells, silent on the calibrated ones, UNKNOWN")
    print("where there is no record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
