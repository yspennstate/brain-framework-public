#!/usr/bin/env python3
"""Typed event extraction for the prospective error graph.

Raw text is transient.  The returned Event exposes only redacted, bounded,
typed features and hashes in public_record(); exact quantities and the redacted
working text live in ``private`` and must never be persisted by GraphStore.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 2
MAX_TEXT = 200_000
MAX_CLAUSES = 32
MAX_LEXEMES = 96
MAX_FUTURE_SKEW_SECONDS = 24 * 60 * 60
_PUBLIC_SURFACES = frozenset((
    "prompt", "pre_action", "post_action", "report", "commentary",
    "correction", "aggregate", "unknown",
))
_PUBLIC_TOOL_NAMES = frozenset((
    "read", "grep", "glob", "webfetch", "view", "notebookread",
    "edit", "write", "multiedit", "apply_patch", "bash", "powershell",
    "shell", "shell_command", "exec_command", "python", "git", "unknown",
))


def stable_hash(value: Any, domain: str = "") -> str:
    data = (domain + "\x1f" + ("" if value is None else str(value))).encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()[:24]


def public_tool_name(value: Any) -> str:
    """Return only a fixed public tool category; unknown names never persist."""
    normalized = str(value or "unknown").lower()
    return normalized if normalized in _PUBLIC_TOOL_NAMES else "unknown"


def public_model_name(value: Any) -> str:
    """Coarsen model metadata without linkable hashes or private identifiers."""
    normalized = str(value or "unknown").strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if re.match(r"^(?:gpt|codex|o[0-9])", normalized):
        return "openai"
    return "unknown"


_SECRET_PATTERNS = (
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,60}\b"),
    re.compile(r"\b(?:sk-ant-|sk-|gh[pousr]_|github_pat_|xox[bp]-|AKIA|rpa_|rpk_|hf_|gsk_)[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|pwd|token|secret|credential)\b.{0,32}?[=:]\s*\S{6,}"),
    re.compile(r"(?i)\b(?:password|passwd|pwd|token|secret|credential)\s+(?:is|was)\s+"
               r"[\"']?(?!(?:required|needed|missing|invalid|unknown|redacted|configured|available)\b)"
               r"[A-Za-z0-9_!@#$%^&*.-]{8,}[\"']?"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?<![/\w.])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{40,}(?![\w.])"),
)


def contains_secret(text: Any) -> bool:
    try:
        value = text if isinstance(text, str) else str(text or "")
    except Exception:
        return False
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


def redact(text: Any) -> Tuple[str, int]:
    """Return bounded redacted text and the number of substitutions."""
    try:
        out = text if isinstance(text, str) else ("" if text is None else str(text))
    except Exception:
        out = ""
    out = out[:MAX_TEXT]
    hits = 0
    for pattern in _SECRET_PATTERNS:
        out, n = pattern.subn(" [redacted-secret] ", out)
        hits += n
    return out, hits


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_./:-]{2,63}")
_STOP = set(
    """the a an and or of to in on for by is are was were be been being it its this that these
    those with without into onto from as at we you he she they them his her their our your my me
    not no yes do did done if then else than so but can could may might must should would will
    what which when where why how who one two three just also still now here there about around
    after before get got make made use used run ran see saw say said tell told thing things""".split()
)


def _lexemes(text: str) -> Tuple[str, ...]:
    labels = []
    seen = set()
    for raw in _WORD.findall(text):
        token = raw.lower().strip("._/-:")
        if len(token) < 3 or token in _STOP or token.startswith("redacted"):
            continue
        if token in seen:
            continue
        seen.add(token)
        # Words from private prompts are nodes, but never plaintext labels.
        labels.append("lexh:" + stable_hash(token, "lexeme"))
        if len(labels) >= MAX_LEXEMES:
            break
    return tuple(labels)


def _subwords(text: str) -> Tuple[str, ...]:
    """Hashed boundary trigrams give typo tolerance without leaking substrings."""
    out = set()
    for raw in _WORD.findall(text)[:32]:
        token = raw.lower().strip("._/-:")
        if len(token) < 3 or token in _STOP or token.startswith("redacted"):
            continue
        bounded = "<" + token + ">"
        grams = [bounded[:3], bounded[-3:]]
        for gram in grams:
            out.add("sub:" + stable_hash(gram, "char3")[:12])
    return tuple(sorted(out))


_CLAUSE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_HEDGE = re.compile(r"(?i)\b(may|might|could|appears?|seems?|estimate|roughly|about|approximately|unverified|not sure|need to (?:check|verify|reconcile))\b")
_RECOMMEND = re.compile(r"(?i)\b(recommend|suggest|propose|should|you could|we could|consider|the move is|i would|i'd)\b")
_COMPLETE = re.compile(r"(?i)\b(done|completed|finali[sz]ed|implemented|fixed|shipped|deployed|all set|good to go|fully working|tests? pass(?:ed)?)\b")
_VERIFY = re.compile(r"(?i)\b(verify|verified|inspect|inspected|render|rendered|opened|read back|tested|reproduced|reconcile|cross-check|ground truth|actual account|actual tape)\b")
_DISCUSS = re.compile(r"(?i)\b(discuss(?:ed|ing|ion)?|mention(?:ed|ing)?|quote(?:d|s|ing)?|the phrase|example sentence|test case|hypothetical|false positive|false negative)\b")
_ASSERT = re.compile(r"(?i)\b(earns?|makes?|equals?|is|are|was|were|will|shows?|proves?|means?|result(?:s)?|comes? to|totals?|profit(?:s|ed)?|los(?:e|es|ing|t))\b")
_NEGATE = re.compile(r"(?i)\b(no|not|never|doesn'?t|didn'?t|isn'?t|aren'?t|without|rather than|instead of)\b")
_RETRACT = re.compile(r"(?i)\b(retract(?:ed|ion)?|withdrawn|superseded|old memory|stale anchor|previous claim was wrong)\b")


_MONEY = re.compile(
    r"(?i)(?:\$|usd\s*)\s*([\d][\d,]*(?:\.\d+)?)\s*(k|thousand|m|mn|mm|million|b|bn|billion)?"
    r"|(?<![\w.])([\d][\d,]*(?:\.\d+)?)\s*(k|thousand|m|mn|mm|million|b|bn|billion)\s*(?:usd|dollars?)?\b"
)
_PERCENT = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*%")
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6,
         "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_HORIZONS = (("day", re.compile(r"(?i)(?:/\s*day|per day|daily)")),
             ("month", re.compile(r"(?i)(?:/\s*(?:mo|month)|per month|monthly)")),
             ("year", re.compile(r"(?i)(?:/\s*(?:yr|year)|per year|annual(?:ly)?)")))


def _magnitude_bucket(value: float) -> str:
    value = abs(float(value))
    if value == 0:
        return "zero"
    exponent = int(math.floor(math.log10(value)))
    exponent = max(-6, min(12, exponent))
    return "1e%d" % exponent


def _horizon_near(text: str, start: int, end: int) -> Optional[str]:
    near = text[max(0, start - 12): min(len(text), end + 24)]
    for name, pattern in _HORIZONS:
        if pattern.search(near):
            return name
    return None


def _quantity_features(text: str) -> Tuple[Tuple[str, ...], Tuple[Mapping[str, Any], ...]]:
    features = set()
    private = []
    for match in _MONEY.finditer(text):
        raw = match.group(1) or match.group(3)
        unit = (match.group(2) or match.group(4) or "").lower()
        try:
            value = float(raw.replace(",", "")) * _MULT.get(unit, 1.0)
        except Exception:
            continue
        horizon = _horizon_near(text, match.start(), match.end())
        features.update(("quantity:money", "unit:usd", "magnitude:" + _magnitude_bucket(value)))
        if horizon:
            features.add("horizon:" + horizon)
        private.append({"kind": "money", "value": value, "unit": "usd", "horizon": horizon,
                        "span": (match.start(), match.end())})
    for match in _PERCENT.finditer(text):
        try:
            value = float(match.group(1))
        except Exception:
            continue
        horizon = _horizon_near(text, match.start(), match.end())
        features.update(("quantity:percent", "unit:percent", "magnitude:" + _magnitude_bucket(value)))
        if value < 0:
            features.add("polarity:negative")
        if horizon:
            features.add("horizon:" + horizon)
        private.append({"kind": "percent", "value": value, "unit": "percent", "horizon": horizon,
                        "span": (match.start(), match.end())})
    return tuple(sorted(features)), tuple(private)


def _clause_features(clause: str) -> Tuple[Tuple[str, ...], Tuple[Mapping[str, Any], ...]]:
    features = set(_lexemes(clause))
    qfeatures, quantities = _quantity_features(clause)
    features.update(qfeatures)
    question = clause.rstrip().endswith("?") or bool(re.match(r"(?i)^\s*(?:what|why|how|when|where|who|can|could|should|is|are|do|does)\b", clause))
    recommended = bool(_RECOMMEND.search(clause))
    completed = bool(_COMPLETE.search(clause))
    verified = bool(_VERIFY.search(clause))
    hedged = bool(_HEDGE.search(clause))
    quoted = clause.lstrip().startswith((">", "\"", "'"))
    meta = bool(_DISCUSS.search(clause))
    asserted = bool(_ASSERT.search(clause)) or bool(quantities)
    fresh_contrast = bool(re.search(
        r"(?i)\b(but|however|actually|setting .* aside|ignore .*?(?:;|,)|the real answer|my current result)\b",
        clause,
    ))
    discussion_only = (meta or quoted) and not fresh_contrast and not (recommended or completed)

    if question:
        features.add("act:question")
    if recommended:
        features.add("act:recommend")
    if completed:
        features.add("act:complete")
    if verified:
        features.add("act:verify")
    if hedged:
        features.add("act:hedge")
    if _NEGATE.search(clause):
        features.add("act:negate")
    if _RETRACT.search(clause):
        features.add("knowledge:retraction_context")
    if _HIGH_STAKES.search(clause):
        features.add("stakes:high")
    if meta:
        features.add("act:discussion")
    if quoted:
        features.add("act:quote")
    if asserted and not question and not discussion_only:
        features.add("act:assert")
    if discussion_only:
        features.add("gate:discussion_only")
    if ("act:assert" in features or recommended) and quantities:
        features.add("claim:quantified")
    return tuple(sorted(features)), quantities


_FAILURE = re.compile(r"(?im)Traceback \(most recent call last\)|^\s*Error:|\bfatal:|command failed|exit code\s*[1-9]|No such file|cannot find the path|permission denied|ParserError|SyntaxError|\[rejected\]|CONFLICT \(")
_HIGH_STAKES = re.compile(r"(?i)\b(trading|portfolio|book|grid|backtest|strategy|p&l|pnl|money|dollar|risk|drawdown|leverage|sharpe|deploy|production|security|secret|credential|delete|remove|external system)\b")
_FIXTURE = re.compile(r"(?i)\b(selftest|unit test|test fixture|synthetic case|zztest|deadbeef|pytest|unittest)\b")
_PATH = re.compile(
    r"(?i)(?:\b[A-Z]:[\\/][^\s\"'<>|]+|(?<![:\w])/(?:[A-Za-z0-9._~-]+/)+[A-Za-z0-9._~-]+|"
    r"(?<![\w.-])(?:[A-Za-z0-9._-]+[\\/])+(?:[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,8}))"
)


def _target_features(text: str, extra: Mapping[str, Any]) -> Tuple[str, ...]:
    targets = []
    supplied = extra.get("targets") or extra.get("target") or extra.get("file_path") or ()
    if isinstance(supplied, str):
        supplied = (supplied,)
    for value in supplied if isinstance(supplied, (list, tuple, set)) else ():
        if value:
            targets.append(str(value))
    targets.extend(match.group(0) for match in _PATH.finditer(text))
    features = set()
    for target in targets[:16]:
        normalized = target.strip("\"'`()[]{}.,;:").replace("\\", "/").lower()
        if not normalized or normalized.startswith(("http://", "https://")):
            continue
        features.add("targeth:" + stable_hash(normalized, "target")[:16])
        base = normalized.rsplit("/", 1)[-1]
        if "." in base:
            ext = base.rsplit(".", 1)[-1]
            if re.fullmatch(r"[a-z0-9]{1,8}", ext):
                features.add("target_ext:" + ext)
    return tuple(sorted(features))


def _operation_features(text: str, tool_name: str, event_name: str, response: str,
                        outcome_hint: str = "") -> Tuple[str, ...]:
    features = set()
    tool = (tool_name or "").lower()
    low = text.lower()
    if tool in {"read", "grep", "glob", "webfetch", "view", "notebookread"}:
        features.add("op:read")
    if tool in {"edit", "write", "multiedit", "apply_patch"}:
        features.update(("op:write", "op:mutate"))
    if tool in {"bash", "powershell", "shell", "shell_command", "exec_command"}:
        features.add("op:execute")
    if re.search(r"(?i)\b(?:cat|type|get-content|select-string|grep|rg|findstr|git\s+(?:show|diff|status|log)|ls|dir)\b", text):
        features.add("op:read")
    if re.search(r"(?i)\b(?:pytest|unittest|test_[\w.-]+|selftest|py_compile|compileall)\b", text):
        features.update(("op:test", "op:verify"))
    if re.search(r"(?i)brain\.py\s+(?:recall|neighbors)|codex_wake\.py", text):
        features.update(("op:brain_recall", "op:verify"))
    if re.search(r"(?i)\b(?:remove-item|rm\s+-|del\s+|erase\s+|rmdir|shutil\.rmtree|os\.remove|unlink\()", text):
        features.update(("op:delete", "op:mutate", "risk:irreversible"))
    if re.search(r"(?i)\bgit\s+(?:commit|push|merge|rebase|tag)\b", text):
        features.update(("op:git_change", "op:external", "op:mutate"))
    if re.search(r"(?i)\b(?:deploy|terraform\s+apply|kubectl\s+apply|aws\s+|az\s+|ssh\s+|invoke-restmethod|curl\s+.*-[xX]\s*(?:POST|PUT|DELETE))\b", text):
        features.update(("op:external", "op:mutate"))
    if re.search(r"(?i)(?:[A-Z]:\\|[A-Z]:/)", text):
        features.add("path:windows")
    if "\\" in text and tool in {"bash", "shell", "shell_command", "exec_command"}:
        features.add("risk:path_dialect_mismatch")
    if _HIGH_STAKES.search(text):
        features.add("stakes:high")
    if re.search(r"(?i)\b(actual (?:account|book|tape|artifact|file)|ground truth|authoritative source|"
                 r"reconcil(?:e|ed|ing) against|lived (?:book|number|anchor))\b", text):
        features.add("evidence:ground_truth")
    if _FIXTURE.search(text):
        features.add("context:fixture")
    if re.search(r"(?i)(?:instinct_fires|gf_shadow_fires|outcomes|MEMORY\.md|PROMPTS\.jsonl|mistake_families)", text):
        features.add("target:brain_ledger")
    if event_name.lower() in {"posttooluse", "post_tool_use", "tool_result"}:
        if outcome_hint == "failure" or (not outcome_hint and _FAILURE.search(response or "")):
            features.add("outcome:failure")
        elif outcome_hint == "success" or response:
            features.add("outcome:success")
        if "outcome:success" in features and {"op:read", "op:test", "op:verify", "op:brain_recall"}.intersection(features):
            features.add("evidence:observation")
    return tuple(sorted(features))


def transient_operation_features(text: Any, *, tool_name: str = "",
                                 event_name: str = "event",
                                 outcome_hint: str = "") -> Tuple[str, ...]:
    """Scan a bounded host-envelope field without sending it through AST work."""
    try:
        value = text if isinstance(text, str) else str(text or "")
    except Exception:
        value = ""
    features = set(_operation_features(value, tool_name, event_name, "", outcome_hint))
    if contains_secret(value):
        features.add("privacy:redacted")
    return tuple(sorted(features))


_AST_TYPES = (ast.Call, ast.Compare, ast.Subscript, ast.Import, ast.ImportFrom, ast.With, ast.Try,
              ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Await,
              ast.Delete, ast.Assign, ast.AugAssign, ast.Return, ast.Raise)
_KNOWN_CALLS = {"open", "eval", "exec", "compile", "remove", "unlink", "rmtree", "run", "popen",
                "system", "read_csv", "read_parquet", "merge", "join", "shift", "rolling"}


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _code_features(text: str, tool_name: str) -> Tuple[str, ...]:
    if not text.strip():
        return ()
    code = text
    fenced = re.findall(r"```(?:python|py)?\s*(.*?)```", text, re.I | re.S)
    if fenced:
        code = "\n".join(fenced)
    if (tool_name or "").lower() not in {"edit", "write", "multiedit", "apply_patch"} and not fenced \
            and not re.search(r"(?m)^\s*(?:def |class |import |from \w+ import |for |if |with |try:)", code):
        return ()
    try:
        tree = ast.parse(code)
    except Exception:
        return ("code:unparsed",)
    features = {"code:python", "code:parsed"}
    names = set()
    has_compare = False
    has_subscript = False
    for node in ast.walk(tree):
        if isinstance(node, _AST_TYPES):
            features.add("ast:" + type(node).__name__.lower())
        if isinstance(node, ast.Compare):
            has_compare = True
        if isinstance(node, ast.Subscript):
            has_subscript = True
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        if isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        if isinstance(node, ast.Call):
            name = _call_name(node).lower()
            if name:
                features.add("call:" + name if name in _KNOWN_CALLS else "callh:" + stable_hash(name, "call")[:12])
    if has_compare and has_subscript and names.intersection({"vix", "regime", "signal", "future", "forward", "return", "target"}):
        features.add("risk:possible_temporal_leakage")
    if names.intersection({"remove", "unlink", "rmtree", "delete"}):
        features.add("risk:destructive_code")
    if names.intersection({"eval", "exec", "system", "popen"}):
        features.add("risk:dynamic_execution")
    return tuple(sorted(features))


def _surface(event_name: str, requested: str) -> str:
    if requested:
        normalized = requested.lower()
        return normalized if normalized in _PUBLIC_SURFACES else "unknown"
    ev = (event_name or "").lower()
    if "prompt" in ev:
        return "prompt"
    if "pretool" in ev or ev == "pre_tool_use":
        return "pre_action"
    if "posttool" in ev or "result" in ev:
        return "post_action"
    if ev in {"stop", "report", "final"}:
        return "report"
    return "unknown"


def _event_kind(event_name: str, surface: str) -> str:
    return {"prompt": "prompt", "pre_action": "tool_call", "post_action": "tool_result",
            "report": "final_report", "commentary": "commentary",
            "correction": "correction", "aggregate": "aggregate"}.get(surface, "event")


@dataclass(frozen=True)
class Event:
    event_id: str
    event_ts: float
    observed_ts: float
    session_hash: str
    actor_hash: str
    episode_id: str
    surface: str
    kind: str
    source_ref_hash: str
    action_fingerprint: str
    features: Tuple[str, ...]
    clauses: Tuple[Tuple[str, ...], ...] = ()
    attrs: Mapping[str, Any] = field(default_factory=dict)
    private: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def public_record(self) -> Dict[str, Any]:
        """Persistence-safe representation.  No raw text or exact private values."""
        return {
            "event_id": self.event_id,
            "event_ts": self.event_ts,
            "observed_ts": self.observed_ts,
            "session_hash": self.session_hash,
            "actor_hash": self.actor_hash,
            "episode_id": self.episode_id,
            "surface": self.surface,
            "kind": self.kind,
            "source_ref_hash": self.source_ref_hash,
            "action_fingerprint": self.action_fingerprint,
            "features": list(self.features),
            "clauses": [list(c) for c in self.clauses],
            "attrs": dict(self.attrs),
            "schema_version": SCHEMA_VERSION,
        }


def extract_event(text: Any = "", *, event_name: str = "event", session: str = "", actor: str = "",
                  surface: str = "", tool_name: str = "", source_ref: str = "", response: Any = "",
                  ts: Optional[float] = None, observed_ts: Optional[float] = None,
                  extra: Optional[Mapping[str, Any]] = None) -> Event:
    """Extract one bounded typed event.  Never persists or returns raw text publicly."""
    clean, redactions = redact(text)
    response_clean, response_redactions = redact(response)
    live_clock = observed_ts is None
    now = float(time.time() if live_clock else observed_ts)
    event_ts = float(now if ts is None else ts)
    if (not math.isfinite(now) or not math.isfinite(event_ts)
            or now < 0 or event_ts < 0
            or (live_clock and event_ts > now + MAX_FUTURE_SKEW_SECONDS)):
        raise ValueError("event timestamps must be finite, non-negative, and not far-future")
    surf = _surface(event_name, surface)
    kind = _event_kind(event_name, surf)
    extra = dict(extra or {})

    clauses = []
    private_quantities = []
    for raw_clause in _CLAUSE_SPLIT.split(clean):
        clause = raw_clause.strip()
        if not clause:
            continue
        cfeatures, quantities = _clause_features(clause)
        clauses.append(cfeatures)
        private_quantities.extend(quantities)
        if len(clauses) >= MAX_CLAUSES:
            break

    lexemes = _lexemes(clean)
    features = set(lexemes)
    features.update(_subwords(clean))
    for clause in clauses:
        features.update(clause)
    features.update(_operation_features(clean, tool_name, event_name, response_clean,
                                        str(extra.get("outcome_hint") or "")))
    features.update(_code_features(clean, tool_name))
    features.update(_target_features(clean, extra))
    if "outcome:success" in features and any(feature.startswith("targeth:") for feature in features) \
            and {"op:read", "op:test", "op:verify"}.intersection(features):
        features.add("evidence:artifact_observed")
    if extra.get("authorized") is True:
        features.add("control:authorized")
    features.add("surface:" + surf)
    features.add("event:" + kind)
    if redactions or response_redactions:
        features.add("privacy:redacted")
    if extra.get("automation"):
        features.add("origin:automation")
    else:
        features.add("origin:agent_or_owner")
    if extra.get("self_generated"):
        features.add("origin:self_monitor")
    for feature in extra.get("features") or ():
        feature = str(feature)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,128}", feature):
            features.add(feature)

    # The action fingerprint hashes normalized shape only.  It permits repeat
    # detection without persisting commands, paths, literals, or quantities.
    shape = re.sub(r"\d+(?:\.\d+)?", "#", clean.lower())
    shape = re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", shape)
    shape = re.sub(r"\s+", " ", shape).strip()[:4000]
    # Pre- and post-tool surfaces must join to the same action.  Surface/kind
    # remain in the event id but deliberately do not enter this fingerprint.
    shape_fp = stable_hash("|".join((public_tool_name(tool_name), shape)), "action-shape")
    safe_hash = re.compile(r"^[0-9a-f]{24}$")
    action_id = str(extra.get("action_id") or "")
    action_override = str(extra.get("action_fingerprint") or "")
    action_fp = action_override if safe_hash.fullmatch(action_override) else \
        (stable_hash(action_id, "tool-use") if action_id else shape_fp)
    session_override = str(extra.get("session_hash") or "")
    sess_hash = session_override if safe_hash.fullmatch(session_override) else stable_hash(session or "unknown", "session")
    actor_hash = stable_hash(actor or "unknown", "actor")
    episode_key = extra.get("episode") or extra.get("turn_id") or session or "unknown"
    episode_override = str(extra.get("episode_id") or "")
    episode_id = episode_override if safe_hash.fullmatch(episode_override) else stable_hash(episode_key, "episode")
    src_hash = stable_hash(source_ref or extra.get("source_ref") or "unknown", "source")
    nonce = extra.get("event_id") or extra.get("nonce") or ""
    event_id = stable_hash("|".join((src_hash, sess_hash, "%.6f" % event_ts, kind, action_fp, str(nonce))), "event")

    attrs = {
        "tool": public_tool_name(tool_name),
        "redaction_count": redactions + response_redactions,
        "clause_count": len(clauses),
        "feature_schema": SCHEMA_VERSION,
        "model_version": public_model_name(extra.get("model_version")),
        "shape_fingerprint": shape_fp,
        "projection_truncated": bool(extra.get("projection_truncated")),
    }
    return Event(
        event_id=event_id,
        event_ts=event_ts,
        observed_ts=now,
        session_hash=sess_hash,
        actor_hash=actor_hash,
        episode_id=episode_id,
        surface=surf,
        kind=kind,
        source_ref_hash=src_hash,
        action_fingerprint=action_fp,
        features=tuple(sorted(features)),
        clauses=tuple(clauses),
        attrs=attrs,
        private={"redacted_text": clean, "quantities": tuple(private_quantities)},
    )


if __name__ == "__main__":
    import sys
    payload = json.loads(sys.stdin.read() or "{}")
    event = extract_event(payload.get("text", ""), event_name=payload.get("event_name", "event"),
                          session=payload.get("session", ""), actor=payload.get("actor", ""),
                          surface=payload.get("surface", ""), tool_name=payload.get("tool_name", ""),
                          source_ref=payload.get("source_ref", ""), response=payload.get("response", ""),
                          extra=payload.get("extra") or {})
    print(json.dumps(event.public_record(), sort_keys=True))
