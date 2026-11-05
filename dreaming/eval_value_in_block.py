#!/usr/bin/env python3
"""eval_value_in_block.py - did the stored VALUE reach the block? (solver-free locate-bank instrument, 2026-09-12)

For every L1 / L2 locate question, run the real hook (fresh session id each) and read the block it serves: `served`
is "the hook spoke", `vib` (value in block) is "a number in the served text equals the key's answer within its
tolerance". A solver can only copy a value the block carries, so vib is the ceiling of any solver score on the bank,
and it costs no model calls. Keys are read from the quarantine directory and never printed.

    python eval_value_in_block.py --out X.jsonl [--bank l1,l2] [--env K=V ...] [--jobs 4] [--limit N]
"""
import argparse, io, json, os, re, subprocess, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "math_sense.py")
BANK_DIR = os.path.join(os.path.expanduser("~"), "brain-repo", "12_cognitive_architecture",
                        "brain_improvement", "campaign_2026-09-11_20h_mathbrain", "locate_bank")
KEY_DIR = os.path.join(os.path.expanduser("~"), "bank_l1_keys_quarantine")
NUM_RE = re.compile(r"-?\d+/\d+|-?\d+(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


_STRIP_RE = re.compile(r"\(source-id: [^)]*\)|\(hint: [^)]*\)|\[[^\]]*score [0-9.]+\]|python ~/\.claude/dreaming/math_note\.py SOURCE_ID"
                          # 2026-09-13: the label lost "source-id:" and became "(id | prose)". Without
                          # this alternative a 64-hex claim id is read as CONTENT numbers.
                          "|[(][A-Za-z0-9_]{16,}[ ][|][^)]*[)]")


_NOISE_RE = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)|\d{4}-\d{2}-\d{2}|\b\d{4}\.\d{4,5}\b|\^\s*\(?-?\d+|\b(?:Z|R|N)\^?\d\b|\bchi\s*=\s*\d/\d")
_PAGE_RE = re.compile(r"(?:PDF|pp?\.|pages?)\s*(\d+)(?:\s*-\s*(\d+))?", re.I)


def _numbers(text, family=None):
    """Numbers in the block's CONTENT. The source-id hashes, score tags, hint lines and the header's command are
    stripped first (04:2x audit: a key of 25 matched the digits inside 'claim_b23fe6f25d2a'), then the parameter
    noise every note carries (tuples like (0,0), dates, arXiv ids, exponents, Z^2, chi = 1/3) - the 04:5x negative
    control (each key against foreign blocks) accepted 6.6 pct of shuffled pairs through page numbers 1-3 and the
    campaign key 8 before this. For the ledger-page family only the page citations count (PDF24-25, p. 7, pages 3)."""
    out = []
    text = _STRIP_RE.sub(" ", text or "")
    if family == "openproblem_page":
        for a, b in _PAGE_RE.findall(text):
            out.append(float(a))
            if b:
                out.append(float(b))
        return out
    text = _NOISE_RE.sub(" ", text)
    for tok in NUM_RE.findall(text):
        t = tok.replace(",", "")
        try:
            if "/" in t:
                a, b = t.split("/")
                if float(b) != 0:
                    out.append(float(a) / float(b))
            else:
                out.append(float(t))
        except Exception:
            pass
    return out


def _accept(key, nums):
    """True when some number in the block matches the key (answer +- tolerance, or an answer_alt value / range)."""
    targets = []
    ans = key.get("answer")
    tol = float(key.get("tolerance") or 0.0)
    if isinstance(ans, str) and "/" in ans:
        a, b = ans.split("/")
        ans = float(a) / float(b)
    if isinstance(ans, (int, float)):
        targets.append((float(ans) - tol, float(ans) + tol))
    for alt in key.get("answer_alt") or []:
        if isinstance(alt, (list, tuple)) and len(alt) == 2:
            targets.append((float(alt[0]), float(alt[1])))
        elif isinstance(alt, (int, float)):
            targets.append((float(alt) - tol, float(alt) + tol))
    eps = 1e-9
    for x in nums:
        for lo, hi in targets:
            if lo - eps <= x <= hi + eps:
                return True
    return False


def _run(question, env, timeout=90):
    """(block, ids, seconds, invalid). invalid is '' for a clean call, else why the block is not a measurement: a hook
    that exited nonzero, timed out or printed something other than its JSON delivers nothing in a session. Until 30 Sep
    the exit code was never read, a timeout counted as a silent hook, and output that was not JSON was scored as the
    block (account03/B01, B01-R79-GATE-PROOFBANK-VIB-FAILED-HOOK-OUTPUT-SCORED-20260929)."""
    sess = "vib-" + uuid.uuid4().hex[:12]
    t0 = time.time()
    try:
        out = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": question, "session_id": sess}),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "", [], time.time() - t0, "timeout"
    except OSError as exc:
        return "", [], time.time() - t0, "launch error: %s" % type(exc).__name__
    if out.returncode != 0:
        return "", [], time.time() - t0, "hook exited %s" % out.returncode
    txt = (out.stdout or "").strip()
    block = ""
    if txt:
        try:
            block = json.loads(txt).get("hookSpecificOutput", {}).get("additionalContext", "") or ""
        except Exception:
            return "", [], time.time() - t0, "hook output is not the hook's JSON"
    ids = re.findall('^[ ]*[(](?:source-id:[ ]*)?([A-Za-z0-9_]{16,})[ ]*(?:[|]|[)])', block, re.M)  # id, not label prose (2026-09-13 label change)
    return block, ids, time.time() - t0, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--bank", default="l1,l2")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    env = dict(os.environ)
    for kv in a.env:
        k, _, v = kv.partition("=")
        env[k] = v
    work = []
    for bank in [b.strip() for b in a.bank.split(",") if b.strip()]:
        items = json.load(io.open(os.path.join(BANK_DIR, "bank_%s_items.json" % bank), encoding="utf-8"))
        keys = {k["id"]: k for k in json.load(io.open(os.path.join(KEY_DIR, "bank_%s_keys.json" % bank), encoding="utf-8"))}
        for it in items:
            if it["id"] in keys:
                work.append((bank, it, keys[it["id"]]))
    if a.limit:
        work = work[: a.limit]

    def one(w):
        bank, it, key = w
        block, ids, secs, bad = _run(it["question"], env)
        if bad:
            return {"bank": bank, "id": it["id"], "family": it.get("family"), "served": False, "vib": False,
                    "n_cards": 0, "ids": [], "chars": 0, "seconds": round(secs, 2), "invalid": bad}
        nums = _numbers(block, it.get("family"))
        return {"bank": bank, "id": it["id"], "family": it.get("family"), "served": bool(block),
                "vib": bool(block) and _accept(key, nums), "n_cards": len(ids), "ids": [i[:20] for i in ids],
                "chars": len(block), "seconds": round(secs, 2)}

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for r in ex.map(one, work):
            rows.append(r)
    with io.open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    # summary per bank x family; an invalid call is in no count and is counted at the end of the line
    bad = {}
    for r in rows:
        if r.get("invalid"):
            bad[r["invalid"]] = bad.get(r["invalid"], 0) + 1
    valid = [r for r in rows if not r.get("invalid")]
    groups = {}
    for r in valid:
        g = groups.setdefault((r["bank"], r["family"]), [0, 0, 0])
        g[0] += 1
        g[1] += int(r["served"])
        g[2] += int(r["vib"])
    parts = []
    for (bank, fam), (n, s, v) in sorted(groups.items()):
        parts.append("%s/%s n=%d served %d vib %d" % (bank, fam, n, s, v))
    tot = [len(valid), sum(int(r["served"]) for r in valid), sum(int(r["vib"]) for r in valid)]
    med = sorted(r["seconds"] for r in valid)[len(valid) // 2] if valid else 0
    line = "VIB " + " | ".join(parts) + " | ALL n=%d served %d vib %d | median %.2f s | env %s | invalid %d%s" % (
        tot[0], tot[1], tot[2], med, ",".join(a.env) or "-", sum(bad.values()),
        (" (%s)" % "; ".join("%s: %d" % kv for kv in sorted(bad.items()))) if bad else "")
    print(line)
    with io.open(a.out + ".summary.txt", "w", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
