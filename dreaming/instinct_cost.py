"""How long does each reflex trigger take on a long tool call? (claude-1789621d, 13 Oct 2026, recall front R23;
the built texts 14 Oct 2026, front R27)

instinct_prime runs every trigger in instincts.tsv with re.findall over the JSON text of the tool call, cut at its
MATCH_CAP (16 KB). That text is one line, so for a Write or Edit of a big file it is the whole 16 KB. A trigger whose
cost grows with the square of that length is paid on every such call in every session. One learned trigger, two
DOTALL lookaheads with nothing anchoring them, took 5 to 8 s at the cap (0.4 s at 4 KB) from the dream that added it
on 27 Sep until 13 Oct; the dispatcher logged instinct_prime at 5 s or more about 135 times a day, against 9 a day
before it. The anchor prefilter could not skip it: the trigger has no required literal, so it ran on every tool call
(0.09 s on a 2 KB Bash command, 0.4 s on 4 KB).

check() times every trigger with the prefilter off, on the text the hook would match for a Write of this folder's own
code, cut at the cap and at a quarter of it, and returns the triggers that are slow at the cap or that grow much
faster than the text: a linear trigger costs about 4 times as much at the cap as at a quarter of it, a quadratic one
about 16 times.

One text does not show every slow path. The 5 Oct git trigger is quadratic only on a text that has the word git and
none of its eight exclusion words: 7 to 17 s at the cap on such a Write, and 0.01 s on this folder's code, which has
the exclusion words. check_built() reads each trigger's words out of its parse tree, scrubs all of them out of the
same code text, and times the trigger at an eighth of the cap on texts built from it: none of its words, each word
alone at the start, every word, every word but one. A reading that stands out is timed again at an eighth and at a
quarter of the cap; the growth from one to the other tells a square from a line, and a square that predicts a second
or more at the cap is reported from the prediction instead of waited for. The heavy line names the triggers under the
slow line that still cost the most on the long Write.

instincts.py prints these lines after a compile and never refuses on them.

    python instinct_cost.py [instincts.tsv]   one line per slow trigger, the total, the heavy ones; exit 0
    python instinct_cost.py --each [tsv]      every trigger's time and every built text's as it goes (finds a trigger
                                              that never finishes)
    python instinct_cost.py --selftest        planted slow triggers are flagged, two of them only on a built text, and
                                              their rewrites are not
"""
import io
import json
import os
import re
import sys
import time

from re import _constants as _C
from re import _parser as _P

HERE = os.path.dirname(os.path.abspath(__file__))
SLOW_S = 0.5       # seconds at the cap: paid on every long tool call, so worth a line on its own
GROW = 9.0         # time at the cap over time at a quarter of it; linear is about 4, quadratic about 16
GROW_MIN_S = 0.1   # below this at the cap a large ratio is not worth a line
LOOK_S = 0.0015    # a built text this slow at an eighth of the cap is timed again (a square that reaches 0.1 s at the
                   # cap takes 0.0016 s there)
HEAVY_S = 0.05     # under the slow line but this slow at the cap on the code text: named on the heavy line
BUDGET_S = 70.0    # instincts.py waits 120 s for the whole check; the built texts stop here and say how many were left
MAXVAR = 16        # spellings of one word kept open across a factored prefix, a small class or an optional suffix
MAX_ONE = 16       # words tried alone
MAX_ALL1 = 8       # words left out of the every-word text, one at a time
FILL = "qzxjkvwyfbhgmpu0123456789_"   # a scrub writes the first of these that none of the trigger's words uses

_REPEATS = tuple(op for op in (_C.MAX_REPEAT, _C.MIN_REPEAT, getattr(_C, "POSSESSIVE_REPEAT", None)) if op is not None)
_ATOMIC = getattr(_C, "ATOMIC_GROUP", None)
_MEMO = {}


def _cap():
    """instinct_prime's MATCH_CAP, read from its source so the check follows it if it moves."""
    key = ("cap", HERE)
    if key not in _MEMO:
        try:
            m = re.search(r"^MATCH_CAP = (\d+)", io.open(os.path.join(HERE, "instinct_prime.py"),
                                                         encoding="utf-8").read(), re.M)
            _MEMO[key] = int(m.group(1)) if m else 16384
        except OSError:
            _MEMO[key] = 16384
    return _MEMO[key]


def _parts():
    """(head, body): the hook's text for a Write of this folder's code, split where the content starts. body is the
    code JSON-escaped as the hook sees it, at least the cap long."""
    key = ("parts", HERE)
    if key not in _MEMO:
        cap = _cap()
        src = ""
        for name in ("instincts.py", "instinct_prime.py", "hook_dispatch.py"):
            try:
                src += io.open(os.path.join(HERE, name), encoding="utf-8").read()
            except OSError:
                pass
        while len(src) < cap:
            src += src or "def f(x):\n    return x.y\n"
        head = json.dumps({"file_path": "C:/x/y.py", "content": ""}, ensure_ascii=False)[:-2]
        _MEMO[key] = head, json.dumps(src, ensure_ascii=False)[1:-1][:cap]
    return _MEMO[key]


def _texts():
    # the hook matches json.dumps(tool_input, ensure_ascii=False)[:MATCH_CAP]; a Write's input is its path and content
    cap = _cap()
    head, body = _parts()
    whole = head + body
    return whole[:cap // 4], whole[:cap]


def _read_tsv(tsv):
    out = []
    with io.open(tsv, encoding="utf-8") as f:
        next(f, None)
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4 and p[3]:
                out.append((p[0], p[3]))
    return out


def _secs(rx, text):
    t0 = time.perf_counter()
    rx.findall(text)
    return time.perf_counter() - t0


def check(tsv=None, triggers=None, each=None, times=None):
    """Return (flagged, total_s, n): flagged = [(id, s_quarter, s_cap)] worst first; total_s = every trigger on the
    text at the cap. A borderline reading is re-timed and the best of three kept, so a busy machine does not raise a
    flag. times, when given, gets id -> (s_quarter, s_cap) for every trigger timed."""
    if triggers is None:
        triggers = _read_tsv(tsv or os.path.join(HERE, "instincts.tsv"))
    small, large = _texts()
    flagged, total, n = [], 0.0, 0
    for rid, trig in triggers:
        try:
            rx = re.compile(trig, re.I)
        except re.error:
            continue
        if each:
            each("  %-60s ..." % rid[:60])
        a, b = _secs(rx, small), _secs(rx, large)
        total += b
        n += 1
        if times is not None:
            times[rid] = (a, b)
        if each:
            each("  %-60s %8.3f s at a quarter of the cap %8.3f s at the cap" % (rid[:60], a, b))
        if b < GROW_MIN_S or b >= 4 * SLOW_S:
            if b >= SLOW_S:
                flagged.append((rid, a, b))
            continue
        a = min(a, _secs(rx, small), _secs(rx, small))
        b = min(b, _secs(rx, large), _secs(rx, large))
        if times is not None:
            times[rid] = (a, b)
        if b >= SLOW_S or b / max(a, 1e-6) >= GROW:
            flagged.append((rid, a, b))
    flagged.sort(key=lambda r: -r[2])
    return flagged, total, n


def _plain(seq):
    """The text of a parsed sequence made only of literal characters, else None."""
    out = []
    for op, av in seq:
        if op is not _C.LITERAL:
            return None
        out.append(chr(av))
    return "".join(out)


def _small_set(items):
    """The characters of a class of at most 4 plain characters: [tr], or cat|car after the parser turned it into
    ca[tr]."""
    out = []
    for op, av in items:
        if op is not _C.LITERAL:
            return None
        if chr(av) not in out:
            out.append(chr(av))
    return out if len(out) <= 4 else None


def words(trigger):
    """(groups, excluded): the trigger's literal words of 3 or more characters, read from its parse tree.

    groups are the words a text can hold for the trigger to go on, in pattern order. Each is a list of spellings any
    one of which will do; a probe uses the first. The alternatives of one branch made only of literals are one group.
    excluded are the words inside a negative lookaround. A run of literals stays open across a prefix the parser
    factored out of a branch, a small class and an optional plain suffix, so stash|status gives stash and status
    rather than sta, and tests? gives test and tests."""
    groups, excluded, seen = [], [], set()

    def emit(run, negated):
        vs = []
        for v in run:
            if len(v) >= 3 and v.lower() not in (x.lower() for x in vs):
                vs.append(v)
        if not vs:
            return
        if negated:
            for v in vs:
                if v.lower() not in (x.lower() for x in excluded):
                    excluded.append(v)
        elif not all(v.lower() in seen for v in vs):
            seen.update(v.lower() for v in vs)
            groups.append(vs)

    def walk(seq, negated, run):
        """Walk one parsed sequence with the run open when it starts; return the run still open at its end."""
        run = list(run)
        for op, av in seq:
            if op is _C.LITERAL:
                run = [r + chr(av) for r in run]
                continue
            if op is _C.IN:
                chars = _small_set(av)
                if chars and len(run) * len(chars) <= MAXVAR:
                    run = [r + c for r in run for c in chars]
                    continue
            elif op in _REPEATS:
                lo, hi, item = av
                s = _plain(item)
                if s and lo == 0 and hi == 1 and len(run) * 2 <= MAXVAR:
                    run = run + [r + s for r in run]
                    continue
                emit(run, negated)
                emit(walk(item, negated, [""]), negated)
                run = [""]
                continue
            elif op is _C.BRANCH:
                alts = av[1]
                texts = [_plain(a) for a in alts]
                if all(t is not None for t in texts):
                    if len(run) * len(texts) <= MAXVAR:
                        run = [r + t for r in run for t in texts]
                    else:
                        run = [run[0] + t for t in texts]
                    continue
                for a in alts:
                    emit(walk(a, negated, run), negated)
                run = [""]
                continue
            elif op is _C.SUBPATTERN:
                run = walk(av[3], negated, run)
                continue
            elif _ATOMIC is not None and op is _ATOMIC:
                run = walk(av, negated, run)
                continue
            elif op is _C.ASSERT or op is _C.ASSERT_NOT:
                inner = negated or op is _C.ASSERT_NOT
                emit(run, negated)
                emit(walk(av[1], inner, [""]), inner)
                run = [""]
                continue
            elif op is _C.GROUPREF_EXISTS:
                emit(run, negated)
                for sub in av[1:3]:
                    if sub is not None:
                        emit(walk(sub, negated, [""]), negated)
                run = [""]
                continue
            emit(run, negated)
            run = [""]
        return run

    emit(walk(_P.parse(trigger, re.I), False, [""]), False)
    return groups, excluded


def _filler(ws):
    used = set("".join(ws).lower())
    return next((c for c in FILL if c not in used), " ")


def _scrub(text, ws, fill):
    """text with every word of ws, in any case, written over by fill: the same length, none of the words left."""
    ws = sorted({w.lower() for w in ws}, key=len, reverse=True)
    if not ws:
        return text
    return re.sub("|".join(re.escape(w) for w in ws), lambda m: fill * len(m.group(0)), text, flags=re.I)


def _block(ws):
    # the words at the start of the content, then the escaped newline a real line break turns into
    return " " + " ".join(ws) + " \\n"


def _probes(groups):
    """(condition, block) for each built text of one trigger."""
    reps = [g[0] for g in groups]
    out = [("none of its words", "")]
    for r in reps[:MAX_ONE]:
        out.append(("%r and none of its other words" % r, _block([r])))
    if len(reps) > 1:
        out.append(("every one of its words", _block(reps)))
        for i, r in enumerate(reps[:MAX_ALL1]):
            out.append(("every one of its words but %r" % r, _block(reps[:i] + reps[i + 1:])))
    return out


def _judge(rx, small, large, a, b):
    """R23's rule on one text at a quarter of the cap and at the cap: slow, or growing much faster than the text. A
    borderline reading is timed again and the best of three kept. Returns (a, b) or None."""
    if b < GROW_MIN_S:
        return None
    if b < 4 * SLOW_S:
        a = min(a, _secs(rx, small), _secs(rx, small))
        b = min(b, _secs(rx, large), _secs(rx, large))
    if b >= SLOW_S or (b >= GROW_MIN_S and b / max(a, 1e-6) >= GROW):
        return a, b
    return None


def _confirm(rx, make, d8):
    """A built text took d8 s at an eighth of the cap; make(n) gives it cut at n. Returns (s at an eighth, s at a
    quarter, s at the cap, timed) when it is slow at the cap, else None. A text that grows 3 times or more from an
    eighth to a quarter of the cap grows like a square and is expected at 16 times its quarter's time at the cap, a
    line at 4 times; a line expected under the slow line cannot be flagged and is not timed further, and an expected
    second or more, seen twice, is reported without waiting for it (timed False)."""
    cap = _cap()
    t8 = make(cap // 8)
    d8 = min(d8, _secs(rx, t8))
    if d8 < LOOK_S:
        return None
    t4 = make(cap // 4)
    d4 = _secs(rx, t4)
    for again in (False, True):
        square = d4 >= 3 * d8
        pred = d4 * (16 if square else 4)
        if pred < (0.06 if square else SLOW_S):
            return None
        if pred < 1.0:
            break
        if again:
            return d8, d4, pred, False
        d8 = min(d8, _secs(rx, t8))
        d4 = min(d4, _secs(rx, t4))
    tc = make(cap)
    hit = _judge(rx, t4, tc, d4, _secs(rx, tc))
    return (d8, hit[0], hit[1], True) if hit else None


def check_built(triggers, skip=(), base=None, each=None, deadline=None, stats=None):
    """Time each trigger on texts built from the code text without its words.

    Returns (flagged, nprobes, seconds, left): flagged = [(id, condition, s at an eighth, s at a quarter, s at the cap,
    timed)] worst first; left = triggers not tried because the deadline (a time.monotonic() value) passed. skip: ids
    already flagged on the code text. base: id -> the trigger's time at a quarter of the cap on the code text; a built
    text that takes twice that at an eighth stands out at once, while a trigger that is simply heavy does not. stats,
    when given, gets id -> (slowest reading at an eighth, texts timed, texts looked at again)."""
    cap = _cap()
    n8 = cap // 8
    head, body = _parts()
    base = base or {}
    flagged, nprobes, left = [], 0, 0
    t0 = time.perf_counter()
    todo = [(rid, trig) for rid, trig in triggers if rid not in skip]
    for k, (rid, trig) in enumerate(todo):
        if deadline is not None and time.monotonic() > deadline:
            left = len(todo) - k
            break
        try:
            rx = re.compile(trig, re.I)
            groups, excluded = words(trig)
        except Exception:
            continue
        ws = [v for g in groups for v in g] + excluded
        fill = _filler(ws)
        h, b8 = _scrub(head, ws, fill), _scrub(body[:n8], ws, fill)
        whole = []

        def make(n, blk):
            if n <= n8:
                return (h + blk + b8)[:n]
            if not whole:
                whole.append(_scrub(body, ws, fill))
            return (h + blk + whole[0])[:n]

        hot = max(20 * LOOK_S, 2 * base.get(rid, 0.0))
        readings, hit, slowest, looked = [], None, 0.0, 0
        for cond, blk in _probes(groups):
            if each:
                each("    %-56s %-50s ..." % (rid[:56], cond[:50]))
            d = _secs(rx, make(n8, blk))
            nprobes += 1
            slowest = max(slowest, d)
            if each:
                each("    %-56s %-50s %8.4f s at an eighth of the cap" % (rid[:56], cond[:50], d))
            if d >= hot:
                looked += 1
                hit = _confirm(rx, lambda n, blk=blk: make(n, blk), d)
                if hit:
                    hit = (cond,) + hit
                    break
                continue
            readings.append((d, cond, blk))
        if not hit:
            readings.sort(key=lambda r: -r[0])
            for d, cond, blk in readings[:3]:
                if d < LOOK_S or (deadline is not None and time.monotonic() > deadline):
                    break
                looked += 1
                hit = _confirm(rx, lambda n, blk=blk: make(n, blk), d)
                if hit:
                    hit = (cond,) + hit
                    break
        if hit:
            flagged.append((rid,) + hit)
        if stats is not None:
            stats[rid] = (slowest, len(_probes(groups)), looked)
    flagged.sort(key=lambda r: -r[4])
    return flagged, nprobes, time.perf_counter() - t0, left


def report(tsv=None):
    t0 = time.monotonic()
    triggers = _read_tsv(tsv or os.path.join(HERE, "instincts.tsv"))
    times = {}
    flagged, total, n = check(triggers=triggers, times=times)
    kb = _cap() // 1024
    for rid, a, b in flagged:
        print("TRIGGER COST: %s takes %.2f s on a %d KB Write (%.2f s on %d KB, x%.0f). The hook pays this on every long "
              "tool call. A trigger made only of lookaheads, (?=.*X)(?=.*Y), is retried at every character: put \\A "
              "after its flags; bound any other .* span, or anchor on a required word."
              % (rid, b, kb, a, kb // 4, b / max(a, 1e-6)))
    slow = {r[0] for r in flagged}
    built, nprobes, spent, left = check_built(triggers, skip=slow, base={r: ab[0] for r, ab in times.items()},
                                              deadline=t0 + BUDGET_S)
    for rid, cond, d8, d4, dc, timed in built:
        print("TRIGGER COST: %s takes %s%.2f s on a %d KB Write whose text has %s (%.3f s on %d KB, %.3f s on %d KB); "
              "this folder's code, which has its words, does not show it. The hook pays this on every long tool call "
              "with such a text. A lookahead or alternation inside a .* span is tried again from every character: "
              "hoist it to the top level, start the trigger at \\A, or bound the span."
              % (rid, "" if timed else "about ", dc, kb, cond, d8, kb // 8, d4, kb // 4))
    slow.update(r[0] for r in built)
    print("trigger cost: %d triggers, %.2f s in all on a %d KB Write with the prefilter off; %d slow (%d of them only on "
          "a text without the trigger's own words; %d built texts in %.1f s%s)"
          % (n, total, kb, len(slow), len(built), nprobes, spent,
             "; %d triggers not tried on built texts, the %.0f s budget ran out" % (left, BUDGET_S) if left else ""))
    heavy = sorted(((rid, ab[1]) for rid, ab in times.items() if ab[1] >= HEAVY_S and rid not in slow),
                   key=lambda r: -r[1])
    if heavy:
        print("trigger cost, heavy: %d triggers under the slow line take %.2f s or more each on the %d KB Write, %.2f s "
              "together: %s" % (len(heavy), HEAVY_S, kb, sum(s for _r, s in heavy),
                                ", ".join("%s %.2f" % (r, s) for r, s in heavy)))


def _selftest():
    quad = r"(?is)(?=.*\bzzq_never_word_a\b)(?=.*\bzzq_never_word_b\b)"
    # the 5 Oct git trigger's shape: quadratic only on a text that has its first word and none of the others
    nested = (r"(?is)^(?!.*zzqexcl)(?=.*\bzzqgit\b)(?=.*(?:--zzqauto|(?=.*(?:zzqrepo_a|yyqrepo_b|xxqrepo_c))"
              r"(?=.*\bzzqgit\b\s+zzqverb\b)))")
    hoisted = (r"(?is)^(?!.*zzqexcl)(?=.*\bzzqgit\b)(?:(?=.*--zzqauto)|(?=.*(?:zzqrepo_a|yyqrepo_b|xxqrepo_c))"
               r"(?=.*\bzzqgit\b\s+zzqverb\b))")
    # quadratic only on a text without a word that the code text has near its start
    m = re.search(r"[A-Za-z]{6,}", _parts()[1][:1000])
    behind = r"(?is)^(?!.*%s)(?=.*(?:zzqc|(?=.*(?:zzqa|yyqa|xxqa))))" % re.escape(m.group(0) if m else "import")
    cases = (("quadratic lookaheads", quad, True, None),
             ("the same, anchored", r"(?is)\A" + quad[5:], False, False),
             ("a plain linear trigger", r"\bdef\s+\w+\(", False, False),
             ("nested, git-shaped", nested, False, True),
             ("the same, hoisted", hoisted, False, False),
             ("behind a code word", behind, False, True))
    triggers = [(name, trig) for name, trig, _p, _b in cases]
    flagged, _total, _n = check(triggers=triggers)
    plain = {r[0] for r in flagged}
    built = {r[0] for r in check_built(triggers, skip=plain)[0]}
    ok = True
    for name, _trig, want, want_built in cases:
        good = (name in plain) == want and (want_built is None or (name in built) == want_built)
        ok = ok and good
        print("selftest %-24s flagged=%-5s on a built text=%-5s %s"
              % (name, name in plain, name in built, "PASS" if good else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    paths = [a for a in argv if not a.startswith("--")]
    tsv = paths[0] if paths else None
    if "--each" in argv:
        def say(line):
            print(line, flush=True)
        triggers = _read_tsv(tsv or os.path.join(HERE, "instincts.tsv"))
        times = {}
        flagged, total, n = check(triggers=triggers, each=say, times=times)
        built, nprobes, spent, _left = check_built(triggers, skip={r[0] for r in flagged},
                                                   base={r: ab[0] for r, ab in times.items()}, each=say)
        print("%d triggers, %.2f s in all on the text at the cap; flagged: %s; on a built text: %s (%d built texts, "
              "%.1f s)" % (n, total, ", ".join(r[0] for r in flagged) or "none",
                           ", ".join(r[0] for r in built) or "none", nprobes, spent))
        return 0
    report(tsv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
