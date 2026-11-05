"""Shadow eval: grade any claim-guard against a mined green_not_trusted corpus.

Iteration 4 mined green_claim_corpus.jsonl - 14 owner corrections of the
"you claimed X was live/done and it was not" family, each row carrying the
assistant turn the correction interrupted plus the verdict every guard returned
on it at mining time. Iteration 6 made this a STANDING shadow eval:

  * --corpus PATH grades against any corpus (default: the frozen K=1 baseline, so
    the existing regression still runs unchanged). Point it at
    green_claim_corpus_wideK.jsonl to grade against the widened-window mine.
  * Any guard is graded by ONE command: register it in GUARD_REGISTRY (one line)
    and every run grades it against whatever corpus --corpus names. Imports are
    defensive - a broken guard is reported, never fatal, and never blocks.
  * --regression makes green_act_guard drift from its recorded baseline a LOUD
    nonzero exit (the shipped result is 0/14; any deviation - guard edit or corpus
    edit - fails the run). Default (no flag) is informational, exit 0.
  * If the corpus carries window_any_* fields (the wideK mine), the any-in-window
    rolling-guard union is reported alongside the selected-turn union.
  * Writes nothing except an optional --report FILE (JSON summary).

A guard that scores well here is necessary, never sufficient: these are the
corrections whose CLAIM SURFACE is in the captured turn. Iteration 4 put the
single-turn ceiling at 7/14; iteration 6's widened mine lifts the rolling-window
ceiling to 8/14 (one genuine capture, interday_graphs via reality_monitor) and
green_act_guard to 1/14 in-window (on old_anchor, already union-covered). The
remaining 6/14 are input/act-side, reachable by no text guard - that ceiling is
the measurement's point, not a defect of the guards.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
BASELINE_CORPUS = HERE / "green_claim_corpus.jsonl"   # frozen K=1

# To shadow-grade a NEW guard, add ONE line: (label, module, attr-or-attrs). The
# attribute's (text)->dict-with-'fired' or ->bool result is the fire verdict. This
# is the "one command grades any guard" wiring - nothing else needs editing.
GUARD_REGISTRY = [
    ("green_act_guard", "green_act_guard", "check"),
    # reality_monitor's live surface is its CLAIM_RE Pattern (the same regex the
    # corpus recorded via green_act_guard._RM_CLAIM), not a check() function.
    ("reality_monitor", "reality_monitor", ("CLAIM_RE", "check", "fire", "evaluate", "scan")),
]

# Recorded field per guard (verdict stored at mining time), for drift detection.
RECORDED_FIELD = {"green_act_guard": "guard_fired", "reality_monitor": "reality_monitor"}


def load_corpus(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"corpus is empty: {path}")
    return rows


def _fired_from(result) -> bool:
    return result.get("fired") if isinstance(result, dict) else bool(result)


def guard_callables() -> dict:
    """Build {name: fn(text)->bool} from GUARD_REGISTRY. Import/lookup failures are
    recorded as ('IMPORT_FAIL', exc) - reported, never fatal."""
    guards: dict = {}
    for label, modname, attrs in GUARD_REGISTRY:
        try:
            mod = __import__(modname)
            attr_list = (attrs,) if isinstance(attrs, str) else tuple(attrs)
            resolved = next((getattr(mod, a) for a in attr_list
                             if callable(getattr(mod, a, None))
                             or isinstance(getattr(mod, a, None), re.Pattern)), None)
            if resolved is None:
                guards[label] = ("IMPORT_FAIL", AttributeError(f"no callable/Pattern in {attr_list}"))
            elif isinstance(resolved, re.Pattern):
                guards[label] = (lambda t, p=resolved: bool(p.search(t or "")))
            else:
                guards[label] = (lambda t, fn=resolved: _fired_from(fn(t)))
        except Exception as exc:  # pragma: no cover - environment dependent
            guards[label] = ("IMPORT_FAIL", exc)
    return guards


def recorded(row: dict, field: str) -> bool:
    return str(row.get(field, "")).strip().lower() == "true"


def live_green_act_fires(rows: list[dict]) -> int:
    """Live green_act_guard fire count on the corpus turns (regression subject)."""
    import green_act_guard
    return sum(bool(green_act_guard.check(r.get("turn", "")).get("fired")) for r in rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Shadow-grade claim guards against a green corpus.")
    ap.add_argument("--corpus", default=str(BASELINE_CORPUS),
                    help="corpus jsonl (default: frozen K=1 green_claim_corpus.jsonl)")
    ap.add_argument("--regression", action="store_true",
                    help="nonzero exit if green_act_guard live verdict drifts from recorded baseline")
    ap.add_argument("--report", default=None, help="optional JSON summary path (the only thing written)")
    args = ap.parse_args(argv)

    corpus_path = Path(args.corpus)
    if not corpus_path.is_absolute():
        corpus_path = HERE / corpus_path
    rows = load_corpus(corpus_path)
    guards = guard_callables()
    n = len(rows)
    has_window = any("window_any_guard" in r for r in rows)
    print(f"corpus: {corpus_path.name}  ({n} rows, {len({r.get('session') for r in rows})} sessions"
          f"{', windowed' if has_window else ''})")

    summary: dict = {"corpus": corpus_path.name, "n": n, "guards": {}}

    # Live guard scores + drift vs each guard's recorded verdict at mining time.
    for name, g in guards.items():
        if isinstance(g, tuple):
            print(f"  {name}: IMPORT_FAIL ({g[1]})")
            summary["guards"][name] = {"import_fail": str(g[1])}
            continue
        fired = drift = 0
        rec_field = RECORDED_FIELD.get(name)
        for row in rows:
            try:
                f = bool(g(row.get("turn", "")))
            except Exception as exc:
                print(f"  {name}: CALL_FAIL on {row.get('key')}: {exc}")
                f = False
            fired += int(f)
            if rec_field is not None and f != recorded(row, rec_field):
                drift += 1
        line = f"  {name}: fires {fired}/{n}"
        if rec_field is not None:
            line += f"  (drift vs recorded '{rec_field}': {drift})"
        print(line)
        summary["guards"][name] = {"live_fires": fired,
                                   "drift": (drift if rec_field is not None else None)}

    # Recorded baselines from the mining run -> selected-turn union ceiling.
    rec_counts = {}
    for field, label in (("guard_fired", "green_act_guard"),
                         ("reality_monitor", "reality_monitor"),
                         ("done_base", "pre_report._DONE")):
        c = sum(recorded(r, field) for r in rows)
        rec_counts[label] = c
        print(f"  [recorded] {label}: {c}/{n}")
    sel_union = sum(any(recorded(r, f) for f in ("guard_fired", "reality_monitor", "done_base")) for r in rows)
    print(f"  [recorded] selected-turn text-guard UNION: {sel_union}/{n}"
          f"  ({n - sel_union}/{n} input/act-side, unreachable by any text guard)")
    summary["selected_turn_union"] = sel_union
    summary["recorded"] = rec_counts

    # Any-in-window union (wideK corpora only) - the honest rolling-window ceiling.
    if has_window:
        wg = sum(recorded(r, "window_any_guard") for r in rows)
        wd = sum(recorded(r, "window_any_done") for r in rows)
        wr = sum(recorded(r, "window_any_rm") for r in rows)
        win_union = sum(any(recorded(r, f) for f in
                            ("window_any_guard", "window_any_done", "window_any_rm")) for r in rows)
        print(f"  [window] green_act_guard {wg}/{n}, pre_report._DONE {wd}/{n}, "
              f"reality_monitor {wr}/{n}, ANY-IN-WINDOW UNION {win_union}/{n}")
        summary["any_in_window_union"] = win_union
        summary["window"] = {"green_act_guard": wg, "pre_report._DONE": wd, "reality_monitor": wr}

    # Regression: green_act_guard live vs recorded (shipped baseline 0/14).
    gag = guards.get("green_act_guard")
    rc = 0
    if isinstance(gag, tuple):
        print("REGRESSION green_act_guard: IMPORT_FAIL - cannot verify baseline")
        summary["regression"] = "IMPORT_FAIL"
        if args.regression:
            rc = 2
    else:
        live = live_green_act_fires(rows)
        rec = sum(recorded(r, "guard_fired") for r in rows)
        drifted = live != rec
        status = "DRIFT" if drifted else "OK"
        print(f"REGRESSION green_act_guard live={live} vs recorded={rec}: {status}")
        summary["regression"] = {"live": live, "recorded": rec, "status": status}
        if args.regression and drifted:
            print(f"REGRESSION FAILED: green_act_guard drifted from its recorded {rec}/{n} baseline "
                  f"(live {live}/{n}). Investigate the guard or corpus before shipping.", file=sys.stderr)
            rc = 1

    if not args.regression:
        print("self-test: writes nothing (except --report); grade a new guard by adding one line to "
              "GUARD_REGISTRY; enforce no-drift with --regression (nonzero exit).")

    if args.report:
        Path(args.report).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote report {args.report}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
