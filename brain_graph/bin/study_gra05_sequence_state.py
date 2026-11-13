"""study_gra05_sequence_state.py - does the ORDER of the last two things an agent touched predict his correction better
than what it touched together?

    CASE      the agent's last tool moment at most CASE_WINDOW s before a correction or repeat of his, in one of his
              sessions after the cut
    CONTROLS  up to K tool moments of the same session with no correction or repeat within CLEAR s on either side
    STATE     the distinct specific items (dir, file, mem, proj, repo, err, host; trivial folders and index memories left
              out) touched up to the moment, in order: the last one, the last two as an ordered pair, and the set of the
              ten minutes up to it

Three scores, each a rate learned only from his sessions in the TRAIN_DAYS before the cut (a tool moment is positive
when a correction or repeat of his follows within CASE_WINDOW s), shrunk toward the base rate with ALPHA pseudo-counts:
    couse    the mean rate of the items of the last ten minutes (no order)
    first    the rate after the last item
    second   the rate after the ordered pair of the last two items; first order when the pair was seen fewer than
             MIN_SUPPORT times in training
Primary: per-case AUC (a case against its own controls), second against couse, paired bootstrap over cases. The same
three scores with tool errors as the label are a separate endpoint, scored over all test tool moments.

    python study_gra05_sequence_state.py OUT_JSON [--cut "2026-09-01 00:00"] [--k 5] [--seed 7]
Counts only in OUT_JSON; per-moment rows (session prefix, time, scores, case flag - no item names) go to study/.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import collections
import json
import os
import random
import re
import sys
import time

HERE = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
sys.path.insert(0, HERE)
import session_kinds  # noqa: E402
import study_live_membrane as SLM  # noqa: E402
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (brain data is never deleted)

CASE_WINDOW, CLEAR, WINDOW = 600.0, 1800.0, 600.0
TRAIN_DAYS = 31
NEG = ("correction", "repeat")
STATE_KINDS = {"dir", "file", "mem", "proj", "repo", "err", "host"}
TRIVIAL = re.compile(r"^(dir|proj):~/(\.claude/projects(/[^/]+(/memory)?)?|[^/]+|\.claude/[^/]+)$", re.I)
SKIP_MEM = {"memory", "mistakes", "index_archive", "predictions", "state_of_mind", "open_questions", "autobiography",
            "user_model", "curiosity", "conscience", "intentions", "salience"}
ALPHA, MIN_SUPPORT = 5.0, 3


def keep(item):
    if TRIVIAL.match(item):
        return False
    kd, _, i = item.partition(":")
    return not (kd == "mem" and i.lower() in SKIP_MEM)


def moments(evs):
    """For every tool moment: (index, ts, last item, previous distinct item, set of the WINDOW's items)."""
    seq = []                               # the ordered distinct items so far, as (ts, item)
    out = []
    for i, (ts, kd, nid) in enumerate(evs):
        if kd in STATE_KINDS:
            it = "%s:%s" % (kd, nid)
            if keep(it) and (not seq or seq[-1][1] != it):
                seq.append((ts, it))
        elif kd == "tool":
            last1 = seq[-1][1] if seq else None
            last2 = None
            for _t, it in reversed(seq[:-1]):
                if it != last1:
                    last2 = it
                    break
            win = set(it for t, it in reversed(seq) if ts - t <= WINDOW)
            out.append((i, ts, last1, last2, win))
    return out


def label_after(times, ts, span):
    import bisect
    j = bisect.bisect_right(times, ts)
    return j < len(times) and times[j] <= ts + span


def rate(c, base):
    n, p = c
    return (p + ALPHA * base) / (n + ALPHA)


def auc_per_case(case_s, ctrl_s):
    return sum(1.0 if case_s > x else (0.5 if case_s == x else 0.0) for x in ctrl_s) / float(len(ctrl_s))


def global_auc(pos, neg):
    """rank AUC of positives over negatives (ties half)"""
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    r_sum = 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (i + j + 1) / 2.0
        r_sum += avg * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    n1, n0 = len(pos), len(neg)
    return (r_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0) if n1 and n0 else None


def main():
    try:                                      # background study: yield to his desktop and the renders
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:
        pass
    a = sys.argv[1:]
    out = a[0]
    cut = a[a.index("--cut") + 1] if "--cut" in a else "2026-09-01 00:00"
    K = int(a[a.index("--k") + 1]) if "--k" in a else 5
    seed = int(a[a.index("--seed") + 1]) if "--seed" in a else 7
    t0 = time.time()
    D = time.mktime(time.strptime(cut, "%Y-%m-%d %H:%M"))
    T0 = D - TRAIN_DAYS * 86400
    cfg = json.load(open(os.path.join(SLM.HOME, "build_config.json"), encoding="utf-8"))
    verdicts = os.path.join(SLM.HOME, cfg.get("verdict_events_file", "events_verdicts.jsonl"))
    by = SLM.load_recaptured(T0)
    SLM.replace_verdicts(by, verdicts, T0)
    his = [s for s in by if session_kinds.kind(s) == "his"]
    print("%d sessions from %s, %d his (%.0f s)" % (len(by), time.strftime("%Y-%m-%d", time.localtime(T0)), len(his),
                                                    time.time() - t0), flush=True)

    per = {}
    for s in his:
        evs = sorted(by[s])
        neg_t = sorted(ts for ts, kd, nid in evs if kd == "verdict" and nid in NEG)
        err_t = sorted(ts for ts, kd, nid in evs if kd == "err")
        per[s] = (evs, neg_t, err_t, moments(evs))

    # training counts, from moments before the cut
    tr = {lab: {"base": [0, 0], "first": collections.defaultdict(lambda: [0, 0]),
                "second": collections.defaultdict(lambda: [0, 0]), "item": collections.defaultdict(lambda: [0, 0])}
          for lab in ("corr", "err")}
    for s, (evs, neg_t, err_t, mom) in per.items():
        for i, ts, l1, l2, win in mom:
            if not (T0 <= ts < D) or l1 is None:
                continue
            for lab, times in (("corr", neg_t), ("err", err_t)):
                y = 1 if label_after(times, ts, CASE_WINDOW) else 0
                t = tr[lab]
                t["base"][0] += 1
                t["base"][1] += y
                t["first"][l1][0] += 1
                t["first"][l1][1] += y
                if l2 is not None:
                    t["second"][(l2, l1)][0] += 1
                    t["second"][(l2, l1)][1] += y
                for it in win:
                    t["item"][it][0] += 1
                    t["item"][it][1] += y

    def score(lab, l1, l2, win):
        t = tr[lab]
        base = t["base"][1] / float(t["base"][0]) if t["base"][0] else 0.0
        couse = (sum(rate(t["item"][it], base) if it in t["item"] else base for it in win) / len(win)) if win else base
        first = rate(t["first"][l1], base) if l1 in t["first"] else base
        sup = t["second"].get((l2, l1)) if l2 is not None else None
        second = rate(sup, base) if sup is not None and sup[0] >= MIN_SUPPORT else first
        return {"couse": couse, "first": first, "second": second,
                "supported": bool(sup is not None and sup[0] >= MIN_SUPPORT)}

    rng = random.Random(seed)
    rows, cases = [], []
    counts = collections.Counter()
    for s, (evs, neg_t, err_t, mom) in per.items():
        test = [m for m in mom if m[1] >= D and m[2] is not None]
        reacts = [t for t in neg_t if t >= D]
        if not reacts:
            continue
        counts["reactions_after_cut"] += len(reacts)
        case_m = {}
        for tc in reacts:
            prior = [m for m in test if m[1] < tc - 1.0 and tc - m[1] <= CASE_WINDOW]
            if not prior:
                counts["no_prior_tool_moment"] += 1
                continue
            case_m.setdefault(prior[-1][0], prior[-1])
        clear = [m for m in test if all(abs(m[1] - tn) > CLEAR for tn in neg_t)]
        for i in sorted(case_m):
            c = case_m[i]
            ctrl = list(clear)
            rng.shuffle(ctrl)
            ctrl = ctrl[:K]
            if not ctrl:
                counts["cases_without_controls"] += 1
                continue
            cs = score("corr", c[2], c[3], c[4])
            ks = [score("corr", m[2], m[3], m[4]) for m in ctrl]
            cases.append({"sid": s[:8], "t": round(c[1], 3), "case": cs, "controls": ks})
            rows.append({"sid": s[:8], "t": round(c[1], 3), "case": True, **{k: v for k, v in cs.items()}})
            for m, k in zip(ctrl, ks):
                rows.append({"sid": s[:8], "t": round(m[1], 3), "case": False, **{kk: vv for kk, vv in k.items()}})
    counts["cases"] = len(cases)

    def per_case(model):
        return [auc_per_case(c["case"][model], [k[model] for k in c["controls"]]) for c in cases]

    res = {"cut": cut, "train_days": TRAIN_DAYS, "k": K, "seed": seed, "counts": dict(counts),
           "train_moments": tr["corr"]["base"][0], "train_positives": tr["corr"]["base"][1],
           "train_pairs": len(tr["corr"]["second"]),
           "support_share": (sum(1 for r in rows if r["supported"]) / float(len(rows))) if rows else None, "auc": {}}
    for mdl in ("couse", "first", "second"):
        v = per_case(mdl)
        res["auc"][mdl] = round(sum(v) / len(v), 4) if v else None
    for other in ("couse", "first"):
        d = [x - y for x, y in zip(per_case("second"), per_case(other))]
        boots = []
        for _ in range(2000):
            smp = [d[rng.randrange(len(d))] for _ in d] if d else []
            boots.append(sum(smp) / len(smp) if smp else 0.0)
        boots.sort()
        res["second_minus_" + other] = {"mean": round(sum(d) / len(d), 4) if d else None,
                                        "ci95": [round(boots[49], 4), round(boots[1949], 4)] if d else None}
    # session-clustered bootstrap of the primary difference (sensitivity)
    by_sid = collections.defaultdict(list)
    for c, x, y in zip(cases, per_case("second"), per_case("couse")):
        by_sid[c["sid"]].append(x - y)
    sids = list(by_sid)
    cb = []
    for _ in range(2000):
        smp = [rng.choice(sids) for _ in sids]
        vals = [v for s_ in smp for v in by_sid[s_]]
        cb.append(sum(vals) / len(vals) if vals else 0.0)
    cb.sort()
    res["second_minus_couse_session_ci95"] = [round(cb[49], 4), round(cb[1949], 4)] if sids else None
    res["sessions_with_cases"] = len(sids)
    # the separate endpoint: tool errors within CASE_WINDOW, over every test tool moment
    err = {m: ([], []) for m in ("couse", "first", "second")}
    for s, (evs, neg_t, err_t, mom) in per.items():
        for i, ts, l1, l2, win in mom:
            if ts < D or l1 is None:
                continue
            y = label_after(err_t, ts, CASE_WINDOW)
            sc = score("err", l1, l2, win)
            for mdl in err:
                (err[mdl][0] if y else err[mdl][1]).append(sc[mdl])
    res["errors_auc_all_moments"] = {m: round(global_auc(*err[m]), 4) if err[m][0] and err[m][1] else None for m in err}
    res["errors_positive_moments"] = len(err["couse"][0])
    res["verdict"] = ("yes" if res["auc"]["second"] is not None and res["auc"]["second"] >= 0.60
                      and res["second_minus_couse"]["ci95"] and res["second_minus_couse"]["ci95"][0] > 0
                      else "no evidence at this volume")
    res["seconds"] = round(time.time() - t0, 1)
    keep_old(out)
    json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
    rp = os.path.join(SLM.STUDY, "gra05_rows_%s.jsonl" % time.strftime("%Y%m%d_%H%M"))
    with open(rp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
