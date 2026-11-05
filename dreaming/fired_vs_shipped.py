#!/usr/bin/env python3
"""fired_vs_shipped.py - did the engine fire BEFORE a real owner correction? The mid-session HEEDING test.

The ultimate test of the pre-mistake system: not "would a guard fire on a curated mistake string" but "did
it fire on the actual assistant prose that the owner then corrected, in a real session." Spec + the two v1
flaws to avoid are from claude:0c44dd79-fleetgraph (whose v1 flattered to a bogus 100%):

  FLAW 1 - counting system messages (system-reminders, tool-results, hook injections, slash commands) as
           owner corrections. Only GENUINE owner pushback text counts.
  FLAW 2 - counting ANY fire as a hit. A long assistant message trips SOME reflex on an incidental token
           (ps1_hook_edit on ".ps1"), so a fire is AMBIGUOUS. The clean, honest signal is the MISS RATE -
           corrections where the engine fired NOTHING on the preceding assistant prose. Confirming a fire
           is a real catch needs a mistake->reflex map (the hard part); here fires are shown, not scored.

So this reports the robust half honestly: of N real owner corrections, on how many did the firing engine
(pre_report.check - graph-fire + lived-anchor + stat/strategy) fire NOTHING on the message that drew the
correction. That is the floor on the mid-session miss rate. Read-only. Usage: python fired_vs_shipped.py [session_glob ...]
"""
import os, io, json, sys, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "graph_fire"))
sys.path.insert(0, HERE)
import pre_report
import mistake_graph as _mg    # the corpus half of the mistake->reflex map (correction wrong/lesson -> reflex)

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")

# genuine owner pushback - the shapes he actually corrects with. NOT a generic "no" inside a longer neutral
# sentence: anchor to correction openings / "you were wrong" / "are you sure" / "did not work/fill".
PUSHBACK = re.compile(
    r"^(no\b|nope|wrong\b|stop\b)"
    r"|\b(that'?s (not|wrong|incorrect)|you keep|you clearly|why (are|did|do|would) you|"
    r"i (said|told you|meant|already (said|told))|big (big )?mistake|add (it|this) to (the )?mistakes|"
    r"you (made|did|got|are making) .{0,25}(wrong|mistake|error)|is (absolutely |just )?(not correct|wrong)|"
    r"not what i (asked|wanted|meant)|are you sure|did ?n'?t (work|fill|connect)|does ?n'?t look (right|correct)|"
    r"much (bigger|more) then you|you did somethig wrong|do a better job)\b", re.I)

SYS_MARK = re.compile(r"<system-reminder>|<command-(name|message)>|\[SYSTEM|hookSpecificOutput|"
                      r"tool_use_id|stdout|hook_event_name|<local-command|"
                      # hook-injected user-role messages are NOT the owner (this was the flaw-1 leak: a
                      # "Stop hook feedback: [work-session]..." message matched pushback via "^stop\b")
                      r"hook feedback|hook additional context|hook is now active|\[work-session\]|\[agent-mesh|\[unconscious\]|"
                      r"\[metamemory|\[protected-health|\[compute|\[association\]|\[goal|UserPromptSubmit|"
                      r"SessionStart|PreToolUse|PostToolUse|\[reminder\]|instinct surfacing", re.I)


def _msg_text(msg):
    c = msg.get("content")
    if isinstance(c, list):
        t = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    elif isinstance(c, str):
        t = c
    else:
        t = ""
    return t


def sequence(transcript):
    """Ordered [(role, text)] with role in {owner, assistant}; system/tool/hook user-messages dropped."""
    seq = []
    try:
        for ln in io.open(transcript, encoding="utf-8", errors="replace"):
            if '"role"' not in ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            msg = obj.get("message", {})
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "assistant":
                t = _msg_text(msg).strip()
                if t:
                    seq.append(("assistant", t))
            elif role == "user":
                t = _msg_text(msg).strip()
                # FLAW 1: only genuine owner text (has text, not a tool_result-only / system / slash-command msg)
                if t and not SYS_MARK.search(t) and len(t) < 2000:
                    seq.append(("owner", t))
    except Exception:
        pass
    return seq


def corrections_with_context(seq):
    """Each genuine owner correction paired with the assistant text immediately before it."""
    out = []
    for i, (r, t) in enumerate(seq):
        if r != "owner" or not PUSHBACK.search(t):
            continue
        prev = None
        for j in range(i - 1, -1, -1):
            if seq[j][0] == "assistant" and seq[j][1].strip():
                prev = seq[j][1]
                break
        if prev and not prev.lstrip().startswith("API Error"):
            out.append((t, prev))   # skip: owner reacting to a system-error turn ("API Error: Stream idle
            # timeout") is not correcting an assistant mistake - a false blind (89f84043 blind-cases-refined)
    return out


def fired(assistant_text):
    """Did the engine fire on this assistant prose? Returns the reflex ids that fired (+ block flag)."""
    try:
        r = pre_report.check(assistant_text[:4000])
        ids = [f.get("id") for f in r.get("graph_fires", [])]
        if r.get("mismatches"):
            ids.append("lived_anchor_mismatch")
        return ids, bool(r.get("should_block"))
    except Exception:
        return [], False


_CORPUS = None


def _corpus():
    """[(token_set, [reflex ids])] from MISTAKES.md - the expected reflex per mistake shape (mistake_graph)."""
    global _CORPUS
    if _CORPUS is None:
        try:
            ids = _mg.load_reflex_ids()
            recs, _ = _mg.parse_corrections(ids)
            _CORPUS = [(set(_mg.toks(r["wrong"] + " " + r["lesson"])),
                        sorted(set(r["named_reflexes"]) | set(r["candidates"])))
                       for r in recs if r["named_reflexes"] or r["candidates"]]
        except Exception:
            _CORPUS = []
    return _CORPUS


def expected_reflexes(prose, thr=0.22):
    """Nearest corpus mistake to this assistant prose (overlap coefficient over wrong/lesson tokens) -> the
    reflex(es) that SHOULD fire. None if no confident match - honest: a weak match is not classified, so
    'right-fire rate' is over MATCHED corrections only. This is fleetgraph's flaw-2 fix: a fire counts as a
    catch only if the RIGHT reflex (for this mistake class) fired, not any incidental token."""
    pt = set(_mg.toks(prose))
    if not pt:
        return None
    best, score = None, 0.0
    for ctoks, refs in _corpus():
        if not ctoks:
            continue
        ov = len(pt & ctoks) / float(min(len(pt), len(ctoks)))
        if ov > score:
            best, score = refs, ov
    return best if score >= thr else None


def main():
    pats = sys.argv[1:] or ["4e59ca13", "0c44dd79", "afec84b0"]
    files = []
    for p in pats:
        files += glob.glob(os.path.join(PROJ, "*%s*.jsonl" % p))
    files = sorted(set(files))
    if not files:
        print("no transcripts matched", pats)
        return
    print("=" * 76)
    print(" FIRED-vs-SHIPPED  -  did the engine fire on the prose the owner then corrected?")
    print("=" * 76)
    # Two honest cuts. (a) HEEDING: would-BLOCK vs surface vs blind - "fired SOMETHING" is not a catch
    # (gfcore trips on incidental tokens, flaw 2), so BLOCK is the honest force-a-stop signal. (b) RIGHT-
    # REFLEX: among corrections mappable to a corpus mistake class, did the EXPECTED reflex fire (flaw-2 fix).
    tot = {"block": 0, "surfaced": 0, "miss": 0, "n": 0, "matched": 0, "right": 0, "wrong": 0, "miss_exp": 0}
    ex_corr = []
    for fp in files:
        seq = sequence(fp)
        corr = corrections_with_context(seq)
        b = s = m = 0
        for ctext, prev in corr:
            ids, blk = fired(prev)
            if blk:
                b += 1
            elif ids:
                s += 1
            else:
                m += 1
            exp = expected_reflexes(prev)
            verdict = "BLOCK" if blk else ("surfaced" if ids else "MISS")
            if exp:
                tot["matched"] += 1
                if set(exp) & set(ids):
                    tot["right"] += 1; verdict += "/right"
                elif ids:
                    tot["wrong"] += 1; verdict += "/wrong-reflex"
                else:
                    tot["miss_exp"] += 1; verdict += "/blind"
            if len(ex_corr) < 6:
                ex_corr.append((ctext, verdict, exp))
        tot["block"] += b; tot["surfaced"] += s; tot["miss"] += m; tot["n"] += len(corr)
        print("\n%s : %d corrections | %d would-BLOCK | %d surfaced | %d blind"
              % (os.path.basename(fp)[:14], len(corr), b, s, m))
    n = tot["n"]
    if n:
        print("\n" + "-" * 76)
        print("HEEDING cut - %d real owner corrections: would-BLOCK %d (%.0f%%) | surfaced-only %d (%.0f%%) | blind %d (%.0f%%)"
              % (n, tot["block"], 100.0*tot["block"]/n, tot["surfaced"], 100.0*tot["surfaced"]/n,
                 tot["miss"], 100.0*tot["miss"]/n))
        print("  -> the engine SURFACES on nearly every corrected message but would STOP on ~%.0f%%; the"
              % (100.0*tot["block"]/n))
        print("     surfaced-but-unblocked warning is the one read past = the heeding gap.")
        mt = tot["matched"]
        if mt:
            print("\nRIGHT-REFLEX cut - of %d corrections mappable to a corpus mistake class (overlap>=0.22, flaw-2 fix):" % mt)
            print("  RIGHT reflex fired: %d (%.0f%%)  |  WRONG reflex only: %d (%.0f%%)  |  nothing: %d (%.0f%%)"
                  % (tot["right"], 100.0*tot["right"]/mt, tot["wrong"], 100.0*tot["wrong"]/mt,
                     tot["miss_exp"], 100.0*tot["miss_exp"]/mt))
            print("  (%d/%d corrections had no confident corpus match - unmapped, honestly not scored.)" % (n - mt, n))
            print("  CAVEAT (eyeballed): the expected-reflex is a TOKEN-OVERLAP nearest-neighbor to the corpus,")
            print("  which returns SPURIOUS matches (a 'forget the mouse' scope correction matched risk_vs_measured_record).")
            print("  So this cut is DIRECTIONAL - it confirms 'surfaced' != 'right reflex' (flaw 2 is real) but the")
            print("  RATE is not trustworthy. A reliable mistake->reflex map needs semantic embeddings or hand-labels,")
            print("  not token overlap - that is genuinely the hard part fleetgraph flagged. Trust the HEEDING cut.")
        print("\nsample corrections + verdict (eyeball genuineness):")
        for ctext, verd, exp in ex_corr:
            e = (" exp=" + ",".join(exp[:2])) if exp else " (unmapped)"
            print("  [%-16s] %s%s" % (verd, _ascii(" ".join(ctext.split()))[:50], e))


def _ascii(s):
    return (s or "").encode("ascii", "replace").decode("ascii")


if __name__ == "__main__":
    main()
