#!/usr/bin/env python3
"""ern_calibration.py - does the felt error (error_monitor.py) cover the real failures, and does it slow us down?
(1000-ways shortlist #2, the ERN -> prediction -> calibration loop.)

outcomes.jsonl logs only the calls where the ERN fired, so it has no denominator: turning each line into a resolved
"no error expected, got one" bet in PREDICTIONS.md would add misses and never a hit, dragging the global base rate the
calibration shrinks every scope toward. This measures against a denominator instead. Every tool call in the Claude
transcripts is an implicit forecast that it works; the tool_result's is_error flag (a non-zero exit, a refused or
failed tool) is the outcome, and the PostToolUse hook attachment carrying the ERN text, joined by toolUseID, says
whether the felt error fired on that call. Reports:

  base        P(is_error) per call, by tool
  coverage    share of is_error calls the ERN fired on (recall) and share of ERN fires on is_error calls (precision)
  post-error  P(next call in the same session errors | this one errored) against | this one did not, and the errored
              calls split by whether the ERN fired - a lower next-call error rate after a fire is post-error slowing
              (descriptive only: the ERN fires on chosen signatures, so the split is not a randomized comparison)

usage: python3 ern_calibration.py [--since-hours N] [--tool Bash] [--json] [--ledger] [transcript.jsonl ...]
       --ledger appends one row per run to ern_calibration.tsv beside this file (for the dream to trend)
"""
import collections, glob, json, os, sys, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}")
ERN_TEXT = "that just felt wrong"


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def calls(path):
    """Main-chain tool calls of one transcript in order: dict(id, sess, t, tool, err, ern)."""
    order, by_id, ern = [], {}, set()
    for line in open(path, encoding="utf-8", errors="replace"):
        if '"tool_use' not in line and ERN_TEXT not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        # api-request-blob records (no timestamp) repeat earlier tool_use blocks under the same id; read here they
        # replaced the real call in by_id and 111 of 19,340 calls in 48 h lost their result (30 Oct)
        if d.get("isSidechain") or d.get("type") == "api-request-blob":
            continue
        a = d.get("attachment") or {}
        # failed calls come back on PostToolUseFailure (wired 30 Oct), ok ones on PostToolUse
        if a.get("hookEvent") in ("PostToolUse", "PostToolUseFailure") and ERN_TEXT in (a.get("stdout") or ""):
            ern.add(a.get("toolUseID"))
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                rec = dict(id=c.get("id"), sess=d.get("sessionId", ""), t=_ts(d.get("timestamp", "")),
                           tool=c.get("name", "?"), err=None, ern=False)
                by_id[rec["id"]] = rec
                order.append(rec)
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in by_id:
                by_id[c["tool_use_id"]]["err"] = bool(c.get("is_error"))
    for r in order:
        r["ern"] = r["id"] in ern
    return [r for r in order if r["err"] is not None and r["t"] is not None]


def measure(paths, tool=None, after=0.0):
    tot, err, fire, fire_err = collections.Counter(), collections.Counter(), 0, 0
    nxt = collections.Counter()   # (state of this call) -> [n, next errored]
    nxt_err = collections.Counter()
    n_calls = 0
    for p in paths:
        cs = [r for r in calls(p) if r["t"] >= after]
        for i, r in enumerate(cs):
            if tool and r["tool"] != tool:
                continue
            n_calls += 1
            tot[r["tool"]] += 1
            err[r["tool"]] += r["err"]
            fire += r["ern"]
            fire_err += r["ern"] and r["err"]
            if i + 1 < len(cs) and cs[i + 1]["sess"] == r["sess"]:
                state = ("error+ern" if r["ern"] else "error,no-ern") if r["err"] else ("ok+ern" if r["ern"] else "ok")
                nxt[state] += 1
                nxt_err[state] += cs[i + 1]["err"]
    n_err = sum(err.values())

    def rate(k, n):
        return round(k / n, 4) if n else None

    errored_next = nxt["error+ern"] + nxt["error,no-ern"]
    ok_next = nxt["ok"] + nxt["ok+ern"]
    return dict(transcripts=len(paths), calls=n_calls, errors=n_err, base_error_rate=rate(n_err, n_calls),
                by_tool={t: [n, err[t], rate(err[t], n)] for t, n in tot.most_common(6)},
                ern_fires=fire, ern_coverage=rate(fire_err, n_err), ern_precision=rate(fire_err, fire),
                next_error_after_error=rate(nxt_err["error+ern"] + nxt_err["error,no-ern"], errored_next),
                next_error_after_ok=rate(nxt_err["ok"] + nxt_err["ok+ern"], ok_next),
                next_by_state={s: [nxt[s], nxt_err[s], rate(nxt_err[s], nxt[s])] for s in sorted(nxt)})


if __name__ == "__main__":
    a = sys.argv[1:]
    since, tool, as_json, ledger = 48.0, None, "--json" in a, "--ledger" in a
    a = [x for x in a if x not in ("--json", "--ledger")]
    if "--since-hours" in a:
        i = a.index("--since-hours"); since = float(a[i + 1]); del a[i:i + 2]
    if "--tool" in a:
        i = a.index("--tool"); tool = a[i + 1]; del a[i:i + 2]
    after = 0.0 if a else time.time() - since * 3600
    paths = a or [p for p in glob.glob(os.path.join(PROJ, "*.jsonl")) if os.path.getmtime(p) > after]
    r = measure(paths, tool, after)
    if ledger:
        row = "\t".join(str(x) for x in (time.strftime("%Y-%m-%dT%H:%M:%S"), since, tool or "all", r["calls"], r["errors"],
                                         r["ern_fires"], r["ern_coverage"], r["ern_precision"],
                                         r["next_error_after_error"], r["next_error_after_ok"]))
        path = os.path.join(HERE, "ern_calibration.tsv")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write("utc_local\tsince_h\ttool\tcalls\terrors\tern_fires\tcoverage\tprecision\tnext_err_after_err\tnext_err_after_ok\n")
            f.write(row + "\n")
    if as_json:
        print(json.dumps(r, indent=1))
    else:
        print("%d calls in %d transcripts, %d errored (base %.1f%%)" % (r["calls"], r["transcripts"], r["errors"],
                                                                      100 * (r["base_error_rate"] or 0)))
        print("ERN fired %d times: coverage of real errors %s, precision %s" % (r["ern_fires"], r["ern_coverage"],
                                                                             r["ern_precision"]))
        print("next call errors: after an error %s, after an ok call %s" % (r["next_error_after_error"],
                                                                          r["next_error_after_ok"]))
        for s, v in r["next_by_state"].items():
            print("  %-13s n=%-6d next errored %-5d rate %s" % (s, v[0], v[1], v[2]))
