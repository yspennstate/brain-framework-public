"""Deterministic, multi-label project assignment with an explicit unknown lane."""
from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path

CLASSIFIER_VERSION = "project-rules-v2"
LEGACY_CLASSIFIER_VERSION = "project-rules-v1"
UNKNOWN_PROJECT = "unknown"
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,63}")
_STOP = frozenset("the a an and or of to in on for by is are was were be this that with from as at".split())


def normalize_project_id(value: object) -> str:
    text = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-_")
    if not text or not text[0].isalpha():
        text = "project-" + (text or "unknown")
    return text[:64]


def _tokens(value: object) -> set[str]:
    return {w.lower() for w in _WORD.findall(str(value or "")) if w.lower() not in _STOP}


def _grams(tokens: set[str]) -> set[str]:
    out = set()
    for token in tokens:
        bounded = "<" + token + ">"
        out.update(bounded[i:i + 3] for i in range(max(0, len(bounded) - 2)))
    return out


def default_config() -> dict:
    return {
        "schema": "project-memory-config-v1",
        "review_interval_seconds": 3600,
        "review_window_occurrences": 1000,
        "strict_raw_reread": True,
        "deny_exact_negative": False,
        "instant_drift_threshold": 0.05,
        "drift_min_progress_actions": 5,
        "drift_min_low_streak": 3,
        "drift_warning_cooldown_seconds": 900,
        "max_cache_age_seconds": 7500,
        "classification_threshold": 2.0,
        "multi_label_ratio": 0.72,
        "max_labels_per_clause": 64,
        "source_ingest_budget_bytes": 256000000,
        "sources": [],
        "replay_sources": [],
        "projects": [],
    }


def load_config(path: str | os.PathLike[str] | None) -> dict:
    cfg = default_config()
    if path and Path(path).exists():
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("project-memory config must be an object")
        cfg.update(loaded)
    if cfg.get("schema") != "project-memory-config-v1":
        raise ValueError("unsupported project-memory config schema")
    if cfg.get("deny_exact_negative"):
        raise ValueError("exact denial is not enabled in the advisory-only reference release")
    cfg["deny_exact_negative"] = False
    projects = []
    seen = set()
    for row in cfg.get("projects") or []:
        if not isinstance(row, dict):
            continue
        project_id = normalize_project_id(row.get("id"))
        if not _ID.fullmatch(project_id) or project_id in seen:
            raise ValueError("project ids must be unique stable identifiers")
        seen.add(project_id)
        item = dict(row)
        item["id"] = project_id
        item["active"] = bool(row.get("active", True))
        item["pinned"] = bool(row.get("pinned", False))
        roots = []
        home = os.path.normcase(os.path.abspath(str(Path.home())))
        temp_roots = {os.path.normcase(os.path.abspath(os.getenv(name))) for name in ("TEMP", "TMP")
                      if os.getenv(name)}
        for raw_root in row.get("roots") or []:
            if not str(raw_root).strip():
                continue
            expanded = os.path.expandvars(os.path.expanduser(str(raw_root)))
            if expanded.startswith("\\\\?\\"):
                expanded = expanded[4:]
            root = os.path.normcase(os.path.abspath(expanded)).rstrip("\\/")
            drive, tail = os.path.splitdrive(root)
            if not root or root == home or (drive and not tail) or root in temp_roots:
                raise ValueError("project roots must be narrow deployment-local directories")
            roots.append(root)
        item["roots"] = sorted(set(roots), key=lambda value: (-len(value), value))
        item["aliases"] = [str(x).strip().casefold() for x in row.get("aliases") or [] if str(x).strip()]
        item["anchors"] = [str(x).strip() for x in row.get("anchors") or [] if str(x).strip()]
        projects.append(item)
    cfg["projects"] = projects
    cfg["review_window_occurrences"] = max(1, min(1000, int(cfg.get("review_window_occurrences", 1000))))
    cfg["review_interval_seconds"] = max(300, int(cfg.get("review_interval_seconds", 3600)))
    cfg["instant_drift_threshold"] = max(0.0, min(1.0, float(cfg.get("instant_drift_threshold", 0.05))))
    cfg["drift_min_progress_actions"] = max(1, int(cfg.get("drift_min_progress_actions", 5)))
    cfg["drift_min_low_streak"] = max(2, int(cfg.get("drift_min_low_streak", 3)))
    cfg["drift_warning_cooldown_seconds"] = max(
        60, int(cfg.get("drift_warning_cooldown_seconds", 900))
    )
    cfg["max_cache_age_seconds"] = max(300, int(cfg.get("max_cache_age_seconds", 7500)))
    return cfg


def classification_digest_for_version(config: dict, classifier_version: str) -> str:
    fields = [{key: project.get(key) for key in ("id", "active", "roots", "aliases", "anchors")}
              for project in config.get("projects") or []]
    policy = {"threshold": config.get("classification_threshold"),
              "multi_label_ratio": config.get("multi_label_ratio"),
              "max_labels": config.get("max_labels_per_clause"), "projects": fields}
    if classifier_version != LEGACY_CLASSIFIER_VERSION:
        policy["classifier_version"] = classifier_version
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def classification_digest(config: dict) -> str:
    return classification_digest_for_version(config, CLASSIFIER_VERSION)


def classify_clause(raw: str, *, cwd: str = "", source_project: str = "",
                    session_prior: tuple[str, ...] = (), config: dict) -> list[dict]:
    """Return zero or more project assignments; unknown is a real retained label."""
    words = _tokens(raw)
    grams = _grams(words)
    cwd_norm = os.path.normcase(os.path.abspath(cwd)) if cwd else ""
    source_fold = str(source_project or "").casefold()
    scored = []
    for project in config.get("projects") or []:
        if not project.get("active", True):
            continue
        score = 0.0
        reasons = []
        pid = project["id"]
        explicit = re.search(r"(?i)\[project\s*:\s*([a-z0-9_-]+)\]", raw)
        if explicit and normalize_project_id(explicit.group(1)) == pid:
            score += 10.0
            reasons.append("explicit_tag")
        if cwd_norm and any(cwd_norm == root or cwd_norm.startswith(root + os.sep)
                            for root in project.get("roots") or []):
            score += 8.0
            reasons.append("cwd_root")
        if source_fold and source_fold in set(project.get("aliases") or []):
            score += 7.0
            reasons.append("source_alias")
        if pid in session_prior:
            score += 1.25
            reasons.append("session_prior")
        best_anchor = 0.0
        for anchor in project.get("anchors") or []:
            aw = _tokens(anchor)
            if not aw:
                continue
            exact = len(words & aw) / float(len(aw))
            ag = _grams(aw)
            fuzzy = len(grams & ag) / float(max(1, len(ag)))
            best_anchor = max(best_anchor, 4.0 * exact + min(1.0, fuzzy))
        if best_anchor:
            score += best_anchor
            reasons.append("anchor_match")
        scored.append((score, pid, reasons))
    threshold = float(config.get("classification_threshold", 2.0))
    viable = [row for row in scored if row[0] >= threshold]
    if not viable:
        return [{"project_id": UNKNOWN_PROJECT, "score": 0.0,
                 "confidence_band": "unknown", "evidence_codes": ["no_threshold_match"]}]
    viable.sort(reverse=True)
    top = viable[0][0]
    ratio = float(config.get("multi_label_ratio", 0.72))
    cap = max(1, min(64, int(config.get("max_labels_per_clause", 64))))
    out = []
    for score, pid, reasons in viable:
        strong = bool(set(reasons) & {"explicit_tag", "cwd_root", "source_alias", "anchor_match"})
        if len(out) >= cap or (score < top * ratio and not strong):
            continue
        confidence = "high" if score >= 7 else "medium" if score >= 4 else "low"
        out.append({"project_id": pid, "score": round(score, 4),
                    "confidence_band": confidence, "evidence_codes": sorted(set(reasons))})
    return out or [{"project_id": UNKNOWN_PROJECT, "score": 0.0,
                    "confidence_band": "unknown", "evidence_codes": ["no_ratio_survivor"]}]


def discover_candidates(records: list[dict], *, limit: int = 50) -> dict:
    """Create a deployment-local starter config without reading prompt prose.

    Only transcript project metadata and working directories are used.  The result is
    never suitable for committing; a human should rename/merge stable IDs locally.
    """
    counts: dict[str, dict] = {}
    for record in records:
        source = str(record.get("project") or "").strip()
        cwd = str(record.get("cwd") or "").strip()
        label = source or (os.path.basename(os.path.normpath(cwd)) if cwd else "")
        if not label or label.casefold() in {"codex", "unknown", "owner"}:
            continue
        pid = normalize_project_id(label)
        row = counts.setdefault(pid, {"id": pid, "active": True, "pinned": False,
                                      "roots": [], "aliases": [], "anchors": [] , "_count": 0})
        row["_count"] += 1
        if source and source.casefold() not in row["aliases"]:
            row["aliases"].append(source.casefold())
        if cwd:
            root = os.path.abspath(cwd)
            if root not in row["roots"]:
                row["roots"].append(root)
        row["anchors"] = sorted(set(row["anchors"]) | _tokens(label))
    cfg = default_config()
    ranked = sorted(counts.values(), key=lambda x: (-x["_count"], x["id"]))[:limit]
    for row in ranked:
        row.pop("_count", None)
    cfg["projects"] = ranked
    return cfg
