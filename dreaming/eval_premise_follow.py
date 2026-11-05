#!/usr/bin/env python3
"""eval_premise_follow.py - does a premise-check fire CHANGE behavior, or just fire?

Scores PREDICTIONS p31: when the read-path premise-check tells a session "the memory you just read
was superseded by X", does the session go on to READ X? A fire without a follow is the p9-family
failure (a pointer alone doesn't pay the reading cost); a followed fire is the mechanism working.

Join: surfaced.tsv rows tagged 'premise' (ts, session12, correction_slug - written by spread_recall
at fire time, real uuid sessions only) against retrievals.tsv (the memory-read trace). FOLLOW = the
same session reads the correction within FOLLOW_WINDOW of the fire. Read-only; zero-state honest.
Usage: python eval_premise_follow.py
"""
import io
import os

DR = os.path.dirname(os.path.abspath(__file__))
FOLLOW_WINDOW = 3600.0   # seconds; a same-session read an hour later still counts as acting on it


def main():
    fires = []
    try:
        with io.open(os.path.join(DR, "surfaced.tsv"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 4 and p[3] == "premise":
                    try:
                        fires.append((float(p[0]), p[1], p[2]))
                    except Exception:
                        pass
    except Exception:
        pass
    if not fires:
        print("eval_premise_follow: no live premise fires logged yet (surfaced.tsv has no 'premise' "
              "rows from uuid sessions) - p31 pending data. Fixture fires never log, by design.")
        return

    reads = []   # (epoch, slug, session)
    try:
        with io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 4:
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        reads.append((float(p[1]), slug, p[3]))
                    except Exception:
                        pass
    except Exception:
        pass

    followed = 0
    print("eval_premise_follow: %d live premise fire(s)" % len(fires))
    for ts, sess12, corr in fires:
        hit = any(r_sess.startswith(sess12) and r_slug == corr and ts <= r_ts <= ts + FOLLOW_WINDOW
                  for r_ts, r_slug, r_sess in reads)
        followed += bool(hit)
        print("  %s  session %s  correction %-55s %s"
              % (int(ts), sess12, corr[:55], "FOLLOWED" if hit else "not followed"))
    print("follow-rate: %d/%d  (p31: needs >=1 followed fire by 2026-07-17 to score a hit)"
          % (followed, len(fires)))


if __name__ == "__main__":
    main()
