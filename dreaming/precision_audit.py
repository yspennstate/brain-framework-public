#!/usr/bin/env python3
"""precision_audit.py - on WHAT SURFACE do reflexes fire, vs where the fleet acts at all?

The 07-15 hand pilot (heed_measured_gut_loadbearing_2026_07_15) found ~half of judgment-
reflex fires were vocabulary matches on content being manipulated (memory files, brain
code, mesh commands), not the reflex's domain. This mechanizes that count over the whole
fire log and adds the missing control: the EXPOSURE distribution (what fraction of ALL
act/write tool calls land on each surface), sampled from recent transcripts. A reflex is
DOMAIN-INVERTED when it over-concentrates on the brain's own surfaces (where its topic
vocabulary lives as text) and fails to concentrate on the domain it exists for:

    brainsys_ratio = share(fires on self-referential surface) / exposure share  >= ~2
    domain_ratio   = share(fires on market surface) / exposure share          <= ~1

First measurement (2026-07-16, whole log, exposure n=2208 over 60 transcripts:
self-ref 21%, market 8%):
    sim_flatters      56% self-ref (2.7x)   8% market (1.0x)   INVERTED
    risk_vs_measured_record  47% (2.2x)            6% (0.8x)           INVERTED
    ib_marks          46% (2.2x)            4% (0.5x)           INVERTED
    lookahead         46% (2.2x)            4% (0.5x)           INVERTED (safety-floored)
    his_never_is_mach 45% (2.1x)            4% (0.5x)           INVERTED (never-suppress)
    vxn_long_tenor    29% (1.4x)           16% (2.0x)           ok
    stale_mark_banks  24% (1.1x)           23% (2.9x)           well-targeted
Mechanical reflexes are high on brain-sys too (57-72%) but their domain IS the command
shape, so those fires are in-domain; the inversion test only means something for reflexes
whose trigger vocabulary is topical (build_reflex_ranking tests that via the trigger).

Read-only except for its own scan cache (exposure_scan_cache.json, see exposure_sample).
Usage: python precision_audit.py [--days 7 | --all] [--exposure]
"""
import argparse, io, json, glob, hashlib, os, re, time, collections

import fire_surface
from fire_surface import classify, SELF_REFERENTIAL

HERE = os.path.dirname(os.path.abspath(__file__))
FIRELOG = os.path.join(HERE, "instinct_fires.tsv")
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")
EXPOSURE_CACHE = os.path.join(HERE, "exposure_scan_cache.json")

JUDGMENT = ["sim_flatters", "risk_vs_measured_record", "his_never_is_machinery", "ib_marks",
            "lookahead", "sim_sign_vs_lived_nav", "vxn_long_tenor_state",
            "stale_mark_banks", "check_the_brain_first"]
MECHANICAL = ["ps1_hook_edit", "utf8_text_surgery", "secrets_in_git", "windows_backslash_path"]


# The exposure scan keeps per-file counts (13 Oct 2026). By October the 60 newest transcripts held 11.5 GB, one
# file 2.2 GB, and the dream read all of it twice a cycle (here and in build_reflex_ranking's context damp),
# about 21 minutes each under dream.lock. Transcripts are append-only, so each file's counts are kept with the
# offset just past its last complete line, and a run reads only the bytes added since; a line still being written
# waits for the next run. The sample is drawn per tool call from a hash of the seed and its tool_use id rather
# than one random stream across files, so a file's counts do not depend on which files were read before it; the
# share kept is still `keep`. The cache is keyed by this file's and fire_surface.py's source and by (keep, seed),
# so a change to the scan or the classifier rebuilds it from nothing.
_NEEDLE = b'"tool_use"'
_CHUNK = 16 << 20
_HEAD = 4096
_SAVE_EVERY = 64 << 20


def _exposure_key(keep, seed):
    h = hashlib.sha256()
    for p in (os.path.abspath(__file__), os.path.abspath(fire_surface.__file__)):
        with io.open(p, "rb") as fh:
            h.update(fh.read())
    h.update(repr((float(keep), seed)).encode())
    return h.hexdigest()[:16]


def _head_sig(path, n):
    with io.open(path, "rb") as fh:
        return hashlib.sha256(fh.read(n)).hexdigest()[:16]


def _kept(tid, keep, seed):
    if keep >= 1.0:
        return True
    d = hashlib.blake2b(("%s:%s" % (seed, tid)).encode("utf-8", "replace"), digest_size=8).digest()
    return int.from_bytes(d, "big") / 18446744073709551616.0 < keep


def _count_line(raw, at, keep, seed, counts):
    """Count the act/write tool calls on one transcript line (bytes, no newline) that the sample keeps."""
    try:
        r = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return
    if not isinstance(r, dict) or r.get("type") != "assistant":
        return
    msg = r.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    for i, b in enumerate(content):
        if not (isinstance(b, dict) and b.get("type") == "tool_use"):
            continue
        tn = b.get("name", "")
        if tn not in ("Bash", "PowerShell", "Edit", "Write", "NotebookEdit"):
            continue
        if not _kept(b.get("id") or "%s:%d" % (at, i), keep, seed):
            continue
        try:
            # same 120-char quantum the fire log stores: the exposure control
            # and the fire rows must be classified from equal-width heads, or
            # the ratio inherits the asymmetry (instruments-quantum lesson)
            head = json.dumps(b.get("input") or {}, ensure_ascii=False)[:120]
        except Exception:
            continue
        proxy = "write" if tn in ("Edit", "Write", "NotebookEdit") else "act"
        counts[classify(proxy, head)] += 1


def _scan_tail(path, start, on_line):
    """Hand on_line(raw, offset) every complete line from byte `start` on that carries the tool_use needle;
    return the offset just past the last complete line."""
    done = start
    with io.open(path, "rb") as fh:
        fh.seek(start)
        carry, base = b"", start
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf = carry + chunk
            last = buf.rfind(b"\n")
            if last < 0:
                carry = buf
                continue
            pos = 0
            while True:
                i = buf.find(_NEEDLE, pos, last)
                if i < 0:
                    break
                ls = buf.rfind(b"\n", 0, i) + 1
                le = buf.find(b"\n", i)
                on_line(buf[ls:le], base + ls)
                pos = le + 1
            done = base + last + 1
            carry, base = buf[last + 1:], done
    return done


def _save_cache(cache, key, files):
    try:
        tmp = "%s.tmp.%d" % (cache, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": files}, fh)
        os.replace(tmp, cache)
    except Exception:
        pass


def exposure_sample(n_files=60, keep=0.25, seed=42, cache=EXPOSURE_CACHE):
    """Surface distribution of ALL act/write tool calls in recent transcripts (the control).

    The cache is saved after every file that took more than _SAVE_EVERY bytes of reading, so a first build cut short (the
    dream caps a step at 45 minutes, and a cold read of a large transcript runs at a few MB/s when the disk is
    busy) keeps what it read and the next run carries on from there."""
    files = sorted(glob.glob(os.path.join(PROJ, "*.jsonl")), key=os.path.getmtime)[-n_files:]
    key = _exposure_key(keep, seed)
    old = {}
    if cache:
        try:
            with io.open(cache, encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("key") == key and isinstance(blob.get("files"), dict):
                old = blob["files"]
        except Exception:
            pass
    new = {p: e for p, e in old.items() if os.path.exists(p)}
    base = collections.Counter()
    for path in files:
        e = old.get(path)
        try:
            size = os.path.getsize(path)
            if not (e and e["done"] <= size and e["hlen"] <= size and _head_sig(path, e["hlen"]) == e["head"]):
                e = {"done": 0, "hlen": min(size, _HEAD), "head": _head_sig(path, min(size, _HEAD)), "counts": {}}
            counts = collections.Counter(e["counts"])
            done = _scan_tail(path, e["done"], lambda raw, at: _count_line(raw, at, keep, seed, counts))
            read, e = done - e["done"], dict(e, done=done, counts=dict(counts))
            if cache and read > _SAVE_EVERY:
                new[path] = e
                _save_cache(cache, key, new)
        except Exception:
            pass   # an unreadable file keeps the counts it had; the next run tries again from its old offset
        if e:
            new[path] = e
            base.update(e["counts"])
    if cache:
        _save_cache(cache, key, new)
    return base


def fire_counts(days=None, claude_backed_only=False):
    """reflex -> Counter(surface) over act/write fires from real sessions.

    claude_backed_only: restrict to sessions with a Claude transcript in PROJ - REQUIRED
    whenever the counts will be ratioed against exposure_sample(), which can only see
    Claude tool calls (audit 010 finding 1: the fire log carries Claude AND Codex hook
    rows; an unmatched ratio shipped a damp that failed matched-population replay)."""
    cutoff = 0 if days is None else time.time() - days * 86400
    per = collections.defaultdict(collections.Counter)
    have = {}

    def backed(sid):
        if sid not in have:
            have[sid] = bool(glob.glob(os.path.join(PROJ, sid[:8] + "*.jsonl")))
        return have[sid]

    with io.open(FIRELOG, encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            try:
                ts = float(p[0])
            except Exception:
                continue
            if ts < cutoff or p[1] != "PreToolUse" or p[2] not in ("act", "write"):
                continue
            if not re.match(r"^[0-9a-f]{8}-", p[5]):
                continue
            if claude_backed_only and not backed(p[5]):
                continue
            per[p[3]][classify(p[2], p[4])] += 1
    return per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--exposure", action="store_true", help="also sample + print the exposure control")
    ap.add_argument("--by-mechanism", action="store_true",
                    help="split fire counts by the log's mechanism column (regex|graph|ff; col 7, since 07-16 ~16:15)")
    a = ap.parse_args()

    if a.by_mechanism:
        # per-layer view: which mechanism produces each reflex's fires (rows before the
        # column exist are labeled pre-column). The full-text audit found ib_marks mostly
        # graph-fired while risk_vs_measured_record was mostly regex-fired - opposite repair
        # paths; this makes that visible per night without a corpus build.
        per_m = collections.defaultdict(collections.Counter)
        cutoff = 0 if a.all else time.time() - a.days * 86400
        with io.open(FIRELOG, encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 6:
                    continue
                try:
                    ts = float(p[0])
                except Exception:
                    continue
                if ts < cutoff or p[1] != "PreToolUse" or p[2] not in ("act", "write"):
                    continue
                if not re.match(r"^[0-9a-f]{8}-", p[5]):
                    continue
                per_m[p[3]][p[6] if len(p) > 6 else "pre-column"] += 1
        print("mechanism split (act/write fires, %s):" % ("whole log" if a.all else "last %dd" % a.days))
        for rid in JUDGMENT + MECHANICAL:
            c = per_m.get(rid)
            if not c:
                continue
            print("  %-26s %s" % (rid, "  ".join("%s=%d" % kv for kv in sorted(c.items()))))
        return

    # whenever the exposure control is in play, the fire side must be restricted to the
    # same population the control can see (claude-transcript-backed sessions) - the
    # unmatched combination shipped a damp that failed matched replay (audit 010 F1)
    per = fire_counts(None if a.all else a.days, claude_backed_only=a.exposure)
    base = exposure_sample() if a.exposure else None
    base_tot = sum(base.values()) if base else 0

    def selfshare(c):
        n = sum(c.values())
        return (sum(c[k] for k in SELF_REFERENTIAL) / n, c["market"] / n, n)

    def report(names, label):
        print("== %s ==" % label)
        cols = ["brain_write", "brain_sys", "mesh_cmd", "market", "prose_write",
                "code_write", "sim_cmd", "other_cmd", "unknown"]
        for rid in names:
            c = per.get(rid)
            if not c:
                print("  %-26s no act/write fires in window" % rid)
                continue
            sref, trad, n = selfshare(c)
            extra = ""
            if base:
                bs = sum(base[k] for k in SELF_REFERENTIAL) / base_tot
                bt = base["market"] / base_tot
                extra = "  [x%.1f self, x%.1f domain]" % (sref / bs if bs else 0, trad / bt if bt else 0)
            parts = " ".join("%s=%d" % (k, c[k]) for k in cols if c[k])
            print("  %-26s n=%-5d self-ref=%3.0f%% market=%3.0f%%%s  %s"
                  % (rid, n, 100 * sref, 100 * trad, extra, parts))

    span = "whole log" if a.all else "last %dd" % a.days
    print("precision_audit over %s (act/write fires, real sessions only)" % span)
    if base:
        bs = sum(base[k] for k in SELF_REFERENTIAL) / base_tot
        print("exposure control: n=%d, self-ref=%.0f%% market=%.1f%%"
              % (base_tot, 100 * bs, 100 * base["market"] / base_tot))
    report(JUDGMENT, "judgment reflexes (pilot trio first)")
    report(MECHANICAL, "mechanical reflexes (contrast)")


if __name__ == "__main__":
    raise SystemExit(main())
