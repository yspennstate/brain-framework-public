#!/usr/bin/env python3
"""coactivation_log.py - PostToolUse hook: log which memories fire together (Hebbian).

"Cells that fire together wire together" (Hebb 1949; Lowel & Singer 1992). The nav graph is static
Jaccard - amnesic about its own use: two memories always retrieved together weigh the same as two
that merely share vocabulary. This logs each READ of a memory file to a per-session file; the Stop
hook (coactivation_end.py) aggregates a session's co-reads into coactivation.tsv, which
build_brain_architecture.py blends into the association graph - so links come to reflect what the
brain actually reasons with together.

Fail-safe silent; never blocks or slows a tool call. Reads stdin (PostToolUse JSON), no output.
"""
import sys, os, io, json

HOME = os.path.expanduser("~")
MEMDIR = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory").lower()
LOGDIR = os.path.join(HOME, ".claude", "dreaming", "coactivation_session")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
        "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "CONSCIENCE",
        "MISTAKES", "CURIOSITY"}  # substrate pages, not memories (MISTAKES leaked in as a false hub)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    raw = sys.stdin.read() or "{}"
    if "memory" not in raw:      # cheap gate before the parse: this hook now also sees every Bash call
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    try:
        if data.get("tool_name") == "Bash":
            _durable_bash(data)
            return
        if data.get("tool_name") != "Read":
            return
        fp = ((data.get("tool_input", {}) or {}).get("file_path", "") or "")
        fpl = fp.replace("/", os.sep).lower()
        if MEMDIR not in fpl or not fpl.endswith(".md"):
            return
        slug = os.path.basename(fp)[:-3]
        if slug in SKIP or slug.startswith("dream_"):
            return
        sess = data.get("session_id") or "nosess"
        os.makedirs(LOGDIR, exist_ok=True)
        with io.open(os.path.join(LOGDIR, sess + ".log"), "a", encoding="utf-8") as f:
            f.write(slug + "\n")
    except Exception:
        return
    _durable(slug, sess, "read", data.get("tool_use_id"))


def _read_trace():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import read_trace
    return read_trace


def _durable(slug, sess, src, ref=None):
    """The session log above is deleted at the next Stop; this line is kept (read_trace.py -> retrievals.tsv)."""
    try:
        _read_trace().append(slug, sess, src, ref or "")
    except Exception:
        pass


def _durable_bash(data):
    """A plain shell read of a memory file (cat, sed -n, head ... on its full path) goes to the durable record only;
    the co-read session log keeps its Read-tool meaning."""
    try:
        cmd = (data.get("tool_input", {}) or {}).get("command", "") or ""
        if "/memory/" not in cmd:
            return
        rt = _read_trace()
        for slug in rt.bash_reads(cmd):
            if slug not in SKIP and not slug.startswith("dream_"):
                rt.append(slug, data.get("session_id") or "nosess", "bash", data.get("tool_use_id") or "")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
