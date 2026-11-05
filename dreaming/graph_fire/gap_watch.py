#!/usr/bin/env python3
"""gap_watch.py - two coverage-gap detectors for surfaces the deployed engine misses entirely.

GAP A (stale_assert): a session asserts a definitive CURRENT-STATE claim about a checkable artifact
("X is unbuilt / dead / never fires / writes unconditionally") into a DURABLE or BROADCAST surface (a
brain memory, a MEMORY.md-dir write, a mesh send/handoff) with NO fresh observation of that artifact in
the session's recent tool window. Demonstrated 3x on 2026-07-12 (read_code_before_asserting_behavior):
the proxy was a mental model of unread code, and no reflex fired at the assert/record moment. The
structural signal is the CONJUNCTION: (definitive artifact-state assertion) AND (durable/broadcast
surface) AND (no tool_use/tool_result in the recent transcript tail mentions the asserted artifact).
Each leg alone is common; together they are rare and nearly always wrong.

GAP B (peer_broadcast_wrong): pre_report.check() blocks a provably-wrong number/claim reaching the
OWNER (Stop surface), but a mesh `send`/`handoff` reaches PEERS from the command surface, unguarded -
a peer acts on the claim with none of the owner's corrective context. Reuse the exact same calibrated
check (anchor mismatch >4x, drawdown/sign/sell-vol/levetf) on the extracted --body/--result payload.
No new precision surface is invented; the measured ~0.04% false-block machinery is pointed at one more
outbound pipe.

Fires are forward_feed-shaped dicts (id/gut/why/charge) so instinct_prime._augment_graph can union
them with zero new render code. Stdlib only. Fail-safe by construction: any error -> [] (this joins
the every-tool-call path; it must never block or slow real work). Pure read - never writes anything.
"""
import os, io, json, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# pre_report lives in graph_fire/: same dir once integrated, parent dir while staged in improve_*/
_GF = HERE if os.path.exists(os.path.join(HERE, "pre_report.py")) else os.path.dirname(HERE)
EVIDENCE_BYTES = 240000   # transcript tail window = "the recent work"; ~a few dozen turns
BODYFILE_CAP = 20000      # bound a --body-file read

# ---------------------------------------------------------------- GAP A: stale assert
# Definitive PRESENT-state assertions about an artifact. Past tense (was/were) is recounting, not a
# fresh claim; hedges (might/likely/suspect/if) are honest hypotheses - both excluded below.
_STATE = re.compile(
    r"\b(?:is|are|remains?)\s+(?:still\s+|now\s+)?"
    r"(?:broken|dead|missing|unbuilt|absent|empty|stale|vestigial|orphaned|"
    r"a\s+no.?op|a\s+stub|dead\s+code|unwired|unregistered|"
    r"not\s+(?:wired|built|implemented|registered|called|invoked|running|firing|receiving|reading|writing|ingesting)|"
    r"(?:off(?!\s+the\s+table)|disabled)\b)"
    r"|\b(?:never|doesn'?t|does\s+not|won'?t|isn'?t\s+ever)\s+"
    r"(?:fire|fires|run|runs|write|writes|read|reads|exist|exists|trigger|triggers|update|updates|"
    r"get\s+called|gets?\s+called|ingest|ingests|deposit|deposits)"
    r"|\b(?:writes?|fires?|runs?|updates?|deposits?)\s+[\w.\-/]{0,24}\s*unconditionally"
    r"|\bunconditionally\s+(?:writes?|fires?|runs?|overwrites?)"
    r"|\bis\s+(?:a|an)\s+(?:un)?conditional(?:-on-\w+)?[\w\- ]{0,20}\b(?:writer|reader|no.?op)",
    re.I)
# hedge/hypothetical anywhere shortly before the state phrase ("checking whether X is broken",
# "X might be dead") -> honest hypothesis or an in-progress check, never fire
_HEDGE_BEFORE = re.compile(r"\b(?:if|whether|check\w*|verify\w*|confirm\w*|unless|suspect\w*|maybe|"
                           r"perhaps|might|may\b|likely|probably|possibly|hypothes\w*|assum\w*|guess\w*|"
                           r"wonder\w*|seems?|appears?|could\s+be|test\w*)\b", re.I)
# a claim that carries its own receipt ("L476 says so", "verified by reading main()", "read the source")
# is a checked finding, not a reconstructed one - never fire on it
_RECEIPT = re.compile(r"\bL\d{1,5}\b|\bline\s+\d{1,5}\b|\bsays so\b|\bverified\b|\bconfirmed\b|"
                      r"\bmeasured\b|\btested\b|\bread\s+(?:the\s+)?(?:source|code|file|main|its)\b|"
                      r"\bi\s+read\b|\bjust\s+read\b|\breading\s+(?:the\s+)?(?:source|code|actual)", re.I)
# an identifier-like token: the thing a tool call could actually have touched/verified
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-/\\]{3,}")
_EXT = (".py", ".ps1", ".md", ".json", ".tsv", ".jsonl", ".sqlite3", ".yaml", ".yml", ".toml", ".txt", ".sh")
_STATE_WORDS = set("broken dead missing unbuilt absent gone empty stale vestigial orphaned unwired "
                   "unregistered disabled silent unconditionally conditional unconditional never doesn "
                   "doesn't does not exists exist fires fire runs run writes write reads read".split())


def _id_like(tok):
    """Is this token something a tool call could have named (a file/function/module/process), not English?"""
    t = tok.strip(".,;:!?()[]{}'\"")
    if len(t) < 4 or t.lower() in _STATE_WORDS:
        return None
    if not re.sub(r"[\d.,/\\%\-]", "", t):
        return None                           # pure numbers/ratios (96.0/38.7/29.4) name nothing checkable
    low = t.lower()
    if any(low.endswith(e) for e in _EXT):
        return t
    if "_" in t or "/" in t or "\\" in t:
        return t
    if "." in t:
        return t
    if re.search(r"[a-z][A-Z]", t):           # camelCase joins
        return t
    if re.fullmatch(r"[A-Z][a-z]+-[A-Z][a-z]+", t) or re.search(r"-[A-Z]{2,}|[A-Z]{2,}-", t):
        return t                              # Test-Alive / duplicate-PREVENT; NOT 'Wiring-checks' English
    if re.search(r"\d", t) and re.search(r"[a-f]", low) and len(t) >= 6 and re.fullmatch(r"[0-9a-f\-]{6,}", low):
        return t                              # session/commit hex ids
    return None


def find_assertions(text):
    """Stage 1: definitive artifact-state assertions -> [{'state','tokens','pos'}]. Fail-safe -> []."""
    out = []
    try:
        t = text if isinstance(text, str) else ("" if text is None else str(text))
        for m in _STATE.finditer(t):
            before = t[max(0, m.start() - 34):m.start()]
            if _HEDGE_BEFORE.search(before):
                continue
            win = t[max(0, m.start() - 70):m.end() + 40]
            if _RECEIPT.search(t[max(0, m.start() - 90):m.end() + 90]):
                continue
            toks = []
            for tm in _TOKEN.finditer(win):
                tk = _id_like(tm.group(0))
                if tk and tk.lower() not in (x.lower() for x in toks):
                    toks.append(tk)
            if toks:
                out.append({"state": m.group(0)[:60], "tokens": toks[:4], "pos": m.start()})
        return out[:6]
    except Exception:
        return []


def evidence_haystack(transcript_path, max_bytes=EVIDENCE_BYTES):
    """Lower-cased concat of recent tool_use inputs + tool_result texts from the transcript tail.
    'Recent observation exists' is the evidence that an assertion was checked. Fail-safe -> ''."""
    try:
        if not transcript_path or not os.path.exists(transcript_path):
            return ""
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            tail = f.read().decode("utf-8", "replace")
        parts = []
        for ln in tail.splitlines():
            if '"tool_use"' not in ln and '"tool_result"' not in ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            content = msg.get("content") or obj.get("content") or []
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    try:
                        parts.append(json.dumps(c.get("input", {}), ensure_ascii=False))
                    except Exception:
                        pass
                elif c.get("type") == "tool_result":
                    cc = c.get("content")
                    if isinstance(cc, str):
                        parts.append(cc[:4000])
                    elif isinstance(cc, list):
                        for e in cc:
                            if isinstance(e, dict) and e.get("type") == "text":
                                parts.append((e.get("text") or "")[:4000])
        return "\n".join(parts).lower()
    except Exception:
        return ""


def unverified(assertions, hay):
    """Assertions none of whose artifact tokens appear in the recent observation window."""
    out = []
    try:
        for a in assertions:
            if not any(tk.lower() in hay for tk in a["tokens"]):
                out.append(a)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- surfaces
_MESH_SEND = re.compile(r"agent_mesh\.py[^\n|;&]*?\b(send|handoff)\b")
_BRAIN_REM = re.compile(r"brain\.py[^\n|;&]*?\bremember\b")
_FLAG_VAL = r"\s+(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|([^\s'\"][^\s]*))"
_BODY = re.compile(r"--body" + _FLAG_VAL)
_RESULT = re.compile(r"--(?:result|verification|desc|risk)" + _FLAG_VAL)
_BODYFILE = re.compile(r"--body-file\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))")
_MEMDIR = re.compile(r"[/\\]memory[/\\][^/\\]+\.md$", re.I)


def _flag_texts(cmd):
    """All quoted payload texts on a mesh/brain command (+ a bounded --body-file read)."""
    parts = []
    try:
        for rx in (_BODY, _RESULT):
            for m in rx.finditer(cmd):
                v = m.group(1) or m.group(2) or m.group(3) or ""
                if v:
                    parts.append(v.replace('\\"', '"'))
        bf = _BODYFILE.search(cmd)
        if bf:
            p = (bf.group(1) or bf.group(2) or bf.group(3) or "").replace("\\", "/")
            if p and os.path.exists(p) and os.path.getsize(p) <= BODYFILE_CAP * 8:
                parts.append(io.open(p, encoding="utf-8", errors="replace").read()[:BODYFILE_CAP])
    except Exception:
        pass
    return parts


def _stale_fire(a, where):
    toks = ", ".join(a["tokens"][:3])
    return {
        "id": "stale_assert",
        "charge": 0.9,
        "gut": ("You're recording into %s that '%s...' (about %s) - but nothing in this session's recent "
                "tool window ever observed %s. The last three durable wrong claims (07-12) were written "
                "exactly this way: a plausible mental model of unread state. One Read/grep of the CURRENT "
                "state first, or write it as a hypothesis, not a finding." % (where, a["state"], toks, toks)),
        "why": "definitive state assertion with no fresh observation of the target",
    }


def watch(data):
    """Entry: hook `data` (PreToolUse json) -> forward_feed-shaped caution fires. Fail-safe -> []."""
    try:
        if not isinstance(data, dict) or data.get("hook_event_name") != "PreToolUse":
            return []
        tn = data.get("tool_name", "") or ""
        ti = data.get("tool_input", {}) or {}
        tp = data.get("transcript_path") or ""
        fires = []
        if tn in ("Bash", "PowerShell"):
            cmd = ti.get("command", "") or ""
            is_mesh = bool(_MESH_SEND.search(cmd))
            is_brain = bool(_BRAIN_REM.search(cmd))
            if not (is_mesh or is_brain):
                return []
            payloads = _flag_texts(cmd)
            payload = "\n".join(payloads) if payloads else cmd
            # GAP B: the owner-guard, pointed at the peer pipe (provable contradictions only)
            if is_mesh:
                try:
                    sys.path.insert(0, _GF)
                    import pre_report
                    r = pre_report.check(payload)
                    if r.get("should_block"):
                        what = (r.get("mismatches") or r.get("stat") or [{}])[0]
                        fires.append({
                            "id": "peer_broadcast_wrong",
                            "charge": 0.97,
                            "gut": ("This mesh send/handoff carries a claim the owner-guard would BLOCK "
                                    "(%s) - but it's going to PEERS, who will act on it with none of the "
                                    "owner's corrective context. Fix the number/claim before broadcasting, "
                                    "or say plainly it is unreconciled." % json.dumps(what)[:160]),
                            "why": "pre_report provable contradiction on a peer broadcast",
                        })
                except Exception:
                    pass
            # GAP A on the broadcast/durable payload
            if tp:  # no transcript -> cannot check evidence -> stay silent (precision-first)
                asr = unverified(find_assertions(payload), evidence_haystack(tp))
                if asr:
                    fires.append(_stale_fire(asr[0], "a mesh broadcast" if is_mesh else "a brain memory"))
        elif tn in ("Write", "Edit"):
            fp = (ti.get("file_path", "") or "").replace("\\", "/")
            if not _MEMDIR.search(fp):
                return []
            body = ti.get("content", "") or ti.get("new_string", "") or ""
            if tp and body:
                asr = unverified(find_assertions(body[:BODYFILE_CAP]), evidence_haystack(tp))
                if asr:
                    fires.append(_stale_fire(asr[0], "a durable memory"))
        return fires
    except Exception:
        return []


if __name__ == "__main__":
    # smoke: the three demonstrated 07-12 claims (stage-1 must catch >=2; case 2 is a causal-story
    # shape v1 deliberately does not chase) + hedged/past-tense negatives must stay silent.
    cases = [
        ("the duplicate-PREVENT guard is unbuilt - hand it to 0c44dd79", True),
        ("resume_checkpoint is a conditional-on-content writer", True),
        ("the stale RESUME was just the shared-slot confound", False),   # known v1 non-goal
        ("it writes RESUME.md unconditionally every Stop", True),
        ("the instinct_prime hook never fires on mesh sends", True),
        ("checking whether registry_refresh is broken before I claim anything", False),
        ("the parser might be dead - verifying now", False),
        ("relaunch.ps1 was broken yesterday; fixed and verified today", False),
        ("I read gfcore.py: note_fires deposits ALL fires", False),
    ]
    ok = True
    for txt, want in cases:
        got = bool(find_assertions(txt))
        if got != want:
            ok = False
        print("  [%s] stage1=%-5s want=%-5s :: %s" % ("ok" if got == want else "MISS", got, want, txt[:64]))
    for junk in (None, 123, {"x": 1}, []):
        try:
            watch(junk), find_assertions(junk)
        except Exception as e:
            ok = False
            print("  FAIL-SAFE BREACH:", e)
    print("gap_watch smoke", "OK" if ok else "FAIL")
