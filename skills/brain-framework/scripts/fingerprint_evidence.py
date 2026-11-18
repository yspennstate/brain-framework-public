"""Read-only typed fingerprint evidence; no admission or trust refresh."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib

import connector as c


def semantic_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inspect(deployment):
    from receiver_probe import deployment_fingerprint
    from brain_binding import admission_fingerprint
    profile = Path(deployment["profile"])
    config = profile / "config.toml"
    settings = tomllib.loads(config.read_text()) if config.exists() else {}
    hooks = Path(deployment["hook_config"])
    effective = c.read_json(hooks) if hooks.exists() else None
    evidence = c.state_dir(profile) / "setup-evidence.json"
    base = deployment_fingerprint(deployment)
    selected = admission_fingerprint(deployment)
    return {"schema": "brain-fingerprint-evidence-v1", "profile": str(profile),
            "deployment": {"kind": "deployment", "value": base},
            "admission": {"kind": "admission", "value": selected,
                          "inputs": [base, c.sha(evidence) if evidence.is_file() else None]},
            "raw_config_sha256": c.sha(config) if config.exists() else None,
            "native_hook_trust_semantic_sha256": semantic_sha(settings.get("hooks", {})),
            "native_write_scope_semantic_sha256": semantic_sha(settings.get("sandbox_workspace_write", {}).get("writable_roots", [])),
            "effective_hooks_raw_sha256": c.sha(hooks) if hooks.exists() else None,
            "effective_hooks_semantic_sha256": semantic_sha(effective),
            "qualification": "Different kinds are not a configuration transition. Raw byte changes can still reopen original qualification even when parsed values agree."}
