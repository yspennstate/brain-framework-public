"""study_drift_live.py (from study_drift_casecontrol.py, with the live arm) - can the brain graph tell that a session has drifted from his last ask, before he
corrects it?

His words, 19 Jul: "if we deviate too much from the propts then a propt fires, so every single part of the propt should be
part of the brian, and if we move too far away it should fire, algorithmicl as part of the network". The question put to
the data: at the agent's last tool moment before a correction or repeat of his, does what the session is touching sit
further from his last ask than at quiet moments of the same session - and does the use-grown graph see that better than
plain words?

    CASE      the agent's last TOOL moment at most CASE_WINDOW s before a correction or repeat of his (tool moments only:
              drift is read off what the agent does; study_corrections_casecontrol also took prompt moments)
    CONTROLS  up to K tool moments of the same session with no correction or repeat within CLEAR s on either side
    HIS ASK   his latest prompt before the moment with at least MIN_ASK distinct words (the capture's kw events)
    ACTIVITY  the distinct items of the ten minutes up to the moment whose kind the graph links to (dir, err, file, host,
              mem, proj, repo, tool) - the same set for every score

Scores, higher = more drift: elapsed (seconds since the ask; the baseline to beat), word (share of the ask's words that
appear in no activity item's name), graph (share with no learned edge from kw:<word> to any activity item in the cut's
sidecar), union (share with neither). Primary: union against word, per case, paired bootstrap over cases.

    python study_drift_casecontrol.py OUT_JSON [--cut "2026-09-01 00:00"] [--until "2026-09-08 00:00"] [--tag X_drift0901]
                                      [--k 5] [--seed 7]
--until keeps only the reactions and tool moments before that time, so one store scores one week (the rolling cut of
17:0x: a store cut on 1 Sep knew 78 pct of the ask words and little of September's territory).
Counts only in OUT_JSON; per-moment rows (session prefix, time, scores, case flag - no words) go to study/.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import random
import re
import sys
import time

HERE = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
sys.path.insert(0, HERE)
import live_membrane as LM  # noqa: E402
import session_kinds  # noqa: E402
import study_live_membrane as SLM  # noqa: E402
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (brain data is never deleted)

CASE_WINDOW = 600.0
CLEAR = 1800.0
WINDOW = 600.0
NEG = ("correction", "repeat")
MIN_ASK = 4
MIN_ACTS = 3                       # at least this many distinct items since his ask
ELAPSED_BAND = 2.0                 # a control's time since its ask within a factor of this of the case's
RARE = 3                           # "unfamiliar": fewer than this many uses in the cut's store
ACT_KINDS = {"dir", "err", "file", "host", "mem", "proj", "repo", "tool"}
NOISE = {"users", "owner", "appdata", "local", "temp", "claude", "scratchpad", "projects", "com", "www", "http", "https",
         "exe", "bin", "tmp", "the", "and", "for", "with", "src", "lib", "home", "jsonl", "json", "txt", "html"}


def name_tokens(name):
    _k, _, rest = name.partition(":")
    return {w for w in re.findall(r"[a-z0-9]+", rest.lower()) if len(w) >= 3 and w not in NOISE}


def auc_per_case(c, key):
    x = c["case"][key]
    ks = [k[key] for k in c["controls"]]
    return (sum(1.0 for v in ks if v < x) + 0.5 * sum(1.0 for v in ks if v == x)) / len(ks)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def boot(values, rng, n=2000):
    if not values:
        return None
    bs = sorted(mean([values[rng.randrange(len(values))] for _ in values]) for _ in range(n))
    return [round(bs[int(0.025 * n)], 3), round(bs[int(0.975 * n) - 1], 3)]


def main():
    try:                                      # background study: yield to his desktop and the renders
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:
        pass
    a = sys.argv[1:]
    out = a[0]
    cut = a[a.index("--cut") + 1] if "--cut" in a else "2026-09-01 00:00"
    tag = a[a.index("--tag") + 1] if "--tag" in a else "X_drift0901"
    K = int(a[a.index("--k") + 1]) if "--k" in a else 5
    seed = int(a[a.index("--seed") + 1]) if "--seed" in a else 7
    U = time.mktime(time.strptime(a[a.index("--until") + 1], "%Y-%m-%d %H:%M")) if "--until" in a else float("inf")
    t0 = time.time()
    D = time.mktime(time.strptime(cut, "%Y-%m-%d %H:%M"))
    cfg = json.load(open(os.path.join(SLM.HOME, "build_config.json"), encoding="utf-8"))
    verdicts = os.path.join(SLM.HOME, cfg.get("verdict_events_file", "events_verdicts.jsonl"))
    D, side = SLM.ensure_cut(D, extra=cfg.get("extra_event_files", []), tag=tag, half_life=cfg.get("half_life_days"),
                             verdict_file=verdicts)
    print("cut %s built or reused (%.0f s)" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(D)), time.time() - t0),
          flush=True)
    by = SLM.load_recaptured(D - 700)
    SLM.replace_verdicts(by, verdicts, D - 700)
    his = [s for s in by if session_kinds.kind(s) == "his"]
    print("%d sessions in the window, %d his (%.0f s)" % (len(by), len(his), time.time() - t0), flush=True)
    lv = LM.Live(sidecar=side, learned=os.path.join(SLM.STUDY, "no_learned.sqlite"), delta=None)
    rng = random.Random(seed)
    # ---- the live arm: every other session's events from the cut to the moment, by the store's rule -------------
    import collections
    import live_delta as LD
    try:
        hubs = set(json.load(open(os.path.join(SLM.STUDY, "store_wf" + tag, "nodes.json"),
                                  encoding="utf-8"))["meta"].get("hubs") or [])
    except Exception:
        hubs = set()
    kw_first = {}                             # (word, item) -> {session: first time that session paired them}
    uses_at = collections.defaultdict(list)   # item -> [(time, session)] of every use as a target
    t_live = time.time()
    n_live_ev = 0
    for s0, evs0 in by.items():
        if session_kinds.kind(s0) == "test":
            continue
        ev0 = sorted(evs0, key=lambda r: r[0])
        seen0 = set()
        for j0, (t, kd, nid) in enumerate(ev0):
            x = "%s:%s" % (kd, nid)
            is_t = kd in LD.TARGET_KINDS and (kd not in LD.FIRST_TOUCH or x not in seen0)
            seen0.add(x)
            if not is_t or t < D or t >= U:
                continue
            n_live_ev += 1
            uses_at[x].append((t, s0))
            if kd == "tool" and not LD.keep_tool(s0, t, x):
                continue
            i0, got0 = j0 - 1, set()
            while i0 >= 0 and t - ev0[i0][0] <= LD.WINDOW and len(got0) < LD.K_CTX:
                tt, kk, nn = ev0[i0]
                y = "%s:%s" % (kk, nn)
                if tt < t and y not in hubs and y not in got0:
                    got0.add(y)
                    if kk == "kw":
                        d0 = kw_first.setdefault((str(nn).lower(), x), {})
                        if t < d0.get(s0, float("inf")):
                            d0[s0] = t
                i0 -= 1
    for x in uses_at:
        uses_at[x].sort()
    print("live arm: %d target events of non-test sessions, %d word-item pairs (%.0f s)" % (
        n_live_ev, len(kw_first), time.time() - t_live), flush=True)

    def live_pair(w, item, T, S):
        d0 = kw_first.get((w, item))
        return bool(d0) and any(s_ != S and t_ < T for s_, t_ in d0.items())

    def live_uses(item, T, S, cap=RARE):
        n = 0
        for t_, s_ in uses_at.get(item, ()):
            if t_ >= T:
                break
            if s_ != S:
                n += 1
                if n >= cap:
                    break
        return n

    def ask_before(evs, i, ts):
        """His latest prompt (a group of kw events sharing one time) before ts with at least MIN_ASK distinct words."""
        j = i - 1
        while j >= 0:
            if evs[j][1] == "kw":
                t_ask = evs[j][0]
                words = set()
                k = j
                while k >= 0 and evs[k][1] == "kw" and evs[k][0] == t_ask:
                    words.add(str(evs[k][2]).lower())
                    k -= 1
                if len(words) >= MIN_ASK and t_ask < ts:
                    return t_ask, words
                j = k
            else:
                j -= 1
        return None, None

    def scores(evs, i, S=None):
        ts = evs[i][0]
        t_ask, words = ask_before(evs, i, ts)
        if not words:
            return None
        acts = []
        seen = set()
        counts = {}
        errs = 0
        j = i
        # only what the agent did SINCE his ask (and in the last ten minutes): a window reaching back past the ask holds
        # the previous task's items, which read as drift by construction (the 16:3x pilot's confound)
        while j >= 0 and ts - evs[j][0] <= WINDOW and evs[j][0] > t_ask:
            kd, nid = evs[j][1], evs[j][2]
            nm = "%s:%s" % (kd, nid)
            if kd in ACT_KINDS:
                counts[nm] = counts.get(nm, 0) + 1
                if kd == "err":
                    errs += 1
                if nm not in seen:
                    seen.add(nm)
                    acts.append(nm)
            j -= 1
        if len(acts) < MIN_ACTS:
            return None
        # his "an agent spent a lot of time on that": errors and the same item hit again and again since his ask
        struggle = errs + sum(c - 2 for c in counts.values() if c >= 3)
        out = {"elapsed": ts - t_ask, "struggle": float(struggle), "n_acts": len(acts), "_acts": acts}
        out.update(cover(words, acts))
        out["unfamiliar"] = unfamiliar(acts)
        cl = cover(words, acts, T=ts, S=S)
        out["graph_live"], out["union_live"] = cl["graph"], cl["union"]
        out["unfamiliar_live"] = unfamiliar(acts, T=ts, S=S)
        out["_t"], out["_s"] = ts, S
        return out

    def unfamiliar(acts, T=None, S=None):
        """Share of what the agent touches that the use-grown graph has seen fewer than RARE times before the cut
        (absent counts as unseen): off the well-worn paths (post-hoc from the 16:4x pilot, pre-registered 16:4x)."""
        got = {}
        # only kinds whose nodes carry a use record ([recent use times, older count, first time]); an error or a host
        # node has none and would read as unseen
        names = [nm for nm in acts if nm.split(":", 1)[0] in ("dir", "file", "tool", "mem")]
        if not names:
            return 0.0
        for k in range(0, len(names), 400):
            chunk = names[k:k + 400]
            q = "SELECT name, uses FROM node WHERE name IN (%s)" % ",".join("?" * len(chunk))
            for nm, u in lv.db.execute(q, chunk):
                try:
                    last, old_n, _first = json.loads(u) if u else ([], 0, None)
                    n_u = len(last or []) + int(old_n or 0)
                except (ValueError, TypeError):
                    n_u = 0
                got[nm] = n_u
        if T is not None:                     # the live arm: other sessions' uses before the moment count too
            for nm in names:
                if got.get(nm, 0) < RARE:
                    got[nm] = got.get(nm, 0) + live_uses(nm, T, S, cap=RARE - got.get(nm, 0))
        return sum(1 for nm in names if got.get(nm, 0) < RARE) / float(len(names))

    def cover(words, acts, T=None, S=None):
        """How much of an ask the activity covers: by name (words) and by learned edge from kw:<word> (graph)."""
        toks = set()
        for nm in acts:
            toks |= name_tokens(nm)
        by_word = {w: (w in toks) for w in words}
        ids = lv.node_ids(["kw:" + w for w in words] + list(acts))
        kw_ids = {ids["kw:" + w]: w for w in words if "kw:" + w in ids}
        act_ids = [ids[nm] for nm in acts if nm in ids]
        linked = set()
        strength = 0.0
        if kw_ids and act_ids:
            q = "SELECT src, s FROM edge WHERE src IN (%s) AND dst IN (%s)" % (",".join("?" * len(kw_ids)),
                                                                             ",".join("?" * len(act_ids)))
            for src, s in lv.db.execute(q, list(kw_ids) + act_ids):
                if s and s > 0:
                    linked.add(kw_ids[src])
                    strength += float(s)
        if T is not None:                     # the live arm: a pairing by any other session before the moment
            for w in words:
                if w not in linked and any(live_pair(w, a, T, S) for a in acts):
                    linked.add(w)
        n = float(len(words))
        return {"word": 1.0 - sum(1 for w in words if by_word[w]) / n,
                "graph": 1.0 - len(linked) / n,
                "union": 1.0 - sum(1 for w in words if by_word[w] or w in linked) / n,
                "graph_weak": -strength, "n_words": len(words), "words_known": len(kw_ids)}

    import bisect
    cases, rows = [], []
    counts = {"reactions_after_cut": 0, "no_prior_tool_moment": 0, "case_without_ask_or_activity": 0,
              "cases_without_matched_controls": 0}
    # every substantive ask of his in the window, by session: the placebo scores a moment against an ask from ANOTHER
    # session - if a stranger's ask separates cases from controls as well as his own, the signal is not drift from his ask
    ask_pool = []
    for s0 in his:
        grp = {}
        for ts, kd, nid in by[s0]:
            if kd == "kw":
                grp.setdefault(ts, set()).add(str(nid).lower())
        ask_pool += [(s0, ws) for ws in grp.values() if len(ws) >= MIN_ASK]
    for s in his:
        evs = by[s]
        neg_all = [ts for ts, kd, nid in evs if kd == "verdict" and nid in NEG and ts >= D]
        neg = [ts for ts in neg_all if ts < U]      # cases: reactions inside [cut, until)
        if not neg:
            continue
        counts["reactions_after_cut"] += len(neg)
        kw_at = {}
        for ts, kd, nid in evs:
            if kd == "kw":
                kw_at.setdefault(ts, set()).add(nid)
        ask_ts = sorted(ts for ts, ws in kw_at.items() if len(ws) >= MIN_ASK)

        def elapsed_at(i):
            k = bisect.bisect_left(ask_ts, evs[i][0]) - 1
            return evs[i][0] - ask_ts[k] if k >= 0 else None
        tools = [i for i, (ts, kd, nid) in enumerate(evs) if D <= ts < U and kd == "tool"]
        case_idx = {}
        for tc in neg:
            prior = [i for i in tools if evs[i][0] < tc - 1.0 and tc - evs[i][0] <= CASE_WINDOW]
            if not prior:
                counts["no_prior_tool_moment"] += 1
                continue
            case_idx.setdefault(prior[-1], tc)
        clear = [i for i in tools if all(abs(evs[i][0] - tn) > CLEAR for tn in neg_all)]   # quiet of ANY reaction
        for i in sorted(case_idx):
            c_sc = scores(evs, i, S=s)
            if c_sc is None:
                counts["case_without_ask_or_activity"] += 1
                continue
            # controls matched on time since his ask (within a factor ELAPSED_BAND of the case's): corrections come
            # minutes after an ask, quiet moments hours after, and that alone must not pass for drift
            e0 = max(1.0, c_sc["elapsed"])
            band = [j for j in clear if elapsed_at(j) is not None
                    and e0 / ELAPSED_BAND <= max(1.0, elapsed_at(j)) <= e0 * ELAPSED_BAND]
            rng.shuffle(band)
            k_sc = []
            for j in band:
                x = scores(evs, j, S=s)
                # and on how much the agent has touched since the ask, so 'fewer links' cannot just mean 'less done yet'
                if x is not None and c_sc["n_acts"] / ELAPSED_BAND <= x["n_acts"] <= c_sc["n_acts"] * ELAPSED_BAND:
                    k_sc.append(x)
                    if len(k_sc) >= K:
                        break
            if not k_sc:
                counts["cases_without_matched_controls"] += 1
                continue
            others = [ws for s0, ws in ask_pool if s0 != s]
            if others:
                pw = others[rng.randrange(len(others))]
                for x in [c_sc] + k_sc:
                    pc = cover(pw, x["_acts"])
                    x["placebo_graph"], x["placebo_union"], x["placebo_word"] = pc["graph"], pc["union"], pc["word"]
                    pl = cover(pw, x["_acts"], T=x["_t"], S=x["_s"])
                    x["placebo_graph_live"], x["placebo_union_live"] = pl["graph"], pl["union"]
            cases.append({"sid": str(s)[:8], "case": c_sc, "controls": k_sc})
            strip = lambda d: {k: v for k, v in d.items() if k not in ("_acts", "_t", "_s")}   # scores, never names
            rows.append(dict(strip(c_sc), sid=str(s)[:8], t=evs[i][0], case=True, lag_s=case_idx[i] - evs[i][0]))
            rows += [dict(strip(x), sid=str(s)[:8], case=False) for x in k_sc]
    keys = ("elapsed", "struggle", "word", "graph", "union", "graph_weak", "placebo_graph", "placebo_union", "unfamiliar",
            "graph_live", "union_live", "placebo_graph_live", "placebo_union_live", "unfamiliar_live")
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "cut": time.strftime("%Y-%m-%d %H:%M", time.localtime(D)),
           "until": time.strftime("%Y-%m-%d %H:%M", time.localtime(U)) if U != float("inf") else None,
           "store_tag": tag, "his_sessions_in_window": len(his), "cases": len(cases), "k": K, "seed": seed,
           "case_window_s": CASE_WINDOW, "clear_s": CLEAR, "window_s": WINDOW, "min_ask_words": MIN_ASK,
           "sessions_with_a_case": len({c["sid"] for c in cases}), "counts": counts,
           "note": "cases are the agent's last TOOL moment before his reaction (drift is read off what the agent does)"}
    res["auc"] = {}
    for key in keys:
        per = [auc_per_case(c, key) for c in cases]
        res["auc"][key] = {"auc": round(mean(per), 3) if per else None, "ci95": boot(per, rng)}
    diff = [auc_per_case(c, "union") - auc_per_case(c, "word") for c in cases]
    res["union_minus_word"] = {"mean": round(mean(diff), 3) if diff else None, "ci95": boot(diff, rng),
                               "cases_up": sum(1 for d in diff if d > 0), "cases_down": sum(1 for d in diff if d < 0)}
    by_s = {}
    for c in cases:
        by_s.setdefault(c["sid"], []).append(c)
    res["by_session"] = {sid: {"cases": len(cs), "union": round(mean([auc_per_case(c, "union") for c in cs]), 3),
                               "word": round(mean([auc_per_case(c, "word") for c in cs]), 3)}
                         for sid, cs in sorted(by_s.items(), key=lambda kv: -len(kv[1]))}
    if by_s:
        big = max(by_s, key=lambda sid: len(by_s[sid]))
        rest = [c for c in cases if c["sid"] != big]
        res["without_largest_session"] = {
            "left_out": big, "cases": len(rest),
            "union_minus_word": round(mean([auc_per_case(c, "union") - auc_per_case(c, "word") for c in rest]), 3)
            if rest else None,
            "union": round(mean([auc_per_case(c, "union") for c in rest]), 3) if rest else None}
    means = {}
    for key in keys:
        cs = [c["case"][key] for c in cases]
        ks = [x[key] for c in cases for x in c["controls"]]
        means[key] = {"cases": round(mean(cs), 3) if cs else None, "controls": round(mean(ks), 3) if ks else None}
    res["mean_score"] = means
    res["words_known_to_graph"] = {"share": round(sum(c["case"]["words_known"] for c in cases) /
                                                  max(1, sum(c["case"]["n_words"] for c in cases)), 3)}
    res["seconds"] = round(time.time() - t0)
    rp = os.path.join(SLM.STUDY, "drift_live_rows_%s_%s.jsonl" % (tag, time.strftime("%Y%m%d_%H%M")))
    with open(keep_old(rp), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res["rows_file"] = os.path.basename(rp)
    json.dump(res, open(keep_old(out), "w", encoding="utf-8"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
