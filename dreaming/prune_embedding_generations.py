"""prune_embedding_generations.py - rotate old embedding generations, never the live one.

    python prune_embedding_generations.py                 # dry run: says what it WOULD free, deletes nothing
    python prune_embedding_generations.py --keep 8        # dry run, keeping the 8 newest
    python prune_embedding_generations.py --keep 8 --apply

WHY THIS EXISTS. publish_memory_embeddings.py writes each embedding build as an IMMUTABLE content-addressed
generation beside the last and then atomically flips .embedding_current.json to point at it. That design is
right - a crash or a sharing violation can never leave recall without an index - but nothing ever removes an old
generation, and nothing was ever written to. Measured 2026-09-16: 211 files, 5.23 GB, the oldest 64 days old, on
a drive sitting at 10 per cent free.

WHY IT IS NOT A DISK SWEEP. The live index is one of those files, selected by the pointer, NOT by name or date -
the plain memory_embeddings.npz is a legacy fallback holding 1,811 vectors from July while the pointer names a
16,301-vector artifact built this morning. Deleting by age would eventually delete the generation recall is
reading. So this reads the pointer FIRST and treats its target as untouchable; if the pointer is missing,
unreadable, or names a file that is not there, it refuses to delete anything at all.

Rollback is the reason to keep more than one, so the default keeps several and the newest are always kept.
"""
import argparse
import glob
import json
import os
import sys

DR = os.path.dirname(os.path.abspath(__file__))
POINTER = os.path.join(DR, ".embedding_current.json")
PATTERN = os.path.join(DR, "memory_embeddings.*.npz")


def live_generation():
    """The generation the authority pointer names, or None when it cannot be established."""
    try:
        with open(POINTER, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return None, "pointer unreadable (%s)" % e
    tgt = d.get("sidecar") or d.get("path") or d.get("artifact")
    if not isinstance(tgt, str) or not tgt:
        return None, "pointer names no sidecar"
    if not os.path.isabs(tgt):
        tgt = os.path.join(DR, tgt)
    if not os.path.exists(tgt):
        return None, "pointer names a file that does not exist: %s" % os.path.basename(tgt)
    return os.path.normcase(os.path.abspath(tgt)), None


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=6, help="how many newest generations to keep (default 6)")
    ap.add_argument("--apply", action="store_true", help="actually delete; without this it only reports")
    a = ap.parse_args(argv)
    if a.keep < 1:
        sys.stderr.write("refusing: --keep must be at least 1\n")
        return 2

    live, why = live_generation()
    if live is None:
        sys.stderr.write("REFUSING TO DELETE ANYTHING: %s\n" % why)
        sys.stderr.write("the live generation cannot be identified, and deleting by date would eventually "
                         "take the one recall is reading.\n")
        return 3

    files = sorted(glob.glob(PATTERN), key=os.path.getmtime, reverse=True)
    keep = set(os.path.normcase(os.path.abspath(p)) for p in files[:a.keep])
    keep.add(live)                                  # belt and braces: the pointer's target is never a candidate
    victims = [p for p in files if os.path.normcase(os.path.abspath(p)) not in keep]
    freed = sum(os.path.getsize(p) for p in victims)
    total = sum(os.path.getsize(p) for p in files)

    print("generations   : %d files, %.2f GB" % (len(files), total / 1e9))
    print("live (pointer): %s" % os.path.basename(live))
    print("keeping       : %d newest + the live one" % a.keep)
    print("candidates    : %d files, %.2f GB" % (len(victims), freed / 1e9))
    if not a.apply:
        print("\nDRY RUN - nothing deleted. Re-run with --apply to rotate.")
        return 0
    gone = 0
    for p in victims:
        try:
            os.remove(p)
            gone += 1
        except Exception as e:
            print("  could not remove %s: %s" % (os.path.basename(p), e))
    print("\nremoved %d of %d, freed about %.2f GB" % (gone, len(victims), freed / 1e9))
    still = live_generation()[0]
    print("live generation still present after the rotation: %s" % bool(still and os.path.exists(still)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
