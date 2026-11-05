#!/usr/bin/env python3
"""fetch_prompt_shards.py - bring the brain repo's monthly prompt shards up to origin/main before a union publish.

shard_prompt_archive.py --union-existing keeps every row it can SEE in the published shards. On a second machine
the shards on disk can be older than the ones Windows pushed since, so this runs first: `git fetch origin main`,
then each archive/PROMPTS_*.jsonl that is unmodified against HEAD in the working tree is replaced by its
origin/main content (git show reads only those blobs). A shard this machine has changed locally is left alone and
reported - the union step then merges into it, and the sync step resolves it. Nothing is committed or pushed.

usage: python3 fetch_prompt_shards.py [--dry-run]
"""
import os
import subprocess
import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    print(__doc__)
    raise SystemExit(0)

REPO = os.path.realpath(os.path.expanduser("~/brain-repo"))
REL = "01_ai_consciousness/claude/memory/prompts/archive"
DRY = "--dry-run" in sys.argv[1:]


def git(*args, check=True):
    return subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True, check=check)


def main():
    r = git("fetch", "-q", "origin", "main", check=False)
    if r.returncode != 0:
        print("fetch failed; shards left as they are:", r.stderr.strip()[:200])
        return 0
    remote = [p for p in git("ls-tree", "--name-only", "origin/main", REL + "/").stdout.split()
              if os.path.basename(p).startswith("PROMPTS_") and p.endswith(".jsonl")]
    updated, kept, same = [], [], 0
    for path in remote:
        local = os.path.join(REPO, path)
        dirty = git("diff", "--quiet", "HEAD", "--", path, check=False).returncode != 0
        if dirty:
            kept.append(path)
            continue
        new = subprocess.run(["git", "-C", REPO, "show", "origin/main:" + path], capture_output=True, check=True).stdout
        try:
            if open(local, "rb").read() == new:
                same += 1
                continue
        except OSError:
            pass
        if not DRY:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            tmp = local + ".fetch_tmp"
            with open(tmp, "wb") as f:
                f.write(new)
            os.replace(tmp, local)
        updated.append(os.path.basename(path))
    print("prompt shards: %d on origin/main, %d already current, %s %d (%s), %d locally modified kept (%s)"
          % (len(remote), same, "would update" if DRY else "updated", len(updated), ", ".join(updated) or "-",
             len(kept), ", ".join(os.path.basename(k) for k in kept) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
