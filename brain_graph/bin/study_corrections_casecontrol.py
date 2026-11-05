"""study_corrections_casecontrol.py - does the membrane's correction line rise before his corrections? (27 Sep,
claude-825b1305; pre-registered 14:2x in study/TWO_DAY_RUN_2026-09-26.md).

The per-session replay (study_live_membrane + score_corrections_per_session) had three of his sessions with a correction
after a sampled moment, too few to tell. This uses every correction or repeat of his after the cut instead of a sample:

    CASE      the agent's last tool or prompt moment at most CASE_WINDOW s before a correction or repeat of his
    CONTROLS  up to K tool moments of the same session with no correction or repeat within CLEAR s on either side

Each moment is evaluated exactly as study_live_membrane does (the distinct nodes of the ten minutes before, newest
first; a prompt moment adds its own words; the cut's sidecar; memories seen before the cut), and scored by the top
correction/repeat line, 0 when there is none. Primary: the matched AUC - for each case, the share of its own controls
that score lower (ties count half) - averaged over cases, with a bootstrap interval over cases. Secondary: the share
of case and control moments at or above the live bar (the policy's verdict_hebb_min).

    python study_corrections_casecontrol.py OUT_JSON [--tag W_wide] [--k 5] [--seed 7] [--placebo 2400,3000]
Counts only in OUT_JSON; per-moment rows (session prefix, time, score, case or control) go to study/ under a stamped name.

--placebo LO,HI adds a placebo arm (pre-registered 27 Sep 14:3x): for each case, the agent's last moment LO to HI s before
the same reaction, scored against the SAME controls the case drew. A line that anticipates corrections scores the case
above its placebo; a line that only rises with the session's phase scores them alike. The draws of the controls are not
changed by the option, so the case arm reproduces the run without it.

Each result carries evidence_generation (28 Sep, account01/B01 r66, B01-R13-CORRECTION-CASECONTROL-EVIDENCE-GENERATION-
UNBOUND-20260928): the sha256 of the code that scored, the policy, the cut file, the sidecar and learned store, a digest of
every event of his sessions the study read (recaptured transcripts with the verdicts put back, so one changed transcript
or verdict row changes it even when the cut does not move), and the per-moment rows file with its sha256 and row count;
its id is the sha256 of all of them. Digests, counts and cutoffs only: no transcript text. verify_casecontrol.py
recomputes the result from the rows file and refuses a missing or altered one.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import hashlib
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import live_membrane as LM  # noqa: E402
import session_kinds  # noqa: E402
import study_live_membrane as SLM  # noqa: E402
from quarantine_rows import keep_old  # noqa: E402

CASE_WINDOW = 600.0
CLEAR = 1800.0
NEG = ("correction", "repeat")


def _sha_file(p):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def events_digest(by, sessions):
    """sha256 of every event (time, kind, node) of the given sessions, in time order, and the counts: the transcripts and
    verdicts as the study read them. Node names go into the hash only."""
    h, n = hashlib.sha256(), 0
    for s in sorted(sessions):
        h.update(("\x1e%s" % s).encode("utf-8"))
        for ts, kd, nid in by[s]:
            h.update(json.dumps([round(float(ts), 3), kd, nid], ensure_ascii=False).encode("utf-8"))
            n += 1
    return {"sha256": h.hexdigest(), "sessions": len(sessions), "events": n}


def evidence_generation(by, sessions, D, tag, pol, side, learned, rows_path, n_rows):
    """The inputs that fix this result, and one id over them (see the module note)."""
    code = {os.path.basename(m.__file__): _sha_file(m.__file__) for m in (LM, SLM, session_kinds)}
    code[os.path.basename(__file__)] = _sha_file(os.path.abspath(__file__))
    g = {"code_sha256": code,
         "policy_sha256": hashlib.sha256(json.dumps(pol, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
         "cut": {"file": "cut%s.json" % tag, "sha256": _sha_file(os.path.join(SLM.STUDY, "cut%s.json" % tag)), "D": D},
         "sidecar": {"file": os.path.basename(side), "sha256": _sha_file(side)},
         "learned": {"file": os.path.basename(learned), "sha256": _sha_file(learned)},
         "events": events_digest(by, sessions),
         "rows": {"file": os.path.basename(rows_path), "sha256": _sha_file(rows_path), "rows": n_rows}}
    g["id"] = hashlib.sha256(json.dumps(g, sort_keys=True).encode("utf-8")).hexdigest()
    return g


def items_at(evs, i, ts, kd, pol):
    """study_live_membrane's items for the moment at index i, verbatim in effect."""
    items, got = [], set()
    for ts2, kd2, nid2 in reversed(evs[:i]):
        if ts - ts2 > pol["window_s"] or len(items) >= pol["k_items"]:
            break
        nm = "%s:%s" % (kd2, nid2)
        if nm not in got:
            got.add(nm)
            items.append((nm, ts - ts2))
    if kd == "kw":
        for ts2, kd2, nid2 in evs[i:i + 40]:
            if ts2 != ts or kd2 != "kw":
                break
            items.insert(0, ("kw:" + nid2, 0.0))
    return items


def main():
    a = sys.argv[1:]
    out = a[0]
    tag = a[a.index("--tag") + 1] if "--tag" in a else "W_wide"
    K = int(a[a.index("--k") + 1]) if "--k" in a else 5
    seed = int(a[a.index("--seed") + 1]) if "--seed" in a else 7
    placebo = [float(x) for x in a[a.index("--placebo") + 1].split(",")] if "--placebo" in a else None
    t0 = time.time()
    D = json.load(open(os.path.join(SLM.STUDY, "cut%s.json" % tag), encoding="utf-8"))["D"]
    side = os.path.join(SLM.STUDY, "sidecar_wf%s.sqlite" % tag)
    verdicts = os.path.join(SLM.HOME, "events_verdicts.jsonl")
    by = SLM.load_recaptured(D - 700)
    SLM.replace_verdicts(by, verdicts, D - 700)
    his = [s for s in by if session_kinds.kind(s) == "his"]
    print("cut %s; %d sessions in the window, %d his (%.0f s)" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(D)),
                                                                  len(by), len(his), time.time() - t0), flush=True)
    learned = os.path.join(SLM.STUDY, "no_learned.sqlite")
    lv = LM.Live(sidecar=side, learned=learned)
    pol = LM.policy()
    bar = float(pol.get("verdict_hebb_min", 76.0))
    rng = random.Random(seed)

    def score(evs, i, seen):
        ts, kd, _ = evs[i]
        ev = lv.evaluate(items_at(evs, i, ts, kd, pol), ts, seen_slugs=seen, pol=pol, mem_asof=ts)
        vd = [v for v in ev.get("verdict", []) if v[0] in ("verdict:correction", "verdict:repeat")]
        return float(vd[0][1]) if vd else 0.0

    cases, rows, no_moment, no_controls = [], [], 0, 0
    n_reactions = 0
    for s in his:
        evs = by[s]
        seen = {nid.lower() for ts, kd, nid in evs if ts < D and kd in LM.MEM_KINDS}
        neg = [ts for ts, kd, nid in evs if kd == "verdict" and nid in NEG and ts >= D]
        if not neg:
            continue
        n_reactions += len(neg)
        acts = [i for i, (ts, kd, nid) in enumerate(evs) if ts >= D and kd in ("tool", "kw")]
        case_idx = {}
        for tc in neg:
            # the agent's last moment strictly before his reaction (a prompt's own words carry its time, so < tc - 1)
            prior = [i for i in acts if evs[i][0] < tc - 1.0 and tc - evs[i][0] <= CASE_WINDOW]
            if not prior:
                no_moment += 1
                continue
            case_idx.setdefault(prior[-1], tc)
        clear = [i for i in acts if evs[i][1] == "tool" and all(abs(evs[i][0] - tn) > CLEAR for tn in neg)]
        for i in sorted(case_idx):
            ctl = rng.sample(clear, min(K, len(clear))) if clear else []
            if not ctl:
                no_controls += 1
            c_score = score(evs, i, seen)
            k_scores = [score(evs, j, seen) for j in ctl]
            case = {"case": c_score, "controls": k_scores, "sid": str(s)[:8]}
            if placebo:
                tc = case_idx[i]
                pre = [j for j in acts if placebo[0] <= tc - evs[j][0] <= placebo[1]]
                if pre:
                    case["placebo"] = score(evs, pre[-1], seen)
                    rows.append({"sid": str(s)[:8], "t": evs[pre[-1]][0], "score": case["placebo"], "case": False,
                                 "placebo": True, "lag_s": tc - evs[pre[-1]][0]})
            cases.append(case)
            rows.append({"sid": str(s)[:8], "t": evs[i][0], "score": c_score, "case": True, "lag_s": case_idx[i] - evs[i][0]})
            rows += [{"sid": str(s)[:8], "t": evs[j][0], "score": x, "case": False} for j, x in zip(ctl, k_scores)]
    matched = [c for c in cases if c["controls"]]

    def auc(cs):
        per = [(sum(1.0 for x in c["controls"] if x < c["case"]) + 0.5 * sum(1.0 for x in c["controls"] if x == c["case"]))
               / len(c["controls"]) for c in cs]
        return sum(per) / len(per) if per else None
    A = auc(matched)
    boots = []
    for _ in range(2000):
        smp = [matched[rng.randrange(len(matched))] for _ in matched] if matched else []
        v = auc(smp)
        if v is not None:
            boots.append(v)
    boots.sort()
    ci = [round(boots[int(0.025 * len(boots))], 3), round(boots[int(0.975 * len(boots)) - 1], 3)] if boots else None
    ctl_all = [x for c in matched for x in c["controls"]]
    case_all = [c["case"] for c in matched]
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "cut": time.strftime("%Y-%m-%d %H:%M", time.localtime(D)),
           "store_tag": tag, "his_sessions_in_window": len(his), "his_negative_reactions_after_cut": n_reactions,
           "cases": len(cases), "cases_with_controls": len(matched), "reactions_without_a_prior_moment": no_moment,
           "cases_without_controls": no_controls, "sessions_with_a_case": len({r["sid"] for r in rows if r["case"]}),
           "k": K, "case_window_s": CASE_WINDOW, "clear_s": CLEAR, "seed": seed,
           "matched_auc": round(A, 3) if A is not None else None, "auc_ci95": ci,
           "live_bar": bar,
           "share_at_bar": {"cases": round(sum(1 for x in case_all if x >= bar) / max(1, len(case_all)), 3),
                            "controls": round(sum(1 for x in ctl_all if x >= bar) / max(1, len(ctl_all)), 3)},
           "share_with_any_line": {"cases": round(sum(1 for x in case_all if x > 0) / max(1, len(case_all)), 3),
                                   "controls": round(sum(1 for x in ctl_all if x > 0) / max(1, len(ctl_all)), 3)},
           "mean_score": {"cases": round(sum(case_all) / max(1, len(case_all)), 2),
                          "controls": round(sum(ctl_all) / max(1, len(ctl_all)), 2)},
           "seconds": round(time.time() - t0)}
    by_s = {}
    for c in matched:
        by_s.setdefault(c["sid"], []).append(c)
    res["by_session"] = {s: {"cases": len(cs), "auc": round(auc(cs), 3)} for s, cs in
                         sorted(by_s.items(), key=lambda kv: -len(kv[1]))}
    if by_s:
        big = max(by_s, key=lambda s: len(by_s[s]))
        rest = [c for c in matched if c["sid"] != big]
        res["auc_without_largest_session"] = {"left_out": big, "cases": len(rest),
                                              "auc": round(auc(rest), 3) if rest else None}
    if placebo:
        both = [c for c in matched if "placebo" in c]
        res["placebo"] = {"lag_s": placebo, "cases_with_a_placebo_moment": len(both),
                          "case_auc_same_cases": round(auc(both), 3) if both else None,
                          "placebo_auc": round(auc([dict(c, case=c["placebo"]) for c in both]), 3) if both else None,
                          "case_above_placebo": sum(1 for c in both if c["case"] > c["placebo"]),
                          "case_below_placebo": sum(1 for c in both if c["case"] < c["placebo"])}
    rp = os.path.join(SLM.STUDY, "casecontrol_rows_%s.jsonl" % time.strftime("%Y%m%d_%H%M"))
    with open(keep_old(rp), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res["rows_file"] = os.path.basename(rp)
    res["evidence_generation"] = evidence_generation(by, his, D, tag, pol, side, learned, rp, len(rows))
    json.dump(res, open(keep_old(out), "w", encoding="utf-8"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
