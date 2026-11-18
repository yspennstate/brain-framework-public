"""Review a GitHub source candidate without changing the installed authority or runtime."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import urllib.request
import uuid

import connector as c


API = "https://api.github.com/repos/yspennstate/brain-framework-public"


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "brain-framework-skill", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def review(profile, revision=None):
    if revision and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise c.ConnectorError("A source candidate must use a full Git commit SHA")
    commit = get_json(API + "/commits/" + (revision or "HEAD"))
    actual = commit["sha"]
    if revision and actual != revision:
        raise c.ConnectorError("GitHub resolved a different commit")
    current = c.source_lock()
    if actual == current["revision"]:
        return {"authority": API, "reviewed_revision": actual, "changed": False, "promotion": "none; current lock is unchanged"}
    tree = get_json(API + "/git/trees/" + commit["commit"]["tree"]["sha"] + "?recursive=1")
    if tree.get("truncated"):
        raise c.ConnectorError("GitHub truncated the candidate tree")
    entries = []
    for row in tree["tree"]:
        if row["type"] == "tree":
            continue
        if row["type"] != "blob" or row.get("mode") not in {"100644", "100755"}:
            raise c.ConnectorError("Candidate includes a link/submodule requiring explicit review: " + row["path"])
        entries.append({"path": row["path"], "sha": row["sha"]})
    before = {r["path"]: r["sha"] for r in current["files"]}
    after = {r["path"]: r["sha"] for r in entries}
    folder = c.state_dir(profile) / "source-candidates" / (actual + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    archive = folder / "candidate.tar.gz"
    url = "https://codeload.github.com/yspennstate/brain-framework-public/tar.gz/" + actual
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "brain-framework-skill"}), timeout=60) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    metadata = {"revision": actual, "files": entries, "archive_sha256": c.sha(archive)}
    verified = c.unpack_source(archive, folder / "source", metadata)
    report = {"authority": API, "at": c.now(), "current_revision": current["revision"], "candidate_revision": actual,
              "changed": True, "added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys()),
              "modified": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
              "source": str(folder / "source"), "integrity": verified,
              "promotion": "Review changes, reread affected originals, build a new lock/archive, then rerun installation and fresh receiver acceptance. No active files changed."}
    c.atomic(folder / "candidate-lock.json", metadata)
    c.atomic(folder / "review.json", report)
    return report
