"""Publish the prompt ledger to the repo as immutable MONTHLY shards.

WHY THIS EXISTS. The ledger `memory/prompts/PROMPTS.jsonl` is append-only and had reached
56.9 MB (16,139 records) by 2026-08-17. GitHub warns past 50 MB and HARD-REJECTS a push at
100 MB, and the nightly mirror copied the whole monolith into the repo, so every commit stored
another ~57 MB blob. On its measured growth that wall was months away, not years - and the way
you find it is the push that fails, with the brain's backup as the casualty.

THE SHAPE OF THE FIX. The LIVE ledger is never touched: 27 modules read it by its own path and
the UserPromptSubmit hook appends to it, so changing it would be a wide blast radius for a
storage problem. Only the REPO COPY changes. Records are grouped by the month of their `ts` and
written as `prompts/archive/PROMPTS_<YYYY-MM>.jsonl`. A closed month never changes again, so git
stores it exactly once, forever; only the current month's shard is rewritten, and at the measured
rate that is about 6 MB. The largest shard is 2026-06 at 31 MB and it is now immutable.

LOSSLESSNESS IS ENFORCED, NOT ASSUMED. Nothing is written unless every source line is accounted
for: the shards are compared to the source as a multiset of exact lines, both directions, and the
run aborts before replacing anything if the check fails. Lines that do not parse, or that carry no
usable `ts`, are NOT dropped - they go to PROMPTS_UNDATED.jsonl so the round-trip still balances.
Writes are atomic (temp file + os.replace) and only happen when content actually changed, so a
crash cannot leave a half-written shard and an unchanged month does not churn the repo.

A MONTH PAST PART_CAP IS PUBLISHED IN PARTS. A busy month can outgrow one file: with agents on
five-minute loops one month reached 67.8 MB and the next 32 MB in six days. A secret scanner that
refuses objects over 64 MB then blocks the whole push, and GitHub refuses anything over 100 MB. Such
a month is written as `PROMPTS_<YYYY-MM>.pNN.jsonl`, broken only at day boundaries and filled from
the 1st, so a closed part never changes when later days arrive. A month under the cap keeps its one
file, so nothing already published churns.

Usage:
    python shard_prompt_archive.py            # publish shards, print the manifest
    python shard_prompt_archive.py --union-existing  # merge a partial ledger into existing shards
    python shard_prompt_archive.py --check     # strict local-ledger audit, write nothing

Union mode preserves existing shard order and the maximum occurrence count of every exact
line across the archive and local ledger. It does not deduplicate repeated ledger entries.
Fetch/merge the latest repo archive before publishing; this is not a concurrent-writer lock.
--check always audits against the local ledger, even with --union-existing: archive-only rows
still fail that audit until the ledger is reconciled. Union publishing instead verifies the
exact merged multiset, including archive-only rows, both before and after writing.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import sys

LIVE = os.path.expanduser(
    r"~/.claude/projects/{{BRAIN_SLUG}}/memory/prompts/PROMPTS.jsonl")
REPO_PROMPTS = os.path.expanduser(
    r"~/brain-repo/01_ai_consciousness/claude/memory/prompts")
ARCHIVE = os.path.join(REPO_PROMPTS, "archive")
UNDATED = "UNDATED"


def month_of(line: str) -> str:
    """YYYY-MM for a ledger line, or UNDATED when there is no usable timestamp.

    Never raises: an unparseable or undated line must still be published somewhere, or the
    round-trip check would fail and the whole publish would abort on one bad record.
    """
    try:
        rec = json.loads(line)
    except Exception:
        return UNDATED
    ts = str(rec.get("ts") or "") if isinstance(rec, dict) else ""
    # "2026-07-09T12:07:09" -> "2026-07"; reject anything not shaped like a real date.
    if len(ts) >= 7 and ts[4] == "-" and ts[:4].isdigit() and ts[5:7].isdigit():
        return ts[:7]
    return UNDATED


def read_source() -> list[str]:
    out = []
    with io.open(LIVE, encoding="utf-8") as f:
        for line in f:
            line = line.strip("\n").strip("\r")
            if line.strip():
                out.append(line)
    return out


def group(lines: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for line in lines:
        buckets[month_of(line)].append(line)
    return buckets


PART_CAP = 40 * 1048576   # bytes; a month over this is published as day-bounded parts


def split_big_months(buckets: dict[str, list[str]]) -> dict[str, list[str]]:
    """A month whose shard would pass PART_CAP becomes PROMPTS_<YYYY-MM>.pNN.jsonl parts.

    Parts break only at a day boundary and are filled greedily from the 1st, so a part's edges depend only on the
    days before it: a closed part never changes when later days are added. A single day over the cap stays whole in
    its own part. Months under the cap keep their one-file name, so nothing old churns.
    """
    out: dict[str, list[str]] = {}
    for key, lines in buckets.items():
        size = sum(len(l.encode("utf-8")) + 1 for l in lines)
        if key == UNDATED or size <= PART_CAP:
            out[key] = lines
            continue
        days: dict[str, list[str]] = collections.OrderedDict()
        for line in lines:
            try:
                d = str(json.loads(line).get("ts") or "")[:10]
            except Exception:
                d = ""
            days.setdefault(d if len(d) == 10 else "~", []).append(line)
        part, cur, cur_size = 1, [], 0
        for d in sorted(days):
            dl = days[d]
            ds = sum(len(l.encode("utf-8")) + 1 for l in dl)
            if cur and cur_size + ds > PART_CAP:
                out["%s.p%02d" % (key, part)] = cur
                part, cur, cur_size = part + 1, [], 0
            cur.extend(dl)
            cur_size += ds
        if cur:
            out["%s.p%02d" % (key, part)] = cur
    return out


def write_atomic(path: str, lines: list[str]) -> bool:
    """Write only when content differs. Returns True if the file changed."""
    body = "\n".join(lines) + "\n"
    if os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                if f.read() == body:
                    return False
        except Exception:
            pass
    tmp = path + ".tmp-%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, path)
    return True


def existing_shards() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if not os.path.isdir(ARCHIVE):
        return found
    for name in sorted(os.listdir(ARCHIVE)):
        if not (name.startswith("PROMPTS_") and name.endswith(".jsonl")):
            continue
        key = name[len("PROMPTS_"):-len(".jsonl")]
        with io.open(os.path.join(ARCHIVE, name), encoding="utf-8") as f:
            found[key] = [l.strip("\n").strip("\r") for l in f if l.strip()]
    return found


def union_shards(source: list[str], published: dict[str, list[str]]) -> dict[str, list[str]]:
    """Keep all archived occurrences; append only missing occurrences from source.

    Compare globally so an existing line in an unexpected shard is still preserved rather
    than duplicated. Existing shard contents and ordering are never rewritten from source.
    """
    merged = {key: list(lines) for key, lines in published.items()}
    available = collections.Counter(line for lines in published.values() for line in lines)
    for line in source:
        if available[line]:
            available[line] -= 1
        else:
            month = month_of(line)
            # a month already published in parts takes new lines in its last part, never a second whole-month file
            parts = sorted(k for k in merged if k.startswith(month + ".p"))
            key = month if month in merged or not parts else parts[-1]
            merged.setdefault(key, []).append(line)
    return merged


def digest(lines: list[str]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify the published shards against the live ledger; write nothing")
    ap.add_argument("--union-existing", action="store_true",
                    help="publish the multiset union with existing shards; --check remains "
                         "a strict local-ledger audit")
    args = ap.parse_args()

    if not os.path.exists(LIVE):
        print("shard_prompt_archive: no live ledger at %s" % LIVE, file=sys.stderr)
        return 2

    source = read_source()
    buckets = split_big_months(group(source))

    if args.check:
        published = existing_shards()
        flat = [l for k in sorted(published) for l in published[k]]
        # TWO NUMBERS, DELIBERATELY NOT ONE - the same discipline the bm25 staleness check needed.
        # INVENTED is the fault and it triggers: a line in the shards that is not in the live
        # ledger means corruption or duplication, and there is no benign reason for it.
        # PENDING is reported and must NOT trigger: the ledger gains a line every time the owner
        # types, so any check run between publishes legitimately sees unpublished records. A gate
        # that failed on that would be red almost always and would teach everyone to ignore it.
        invented = collections.Counter(flat) - collections.Counter(source)
        pending = collections.Counter(source) - collections.Counter(flat)
        ok = not invented
        print("shard_prompt_archive --check: live=%d published=%d shards=%d "
              "pending_publish=%d invented=%d -> %s"
              % (len(source), len(flat), len(published), sum(pending.values()),
                 sum(invented.values()), "ok" if ok else "CORRUPT"))
        if invented:
            print("  %d shard line(s) are absent from the live ledger - the shards disagree with "
                  "their source; do not trust them until this is explained"
                  % sum(invented.values()), file=sys.stderr)
        return 0 if ok else 1

    expected = collections.Counter(source)
    if args.union_existing:
        published_before = existing_shards()
        archived = collections.Counter(line for lines in published_before.values() for line in lines)
        expected |= archived  # maximum multiplicity, not a set or concatenation
        buckets = union_shards(source, published_before)
        print("union existing : %d archive-only occurrence(s) preserved"
              % sum((archived - collections.Counter(source)).values()))

    # Prove the split is lossless BEFORE touching the repo: a multiset comparison both ways,
    # so a duplicated or dropped line cannot pass as equal counts.
    rebuilt = [l for k in sorted(buckets) for l in buckets[k]]
    if collections.Counter(rebuilt) != expected:
        print("shard_prompt_archive: ABORT - the split is not lossless; nothing written",
              file=sys.stderr)
        return 1

    os.makedirs(ARCHIVE, exist_ok=True)
    changed, total_bytes = [], 0
    print("%-10s %8s %10s  %s" % ("shard", "records", "MB", "sha256[:12]"))
    for key in sorted(buckets):
        path = os.path.join(ARCHIVE, "PROMPTS_%s.jsonl" % key)
        lines = buckets[key]
        wrote = write_atomic(path, lines)
        size = os.path.getsize(path)
        total_bytes += size
        if wrote:
            changed.append(key)
        print("%-10s %8d %10.2f  %s %s"
              % (key, len(lines), size / 1048576.0, digest(lines)[:12],
                 "(updated)" if wrote else ""))

    if not args.union_existing:
        # UNDATED is a residue bucket, not a month. A ledger repaired after an undated shard was published leaves that
        # shard behind, and the check below would read its stale lines as invented on every later run. Remove the
        # residue shard when the source has no undated line. A MONTH shard is never removed here: a month missing from
        # the ledger is a loss to investigate, not to mirror.
        undated_path = os.path.join(ARCHIVE, "PROMPTS_%s.jsonl" % UNDATED)
        if UNDATED not in buckets and os.path.exists(undated_path):
            os.remove(undated_path)
            changed.append(UNDATED + " (removed: no undated line left in the ledger)")
            print("%-10s %8d %10s  removed, no undated line left in the ledger" % (UNDATED, 0, "-"))

        # A month that moved between one file and parts leaves the old layout behind. Remove it only when the shards
        # this run wrote already hold every source line (checked from disk), and only names of a month this run
        # published, so a month missing from the ledger is still never removed here.
        stale = [k for k in existing_shards() if k not in buckets and k != UNDATED
                 and k.split(".p")[0] in {b.split(".p")[0] for b in buckets}]
        if stale:
            fresh = existing_shards()
            flat_new = [l for k in sorted(buckets) for l in fresh.get(k, [])]
            if collections.Counter(flat_new) == expected:
                for k in stale:
                    os.remove(os.path.join(ARCHIVE, "PROMPTS_%s.jsonl" % k))
                    changed.append(k + " (removed: its records now live in the month's other layout)")
            else:
                print("shard_prompt_archive: old layout kept - the new shards do not yet hold every line",
                      file=sys.stderr)

    # Re-read from disk and compare to the source again - the check that matters is what the
    # repo will actually carry, not what we intended to write.
    published = existing_shards()
    flat = [l for k in sorted(published) for l in published[k]]
    ok = collections.Counter(flat) == expected
    print()
    print("source records : %d" % len(source))
    print("published      : %d across %d shards, %.2f MB total, largest %.2f MB"
          % (len(flat), len(published), total_bytes / 1048576.0,
             max((os.path.getsize(os.path.join(ARCHIVE, "PROMPTS_%s.jsonl" % k))
                  for k in published), default=0) / 1048576.0))
    print("updated shards : %s" % (", ".join(changed) if changed else "none"))
    print("LOSSLESS       : %s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
