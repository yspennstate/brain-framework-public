"""Standalone signed-cache PreToolUse hook; no SQLite or package initialization."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CAP = 1_000_000
ROOT = Path(os.environ.get("PROJECT_MEMORY_STATE") or
            (Path.home() / ".brain-framework" / "state" / "project_memory"))
CACHE_SCHEMA = "project-contract-hot-cache-v3"
DRIFT_STATE_SCHEMA = "hot-drift-state-v1"
# No \b after a \s alternative: \b cannot match between a space and a dash, so every flag-first call fell
# through - "grep -n x" was classed as progress and "rm -rf d" never reached the delete class.
READ_ONLY = re.compile(r"(?i)^\s*(?:get-content\b|get-childitem\b|type\s|more\s|rg\s|grep\s|ls\b|head\s|tail\s|wc\s"
                       r"|sed\s+-n\b|git\s+(?:status|diff|log|show)\b)")
# A read-only head proves nothing about the rest of a chained or redirected command: "grep x f && rm -rf d" is a
# delete. Pipes into plain filters and redirects to the null device stay read-only.
CHAINED = re.compile(r"(?i)&&|\|\||;|`|\$\(|\|(?!\s*(?:head|tail|grep|rg|wc|sort|uniq|cut|select-object|select-string)\b)"
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
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.:/\\-]{1,63}")
STOP = frozenset("the a an and or of to in on for by is are was were be this that with from as at do not never".split())
OP_WORDS = "delete remove erase write edit change modify overwrite push publish release deploy launch start run execute invoke send message email upload download".split()


def keyed(key: bytes, domain: str, value: object) -> str:
    return hmac.new(key, (domain + "\x1f" + str(value)).encode("utf-8", "replace"), hashlib.sha256).hexdigest()


def features(key: bytes, value: str) -> set[str]:
    out = set()
    for word in WORD.findall(value[:40_000])[:256]:
        whole = word.lower().strip("._/:\\- ")
        parts = [whole] + [part for part in re.split(r"[._/:\\-]+", whole) if part != whole]
        for token in parts:
            if len(token) >= 2 and token not in STOP:
                out.add("lexh:" + keyed(key, "lexeme", token)[:24])
    return out


def _bloom_contains(raw_bits: bytes, feature: str, bits: int, hashes: int) -> bool:
    raw = feature.encode("ascii", "strict")
    for salt in range(hashes):
        index = int.from_bytes(hashlib.sha256(bytes([salt]) + raw).digest()[:8], "big") % bits
        if not raw_bits[index // 8] & (1 << (index % 8)):
            return False
    return True


def operation(tool_name: str, payload: object) -> tuple[str, bool, str]:
    name = str(tool_name or "").lower()
    if isinstance(payload, dict):
        text = next((str(payload[k]) for k in ("command", "cmd", "script", "path", "file_path", "url")
                     if isinstance(payload.get(k), str)), json.dumps(payload, sort_keys=True, ensure_ascii=False))
    else:
        text = str(payload or "")
    tool_words = set(re.split(r"[^a-z0-9]+", name))
    if (name in {"read", "grep", "glob", "view", "webfetch"}
            or tool_words & {"read", "get", "list", "search", "find", "view", "inspect"}
            or (READ_ONLY.search(text) and not CHAINED.search(text) and not rc_hazard(name, text)
                and "\n" not in text.strip())
            or read_chain(name, text)):
        return "read", False, text
    if name in {"edit", "write", "multiedit", "apply_patch"} or tool_words & {
            "edit", "write", "update", "create", "patch", "replace", "type", "click"}:
        return "write", True, text
    if tool_words & {"delete", "remove", "erase"}:
        return "delete", True, text
    if tool_words & {"push", "publish", "release"}:
        return "push", True, text
    if tool_words & {"deploy", "launch", "register", "schedule"}:
        return "deploy", True, text
    if tool_words & {"send", "message", "email", "upload", "post"}:
        return "network", True, text
    patterns = (("delete", r"(?i)\b(?:remove-item\b|rm(?:dir)?(?:\s|$)|delete\b|erase\b)"),
                ("push", r"(?i)\b(?:git\s+push|publish|release)\b"),
                ("deploy", r"(?i)\b(?:deploy|launch|start-process|schtasks|register-scheduledtask)\b"),
                ("network", r"(?i)\b(?:send|message|email|upload)\b"),
                ("verify", r"(?i)\b(?:pytest|unittest|test|verify|validate|check)\b"))
    for label, pattern in patterns:
        if re.search(pattern, text):
            return label, True, text
    if (name in {"bash", "powershell", "shell", "shell_command", "exec_command", "python"}
            or tool_words & {"run", "execute", "invoke", "call"}):
        return "execute", True, text
    return "unknown", False, text


def _age_seconds(value: object) -> float:
    try:
        built = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if built.tzinfo is None:
            built = built.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - built).total_seconds())
    except Exception:
        return float("inf")


@contextmanager
def _state_lock(path: Path, timeout_seconds: float = 0.06):
    """Take a tiny cross-process lock; failure degrades only advisory drift."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    deadline = time.monotonic() + timeout_seconds
    locked = False
    try:
        while time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                time.sleep(0.003)
        yield locked
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _write_state(path: Path, state: dict) -> None:
    payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
    os.replace(tmp, path)


def _update_drift(policy: dict, session_hmac: str, scores: dict[str, float], progress: bool) -> list[str]:
    """Return projects whose calibrated advisory is due.

    The warning stamp is written only after stdout is flushed by ``_mark_delivered``;
    a crashed delivery therefore retries rather than silencing the signal.
    """
    if not progress or not scores:
        return []
    state_path = ROOT / "hot-drift-state.json"
    lock_path = ROOT / "hot-drift-state.lock"
    now = time.time()
    due: list[str] = []
    with _state_lock(lock_path) as locked:
        if not locked:
            return []
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("schema") != DRIFT_STATE_SCHEMA:
                raise ValueError("state schema")
        except Exception:
            state = {"schema": DRIFT_STATE_SCHEMA, "entries": {}}
        entries = state.setdefault("entries", {})
        threshold = float(policy.get("instant_drift_threshold", 0.05))
        min_actions = max(1, int(policy.get("drift_min_progress_actions", 5)))
        min_streak = max(2, int(policy.get("drift_min_low_streak", 3)))
        cooldown = max(60, int(policy.get("drift_warning_cooldown_seconds", 900)))
        for project, score in scores.items():
            key = session_hmac + ":" + project
            entry = entries.get(key) or {}
            if now - float(entry.get("updated_at", 0)) > 14_400:
                entry = {}
            entry["progress_actions"] = int(entry.get("progress_actions", 0)) + 1
            entry["low_streak"] = int(entry.get("low_streak", 0)) + 1 if score < threshold else 0
            entry["updated_at"] = now
            entry["last_score"] = score
            entries[key] = entry
            if (entry["progress_actions"] >= min_actions and entry["low_streak"] >= min_streak
                    and now - float(entry.get("last_warning_delivered_at", 0)) >= cooldown):
                due.append(project)
        # Keep the state bounded even if a host emits unstable session IDs.
        if len(entries) > 4096:
            keep = sorted(entries, key=lambda item: float(entries[item].get("updated_at", 0)), reverse=True)[:4096]
            state["entries"] = {item: entries[item] for item in keep}
        _write_state(state_path, state)
    return due


def _mark_delivered(session_hmac: str, projects: list[str]) -> None:
    if not projects:
        return
    state_path = ROOT / "hot-drift-state.json"
    with _state_lock(ROOT / "hot-drift-state.lock") as locked:
        if not locked:
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            entries = state["entries"]
            now = time.time()
            for project in projects:
                entry = entries.get(session_hmac + ":" + project)
                if entry:
                    entry["last_warning_delivered_at"] = now
            _write_state(state_path, state)
        except Exception:
            return


def main() -> int:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    raw = sys.stdin.buffer.read(CAP + 1)
    if len(raw) > CAP:
        return 0
    try:
        data = json.loads(raw.decode("utf-8", "strict"))
        if not isinstance(data, dict) or data.get("hook_event_name") != "PreToolUse":
            return 0
        key = (ROOT / "project_memory.key").read_bytes()[:32]
        envelope = json.loads((ROOT / "hot-contract-cache.json").read_text(encoding="utf-8"))
        body = envelope["body"]
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if not hmac.compare_digest(str(envelope.get("signature")), hmac.new(key, canonical, hashlib.sha256).hexdigest()):
            raise ValueError("signature")
        if body.get("schema") != CACHE_SCHEMA:
            raise ValueError("schema")
        policy = body.get("policy")
        if not isinstance(policy, dict):
            raise ValueError("policy")
        cache_age = _age_seconds(body.get("built_at"))
        stale = cache_age > float(policy.get("max_cache_age_seconds", 7500))
        session = str(data.get("session_id") or "unknown")
        session_hmac = keyed(key, "session", session)[:32]
        projects = list(body.get("session_projects", {}).get(session_hmac, ()))
        cwd = os.path.normcase(os.path.abspath(str(data.get("cwd") or "")))
        rooted = [(len(root), project["id"]) for project in policy.get("projects") or []
                  for root in project.get("roots") or []
                  if cwd == os.path.normcase(root) or cwd.startswith(os.path.normcase(root) + os.sep)]
        if rooted:
            deepest = max(length for length, _project in rooted)
            projects = sorted({project for length, project in rooted if length == deepest})
        op, progress, text = operation(str(data.get("tool_name") or "unknown"), data.get("tool_input") or {})
        action_features = features(key, text)
        op_features = {"lexh:" + keyed(key, "lexeme", word)[:24] for word in OP_WORDS}
        violations = []
        support_scores: dict[str, float] = {}
        bloom_policy = body.get("bloom") or {}
        bloom_bits = int(bloom_policy.get("bits", 0))
        bloom_hashes = int(bloom_policy.get("hashes", 0))
        for project in projects:
            contracts = body.get("contracts", {}).get(project, ())
            best_overlap = 0.0
            for clause in contracts:
                scope = {item for item in clause.get("features") or () if item.startswith("lexh:")} - op_features
                clause_lex = {item for item in clause.get("features") or () if item.startswith("lexh:")}
                if clause_lex:
                    best_overlap = max(best_overlap, len(clause_lex & action_features) /
                                       float(max(1, min(len(clause_lex), len(action_features)))))
                if (op in set(clause.get("forbidden_ops") or ()) and (progress or op == "read")
                        and (not scope or scope & action_features)):
                    violations.append((project, clause["clause_id"]))
            encoded_bloom = (body.get("support_blooms") or {}).get(project)
            if encoded_bloom and body.get("support_clause_counts", {}).get(project) and action_features:
                raw_bloom = base64.b64decode(encoded_bloom.encode("ascii"), validate=True)
                matched = sum(_bloom_contains(raw_bloom, feature, bloom_bits, bloom_hashes)
                              for feature in action_features)
                support_scores[project] = matched / float(len(action_features))
        drift_due = _update_drift(policy, session_hmac, support_scores, progress)
        lines = ["[prompt-contract] %s: operation/target overlaps active prohibition %s for %s. Re-read the current prompt before retrying."
                 % ("BLOCKED" if policy.get("deny_exact_negative") else "WARNING", clause, project)
                 for project, clause in violations]
        if stale:
            lines.append("[prompt-contract] Signed contract cache is stale (%.0f seconds); exact checks used the last verified generation, but irreversible work should wait for hourly reconciliation." % cache_age)
        if progress and body.get("contracts") and not projects:
            lines.append("[prompt-contract] No signed project contract matched this session or working directory; mutation coverage is unresolved. Reconcile project classification before irreversible work.")
        lines.extend("[prompt-contract] The current progress action is weakly connected to every open clause for %s. Re-read the prompt contract and reconnect the next action; persistent divergence is scored by the hourly graph." % project
                     for project in drift_due)
        acknowledged = set(body.get("acknowledgements", {}).get(session_hmac, ()))
        for project in projects:
            review = body.get("reviews", {}).get(project)
            if review and review.get("window_id") not in acknowledged:
                if review.get("attempt_unresolved"):
                    lines.append("[project-context] The newest hourly review attempt for %s is unresolved; do not treat an older quiet packet as current." % project)
                else:
                    # The CLI migrated to the runtime root beside this hook (2026-07-23);
                    # resolve it as a sibling so the nag never points at a moved path again.
                    cli = Path(__file__).resolve().parent / "project_memory_cli.py"
                    lines.append("[project-context] Hourly review %s is due for %s (%s occurrences). Run: \"%s\" \"%s\" read-latest --project %s --session %s"
                                 % (review["generation"], project, review["occurrence_count"], sys.executable, cli, project, session))
        if not lines:
            return 0
        output = {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}
        sys.stdout.write(json.dumps({"hookSpecificOutput": output}, ensure_ascii=False))
        sys.stdout.flush()
        _mark_delivered(session_hmac, drift_due)
    except Exception:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "additionalContext": "[prompt-contract] Signed hot cache validation failed; avoid irreversible work until reconciliation."}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
