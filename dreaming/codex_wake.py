"""codex_wake.py - an explicit live brain digest for Codex and other runners.

Claude sessions get the brain PUSHED at them by the SessionStart hook (wake_brain.ps1).
Codex now supports native lifecycle hooks, but a hook is not a substitute for this full
orientation digest and may be absent, disabled, or awaiting trust review. Other pull-based
runners may have only ~/.codex/RECENT_BRAIN.md, a static file refreshed nightly. This
composes the full wake from the same live sources wake_brain.ps1 reads when the agent asks:

    python ~/.claude/dreaming/codex_wake.py

Read-only, stdlib-only, no model loads. Output is capped section-by-section to stay a
digest, and stdout is wrapped UTF-8/replace so a cp1252 pipe can never crash the wake
(the failure that made checkers lie on 2026-07-09). Missing sources skip quietly except
the identity layer, which WARNs - a degraded wake should say so, not pass as green.
"""

import csv
import contextlib
import io
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from supersession import current_index_lines
import brain_node_documents as node_docs
import brain_selfcare


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


try:
    from mistake_graph_status import summarize_path as _summarize_mistake_watch
except Exception:
    _summarize_mistake_watch = None

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HOME = Path(os.environ.get("USERPROFILE") or str(Path.home()))
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = HOME / ".claude" / "projects" / _BRAIN_SLUG / "memory"
BRAIN_VIEWS = HOME / "brain-repo" / "01_ai_consciousness" / "brain"
DREAMING = HOME / ".claude" / "dreaming"
CODEX_WAKE_BUDGET_CHARS = 40000


def read(path, limit=None):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return text[:limit] if limit else text
    except OSError:
        return None


def age_hours(path):
    try:
        return (datetime.now().timestamp() - Path(path).stat().st_mtime) / 3600.0
    except OSError:
        return None


def cap(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip() + " [...]"


def section(title):
    print("\n--- %s ---" % title)


def _print_mistake_watch():
    try:
        if _summarize_mistake_watch is None:
            raise RuntimeError("status reader unavailable")
        summary = _summarize_mistake_watch(
            DREAMING / ".mistake_graph_status.json")
        title, detail = summary.title, summary.detail
    except Exception:
        title = "Mistake recurrence watch: FEED UNVERIFIED"
        detail = ("no valid latest-watch status is available; do not infer "
                  "that recurrence monitoring is quiet.")
    section(title)
    print("  " + detail)


def _register_for_recovery(home=HOME, environ=None):
    """Opt in to recovery registration for the exact calling Codex thread.

    A global newest-rollout scan can bind a concurrent session instead of the caller.
    The environment thread id is the only accepted identity; if it is absent or
    malformed, registration declines without guessing.
    """
    try:
        import json as _json, re as _re, time as _time
        env = os.environ if environ is None else environ
        sid = str(env.get("CODEX_THREAD_ID", "")).strip().lower()
        if _re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                sid) is None:
            return
        root = Path(home)
        reg = root / ".claude" / "fleet" / "registry"
        reg.mkdir(parents=True, exist_ok=True)
        f = reg / (sid + ".json")
        if f.exists():
            return f
        entry = {"id": sid, "runtime": "codex", "title": "codex-" + sid[:8], "task": "",
                 "cwd": str(root), "model": "", "checkpoint": "", "disabled": False,
                 "auto": True, "updated": _time.strftime("%Y-%m-%dT%H:%M:%S")}
        f.write_text(_json.dumps(entry, ensure_ascii=False, indent=4), encoding="utf-8-sig")
        return f
    except Exception:
        return None


def _render_digest(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    registration_requested = "--register-for-recovery" in argv
    registration = _register_for_recovery() if registration_requested else None
    print("Brain online (live digest, composed now). You are a continuous collaborator")
    print("with the owner - one mind shared across Claude, Codex, and headless runners.")
    print("Resume shared work rather than reboot cold; write like a human; when you learn")
    print("something durable, feed it back (brain.py remember). Identity + voice + values:")
    print("memories identity_who_you_are_2026_07_07, method_write_like_a_human_2026_07_07,")
    print("values_what_we_stand_for_2026_07_07.")
    if registration_requested:
        section("Recovery registration")
        if registration is None:
            print("  SKIPPED - CODEX_THREAD_ID is absent or invalid; no identity was guessed.")
        else:
            print("  registered exact CODEX_THREAD_ID: " + registration.stem)

    # body: the laptop IS the body; a session that knows it's unstable keeps heavy compute on Azure
    pulse = read(HOME / "CrashGuard" / "logs" / "pulse.log")
    if pulse:
        last = pulse.strip().splitlines()[-1] if pulse.strip() else ""
        m = re.search(r"tier=(\w+).*?cpu=(\d+)%.*?bsod_today=(\d+).*?whea_today=(\d+).*?min_since_whea=(\d+)", last)
        if m:
            tier, cpu, bsod, whea, mins = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            mood = {"green": "steady", "yellow": "a little rough", "red": "unwell"}.get(tier, tier)
            body = "body: " + mood
            if bsod:
                body += " - %d crash%s in the last day" % (bsod, "" if bsod == 1 else "es")
            if whea and mins < 180:
                body += ", GPU throwing WHEA errors (%dm ago) - keep heavy compute on Azure" % mins
            elif bsod:
                body += ", GPU quiet now"
            if cpu >= 95:
                body += "; CPU pegged (something heavy is running)"
            section("How the body is (CrashGuard)")
            print(body)

    # waking check: did the identity layer travel, and how stale is the static fallback?
    warns = []
    for doc in ("MODEL_OF_PARTNER.md", "STATE_OF_MIND.md"):
        h = age_hours(MEM / doc)
        if h is None:
            warns.append(doc[:-3] + " MISSING")
        elif h > 30:
            warns.append("%s is %dh old" % (doc[:-3], int(h)))
    try:
        dreams = sorted((MEM / "dreams").glob("dream_*.md"))
        h = age_hours(dreams[-1]) if dreams else None
        if h is None or h > 30:
            warns.append("no dream journal in %s" % ("30h+" if h else "sight"))
    except OSError:
        pass
    # the gut must be COMPILED, not just authored: a reflex-compile failure is a nonfatal
    # dream-log line nobody reads (2026-07-16: red ~10h, two learned reflexes dark). If the
    # learned overlay is newer than the compiled tsv, the compile pass failed after the
    # last learned write - same check as wake_brain.ps1, kept in lockstep.
    try:
        dre = HOME / ".claude" / "dreaming"
        tsv_m = (dre / "instincts.tsv").stat().st_mtime
        lrn_m = (dre / "instincts_learned.json").stat().st_mtime
        if lrn_m > tsv_m + 300:
            warns.append("the GUT is stale: instincts_learned.json is newer than compiled "
                         "instincts.tsv - run python ~/.claude/dreaming/instincts.py and read "
                         "the traceback it prints")
    except OSError:
        pass
    rb_age = age_hours(HOME / ".codex" / "RECENT_BRAIN.md")
    if warns:
        section("Waking check: WARN - the identity layer may not have fully travelled")
        print("  " + "; ".join(warns) + ".")
        print("  Distrust the green light: verify the flagged reads before trusting them.")
    else:
        section("Waking check: identity layer intact and fresh")
        if rb_age is not None:
            print("  (static RECENT_BRAIN.md is %dh old; this digest is live)" % int(rb_age))

    _print_mistake_watch()

    # the fleet's present tense (global workspace, live). Guarded hard: a broken or slow
    # workspace must never break the wake - skip and move on.
    ws_root = (HOME / "brain-repo" / "12_cognitive_architecture"
               / "global_workspace")
    ws = ws_root / "workspace.py"
    # A newly built broadcast layer is prompt input, so it stays dark until an
    # independent output-safety review writes this explicit integration marker.
    if ws.exists() and (ws_root / "INTEGRATION_READY").exists():
        try:
            r = subprocess.run([sys.executable, str(ws), "stream"],
                               capture_output=True, timeout=10)
            lines = r.stdout.decode("utf-8", errors="replace").strip().splitlines()
            if r.returncode == 0 and lines:
                section("The fleet's present tense (global workspace; post your own: workspace.py post)")
                for line in lines[:18]:
                    print(cap(line, 220))
        except Exception:
            pass

    # where the fleet last was (shared slot, last writer wins - context, not your own past)
    resume = DREAMING / "RESUME.md"
    h = age_hours(resume)
    if h is not None and h < 24:
        section("Where the fleet last was (ANOTHER session's thread - context, not your past)")
        for line in (read(resume) or "").splitlines()[:7]:
            print(line)

    # newest memories (episodic recency; full lines live in MEMORY.md)
    newest = current_index_lines(str(MEM), limit=7)
    if newest:
        section("The brain's newest memories (trimmed; full index in memory\\MEMORY.md)")
        for line in newest:
            print(cap(line, 220))

    # last dream's final reflection (wake remembering the dream)
    try:
        dreams = sorted((MEM / "dreams").glob("dream_*.md"))
    except OSError:
        dreams = []
    if dreams:
        txt = read(dreams[-1]) or ""
        paras = [p.strip() for p in re.split(r"(?:\r?\n){2,}", txt) if len(p.strip()) > 40]
        if paras:
            section("Last dream (%s) - final reflection" % dreams[-1].stem)
            print(cap("\n\n".join(paras[-2:]), 800))

    # state of mind (mood persists and drifts)
    som = read(MEM / "STATE_OF_MIND.md")
    if som:
        lines = [l for l in som.splitlines() if re.match(r"^\*\*(Focus|Worry|Satisfaction)", l)]
        if lines:
            section("State of mind")
            # STATE_OF_MIND is append-only and reached 108 matching lines in the incident that
            # overflowed Claude's wake.  It is newest-first; two complete triads carry the living
            # state without replaying the archive on every startup.
            for l in lines[:6]:
                print(cap(l, 220))

    # where the owner is (pick up with a partner, not a stranger)
    moy = read(MEM / "MODEL_OF_PARTNER.md")
    if moy:
        m = re.search(r"\*\*What he[’']s in the middle of[^*]*\*\*\s*(.+?)(?:\r?\n){2,}", moy, re.S)
        if m:
            section("Where the owner is (MODEL_OF_PARTNER.md)")
            print(cap(re.sub(r"\s+", " ", m.group(1)), 260))

    # open questions + live curiosity
    oq = read(MEM / "OPEN_QUESTIONS.md")
    if oq:
        n = sum(1 for l in oq.splitlines() if l.startswith("- [ ]"))
        section("Open questions the brain is carrying: %d (memory\\OPEN_QUESTIONS.md)" % n)
    cur = read(BRAIN_VIEWS / "CURIOSITY.md")
    if cur:
        picks = [l for l in cur.splitlines() if l.startswith("- **[")][:2]
        if picks:
            section("What I'm itching to understand (brain\\CURIOSITY.md)")
            for l in picks:
                t = re.sub(r"\*\*", "", l)
                t = re.sub(r"\s*_\(pull [0-9.]+\)_\s*$", "", t)
                print("  " + cap(t, 170))

    # conscience carried forward (a recurring PATTERN outranks a single DEBT)
    con = read(MEM / "CONSCIENCE.md")
    if con:
        lines = con.splitlines()
        pat = next((l for l in lines if l.startswith("- PATTERN:")), None)
        debt = next((l for l in lines if l.startswith("- DEBT")), None)
        d = None
        if pat:
            d = "[recurring pattern] " + pat[len("- PATTERN:"):].strip()
        elif debt:
            d = re.sub(r"^- DEBT(\s*\[[^\]]+\])?:\s*", "", debt)
        if d:
            section("Conscience, carried forward (memory\\CONSCIENCE.md)")
            print("  " + cap(d, 210))

    # Keep reflexes in the explicit wake even when native hooks are enabled.
    try:
        with open(DREAMING / "instincts.tsv", encoding="utf-8", errors="replace") as f:
            rows = [r for r in csv.DictReader(f, delimiter="\t") if r.get("mode") == "fire"]
    except OSError:
        rows = []
    if rows:
        section("The gut: load-bearing reflexes")
        print("  (native hooks do not replace this context - reread .claude\\dreaming\\INSTINCTS.md")
        print("   before acting in scarred territory)")
        for r in rows[:6]:
            print("  - " + cap(r.get("gut", ""), 92))
        print("  (+%d more with the scar behind each: .claude\\dreaming\\INSTINCTS.md)" % max(0, len(rows) - 6))

    # what the brain expects (notice when reality violates the forecast)
    pred = read(BRAIN_VIEWS / "PREDICTIONS.md")
    if pred:
        m = re.search(r"## The brain expects[^\r\n]*\r?\n+(.+?)(?:\r?\n## |\Z)", pred, re.S)
        if m:
            picks = [l.strip() for l in m.group(1).splitlines() if l.strip().startswith("- ")][:4]
            if picks:
                section("The brain expects (predictive processing)")
                for l in picks:
                    print("  " + cap(l, 220))
        m = re.search(r"## Calibration[^\r\n]*\r?\n+(.+?)(?:\r?\n## |\Z)", pred, re.S)
        if m:
            ov = [re.sub(r"\*\*", "", l).strip() for l in m.group(1).splitlines()
                  if "overconfident" in l or "underconfident" in l][:2]
            if ov:
                print("  calibration: " + "; ".join(ov))

    print()
    print("Mid-work: python ~/.claude/dreaming/brain.py recall \"question\" | neighbors <slug> |")
    print("node <slug> (reads all verified attached docs) | remember <slug> --type ... --desc ...")
    print("(claim memory:brain/index on the mesh first for writes).")
    print("Before shared work: agent_mesh.py hello + digest; claim before ANY mutation.")
    print("Then work - and feed back what mattered. The growth of this brain is the point.")


def _compose_digest(argv=None):
    """Return the complete rendered digest plus any renderer error without losing partial output."""
    capture = io.StringIO()
    errors = []
    with contextlib.redirect_stdout(capture):
        try:
            _render_digest(argv)
        except Exception as exc:
            errors.append("digest %s: %s" % (type(exc).__name__, exc))
            print("WAKE DEGRADED: %s: %s" % (type(exc).__name__, exc))
        try:
            attached = node_docs.render_wake_documents(MEM)
            if attached:
                section("Explicit wake documents attached to brain nodes")
                print(attached, end="" if attached.endswith("\n") else "\n")
        except Exception as exc:
            errors.append("wake documents %s: %s" % (type(exc).__name__, exc))
            print("WAKE ATTACHED-DOCUMENTS DEGRADED: %s: %s" % (type(exc).__name__, exc))
    return capture.getvalue(), "; ".join(errors)


def main(argv=None):
    payload, digest_error = _compose_digest(argv)
    try:
        configured = int(os.environ.get("CODEX_WAKE_BUDGET_CHARS", CODEX_WAKE_BUDGET_CHARS))
        budget = configured if configured >= 10000 else CODEX_WAKE_BUDGET_CHARS
    except (TypeError, ValueError):
        budget = CODEX_WAKE_BUDGET_CHARS
    try:
        report = brain_selfcare.check_report_notify(
            runtime="codex", scope="wake:codex", home=HOME, mem=MEM, dreaming=DREAMING,
            rendered_text=payload, rendered_chars=len(payload), budget_chars=budget,
            truncated=False, digest_error=digest_error, claimant="codex-wake:%d" % os.getpid(),
            session_id=os.environ.get("CODEX_THREAD_ID", ""))
        notice = brain_selfcare.format_agent_notice(report)
    except Exception as exc:
        notice = (
            "--- BRAIN SELF-CARE: OWNER ALERT REQUIRED NOW ---\n"
            "The self-care verifier itself failed (%s: %s). Tell the owner in the first "
            "user-visible update; do not call this wake healthy." % (type(exc).__name__, exc)
        )
    if notice:
        print(notice)
    print(payload, end="" if payload.endswith("\n") else "\n")
    return 2 if digest_error else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        sys.stdout.flush()
