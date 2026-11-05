"""Horizon profile of post-fill signed mid move (validation of the rung-reversion claim).
For each fill, signed earn vs the nearest validated-quote mid in horizon windows after the
fill: [2,8]s [8,20]s [20,60]s [60,180]s. Real reversion should show a coherent profile
(Jul 6 positive, evolving smoothly); noise jumps around. Same parsing/filters as
kappa_exec_spread.py (execId dedup + replay-burst drop).
"""
import re, glob, os, sys
from datetime import datetime, timedelta
from statistics import median
from collections import defaultdict

S = os.path.dirname(os.path.abspath(__file__))
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
WINDOWS = [(2, 8), (8, 20), (20, 60), (60, 180)]

fill_re = re.compile(
    r"^(" + DATE + r" \d\d:\d\d:\d\d),(\d+) \| INFO \| Execution: symbol=(\S+) .*?execId=(\S+) .*?side=(\S+) shares=([\d.]+) price=([\d.]+)")
quote_re = re.compile(
    r"^(" + DATE + r" \d\d:\d\d:\d\d),(\d+) \| WARNING \| \[(\w+)\] .*?validated quote bid=([\d.]+) ask=([\d.]+) age=([\d.]+)s")

def pt(d, ms):
    return datetime.strptime(d, "%Y-%m-%d %H:%M:%S") + timedelta(milliseconds=int(ms))

fills, seen, quotes = [], set(), {}
for f in sorted(glob.glob(os.path.join(S, "fills_acct*.txt"))):
    acct = "acct" + re.search(r"acct(\d)", f).group(1)
    for line in open(f, encoding="utf-8", errors="replace"):
        m = fill_re.match(line)
        if m:
            d, ms, sym, eid, side, qty, px = m.groups()
            if eid in seen: continue
            seen.add(eid)
            fills.append((pt(d, ms), acct, sym, side, float(px)))
for f in sorted(glob.glob(os.path.join(S, "quotes_acct*.txt"))):
    acct = "acct" + re.search(r"acct(\d)", f).group(1)
    for line in open(f, encoding="utf-8", errors="replace"):
        m = quote_re.match(line)
        if m:
            d, ms, sym, bid, ask, age = m.groups()
            b, a = float(bid), float(ask)
            if a <= b or b <= 0: continue
            quotes.setdefault((acct, sym), []).append((pt(d, ms) - timedelta(seconds=float(age)), (a + b) / 2))
for k in quotes: quotes[k].sort()

by_sec = defaultdict(list)
for t, acct, sym, side, px in fills:
    by_sec[(acct, t.replace(microsecond=0))].append(px)
replay = {k for k, v in by_sec.items() if len(v) >= 3 and (max(v) - min(v)) / min(v) > 0.001}
fills = [f for f in fills if (f[1], f[0].replace(microsecond=0)) not in replay and f[2] == "<ticker>"]

print(f"{DATE}: fills={len(fills)} (<ticker>, deduped, replay-filtered)")
for lo, hi in WINDOWS:
    earns = []
    for t, acct, sym, side, px in fills:
        best = None
        for ts, mid in quotes.get((acct, sym), []):
            dt = (ts - t).total_seconds()
            if lo <= dt <= hi and (best is None or dt < best[0]):
                best = (dt, mid)
        if best:
            mid = best[1]
            earns.append((px - mid) / mid * 1e4 if side == "SLD" else (mid - px) / mid * 1e4)
    rth = None  # combined; small n per window if split
    if earns:
        print(f"  [{lo:3d},{hi:3d}]s: n={len(earns):3d} median={median(earns):+5.1f}bp")
    else:
        print(f"  [{lo:3d},{hi:3d}]s: n=0")
