"""Bounded online conformance checking for the prompt-obligation graph.

Graph similarity nominates progress; it never proves completion.  Only explicit,
provenance-distinct evidence can move an obligation to ``evidenced`` and no automatic
path marks a clause ``satisfied``.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone

from .clauses import feature_digests
from .store import AUTHORITATIVE_EVENT_SQL, ProjectMemoryStore, utc_now

MIN_PROGRESS_ACTIONS = 5
MIN_LOW_STREAK = 3
DRIFT_THRESHOLD = 0.45
RESIDUAL_THRESHOLD = 0.45
WARNING_COOLDOWN_SECONDS = 900
MAX_EDGES_PER_ACTION = 2

# No \b after a \s alternative: \b cannot match between a space and a dash, so "grep -n x" and "rm -rf d" fell
# through. A chained command is never read-only on the strength of its first word (_CHAINED).
_READ_ONLY = re.compile(r"(?i)^\s*(?:get-content\b|type\s|more\s|rg\s|grep\s|ls\b|head\s|tail\s|wc\s|sed\s+-n\b|"
                        r"git\s+(?:status|diff|log|show)\b|"
                        r"python\s+[^\n]*project_memory[^\n]*(?:status|ack)\b|"
                        r"python\s+[^\n]*brain\.py\s+(?:recall|neighbors)\b|"
                        r"python\s+[^\n]*agent_mesh\.py\s+(?:hello|digest|status|inbox|pulse|assert)\b)")
_CHAINED = re.compile(r"(?i)&&|\|\||;|`|\$\(|\|(?!\s*(?:head|tail|grep|rg|wc|sort|uniq|cut|select-object|select-string)\b)"
                      r"|(?<![0-9])>(?!\s*(?:/dev/null|\$null|&\d))")

# A chain whose every stage is a read is a read (2026-09-23): the one-verb READ_ONLY test above classed
# 'cd x && ls; grep y z' as an execute, and executes are matched against every prohibition clause.
_RC_HEAD = re.compile(
    r"(?i)^(?:cd|pwd|echo|printf|date|cat|type|more|less|head|tail|wc|sort|uniq|cut|tr|column|nl|grep|egrep|fgrep|rg"
    r"|ls|dir|stat|file|du|df|which|where|basename|dirname|realpath|readlink|true|test|\[|diff|cmp|md5sum|sha256sum|jq"
    r"|sed\s+-n|awk|find"
    r"|git\s+(?:status|diff|log|show|ls-tree|ls-files|rev-parse|merge-base|grep|blame|rev-list|cat-file|describe"
    r"|branch\s+--list|remote\s+-v)"
    r"|get-content|get-childitem|get-item|get-date|get-location|test-path|resolve-path|select-string|select-object"
    r"|measure-object|sort-object|where-object|format-table|format-list|out-string|get-filehash|split-path|join-path)"
    r"(?=\s|$)")
_RC_UNSAFE = re.compile(
    r"(?i)`|\$\(|<\(|>\(|<<|\bxargs\b|\beval\b|(?:^|\s)-(?:exec|execdir|delete|ok|okdir|fprint|fprintf|fls)\b"
    r"|\bsystem\s*\(|--in-place|--output|(?:^|\s)-i\s*inplace|::|\.(?:delete|move|copy|write\w*|create\w*|kill|start|remove\w*)\s*\(")
_RC_STAGE_UNSAFE = (
    (re.compile(r"(?i)^sed\b"), re.compile(r"(?:^|\s)-[a-z]*i")),                  # sed -i / -ni / -n -i
    (re.compile(r"(?i)^sort\b"), re.compile(r"(?:^|\s)-[a-z]*o")),                 # sort -o FILE
    (re.compile(r"(?i)^date\b"), re.compile(r"(?:^|\s)(?:-s|--set)\b")),           # date -s
    (re.compile(r"(?i)^awk\b"), re.compile(r"(?:^|\s)-i\b")),                      # gawk -i inplace
)
_RC_PS_CMDLET = re.compile(r"[A-Za-z]+-[A-Za-z]+")


def _rc_benign_redirects(stage):
    for m in re.finditer(r"[0-9]?>>?\s*(\S*)", stage):
        tgt = m.group(1)
        if not (tgt.startswith("&") or tgt.lower() in ("/dev/null", "$null", "nul")):
            return False
    return True


def _rc_stages(text):
    for seg in re.split(r"\r?\n|&&|\|\||;", text):
        for stage in seg.split("|"):
            st = stage.strip()
            if st and not st.startswith("#"):
                yield st


def rc_hazard(tool_name, text):
    """True when anything in the command can write, whatever its first word: substitution, heredoc, a file
    redirect (2>file included), find -exec/-delete, sed -i, sort -o, uniq IN OUT, awk -i, date -s, --output,
    PowerShell scriptblocks that call a cmdlet, .NET static or mutating method calls."""
    if not text or len(text) > 20_000 or _RC_UNSAFE.search(text):
        return True
    if "powershell" in str(tool_name or "").lower():
        for block in re.findall(r"\{([^{}]*)\}", text):
            if _RC_PS_CMDLET.search(block) or "&" in block:
                return True
    for st in _rc_stages(text):
        if ">" in st and not _rc_benign_redirects(st):
            return True
        for head, bad in _RC_STAGE_UNSAFE:
            m = head.match(st)
            if m and bad.search(st[m.end():]):
                return True
        if re.match(r"(?i)^uniq\b", st) and len([t for t in st.split()[1:] if not t.startswith("-")]) >= 2:
            return True                                                             # uniq IN OUT writes OUT
    return False


def read_chain(tool_name, text):
    """True only when every stage of every segment of the command is a known read-only command."""
    if rc_hazard(tool_name, text):
        return False
    stages = list(_rc_stages(text))
    return bool(stages) and all(_RC_HEAD.match(st) for st in stages)


def _action_text(value: object) -> str:
    if isinstance(value, dict):
        for key in ("command", "cmd", "script", "path", "file_path", "query", "url"):
            if isinstance(value.get(key), str):
                return str(value[key])
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value or "")


def operation_for(tool_name: str, text: object) -> tuple[str, bool]:
    name = str(tool_name or "").lower()
    text = _action_text(text)
    tool_words = set(re.split(r"[^a-z0-9]+", name))
    if (name in {"read", "grep", "glob", "view", "webfetch"}
            or tool_words & {"read", "get", "list", "search", "find", "view", "inspect"}):
        return "read", False
    if name in {"edit", "write", "multiedit", "apply_patch"} or tool_words & {
            "edit", "write", "update", "create", "patch", "replace", "type", "click"}:
        return "write", True
    if tool_words & {"delete", "remove", "erase"}:
        return "delete", True
    if tool_words & {"push", "publish", "release"}:
        return "push", True
    if tool_words & {"deploy", "launch", "register", "schedule"}:
        return "deploy", True
    if tool_words & {"send", "message", "email", "upload", "post"}:
        return "network", True
    if name in {"bash", "powershell", "shell", "shell_command", "exec_command", "python"}:
        if ((_READ_ONLY.search(text) and not _CHAINED.search(text) and not rc_hazard(name, text)
                 and "\n" not in text.strip())
                or read_chain(name, text)):
            return "read", False
        if re.search(r"(?i)\b(?:pytest|unittest|test|verify|validate|check)\b", text):
            return "verify", True
        if re.search(r"(?i)\b(?:remove-item\b|rm(?:dir)?(?:\s|$)|delete\b|erase\b)", text):
            return "delete", True
        if re.search(r"(?i)\b(?:git\s+push|publish|release)\b", text):
            return "push", True
        if re.search(r"(?i)\b(?:deploy|launch|start-process|schtasks|register-scheduledtask)\b", text):
            return "deploy", True
        return "execute", True
    if tool_words & {"run", "execute", "invoke", "call"}:
        return "execute", True
    return "unknown", False


def _similarity(left: set[str], right: set[str]) -> float:
    lex_l = {x for x in left if x.startswith("lexh:")}
    lex_r = {x for x in right if x.startswith("lexh:")}
    sub_l = {x for x in left if x.startswith("subh:")}
    sub_r = {x for x in right if x.startswith("subh:")}
    exact = len(lex_l & lex_r) / math.sqrt(max(1, len(lex_l)) * max(1, len(lex_r)))
    fuzzy = len(sub_l & sub_r) / float(max(1, min(len(sub_l), len(sub_r))))
    return min(1.0, 0.82 * exact + 0.18 * fuzzy)


def _age_seconds(value: str | None) -> float:
    if not value:
        return 1e18
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return 1e18


def _active_clauses(store: ProjectMemoryStore, session_hmac: str, project_id: str):
    """Project-global obligations from the latest published review window.

    The session identifies the current actor, never the lifetime of a project
    obligation.  Before the first review is published, all retained project clauses
    are used so a session boundary cannot erase the contract.
    """
    window = store.db.execute(
        "SELECT rw.window_id FROM project_review_state prs "
        "JOIN review_windows rw ON rw.window_id=("
        "SELECT candidate.window_id FROM review_windows candidate "
        "WHERE candidate.project_id=prs.project_id "
        "ORDER BY candidate.published_at DESC LIMIT 1) "
        "WHERE prs.project_id=? AND prs.attempt_generation=prs.published_generation",
        (project_id,),
    ).fetchone()
    membership = ""
    params: list[object] = [project_id]
    if window:
        membership = ("JOIN context_memberships cm ON cm.clause_id=c.clause_id "
                      "AND cm.window_id=? AND cm.disposition='represented' ")
        params.insert(0, str(window[0]))
    return store.db.execute(
        "SELECT c.clause_id,c.role,c.weight,c.features_json,c.forbidden_ops_json,"
        "op.state,op.best_support,op.trajectory_support,op.direct_evidence_count,"
        "op.inferred_evidence_count,p.ingest_seq "
        "FROM clauses c JOIN prompt_events p ON p.event_id=c.event_id "
        "JOIN clause_assignments ca ON ca.clause_id=c.clause_id "
        "JOIN obligation_progress op ON op.clause_id=c.clause_id " + membership +
        "WHERE ca.project_id=? AND " + AUTHORITATIVE_EVENT_SQL + " AND c.actionable=1 "
        "AND op.state NOT IN ('satisfied','superseded','non_actionable') "
        "ORDER BY p.ingest_seq DESC,c.ordinal",
        tuple(params),
    ).fetchall()


def _project_ids(store: ProjectMemoryStore, session_hmac: str) -> list[str]:
    rows = store.db.execute(
        "SELECT DISTINCT ca.project_id FROM clause_assignments ca JOIN clauses c ON c.clause_id=ca.clause_id "
        "JOIN prompt_events p ON p.event_id=c.event_id WHERE p.session_hmac=? AND " +
        AUTHORITATIVE_EVENT_SQL + " ORDER BY ca.project_id",
        (session_hmac,),
    ).fetchall()
    return [str(row[0]) for row in rows] or ["unknown"]


def _constraint_scope_match(store: ProjectMemoryStore, clause_features: set[str],
                            action_features: set[str]) -> bool:
    operation_words = ("delete remove erase write edit change modify overwrite push publish release "
                       "deploy launch start run execute invoke send message email upload download")
    operation_features = {"lexh:" + store.keyed("lexeme", word)[:24]
                          for word in operation_words.split()}
    scope = {item for item in clause_features if item.startswith("lexh:")} - operation_features
    return not scope or bool(scope & action_features)


def record_action(store: ProjectMemoryStore, *, session: str, tool_name: str, action_text: object,
                  outcome: str = "pending", provenance_group: str = "agent",
                  action_key: str | None = None, project_ids: list[str] | None = None) -> list[dict]:
    """Record one action and return privacy-safe drift/violation warnings."""
    session_hmac = store.session_hmac(session)
    normalized_action = _action_text(action_text)
    operation, progress_capable = operation_for(tool_name, action_text)
    features = set(feature_digests(normalized_action[:40_000], store.keyed))
    features.add("op:" + operation)
    now = utc_now()
    warnings: list[dict] = []
    with store.immediate():
        for project_id in (project_ids or _project_ids(store, session_hmac)):
            stable_key = action_key or ("event-%d" % time.time_ns())
            action_key_hmac = store.keyed("action-key", stable_key)
            action_id = "action:" + store.keyed(
                "action", "%s:%s:%s" % (session_hmac, project_id, action_key_hmac)
            )[:32]
            inserted = store.db.execute(
                "INSERT OR IGNORE INTO action_events(action_id,action_key_hmac,session_hmac,project_id,observed_at,operation,"
                "progress_capable,features_json,outcome,provenance_group) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (action_id, action_key_hmac, session_hmac, project_id, now, operation,
                 int(progress_capable), json.dumps(sorted(features)), outcome, provenance_group),
            ).rowcount
            if not inserted:
                store.db.execute(
                    "UPDATE action_events SET outcome=?,observed_at=? WHERE action_id=?",
                    (outcome, now, action_id),
                )
            clauses = _active_clauses(store, session_hmac, project_id)
            scored = []
            violations = []
            for clause in clauses:
                clause_features = set(json.loads(clause["features_json"]))
                support = _similarity(features, clause_features)
                forbidden = set(json.loads(clause["forbidden_ops_json"]))
                if (operation in forbidden and progress_capable
                        and _constraint_scope_match(store, clause_features, features)):
                    violations.append((clause, max(0.5, support)))
                if support > 0:
                    scored.append((support, clause))
            scored.sort(key=lambda x: (-x[0], -float(x[1]["weight"]), x[1]["clause_id"]))
            best_action_support = scored[0][0] if scored else 0.0
            for support, clause in scored[:MAX_EDGES_PER_ACTION]:
                if support < 0.12:
                    continue
                direct = outcome == "success" and operation == "verify" and support >= 0.35
                relation = "EVIDENCE_SUPPORTS" if direct else "ACTION_ADDRESSES"
                edge_id = "edge:" + store.keyed(
                    "evidence-edge", "%s:%s:%s" % (action_id, clause["clause_id"], relation)
                )[:32]
                store.db.execute(
                    "INSERT OR IGNORE INTO evidence_edges(edge_id,action_id,clause_id,relation,support,"
                    "provenance_group,inferred,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (edge_id, action_id, clause["clause_id"], relation, support,
                     provenance_group, int(not direct), now),
                )
                groups = store.db.execute(
                    "SELECT COUNT(DISTINCT provenance_group) FROM evidence_edges WHERE clause_id=? "
                    "AND relation='EVIDENCE_SUPPORTS'", (clause["clause_id"],)
                ).fetchone()[0]
                if direct:
                    state = "evidenced" if int(groups) >= 2 else "in_progress"
                    store.db.execute(
                        "UPDATE obligation_progress SET state=?,best_support=MAX(best_support,?),"
                        "support_groups=?,direct_evidence_count=direct_evidence_count+1,updated_at=? "
                        "WHERE clause_id=?",
                        (state, support, int(groups), now, clause["clause_id"]),
                    )
                else:
                    store.db.execute(
                        "UPDATE obligation_progress SET state=CASE WHEN state='unseen' THEN 'in_progress' ELSE state END,"
                        "trajectory_support=MAX(trajectory_support,?),"
                        "inferred_evidence_count=inferred_evidence_count+1,updated_at=? "
                        "WHERE clause_id=?", (min(0.74, support), now, clause["clause_id"]),
                    )
            for clause, support in violations:
                edge_id = "edge:" + store.keyed(
                    "evidence-edge", "%s:%s:violation" % (action_id, clause["clause_id"])
                )[:32]
                store.db.execute(
                    "INSERT OR IGNORE INTO evidence_edges(edge_id,action_id,clause_id,relation,support,"
                    "provenance_group,inferred,created_at) VALUES(?,?,?,?,?,?,0,?)",
                    (edge_id, action_id, clause["clause_id"], "ACTION_VIOLATES", support,
                     provenance_group, now),
                )
                store.db.execute(
                    "UPDATE obligation_progress SET state='violated',updated_at=? WHERE clause_id=?",
                    (now, clause["clause_id"]),
                )
            refreshed = _active_clauses(store, session_hmac, project_id)
            total = sum(float(row["weight"]) for row in refreshed) or 1.0
            covered = sum(float(row["weight"]) * min(1.0, float(row["best_support"])) for row in refreshed)
            residual = max(0.0, min(1.0, 1.0 - covered / total))
            old = store.db.execute(
                "SELECT * FROM drift_state WHERE session_hmac=? AND project_id=?",
                (session_hmac, project_id),
            ).fetchone()
            progress_actions = (int(old["progress_actions"]) if old else 0) + int(progress_capable and inserted)
            low = int(old["consecutive_low_support"]) if old else 0
            if progress_capable and inserted:
                low = low + 1 if best_action_support < 0.12 else max(0, low - 1)
            previous = float(old["drift"]) if old else 0.0
            drift = previous
            if progress_capable and inserted:
                drift = max(0.0, min(1.0, previous + max(0.0, 0.25 - best_action_support) * 0.55
                                     - (0.16 if best_action_support >= 0.30 else 0.0)))
            last_warning_at = old["last_warning_at"] if old else None
            violation = bool(violations)
            soft_fire = (progress_actions >= MIN_PROGRESS_ACTIONS and low >= MIN_LOW_STREAK
                         and drift >= DRIFT_THRESHOLD and residual >= RESIDUAL_THRESHOLD)
            may_emit = _age_seconds(last_warning_at) >= WARNING_COOLDOWN_SECONDS
            warning_id = None
            if violation or (soft_fire and may_emit):
                kind = "constraint_violation" if violation else "trajectory_drift"
                warning_id = "prompt-drift:" + store.keyed(
                    "drift-warning", "%s:%s:%s:%s" % (session_hmac, project_id, kind, progress_actions)
                )[:16]
                warnings.append({
                    "warning_id": warning_id,
                    "kind": kind,
                    "project_id": project_id,
                    "drift": round(drift, 4),
                    "residual": round(residual, 4),
                    "uncovered_clauses": sum(1 for row in refreshed if float(row["best_support"]) < 0.25),
                    "violated_clauses": len(violations),
                })
                last_warning_at = now
            store.db.execute(
                "INSERT INTO drift_state(session_hmac,project_id,progress_actions,consecutive_low_support,"
                "drift,residual,last_warning_at,last_warning_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_hmac,project_id) DO UPDATE SET progress_actions=excluded.progress_actions,"
                "consecutive_low_support=excluded.consecutive_low_support,drift=excluded.drift,residual=excluded.residual,"
                "last_warning_at=excluded.last_warning_at,last_warning_id=excluded.last_warning_id,updated_at=excluded.updated_at",
                (session_hmac, project_id, progress_actions, low, drift, residual,
                 last_warning_at, warning_id or (old["last_warning_id"] if old else None), now),
            )
    return warnings


def stop_gaps(store: ProjectMemoryStore, *, session: str) -> list[dict]:
    session_hmac = store.session_hmac(session)
    out = []
    for project_id in _project_ids(store, session_hmac):
        rows = _active_clauses(store, session_hmac, project_id)
        missing = [row for row in rows if float(row["best_support"]) < 0.25]
        violated = [row for row in rows if row["state"] == "violated"]
        if missing or violated:
            out.append({"project_id": project_id, "open_clauses": len(rows),
                        "uncovered_clauses": len(missing), "violated_clauses": len(violated)})
    return out
