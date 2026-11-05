#!/usr/bin/env python3
"""foresight.py - the brain's PROSPECTIVE pass: not what happened, but what's about to be needed.

Everything else in the brain looks backward or fires reactively. Recall (autobiography, RESUME,
spread_recall) reconstructs the past; the gut (instincts.tsv) fires at the instant of action when a
trigger matches. Neither one PRE-LOADS. This does - at wake, before the first move, it forward-simulates
the session and holds the handful of plans most likely to matter today as explicit if-then commitments.

Grounded:
  - Schacter & Addis 2007 (the prospective brain / constructive episodic simulation): the same core
    network that remembers the past simulates the future by recombining stored episodes. Memory is FOR
    prospection - a brain that only files the past is half a brain.
  - Wamsley et al. 2010; Diba & Buzsaki 2007 (preplay): hippocampal replay is not only backward over what
    happened - sharp-wave ripples also run FORWARD, pre-playing likely trajectories toward a goal before
    they are taken. Anticipatory replay, not just consolidation.
  - Gollwitzer 1999 (implementation intentions): a goal held as a bare intention often fails under load;
    the SAME goal pre-committed as "when situation X arises, I will do Y" fires automatically. Pre-loading
    the if-then is what converts a reactive reflex into a plan that actually triggers.
  - Klinger; Baird et al. 2011 (prospective bias of spontaneous thought): idle cognition runs ~2:1 toward
    the future, rehearsing current concerns. The open loops ARE the concerns; surface them.

What it does (reads only; writes nothing; wires to nothing - safe to run any time):
  1. LIKELY TODAY  - forward preplay from the open loops: the brain's own [owner] forecasts (what he'll
     ask next, from PREDICTIONS.md), the last checkpoint, and open questions.
  2. HOLD THESE    - the reflexes on that likely path, stated as pre-committed if-thens (Gollwitzer),
     ranked by how often the mistake actually recurred x how hot the scar still is. Not all 28 reflexes -
     the few most likely to bite today.
  3. UNGUARDED     - a correction shape that recurs in the mistake ledger with NO reflex ("none yet"):
     a future mistake with no guard. The single highest-value thing to build. Prospective gap-detection.

  python foresight.py
"""
import os, io, re, time
from collections import defaultdict

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
DR = os.path.join(HOME, ".claude", "dreaming")
BRAIN = os.path.join(HOME, "brain-repo", "01_ai_consciousness", "brain")


def _read(path, cap=200000):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read(cap)
    except Exception:
        return ""


def _ascii(s):
    """Fold the common unicode punctuation the memories carry (em-dash, curly quotes, ellipsis) to ASCII,
    then drop anything left, so the line renders clean in the Windows console instead of mojibake."""
    for a, b in (("—", "-"), ("–", "-"), ("‘", "'"), ("’", "'"),
                 ("“", '"'), ("”", '"'), ("…", "..."), ("�", "")):
        s = s.replace(a, b)
    return s.encode("ascii", "ignore").decode("ascii")


def _entries(text):
    """(date or None, text, form) for each entry of the ledger: every bold line, as before, and every dated prose entry
    at column 0 with its indented continuation (the form most corrections take since mid-August). An indented bold line
    is never swallowed into a dated entry, so every bold row reads exactly as it did."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("- **"):
            m = re.search(r"\*\*\s*(\d{4}-\d{2}-\d{2})", ln)
            out.append((m.group(1) if m else None, ln, "bold"))
            i += 1
            continue
        m = re.match(r"^-[ \t]*(\d{4}-\d{2}-\d{2})", ln)
        if m:
            e, j = ln, i + 1
            while j < len(lines) and lines[j].startswith(("  ", "\t")) and not lines[j].lstrip().startswith("- **"):
                e += " " + lines[j].strip()
                j += 1
            out.append((m.group(1), e, "dated"))
            i = j
            continue
        i += 1
    return out


def _mistakes():
    """Parse the correction ledger into (date, reflex_id, lesson) rows. reflex_id is the first token after
    'reflex:' - a name, or 'none' when the correction has no guard yet. A dated prose entry counts on the same terms:
    only when it states 'reflex:' (the ledger's header asks it to since 27 Sep)."""
    rows = []
    gut = _gut()   # read the reflex table once per parse, not once per ledger row
    # The WHOLE ledger (12 Oct 2026, front F10 of the brain run): the default 200,000-char cap of _read stopped at the
    # entries of 2 Sep once MISTAKES.md passed 200 KB (537,705 chars on 12 Oct), so UNGUARDED counted 7 'none yet'
    # rows from August and never saw a correction filed after 2 Sep. Appends go at the END of the file.
    for date, ln, form in _entries(_read(os.path.join(MEM, "MISTAKES.md"), cap=50000000)):
        rm = re.search(r"reflex:\s*(none(?:\s+yet)?|[a-z][a-z0-9_]*)", ln, re.I)
        if form == "bold":
            lm = re.search(r"lesson:\s*(.+?)\s*;?\s*reflex:", ln)
        else:
            lm = re.search(r"(?:lesson|rule):\s*(.+?)\s*[;.]?\s*reflex:", ln, re.I)
        dm = date
        if not (dm and rm):
            continue
        rid = rm.group(1).lower()
        rid = "none" if rid.startswith("none") else rid
        if rid == "none" or rid not in gut:
            # the ledger's organic conventions: "none yet -> (dream ...: now covered - <id>)"
            # when a reflex compiles later, and "reflex: <candidate_name> ... <compiled_id>"
            # when the candidate landed under a different id. Per mistake_graph's coverage
            # rule (a live id anywhere in the entry means covered), resolve to the first
            # tail token that is a LIVE gut id; a none or dead candidate with no live id
            # in its tail stays as-is, so real gaps still warn
            live = gut
            for tok in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", ln[rm.end():]):
                if tok in live:
                    rid = tok
                    break
        if rid == "none":
            # A reflex compiled LATER from this very entry, under its own id, is cited in its sting (the entry's date
            # and its words), not in the ledger: 6 of the 16 'none yet' rows on 12 Oct were covered that way
            # (an_impasse_is_a_routing_decision quotes the 08-03 entry, proxy_validated_on_one the 08-09 one...) and
            # UNGUARDED kept telling every wake to build a reflex that already fired.
            cov = _covered_by_sting(dm, ln[:rm.start()])   # the entry's own words, never its 'reflex:' tail
            if cov:
                rid = cov
        # A word that is neither 'none', a live id, nor shaped like an id ("reflex: the X reflex fired", "reflex: before
        # ...", "reflex: claim ...") is prose after the label, not a citation: it was printed as a missing reflex
        # ('the', 'exists', 'machinery', 'covered', 'last', 'before', 'claim', 'stop' on 12 Oct). Skip the row.
        if rid != "none" and "_" not in rid and rid not in gut:
            continue
        rows.append((dm, rid, (lm.group(1).strip() if lm else "")))
    return rows


_STINGS = []


def _norm_q(s):
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).lower()


def _covered_by_sting(date, entry):
    """The live reflex whose sting/gut/source cites this ledger entry - its date (YYYY-MM-DD or MM-DD) AND a 30-char
    verbatim span of the entry's text - or None. Read-only over instincts.tsv; fail-safe."""
    try:
        if not _STINGS:
            with io.open(os.path.join(DR, "instincts.tsv"), encoding="utf-8") as f:
                hdr = next(f, "").rstrip("\n").split("\t")
                si = hdr.index("sting") if "sting" in hdr else 5
                gi = hdr.index("gut") if "gut" in hdr else 4
                oi = hdr.index("source") if "source" in hdr else 6
                for ln in f:
                    p = ln.rstrip("\n").split("\t")
                    if len(p) > max(si, gi, oi):
                        _STINGS.append((p[0], _norm_q(p[si] + " " + p[gi] + " " + p[oi])))
        e = _norm_q(entry)
        md = date[5:]
        for rid, st in _STINGS:
            if date not in st and md not in st:
                continue
            for i in range(0, max(1, len(e) - 30), 7):
                if e[i:i + 30] in st:
                    return rid
    except Exception:
        return None
    return None


def _gut():
    """id -> (gut_text, charge) from the reflex table."""
    g = {}
    try:
        with io.open(os.path.join(DR, "instincts.tsv"), encoding="utf-8") as f:
            hdr = next(f, "").rstrip("\n").split("\t")
            gi = hdr.index("gut") if "gut" in hdr else 4
            ci = hdr.index("charge") if "charge" in hdr else -1
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) <= gi:
                    continue
                try:
                    ch = float(p[ci])
                except Exception:
                    ch = 0.0
                g[p[0]] = (p[gi], ch)
    except Exception:
        pass
    return g


def _owner_forecasts():
    """The brain's own open bets about what the owner will ask next - the honest preplay source."""
    t = _read(os.path.join(BRAIN, "PREDICTIONS.md"))
    m = re.search(r"## The brain expects[^\n]*\n+(.*?)(\n## |\Z)", t, re.S)
    out = []
    if m:
        for ln in m.group(1).splitlines():
            b = re.match(r"- p=([\d.]+)\s*\[(\w+)\]\s*(.+)", ln.strip())
            if b and b.group(2) == "owner":
                out.append((float(b.group(1)), b.group(3).strip()))
    out.sort(key=lambda x: -x[0])
    return out


def _open_questions(k=3):
    out = []
    for ln in _read(os.path.join(MEM, "OPEN_QUESTIONS.md")).splitlines():
        if ln.lstrip().startswith("- [ ]"):
            out.append(re.sub(r"^\s*- \[ \]\s*", "", ln).strip())
    return out[:k]


def _last_turn():
    t = _read(os.path.join(DR, "RESUME.md"), 4000)
    m = re.search(r"\*\*Last turn:\*\*\s*(.+?)(\n\n|\Z)", t, re.S)
    if not m:
        return ""
    s = _ascii(re.sub(r"\s+", " ", re.sub(r"[*_`]", "", m.group(1))).strip())
    return s[:220] + ("..." if len(s) > 220 else "")


def brief():
    """A compact, forward-only prospective line for the wake breadcrumb (resume_checkpoint writes it into
    RESUME.md, which the wake ritual reads first). The likeliest next ask, the one reflex most worth
    holding today, and any recurring trap still with no guard - so a waking session arrives anticipating,
    not just remembering. Returns '' if there's nothing to say."""
    def _cut(s, n=120):
        s = _ascii(re.sub(r"\s+", " ", s)).strip()
        return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "..."
    parts = []
    try:
        fc = _owner_forecasts()
        if fc:
            # label honestly: the source is the top [owner] PREDICTION (a behavior forecast, not
            # necessarily an ask) - "likely ask" mislabeled it and a waking session misread the line
            # (caught 2026-07-08: "...rotates the ten  hold ->" read as word salad).
            parts.append("owner forecast -> " + _cut(fc[0][1]))
        mis, gut = _mistakes(), _gut()
        dbr = defaultdict(set)
        for d, rid, _ in mis:
            dbr[rid].add(d)
        ranked = sorted(((ch + 0.6 * len(dbr.get(rid, ())), g) for rid, (g, ch) in gut.items()), reverse=True)
        if ranked:
            parts.append("hold -> " + _cut(ranked[0][1]))
        n_none = sum(1 for _, rid, _ in mis if rid == "none")
        if len(dbr.get("none", set())) >= 2:
            parts.append("unguarded -> %d corrections still have no reflex" % n_none)
    except Exception:
        return ""
    return " | ".join(parts)   # a visible seam between slots; two bare spaces blurred them


def main():
    print("=" * 64)
    print(" FORESIGHT  -  what's about to be needed  -  " + time.strftime("%Y-%m-%d %H:%M"))
    print("=" * 64)

    mis = _mistakes()
    gut = _gut()

    # recurrence = distinct dates a reflex-shape was corrected on. old mistakes stopped recurring; the ones
    # that keep coming back are the live risk.
    dates_by_reflex = defaultdict(set)
    lesson_by_reflex = {}
    for d, rid, lesson in mis:
        dates_by_reflex[rid].add(d)
        if lesson and rid not in lesson_by_reflex:
            lesson_by_reflex[rid] = lesson

    # 1. LIKELY TODAY -- forward preplay
    print("\nLIKELY TODAY  (preplay - the brain's forecast, not a fact)")
    lt = _last_turn()
    if lt:
        print("  where you were:  " + lt)
    fc = _owner_forecasts()
    if fc:
        print("  he'll likely ask:")
        for p, txt in fc[:3]:
            print("    - (p=%.2f) %s" % (p, _ascii(txt)[:96]))
    for q in _open_questions():
        print("  still open:  " + _ascii(q)[:96])
    if not (lt or fc):
        print("  (no forecast source yet - PREDICTIONS.md / RESUME.md empty)")

    # 2. HOLD THESE -- reflexes on the likely path, as pre-committed if-thens, ranked recurrence x charge
    ranked = []
    for rid, (g, ch) in gut.items():
        rec = len(dates_by_reflex.get(rid, ()))
        # a reflex earns a pre-load slot by still being hot (charge) and/or having actually recurred
        score = ch + 0.6 * rec
        ranked.append((score, rec, ch, rid, g))
    ranked.sort(reverse=True)
    if ranked:
        print("\nHOLD THESE  (Gollwitzer if-thens - pre-load, don't wait for the trigger)")
        for score, rec, ch, rid, g in ranked[:4]:
            mark = ("  [recurred %dx]" % rec) if rec >= 2 else ""
            print("  * %s%s" % (rid, mark))
            print("      %s" % re.sub(r"\s+", " ", g)[:180])

    # 3. UNGUARDED -- a recurring correction with no reflex is a future mistake with no guard
    none_dates = dates_by_reflex.get("none", set())
    known = set(gut)
    orphan = [(rid, dates_by_reflex[rid]) for rid in dates_by_reflex
              if rid != "none" and rid not in known and len(dates_by_reflex[rid]) >= 1]
    if none_dates or orphan:
        print("\nUNGUARDED  (corrections with no live reflex - a future mistake with no guard)")
        n_none = sum(1 for _, rid, _ in mis if rid == "none")
        if none_dates:
            les = lesson_by_reflex.get("none", "")
            hot = "  <- build a reflex" if len(none_dates) >= 2 else ""
            print("  * %d correction(s) still logged 'none yet'  [%s]%s" % (
                n_none, ", ".join(sorted(none_dates)), hot))
            if les:
                print("      lesson not yet a reflex:  %s" % _ascii(les)[:150])
        for rid, ds in orphan:
            print("  * '%s' cited as its reflex but not in instincts.tsv  [%s]" % (rid, ", ".join(sorted(ds))))
    print()


if __name__ == "__main__":
    main()
