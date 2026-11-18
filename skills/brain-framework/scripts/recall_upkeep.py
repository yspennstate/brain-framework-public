"""Bounded admission repair of clean corpus drift, using original authority and builders."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import connector as c


def coverage(deployment):
    result = c.native(deployment, "script", ["dreaming/embedding_coverage.py", "--mem-dir", deployment["memory"],
                                           "--require-complete", "--json"], check=False)
    data = json.loads(result.stdout)
    if result.returncode not in (0, 1) or not isinstance(data, dict):
        raise c.ConnectorError("Original coverage inspection failed")
    return data


def ensure_current(deployment, session):
    if "semantic" not in deployment["enabled"]:
        return {"state": "not-selected", "attempted": False}
    if not session.strip():
        raise c.ConnectorError("Derived recall repair requires the actual session id")
    # Serialize before touching a canonical mesh identity. A second invocation
    # cannot replace or release the first invocation's active leases.
    with c.lock(c.profile_path(deployment["profile"])):
        return _ensure_locked(deployment, session)


def _ensure_locked(deployment, session):
    before = coverage(deployment)
    if before.get("complete"):
        return {"state": "current", "attempted": False}
    clean = (before.get("readable") and before.get("corpus_readable") and before.get("hashes_available")
             and before.get("schema_version", 0) >= 3 and not before.get("problems")
             and not before.get("duplicates") and not before.get("activation_inconsistent"))
    if not clean or not any(before.get(key) for key in ("missing", "stale", "extra")):
        raise c.ConnectorError("Coverage is not clean corpus drift; inspect the original artifact/authority before repair")
    if not session.strip():
        raise c.ConnectorError("Derived recall repair requires the actual session id")

    home = Path(deployment["home"])
    report_path = home.parent / "recall-admission-upkeep.json"
    agent = "recall-upkeep-" + hashlib.sha256((deployment["profile"] + "\0" + session).encode()).hexdigest()[:20]
    resources = ["memory:brain/index", str(home / ".claude/dreaming"), str(home / ".claude/compute"), str(report_path)]
    identity = ["--agent", agent]
    lease = list(identity)
    for resource in resources:
        lease.extend(["--resource", resource])
    c.native(deployment, "mesh", ["hello", *identity, "--runtime", "connector-maintenance", "--session", session,
                                  "--task", "Bounded repair of measured selected recall drift", "--ttl", "45m"])
    claimed = False
    succeeded = False
    try:
        c.native(deployment, "mesh", ["digest", *identity])
        c.native(deployment, "mesh", ["claim", *lease, "--purpose", "Original lexical/vector maintenance after corpus drift", "--ttl", "45m"])
        claimed = True
        c.native(deployment, "mesh", ["assert", *lease])
        # Refuse corrupt/missing models and defer heavy work under the actual body policy.
        c.native(deployment, "script", ["governor/compute_governor_posix.py", "--once"])
        body = c.read_json(home / ".claude/compute/compute_state.json")
        if body.get("status") != "ok" or body.get("mode") == "throttle":
            raise c.ConnectorError("Recall repair deferred by current compute/body state")
        c.native(deployment, "script", ["dreaming/query_embed.py", "--selftest"])
        c.native(deployment, "mesh", ["assert", *lease])
        maintenance = c.maintain_recall(deployment)
        after = coverage(deployment)
        if not after.get("complete"):
            raise c.ConnectorError("Original maintenance did not establish complete current coverage; admission stays blocked")
        report = {"at": c.now(), "state": "repaired", "attempted": True, "agent": agent,
                  "source_revision": deployment["source_revision"], "deployment": deployment["id"],
                  "before_missing": before["missing"], "before_stale": before["stale"],
                  "before_sidecar_sha256": before["sidecar_sha256"],
                  "after_sidecar_sha256": after["sidecar_sha256"], "eligible": after["eligible"],
                  "maintenance": maintenance}
        c.atomic(report_path, report)
        succeeded = True
        return report
    finally:
        if claimed:
            c.native(deployment, "mesh", ["release", *identity, "--all"])
        c.native(deployment, "mesh", ["bye", *identity, "--status", "done" if succeeded else "offline",
                                      "--summary", "Derived recall upkeep verified" if succeeded else "No upkeep success claimed; inspect admission diagnostics"])
