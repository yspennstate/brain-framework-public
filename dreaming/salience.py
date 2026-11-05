#!/usr/bin/env python3
"""salience.py — the brain's affective salience layer.

A human mind doesn't hold every memory equally. What mattered stays vivid; the flat
middle fades (without being destroyed); what hurt or what we're proud of stays sharp
for years. This computes that: a salience score per memory, plus an affective tag
(valence: was it good or bad; charge: how intense), from human-plausible inputs.

It NEVER deletes or edits a memory. It writes two sidecars:
  - salience.tsv           (fname \t salience \t valence \t charge \t why) — for dream.py
  - <brain>/SALIENCE.md    (what's vivid right now, human-readable) — for the wake ritual

Model (all terms transparent and tunable):
  recency  R = exp(-age_days / TAU_FAST)     — the fast EPISODIC arm (measured tau ~1.3 days)
  persist  P = soft-OR of the reasons a memory stays vivid regardless of age:
             emotional charge, link-centrality (hubs), curation into MEMORY.md, type
             (the type factor carries the measured old-age retrieval plateau, see below)
  salience S = 100 * (1 - (1-R)*(1-P))        — vivid if recent OR persistent
So a flat old project memory decays toward 0; a scar, a hub, or a hard rule stays high
forever. That asymmetry — the boring middle fading while the vivid and the load-bearing
persist — is the point.

Usage:  python salience.py [memory_dir] [brain_dir]
Import: import salience; scores = salience.score_corpus(memory_dir)   # {fname: {...}}
"""
import os, re, io, marshal, math, sys, time
from supersession import active_supersessions

MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
BRAIN_DEFAULT = os.path.expanduser(r"~/brain-repo/01_ai_consciousness/brain")

# Two-scale recency (2026-07-23; RECENCY_KERNEL.md, measured by eval_recency_kernel.py on 1,391
# deliberate-recall reads): retrieval need decays with tau ~1.3 DAYS (episodic head), then sits on
# a nearly flat type-dependent plateau ~110x below the head (per memory-day at 30d+: feedback
# 0.027, reference 0.0047, untyped 0.0036, project 0.0021). The old TAU=45d smeared that episodic
# head over months. The noisy-OR below already had the right FORM; the recency arm now carries the
# measured head, and TYPE_PERSIST carries the plateau at the measured RATIOS (anchored at
# feedback=0.90 - the salience scale is a rank scale, so ratios are what transfer, not the raw
# per-day rates). Ship gates + rollback verification: eval_recency_ship.py.
# BRAIN_RECENCY_V1=1 restores the pre-2026-07-23 kernel (tau 45d + old type table) exactly.
TAU_FAST_DAYS = 1.3   # measured episodic decay
TAU_DAYS = 45.0       # V1 rollback constant (the old single exponential)

# Emotional lexicons. Word-boundary matched over the first ~1600 chars of the body.
# FEAR/PAIN — the scars. We remember what hurt; these get high charge AND high persistence.
FEAR = r"crash|bsod|whea|leak|flood|panic|corrupt|broke|broken|\bfail|stranded|busted|refut|\bstale\b|regression|\bhang\b|\bstuck\b|deadlock|\bkilled\b|\bdark\b|outage|\bdown\b|\blost\b|\bwrong\b|mistake|burned|\bnag\b|\bspam\b|locked out|flatten|silent(ly)? (fail|die|drop)|blew up|melt"
# JOY/PRIDE — the triumphs. High charge, positive valence.
JOY = r"solved|proven|\bproof\b|verified|\bworks\b|working|finally|passed|landed|beautiful|elegant|\bclean\b|reproduc|confirmed|\bnovel\b|breakthrough|\bsharp\b|unlocked|\bgreen\b|nailed|cracked|\bwin\b|dominat"
# STAKES — chronic importance (valence-neutral, high persistence). These matter every day.
STAKES = r"live money|real money|\bowner\b|live book|production|hard rule|\bnever\b|\balways\b|\blaw\b|critical|sacred|load-bearing|non-negotiable|standing rule"
# TRIED + NOT SHIPPED (2026-07-08): a SURPRISE lexicon for "surprise -> salience" (the gist's named
# edge - a memory of an expectation-violating result should stay vivid). Measured against the corpus it
# lit up ~7 memories at >=2 markers, and most were files that DISCUSS surprise (PREDICTIONS.md,
# RECONSOLIDATION_QUEUE, growth_mechanism), not ones that RECORD a surprising event - the same
# meta-vs-event ambiguity the superseded regex had, but for a far smaller, lower-value effect, and a
# precise version would cost complexity out of proportion to the gain. FEAR already carries surprising
# FAILURES (refut/wrong/mistake) and JOY the breakthroughs, so the neutral-surprise gap is thin. Left
# unbuilt on purpose; if revisited, tie it to a resolved high-surprise PREDICTION (predict.py), not a lexicon.

FEAR_RX, JOY_RX, STAKES_RX = (re.compile(p, re.I) for p in (FEAR, JOY, STAKES))

# Count each head once. The same counts feed both session capture and the final
# score; recomputing all three regex scans (and FEAR twice in the second pass)
# made every new audit occurrence pay the full corpus cost repeatedly.
_UNICODE_LEXICONS = (FEAR_RX, JOY_RX, STAKES_RX)
_ASCII_LEXICONS = tuple(re.compile(rx.pattern, (rx.flags & ~re.UNICODE) | re.ASCII)
                       for rx in _UNICODE_LEXICONS)


def lexical_counts(head):
    """Return raw fear, joy, stakes, and the original final fear count.

    On an ASCII-only input, ASCII word boundaries and case-insensitive matches
    have the same meanings as their Unicode counterparts. Non-ASCII heads keep
    the original Unicode expressions, including dotted/dotless I, long s and
    Kelvin-sign case behavior. Never apply the ASCII path just because a note
    looks English. Keep raw-vs-distinct fear semantics unchanged if capture
    groups are edited later.
    """
    fear_rx, joy_rx, stakes_rx = _ASCII_LEXICONS if head.isascii() else _UNICODE_LEXICONS
    fear_matches = fear_rx.findall(head)
    raw_fear = len(fear_matches)
    final_fear = len(set(x.lower() for x in fear_matches if isinstance(x, str))) or raw_fear
    return raw_fear, len(joy_rx.findall(head)), len(stakes_rx.findall(head)), final_fear


# The three lexicon scans were 28 s of a 70 s dream agenda (cProfile, about 30,000 memories, 1 ms a head),
# and consciousness.py pays them again. The counts depend only on the file, so keep them per file under its
# (size, mtime_ns), taken BEFORE the read: a file rewritten mid-read is then stored under its old stat and re-scanned
# next time. Only for the live memory dir (tests score temp dirs), and dropped whole if a lexicon pattern changes.
LEX_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".salience_lex_cache.marshal")
_LEX_TAG = "\0".join(rx.pattern for rx in _UNICODE_LEXICONS)


def _lex_cache_load(mem_dir):
    if os.path.realpath(mem_dir) != os.path.realpath(MEM_DEFAULT):
        return None
    try:
        with open(LEX_CACHE, "rb") as fh:
            got = marshal.load(fh)
        if isinstance(got, dict) and got.get("tag") == _LEX_TAG and isinstance(got.get("e"), dict):
            return got["e"]
    except Exception:
        pass
    return {}


def _lex_cache_save(entries):
    tmp = "%s.%d.tmp" % (LEX_CACHE, os.getpid())
    try:
        with open(tmp, "wb") as fh:
            marshal.dump({"tag": _LEX_TAG, "e": entries}, fh)
        os.replace(tmp, LEX_CACHE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass

# A memory that has itself been SUPERSEDED / REFUTED should FADE, so its correction (the fresh
# memory) is what surfaces bright. Otherwise a busted "winner" keeps its JOY-charge + curation glow
# and re-surfaces vivid - the W#72/W#73 flattering-sharpe bug the dream flagged. Precision matters
# more than recall here (a generic English regex hit 46 memories, several of them the CORRECTIONS
# themselves), so match ONLY the corpus's explicit annotation convention - the warning-sign mark the
# dream's stale-belief pass applies ("⚠ superseded (...)" / "⚠ STALE ...") - and judge each hit in
# code (see _supersession_factor). An un-annotated refuted memory fades the night the dream
# annotates it; clean division of labor.
SUPERSEDED_RX = re.compile(
    u"⚠️?\\s{0,3}(SUPERSEDED|superseded|refuted|retracted|overturned|obsolete|STALE|stale)\\b")
SUPERSEDED_PENALTY = 0.4     # a fully-superseded memory keeps ~40% of its salience: present, not bright
SUPERSEDED_PARTIAL = 0.7     # a PARTIALLY-superseded one ("address only", "partial") fades gently


def _supersession_factor(body, mtype):
    """Salience multiplier from supersession annotations. 1.0 = live. The judgment calls, from
    eyeballing every real hit: (a) a `⚠ superseded`-in-backticks is a memory DESCRIBING the
    convention, not a superseded memory - skip; (b) "⚠ STALE warning/flag/title" describes a
    stale-warning FEATURE - skip; (c) user/feedback memories (standing rules) never fade this way -
    an "address only" update must not dim a live hard rule; (d) a qualified supersession
    ("partial", "only") fades gently, a full one fades hard."""
    if mtype in ("user", "feedback"):
        return 1.0
    factor = 1.0
    for m in SUPERSEDED_RX.finditer(body):
        i, j = m.start(), m.end()
        if body[max(0, i - 2):i].count("`") or body[j:j + 2].count("`"):
            continue                      # quoted/described, not applied
        tail = body[j:j + 24].lower()
        if tail.lstrip().startswith(("warning", "flag", "title", "spot")) or "warning" in tail[:16]:
            continue                      # a stale-warning feature being described
        line_end = body.find("\n", j)
        line = body[i:line_end if line_end > 0 else None][:160].lower()
        if re.search(r"\b(only|partial(ly)?)\b", line):
            factor = min(factor, SUPERSEDED_PARTIAL)
        else:
            return SUPERSEDED_PENALTY     # a full supersession wins outright
    return factor
WIKI = re.compile(r"\[\[([^\]\|#]+)\]\]")
FM_TYPE = re.compile(r"^\s*type:\s*(\w+)", re.M)
FM_DESC = re.compile(r"^description:\s*(.+)$", re.M)
DATE_F = re.compile(r"(202[4-7])[_-](0[1-9]|1[0-2])[_-]?(\d{2})?")
_DATE_TAIL = re.compile(r"[_-]?20\d\d[_-]\d\d(?:[_-]\d\d)?$")

# Plateau ratios from the measured 30d+ rates, anchored at feedback=0.90: project = 0.90/13
# (the measured 13x feedback-over-project gap), reference = 0.90*(0.0047/0.027), untyped (the
# dict default: files with no metadata.type and no prefix score as "project" via _infer_type, so
# the default only reaches unusual explicit types) stays at the untyped-class ratio. "user" is
# unmeasured as a type (few and young) and is anchored WITH feedback: standing owner rules.
# "transient" is unmeasured and deliberately ephemeral; kept lowest.
TYPE_PERSIST = {"user": 0.90, "feedback": 0.90, "reference": 0.16,
                "project": 0.07, "transient": 0.03}
TYPE_PERSIST_DEFAULT = 0.12
TYPE_PERSIST_V1 = {"user": 0.90, "feedback": 0.90, "reference": 0.40,
                   "project": 0.12, "transient": 0.03}
TYPE_PERSIST_V1_DEFAULT = 0.12


def _kernel():
    """(tau_days, type_persist, type_default) - read the rollback env at CALL time so one process
    can score both kernels (eval_recency_ship.py compares arms in-process)."""
    if os.environ.get("BRAIN_RECENCY_V1") == "1":
        return TAU_DAYS, TYPE_PERSIST_V1, TYPE_PERSIST_V1_DEFAULT
    return TAU_FAST_DAYS, TYPE_PERSIST, TYPE_PERSIST_DEFAULT

def _norm(s):
    return _DATE_TAIL.sub("", s.strip().lower().rstrip(".md")).replace("-", "_")

def _read(p):
    try:
        with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""

def _infer_type(fn, body):
    m = FM_TYPE.search(body)
    if m:
        return m.group(1).lower()
    low = fn.lower()
    for pre, ty in (("feedback_", "feedback"), ("user_", "user"), ("reference_", "reference"),
                    ("project_", "project"), ("transient_", "transient"), ("session_", "transient")):
        if low.startswith(pre):
            return ty
    return "project"

def score_corpus(mem_dir=MEM_DEFAULT, curated_names=None):
    """Return {fname: {salience, valence, charge, why, type, date}} for every live memory.
    Pure read-only. curated_names: set of basenames present in MEMORY.md (auto-read if None)."""
    now = time.time()
    today = time.strftime("%Y-%m-%d")

    # curation signal: a memory kept in the LIVE index (MEMORY.md, ~70 lines) is one a
    # human or a dream chose to hold front-and-center — a real vote for "vivid". Overflow
    # in INDEX_ARCHIVE doesn't count; it's just "was indexed once", not discriminative.
    if curated_names is None:
        curated_names = set()
        idx = _read(os.path.join(mem_dir, "MEMORY.md"))
        for m in re.finditer(r"\[([^\]]+?)\.md\]", idx):
            curated_names.add(_norm(m.group(1)))

    mems = {}
    for fn in os.listdir(mem_dir):
        if not fn.endswith(".md") or fn in ("MEMORY.md", "INDEX_ARCHIVE.md",
                                            "OPEN_QUESTIONS.md", "STATE_OF_MIND.md"):
            continue
        p = os.path.join(mem_dir, fn)
        if not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
            stat_key = (st.st_size, st.st_mtime_ns)
        except OSError:
            stat_key = None
        body = _read(p)
        head = body[:1600]
        dm = DATE_F.search(fn) or DATE_F.search(FM_DESC.search(body).group(1) if FM_DESC.search(body) else "")
        date = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3) or '01'}" if dm else None
        if not date:
            # created: frontmatter (stamped by brain.py remember since 07-09) - mtimes lie after any
            # repo sync, so ~200 undated memories were aging by copy-time, not birth (read-side fix)
            cm = re.search(r"^\s*created:\s*[\"']?(\d{4})-(\d{2})-(\d{2})", head, re.M)
            if cm:
                date = "%s-%s-%s" % cm.groups()
        if date and date > today:
            date = None
        mems[fn] = {
            "body": body, "head": head, "date": date, "mtime": os.path.getmtime(p),
            "type": _infer_type(fn, body), "links": set(WIKI.findall(body)), "stat_key": stat_key,
        }

    # Machine-readable supersession is authoritative for active attention. Keep the
    # historical row for diagnostics, but fade it even when prose annotations are absent.
    structural_superseded = active_supersessions(
        mem_dir, known_slugs={fn[:-3] for fn in mems}, include_frontmatter=True)

    # centrality: how many other memories point here (date/sep-insensitive resolve)
    canon = {}
    for fn in mems:
        canon.setdefault(_norm(fn[:-3]), fn)
    backlinks = {fn: 0 for fn in mems}
    for fn, m in mems.items():
        for l in m["links"]:
            tgt = canon.get(_norm(l))
            if tgt and tgt != fn:
                backlinks[tgt] += 1

    # synaptic tagging & capture (Frey-Morris 1997; behavioral tagging, Moncada-Viola 2007): a low-charge
    # memory encoded in the SAME session as a strongly-charged one is stabilised by it - the boring config
    # detail written the same session as the crash is often the load-bearing fact. Pre-compute per-session
    # max charge; below, the low-charge members' persistence is boosted toward it.
    _SESS = re.compile(r"originSessionId:\s*([0-9A-Za-z-]{8,})")
    _ch, _sess, _counts = {}, {}, {}
    _lex_old = _lex_cache_load(mem_dir)
    _lex_new, _lex_hits = {}, 0
    for fn, m in mems.items():
        sk = m["stat_key"]
        hit = _lex_old.get(fn) if (_lex_old is not None and sk is not None) else None
        if hit is not None and hit[0] == sk[0] and hit[1] == sk[1]:
            counts = tuple(hit[2])
            _lex_hits += 1
        else:
            counts = lexical_counts(m["head"])
        if sk is not None:
            _lex_new[fn] = (sk[0], sk[1], counts)
        _counts[fn] = counts
        _ch[fn] = min(1.0, sum(counts[:3]) / 6.0)
        _sm = _SESS.search(m["body"])
        _sess[fn] = _sm.group(1) if _sm else None
    if _lex_old is not None and (_lex_hits != len(_lex_new) or len(_lex_old) != len(_lex_new)):
        _lex_cache_save(_lex_new)
    _sessmax = {}
    for fn, s in _sess.items():
        if s:
            _sessmax[s] = max(_sessmax.get(s, 0.0), _ch[fn])

    kern_tau, kern_types, kern_default = _kernel()
    out = {}
    for fn, m in mems.items():
        # age → recency
        if m["date"]:
            try:
                y, mo, d = map(int, m["date"].split("-"))
                age = max(0.0, (now - time.mktime((y, mo, d, 12, 0, 0, 0, 0, -1))) / 86400.0)
            except Exception:
                age = (now - m["mtime"]) / 86400.0
        else:
            age = (now - m["mtime"]) / 86400.0
        R = math.exp(-age / kern_tau)

        # emotional charge + valence from the lexicons
        raw_fear, joy, stake, fear = _counts[fn]
        raw = fear + joy + stake
        charge = min(1.0, raw / 6.0)                       # 6+ charged hits = fully charged
        valence = (joy - fear) / (joy + fear + 1.0)        # [-1,1]; stakes are neutral

        # persistence: the reasons a memory stays vivid regardless of age (soft-OR)
        # Tuned so the front-of-mind list reads like a person's — the hard rules, the
        # scars, the recent work, real hubs — not a dense cluster of cross-linked scratch.
        c_charge = 0.60 * charge                            # charge alone caps persistence at 0.6
        c_central = min(1.0, max(0.0, (backlinks[fn] - 3) / 20.0))  # needs real hub-degree; a
        #   cluster where everyone links everyone (bl~8) gives ~0.25, a true hub (bl~22) ~0.95
        c_curated = 0.80 if _norm(fn[:-3]) in curated_names else 0.0   # kept in the live index
        c_type = kern_types.get(m["type"], kern_default)
        # synaptic capture: a low-charge memory co-encoded with a strongly-charged one is stabilised by it
        c_capture = 0.35 * _sessmax.get(_sess[fn], 0.0) if (_sess[fn] and charge < 0.5) else 0.0
        P = 1.0 - (1 - c_charge) * (1 - c_central) * (1 - c_curated) * (1 - c_type) * (1 - c_capture)

        S = 100.0 * (1 - (1 - R) * (1 - P))

        # a refuted/superseded memory fades so its CORRECTION is what surfaces bright. Scan the whole
        # body (annotations get appended at the end), not just the head; judgment in _supersession_factor.
        correction = structural_superseded.get(fn[:-3], "")
        sfac = _supersession_factor(m["body"], m["type"])
        if correction:
            sfac = min(sfac, SUPERSEDED_PENALTY)
        S *= sfac

        why = []
        if sfac <= SUPERSEDED_PENALTY: why.append("superseded->faded")
        elif sfac < 1.0: why.append("part-superseded")
        if c_curated: why.append("curated")
        if c_central >= 0.5: why.append(f"hub*{backlinks[fn]}")
        if charge >= 0.5: why.append("charged" + ("+" if valence > 0.2 else ("-" if valence < -0.2 else "")))
        if R >= 0.6: why.append("recent")
        if m["type"] in ("user", "feedback"): why.append(m["type"])
        out[fn] = {"salience": round(S, 1), "valence": round(valence, 2), "charge": round(charge, 2),
                   "recency": round(R, 2), "persist": round(P, 2), "backlinks": backlinks[fn],
                   "type": m["type"], "date": m["date"] or "undated", "why": ",".join(why),
                   "superseded_by": correction}
    return out

def write_sidecars(scores, dreaming_dir, brain_dir):
    # salience.tsv for dream.py
    tsv = os.path.join(dreaming_dir, "salience.tsv")
    # ties on salience alone left FILENAME ORDER as the tie-break (measured 2026-08-22:
    # 361 memories at exactly 100.0). Order ties by why they stay vivid, not by name.
    # backlinks is the 4th key because persist SATURATES at 1.0 for any hub with >=23
    # backlinks (c_central = min(1, (bl-3)/20)), so 22 scars tied on the first three and the
    # 12 shown were still an alphabetical slice - excluding the second-biggest hub in the
    # corpus and five of his standing hard rules purely because their names sort after "m".
    # Measured 2026-08-22.
    _rank = lambda kv: (kv[1]["salience"], kv[1]["persist"], kv[1]["charge"], kv[1]["backlinks"])
    rows = sorted(scores.items(), key=_rank, reverse=True)
    with io.open(tsv, "w", encoding="utf-8") as f:
        f.write("fname\tsalience\tvalence\tcharge\twhy\n")
        for fn, s in rows:
            f.write(f"{fn}\t{s['salience']}\t{s['valence']}\t{s['charge']}\t{s['why']}\n")

    # SALIENCE.md — what's vivid right now, for the wake ritual
    if brain_dir and os.path.isdir(brain_dir):
        live_rows = [kv for kv in rows if not kv[1].get("superseded_by")]
        # Do not arbitrarily hide equally vivid memories at the display boundary.
        cutoff = _rank(live_rows[min(39, len(live_rows) - 1)]) if live_rows else None
        vivid = [kv for kv in live_rows if _rank(kv) >= cutoff] if cutoff is not None else []
        scars = sorted((kv for kv in live_rows if kv[1]["valence"] <= -0.34 and kv[1]["charge"] >= 0.34),
                       key=_rank, reverse=True)[:12]
        prides = sorted((kv for kv in live_rows if kv[1]["valence"] >= 0.34 and kv[1]["charge"] >= 0.34),
                        key=_rank, reverse=True)[:12]
        with io.open(os.path.join(brain_dir, "SALIENCE.md"), "w", encoding="utf-8") as f:
            f.write("# What's vivid right now\n\n")
            f.write("The brain's affective salience layer — not every memory weighs the same. "
                    "Vivid = recent, or charged, or load-bearing (a hub / a hard rule). The flat "
                    "middle fades toward the bottom without being deleted. Regenerated by "
                    "`salience.py`; read at wake to reach first for what matters.\n\n")
            f.write("## Most vivid (top 40, including cutoff ties)\n\n")
            for fn, s in vivid:
                f.write(f"- **{s['salience']:.0f}** `{fn[:-3]}` — {s['why']}\n")
            f.write("\n## The scars (charged, negative — what we don't want to repeat)\n\n")
            for fn, s in scars:
                f.write(f"- **{s['salience']:.0f}** `{fn[:-3]}` — {s['why']}\n")
            f.write("\n## The wins (charged, positive — what we're proud of)\n\n")
            for fn, s in prides:
                f.write(f"- **{s['salience']:.0f}** `{fn[:-3]}` — {s['why']}\n")
    return tsv

def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    mem = sys.argv[1] if len(sys.argv) > 1 else MEM_DEFAULT
    brain = sys.argv[2] if len(sys.argv) > 2 else BRAIN_DEFAULT
    dreaming = os.path.dirname(os.path.abspath(__file__))
    scores = score_corpus(mem)
    write_sidecars(scores, dreaming, brain)
    vals = [s["salience"] for s in scores.values()]
    vals.sort()
    n = len(vals)
    print(f"salience scored {n} memories; "
          f"median={vals[n//2]:.0f} p10={vals[n//10]:.0f} p90={vals[9*n//10]:.0f} "
          f"vivid(>70)={sum(1 for v in vals if v>70)} faded(<25)={sum(1 for v in vals if v<25)}")

if __name__ == "__main__":
    main()
