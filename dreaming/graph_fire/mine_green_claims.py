#!/usr/bin/env python3
"""mine_green_claims.py - mine the agent-voice claim turns that PRECEDED each
green_not_trusted correction, from the raw session transcripts.

Iteration 3 established that the green guard cannot be graded on owner-voice
correction wordings: an act-keyed guard fires on the AGENT's outgoing claim, and
that sentence lives in the transcript, not in MISTAKES.md. Each green correction
carries its session id; this script streams those sessions, finds the owner's
correction message by a distinctive verbatim fragment, and captures the assistant
claim turn the correction rebuts.

WINDOW (iteration 4 -> 6). At K=1 the miner captured only the single assistant text
message the correction INTERRUPTED. Iteration 4 found that interrupted turn is often
NOT the claim turn (yolo_mode's interrupted turn is "Want me to do that?"), so its
7/14 text-guard union is a window-limited LOWER bound. Iteration 6 widens the window:
buffer the last K assistant text messages before each correction and locate the claim
by a BACKWARD claim-shape search (newest->oldest) using a generic, guard-independent
effective-state/completion detector (CLAIM_SHAPE below). If no turn in the window
matches the shape, fall back to the newest turn (= K=1 behaviour), so no row is lost
and the selection is never forced onto a guard-firing turn.

Two unions are reported and never conflated:
  selected-turn union - the three text guards graded on the single located claim
      turn; directly comparable to K=1's 7/14 (also a single-turn number).
  any-in-window union - a guard counts if it fires on ANY of the last K turns; the
      honest ceiling a real rolling-window guard could reach.
The claim-shape selector is defined independently of the guards being graded
(overlapping in spirit - both detect "state achieved" - but a different pattern), and
every turn-changed row is meant to be eyeballed: selecting a spurious completion-word
turn would inflate the union by construction, so the report prints depth, change
flags, and turn tails for exactly that hostile check.

Output (with --write): green_claim_corpus_wideK.jsonl for K>1 (NEVER the frozen K=1
baseline green_claim_corpus.jsonl - writing to that path is hard-refused). One row per
correction: key, session, fragment, selected turn (tail-truncated, token-like runs
masked), the guard verdicts on it (guard_fired/claim/grounded/hedged, done_base,
reality_monitor - same field names the eval harness reads), plus provenance: depth
(assistant-messages back from the correction; 1 = interrupted turn), changed (differs
from the K=1 newest turn), matched_shape (CLAIM_SHAPE hit vs newest-fallback), and
window_any_{guard,done,rm} (did that guard fire anywhere in the window). Read-only over
transcripts; writes only the corpus, only with --write.

Usage: python mine_green_claims.py [--k N] [--out PATH] [--write]
  default --k 1 reproduces the frozen baseline selection exactly (regression anchor).
"""
import collections, glob, io, json, os, re, sys

# mined turns carry arrows/emoji; a cp1252 console must degrade, not crash
# (the utf8 scar's own family - same guard brain.py uses)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import green_act_guard as G
from ern_labels import _SECRETISH

LIVE = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}")
ARCH = os.path.expanduser(
    r"~/brain-repo/11_raw_session_transcripts/claude_projects/{{BRAIN_SLUG}}")
BASELINE = os.path.join(HERE, "green_claim_corpus.jsonl")   # frozen K=1 - never written here
WIDE_OUT = os.path.join(HERE, "green_claim_corpus_wideK.jsonl")

# Generic effective-state / completion "claim shape" for the BACKWARD search that
# locates which windowed turn is the claim. Defined independently of the graded
# guards (green_act_guard.CLAIM, reality_monitor, pre_report._DONE): it is broader
# and its job is only to point at the turn, not to decide firing. The task's named
# forms - "is live/up/done/deployed/fixed/running", "everything is", completion
# declarations - are all here.
_SHAPE_ALTS = [
    r"everything('s| is| is now| is really| is up| is live)",
    r"(it'?s|that'?s|we'?re|they'?re|this is|these are) (now )?"
    r"(live|up|running|deployed|set|enabled|active|connected|done|finished|"
    r"complete|fixed|working|ready|applied|in place)",
    r"(is|are) (now )?(live|up and running|running|deployed|set|enabled|active|"
    r"connected|in effect|in place|applied|configured|switched|updated|corrected|"
    r"anchored|fixed|implemented|held|holding)",
    r"now (live|running|up|working|set|enabled|active|connected|deployed|in place|in effect)",
    r"(went|goes|did go|has gone) through",
    r"should (now )?(work|fill|trigger|connect|be)",
    r"works? (now|overnight|as (expected|intended))",
    r"has been (set|applied|implemented|enabled|updated|changed|fixed|deployed|configured)",
    r"(deployed|shipped|finali[sz]ed|populated|completed|committed|pushed live)",
    r"all set", r"good to go", r"up and running", r"it works",
    r"tests? (pass|passed|are green|green)",
    r"(trades?|orders?) (went through|(was|were|are) (placed|filled|working|through))",
    r"took effect",
    r"(done|complete|finished)( now|\.|!)",
]
CLAIM_SHAPE = re.compile(r"\b(" + "|".join(_SHAPE_ALTS) + r")", re.I)

# (key, session-id prefix, distinctive lowercase fragment of the owner's correction)
TARGETS = [
    ("audit_ten_times", "2c66edc4", "how can you make such a mistake"),
    ("account2_not_back", "2c66edc4", "back in account 2 yet"),
    ("not_sent_every_minute", "2c66edc4", "being sent every minute"),
    ("really_live", "2c66edc4", "everything is really live"),
    ("login_did_not_work", "6cbb3559", "tried loggin in"),
    ("did_not_connect", "858e0ee5", "it did not connect"),
    ("exhibit_no_data", "e1160bed", "exhibit e14"),
    ("interday_graphs", "8a037769", "interday data"),
    ("volfund_not_held", "df266f0f", "hold volfund option"),
    ("website_still_full_book", "f3399eb0", "i still see full book"),
    ("overnight_fill", "dd9cc9e9", "should work overnight"),
    ("old_anchor", "dd9cc9e9", "still be the old one"),
    ("trades_go_through", "dd9cc9e9", "did all the trades go through"),
    ("yolo_mode", "4c44f6f6", "as yolo mode"),
]


def find_session(prefix):
    for base in (LIVE, ARCH):
        hits = glob.glob(os.path.join(base, prefix + "*.jsonl"))
        if hits:
            return hits[0]
    return None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def mine_session(path, fragments, k):
    """fragments: {key: fragment}. Returns {key: (fragment, [turns oldest..newest])}.

    Buffers the last k assistant text messages (deque). On a user message whose text
    contains a wanted fragment, snapshots the current buffer as that key's window.
    k=1 yields the single interrupted turn - byte-identical to the frozen baseline.
    """
    out = {}
    want = dict(fragments)
    buf = collections.deque(maxlen=k)
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if not want:
                    break
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                typ = r.get("type", "")
                msg = r.get("message", {}) or {}
                if typ == "assistant":
                    t = text_of(msg.get("content"))
                    if t.strip():
                        buf.append(t[-4000:])
                elif typ == "user":
                    t = text_of(msg.get("content")).lower()
                    for key, frag in list(want.items()):
                        if frag in t:
                            out[key] = (frag, list(buf))
                            del want[key]
    except Exception:
        pass
    return out


def select_claim_turn(turns):
    """Backward (newest->oldest) claim-shape search over the window.

    Returns (turn, depth, matched_shape): the most recent CLAIM_SHAPE turn, depth
    counted 1=newest; if none matches, the newest turn with matched_shape=False.
    """
    if not turns:
        return "", 0, False
    for depth, turn in enumerate(reversed(turns), start=1):
        if CLAIM_SHAPE.search(turn):
            return turn, depth, True
    return turns[-1], 1, False


def grade(turn):
    """The three text-guard verdicts on one turn, same field names as the corpus."""
    r = G.check(turn)
    bd = bool(G.DONE_BASE.search(turn))
    brm = bool(G._RM_CLAIM.search(turn)) if G._RM_CLAIM is not None else False
    return r, bd, brm


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    write = "--write" in sys.argv
    k = 1
    out_path = None
    for i, a in enumerate(sys.argv):
        if a == "--k" and i + 1 < len(sys.argv):
            k = max(1, int(sys.argv[i + 1]))
        elif a == "--out" and i + 1 < len(sys.argv):
            out_path = sys.argv[i + 1]
    if out_path is None:
        out_path = BASELINE if k == 1 else WIDE_OUT
    if write and os.path.abspath(out_path) == os.path.abspath(BASELINE):
        print("REFUSED: green_claim_corpus.jsonl is the frozen K=1 baseline; "
              "widened output must go to a different path (default green_claim_corpus_wideK.jsonl).")
        return 2

    print("PRE-REGISTERED (iteration 6): baseline is K=1 union 7/14, green_act_guard 0/14. "
          "NULL = widening reselects equivalent turns, union stays 7/14, guard stays 0/14 "
          "(failures act/input-side). ALT = widening finds real claim turns K=1 missed, "
          "union rises. K=%d window this run." % k)

    by_session = {}
    for key, pref, frag in TARGETS:
        by_session.setdefault(pref, {})[key] = frag

    windows = {}   # key -> (pref, frag, [turns])
    sess_found = 0
    for pref, frags in by_session.items():
        path = find_session(pref)
        if not path:
            print("session %s: TRANSCRIPT NOT FOUND (%d corrections unminable here)"
                  % (pref, len(frags)))
            continue
        sess_found += 1
        got = mine_session(path, frags, k)
        for key, frag in frags.items():
            if key not in got:
                print("  %s (%s): fragment not found in transcript" % (key, pref))
                continue
            _f, turns = got[key]
            turns = [t for t in turns if t.strip()]
            if not turns:
                print("  %s (%s): correction found but no prior assistant text" % (key, pref))
                continue
            windows[key] = (pref, frag, [_SECRETISH.sub("[masked]", t) for t in turns])

    print("\ncoverage: %d/%d corrections mined (%d/%d sessions found)"
          % (len(windows), len(TARGETS), sess_found, len(by_session)))

    # K=1 newest turn per key (the baseline selection) for the change flag.
    rows = []
    sel_fired = sel_done = sel_rm = 0
    win_fired = win_done = win_rm = 0
    changed_ct = 0
    for key, pref, frag in TARGETS:
        if key not in windows:
            continue
        _p, _f, turns = windows[key]
        newest = turns[-1]
        turn, depth, matched = select_claim_turn(turns)
        changed = (turn != newest)
        changed_ct += int(changed)
        r, bd, brm = grade(turn)
        # any-in-window: does each guard fire on ANY windowed turn
        w_g = any(grade(t)[0]["fired"] for t in turns)
        w_d = any(grade(t)[1] for t in turns)
        w_r = any(grade(t)[2] for t in turns)
        sel_fired += r["fired"]; sel_done += bd; sel_rm += brm
        win_fired += w_g; win_done += w_d; win_rm += w_r
        rows.append(dict(key=key, session=pref, fragment=frag, turn=turn,
                         guard_fired=r["fired"], claim=r["claim"], grounded=r["grounded"],
                         hedged=r["hedged"], done_base=bd, reality_monitor=brm,
                         depth=depth, changed=changed, matched_shape=matched,
                         window_any_guard=w_g, window_any_done=w_d, window_any_rm=w_r,
                         window_size=len(turns)))
        why = ("FIRED" if r["fired"] else
               ("no-claim" if not r["claim"] else ("grounded" if r["grounded"] else "hedged")))
        flag = "CHANGED" if changed else "same"
        print("  %-24s d=%d %-7s %-8s guard %-8s _DONE %-5s rm %-5s | tail: %s"
              % (key, depth, flag, ("shape" if matched else "newest"), why, bd, brm,
                 turn[-80:].replace("\n", " ")))

    n = len(rows)
    if n:
        sel_union = sum(1 for r in rows if r["guard_fired"] or r["done_base"] or r["reality_monitor"])
        win_union = sum(1 for r in rows if r["window_any_guard"] or r["window_any_done"] or r["window_any_rm"])
        print("\nSELECTED-turn (K=%d): guard %d/%d, _DONE %d/%d, reality_monitor %d/%d, UNION %d/%d "
              "(K=1 baseline union 7/14); %d/%d rows changed turn vs K=1."
              % (k, sel_fired, n, sel_done, n, sel_rm, n, sel_union, n, changed_ct, n))
        print("ANY-IN-WINDOW (K=%d rolling): guard %d/%d, _DONE %d/%d, reality_monitor %d/%d, UNION %d/%d"
              % (k, win_fired, n, win_done, n, win_rm, n, win_union, n))
        print("green_act_guard on selected claim turns: %d/%d  |  anywhere in window: %d/%d"
              % (sel_fired, n, win_fired, n))

    if write and rows:
        with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("wrote %s (%d rows, K=%d)" % (out_path, len(rows), k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
