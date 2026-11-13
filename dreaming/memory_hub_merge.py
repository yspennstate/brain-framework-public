"""Merge another computer's copy of a journal hub into the live one by item, keeping each file's order.
usage: memory_hub_merge.py <live.md> <origin.md> [--apply]   (a backup goes to ~/.claude/brain_graph/memory_sync_backups_<date>)

Journal hubs (OPEN_QUESTIONS, PREDICTIONS, STATE_OF_MIND, MISTAKES) are appended to on every computer, so a plain
file copy in either direction loses the other side's entries. Here an item is a top-level line plus its indented
continuation lines. An item only the origin has goes in after the item that precedes it in the origin; a checkbox
closed in the origin (`- [x]`) over the same open item here (`- [ ]`) takes the origin's version, because it was
resolved on the other computer. A line the live file has is never dropped. Nothing is written without --apply.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import datetime
import difflib
import os
import re
import shutil
import sys

PRED = re.compile(r"^- \[([ x])\] (p\d+) \|")
CHK = re.compile(r"^(\s*- \[)([ x])\] (.*)$")


def _items(L):
    out = []
    for x in L:
        if out and x.strip() and x[:1] in (" ", "\t") and out[-1][0].strip():
            out[-1][1].append(x)
        else:
            out.append([x, []])
    return out


def _similar(x, y):
    return x.strip() in y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.6


def merge_items(lv, og):
    """lv, og: lists of lines (no CR). Returns (out lines, added lines, origin lines not kept, replaced)."""
    LI, OI = _items(lv), _items(og)
    lset, oset = set(lv), set(og)
    live_only = [x for x in lv if x.strip() and x not in oset]
    first_idx = {}
    for i, (f, _) in enumerate(LI):
        if f.strip():
            first_idx.setdefault(f, i)
    adds_after, conts_add, replaced = {}, {}, []
    prev = -1
    for f, conts in OI:
        if not f.strip():
            continue
        if f in first_idx:
            i = first_idx[f]
            for c in conts:
                if c.strip() and c not in lset and not any(_similar(c, y) for y in live_only):
                    conts_add.setdefault(i, []).append(c)
            prev = i
            continue
        hit = next((y for y in live_only if _similar(f, y)), None)
        if hit is not None:
            mo, ml = CHK.match(f), CHK.match(hit)
            if hit in first_idx:
                i = first_idx[hit]
                same_item = mo and ml and (mo.group(3) == ml.group(3) or (
                    PRED.match(f) and PRED.match(hit) and PRED.match(f).group(2) == PRED.match(hit).group(2)))
                if same_item and mo.group(2) == "x" and ml.group(2) == " ":
                    LI[i][0] = f
                    replaced.append(f[:90])
                    for c in conts:
                        if c.strip() and c not in lset:
                            conts_add.setdefault(i, []).append(c)
                prev = i
            continue
        adds_after.setdefault(prev, []).append([f, [c for c in conts if c not in lset]])
    out = []
    for f, conts in adds_after.get(-1, []):
        out += [f] + conts
    for i, (f, conts) in enumerate(LI):
        out += [f] + conts + conts_add.get(i, [])
        for g, gc in adds_after.get(i, []):
            out += [g] + gc
    added = [x for x in out if x not in lset and x.strip()]
    so = set(out)
    lost = [x for x in og if x.strip() and x not in so]
    return out, added, lost, replaced


def backup_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "brain_graph",
                        "memory_sync_backups_" + datetime.date.today().strftime("%Y%m%d"))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__)
        return 2
    live_p, orig_p = argv[0], argv[1]
    with open(live_p, "rb") as fh:
        raw = fh.read()
    crlf = b"\r\n" in raw
    lv = raw.decode("utf-8", "replace").replace("\r", "").split("\n")
    with open(orig_p, "rb") as fh:
        og = fh.read().decode("utf-8", "replace").replace("\r", "").split("\n")
    out, added, lost, replaced = merge_items(lv, og)
    print("%s: live %d -> %d lines; added %d (replaced %d); origin lines not kept %d" % (
        os.path.basename(live_p), len(lv), len(out), len(added), len(replaced), len(lost)))
    for x in added:
        print("  + " + x[:150])
    for x in lost:
        print("  - " + x[:150])
    if "--apply" in argv:
        bk = backup_dir()
        os.makedirs(bk, exist_ok=True)
        shutil.copy2(live_p, os.path.join(bk, os.path.basename(live_p) + ".prehub"))
        t = "\n".join(out)
        if crlf:
            t = t.replace("\n", "\r\n")
        with open(live_p + ".hubtmp", "w", encoding="utf-8", newline="") as fh:
            fh.write(t)
        os.replace(live_p + ".hubtmp", live_p)
        print("applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
