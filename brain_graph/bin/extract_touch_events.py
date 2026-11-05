"""Everything an agent touches, as node events, from the Claude session transcripts (26 Sep). v2.

His design (26 Sep 00:26 voice note, 00:29 prompt): every folder, file, piece of code, keyword, memory, mistake, tool,
box, volume, rule and hook is a node; as agents use them in sequence the edges between them grow, as in a brain. The
July network (network/) was fed only prompts, tool names and a few thousand memory reads, and its prompts never joined
the rest (short session ids). This extractor reads what actually happened in each session and emits one event per touch.
Classes follow brain_graph/NODE_CLASSES.md where the transcripts carry them:

  tool      Read, Edit, Bash:python, Agent:sonnet ...            every tool call
  cmd       git, git push, gh pr, ssh, python -m pytest ...       the program (and its verb) of each shell command
  file      ~/movie_projects/x/build.py                          every path read, written, edited, searched or named
  dir       ~/movie_projects/x                                   the folder of each file
  proj      ~/movie_projects/world_history_series                two levels under home
  repo      ~/brain-repo                      the git checkout a path lives in
  host      build-box, render-box                                ssh/scp targets
  svc       github, youtube, codex, speechify, google-cloud ...  services a command or URL reaches
  store     gs://bucket, archive:, brain.sqlite3, /srv/data      databases, buckets, volumes
  dial      MATHBRAIN_SUBQ_EXACT_TIGHT, RENDER_THREADS           environment variables a command sets
  skill     dream, checklist                                     skills invoked
  site      arxiv.org                                            web domains fetched
  mem       a_slug_2026_09_25                                    a memory file READ (Read tool, cat/sed/grep in a shell)
  memw      a_slug_2026_09_25                                    a memory file written or edited
  recalled  a_slug_2026_09_25                                    a memory brain.py recall returned
  served    a_slug_2026_09_25                                    a memory a hook put in front of the agent
  reflex    a_scar_slug                                          a reflex that fired (its scar memory)
  hook      unconscious, slow-sense, math-brain ...              a hook that spoke (its tag)
  state     commit_high, renders_not_pooled ...                  a machine state the slow-sense probe named
  ask       T-20260926-005                                       one of his checklist asks, named in a command or hook
  err       Bash:git:exit128, Edit:not_found ...                 a tool call that failed, by class
  verdict   correction, praise, repeat                           his reaction, read from his prompt's words
  kw        word                                                 a word of his prompt
  said      word                                                 a word the agent wrote (its text, not its tool calls)

Sessions are the first 8 characters of the session id, so every source joins. Only the transcripts' own records are
read; nothing is written except the output. Incremental: a state file keeps each transcript's byte offset.
Usage: extract_touch_events.py [--days 40] [--out PATH] [--state PATH] [--max-files N]"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~").replace("\\", "/")
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
MEMDIR = (PROJ + "/memory").replace("\\", "/").lower()
HERE = os.path.dirname(os.path.abspath(__file__))
STOP = set("""the a an and or of to in on for with at by from is are was were be been it its this that these those i you
he she we they me my your our their his her them us do does did done not no yes if then else so as but can will would
should could have has had just also any all some more most less very into out up down over under again about after before
when where what which who whom why how than too only own same other such both each few here there please make sure need
want use using used get got let lets like know think see look go going ok okay thanks now one two new still next first
last time way work working file files line lines run running check""".split())
WORD = re.compile(r"[a-z][a-z0-9_]{3,}")
PATH_RX = re.compile(r"(?i)(?:[a-z]:[\\/]+users[\\/]+[^\\/\s]+|~|/c/users/[^/\s]+)((?:[\\/][^\s'\"|;&<>(){}\[\],`*?$%]+)+)")
REL_SCRIPT_RX = re.compile(r"(?:^|[\s;&|(])(?:python3?|pythonw|py)(?:\.exe)?\s+(?:-[A-Za-z]\S*\s+)*([^\s'\"-][^\s'\";&|]*\.py)\b")
PY_MOD_RX = re.compile(r"(?:^|[\s;&|(])(?:python3?|pythonw|py)(?:\.exe)?\s+(?:-[A-Za-z]\S*\s+)*-m\s+([A-Za-z_][\w.]*)")
DIAL_RX = re.compile(r"(?:^|[\s;&|(])(?:export\s+|set\s+|\$env:)?([A-Z][A-Z0-9]*_[A-Z0-9_]{2,})\s*=")
SSH_RX = re.compile(r"(?:^|[\s;&|(])(?:ssh|scp|rsync|sftp)(?:\.exe)?\s+([^\n;&|]*)")
URL_RX = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})", re.I)
GS_RX = re.compile(r"\bgs://([a-z0-9._-]+)", re.I)
RCLONE_RX = re.compile(r"\b(" + "|".join(re.escape(x) for x in (os.environ.get("BRAIN_RCLONE_REMOTES") or "archive").split(",")) + r")(?=:)")
DB_RX = re.compile(r"([A-Za-z0-9_.-]+\.(?:sqlite3|sqlite|db))\b")
SRV_RX = re.compile(r"(/srv/[a-z0-9_]+|/raid\b|/var/local\b)")
TASK_RX = re.compile(r"\bT-20\d{6}-\d{3}\b")
ASK_HOOK_RX = re.compile(r"\[checklist\][^\n]{0,120}?(?:follow-up is on|registered as|your entry)\s+(T-20\d{6}-\d{3})")
RECALL_RX = re.compile(r"(?m)^\s*\d+\.\d+\s+\[[^\]]*\]\s+([a-z0-9_]{8,})\s*$")
SERVED_RX = re.compile(r"this reminds me of `([a-z0-9_]+)`")
SCAR_RX = re.compile(r"->\s*\[\[([a-z0-9_]+)")
TAG_RX = re.compile(r"(?m)^\s*\[([a-z][a-z0-9-]{2,24})\]")
STATE_RX = re.compile(r"\[slow-sense\][^\n]*?\):\s*([a-z_, ]+)")
HOSTS = {"localhost": "localhost", "127.0.0.1": "localhost"}
HOSTS.update(json.loads(os.environ.get("BRAIN_HOSTS_JSON") or "{}"))   # {"10.0.0.5": "build-box", ...}
SVC = [(keys, re.compile(p, re.I), name) for keys, p, name in (
    (("gh ", "github", "git "), r"(?:^|[\s;&|(])gh\s|api\.github\.com|github\.com|git\s+(?:push|pull|fetch|clone)", "github"),
    (("youtube",), r"youtube|googleapis\.com/youtube", "youtube"),
    (("codex",), r"(?:^|[\s;&|(])codex(?:\.cmd|\.exe)?\s", "codex"),
    (("speechify",), r"speechify", "speechify"),
    (("telegram",), r"api\.telegram\.org|telegram", "telegram"),
    (("gcloud", "gsutil", "gs://", "googleapis"), r"(?:^|[\s;&|(])(?:gcloud|gsutil)\s|gs://|googleapis\.com", "google-cloud"),
    (("az ", "windows.net", ), r"(?:^|[\s;&|(])az\s|blob\.core\.windows\.net", "azure"),
    (("agent_mesh",), r"agent_mesh\.py", "mesh"),
    (("semanticscholar", "arxiv"), r"semanticscholar|arxiv\.org", "literature"),
    (("claude",), r"(?:^|[\s;&|(])claude(?:\.exe|\.cmd)?\s+-p\b|claude\s+--print", "claude-headless"),
    (("checklist.py",), r"checklist\.py", "checklist"),
    (("brain.py",), r"brain\.py\s+(?:recall|remember|neighbors)", "brain"),
    (("manim", "ffmpeg", "blender"), r"(?:^|[\s;&|(])(?:manim|ffmpeg|blender)(?:\.exe)?\s", "render"),
    (("lake ", "lean"), r"(?:^|[\s;&|(])lake\s|lean4|\.lean\b", "lean"),
)]
VERB_PROGS = {"git", "gh", "az", "gcloud", "gsutil", "rclone", "schtasks", "sqlite3", "codex", "npm", "pip", "docker",
              "systemctl", "lake", "cargo", "kubectl", "conda", "wsl", "winget", "choco", "claude"}
CORRECTION = re.compile(r"\b(wtf|what the fuck|i said|i told you|i never (?:told|said|asked)|why did you|why the fuck|"
                        r"not what i|wrong|not correct|incorrect|broken|doesn'?t work|not working|fucking|shit|crap|horrible)\b", re.I)
PRAISE = re.compile(r"\b(nice|love it|love this|great|perfect|amazing|beautiful|good job|well done|excellent|awesome)\b", re.I)
# He had to ask again. A bare "again" is mostly "check again" / "audit again" (26 Sep, by eye: 176 of his prompts matched
# the old test, about a third of them repeats); these phrases say he is repeating himself.
REPEAT = re.compile(r"\b(already asked|i already (?:asked|said|told)|asked (?:you|this|for this|for that|about this) "
                    r"(?:before|already)|(?:asked|said|told(?: you)?|mentioned|been over this|gone over this)\b[^.?!\n]{0,40}?"
                    r"\b(?:\d+|ten|many|multiple|several|so many) times|once again|again and again|yet again|"
                    r"i keep (?:asking|saying|telling)|still (?:not|isn't|doesn't|don't|hasn't|haven't|wrong|broken))\b", re.I)
PASTED = re.compile(r"<pasted_content\b.*?(?:</pasted_content>|\Z)", re.S)
NOT_HIS = re.compile(r"^(?:Stop hook feedback|This session is being continued|Base directory for this skill|Caveat: |"
                     r"\[SYSTEM NOTIFICATION|\[Request interrupted)")


def his_text(o, c):
    """The words he typed in a user record, or '' when the record is not a prompt of his. Until 26 Sep 17:13 the capture
    read every string user record as his: of the 4,313 records it had marked as his correction, repeat or praise, 3,970
    were isMeta records (mostly the Stop hook's work-session feedback) and about 300 were his. Pasted blocks are cut
    out: their words are someone else's."""
    if not c or c.startswith("<") or "task-notification" in c[:200]:
        return ""
    if o.get("isMeta") or o.get("isCompactSummary") or o.get("isSidechain") or o.get("isVisibleInTranscriptOnly"):
        return ""
    org = o.get("origin")
    if isinstance(org, dict) and org.get("kind") not in (None, "human"):
        return ""
    if NOT_HIS.match(c):
        return ""
    return PASTED.sub(" ", c).strip()


def his_verdicts(o, c):
    """His reaction, read from the first 600 characters of a prompt of his (c from his_text); interactive sessions only."""
    if o.get("entrypoint") != "cli" or len(c) >= 4000:
        return []
    head = c[:600]
    return [v for v, rx in (("correction", CORRECTION), ("praise", PRAISE), ("repeat", REPEAT)) if rx.search(head)]
ERR_CLASS = [
    (re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b"), None),
    (re.compile(r"(?i)database is locked"), "db_locked"),
    (re.compile(r"(?i)cp1252|charmap|codec can't"), "encoding"),
    (re.compile(r"(?i)string to replace not found"), "not_found"),
    (re.compile(r"(?i)file has not been read yet"), "not_read"),
    (re.compile(r"(?i)no such file|cannot find the path|does not exist"), "no_such_file"),
    (re.compile(r"(?i)permission denied|access is denied"), "permission"),
    (re.compile(r"(?i)timed out|timeout"), "timeout"),
    (re.compile(r"(?i)command not found|is not recognized"), "cmd_not_found"),
    (re.compile(r"(?i)exit code (\d+)"), "exit"),
]


def norm_session(s):
    m = re.match(r"([0-9a-fA-F]{8})", s or "")
    return m.group(1).lower() if m else ""


def words(text, cap=None):
    out = []
    for w in WORD.findall((text or "").lower()):
        if w not in STOP and not w.isdigit():
            out.append(w)
    if cap:
        seen, keep = set(), []
        for w in out:
            if w not in seen:
                seen.add(w)
                keep.append(w)
                if len(keep) >= cap:
                    break
        return keep
    return list(dict.fromkeys(out))


def repo_roots(max_age=3600.0):
    """Git checkouts one or two levels under home (a .git file or folder marks one). Cached for an hour in
    brain_graph/repo_roots_cache.json: the two-level glob cost ~380 ms on a busy disk at every import, and the live
    membrane hook imports this module on every evaluation (26 Sep)."""
    cache = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "repo_roots_cache.json")
    try:
        if time.time() - os.path.getmtime(cache) < max_age:
            with open(cache, encoding="utf-8") as fh:
                got = json.load(fh)
            if isinstance(got, list) and got:
                return got
    except (OSError, ValueError):
        pass
    roots = []
    for pat in ("*/.git", "*/*/.git"):
        for g in glob.glob(os.path.join(os.path.expanduser("~"), pat)):
            roots.append("~/" + os.path.relpath(os.path.dirname(g), os.path.expanduser("~")).replace("\\", "/").lower())
    out = sorted(set(roots), key=lambda r: (-len(r), r))     # longest first; ties in a fixed order
    try:
        tmp = cache + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
        os.replace(tmp, cache)
    except OSError:
        pass
    return out


REPOS = repo_roots()


def path_nodes(raw, cwd=None):
    """A raw path -> [(kind, id)]: a memory slug, or a file with its folder, project and repository."""
    p = raw.replace("\\", "/").strip().rstrip("/.:")
    if p.lower().startswith("/c/"):
        p = "C:/" + p[3:]
    if cwd and not re.match(r"(?i)^(?:[a-z]:/|~|/)", p):
        p = cwd.replace("\\", "/").rstrip("/") + "/" + p
    low = p.lower()
    if low.startswith("~"):
        low = HOME.lower() + low[1:]
    if low.startswith(MEMDIR + "/"):
        name = low[len(MEMDIR) + 1:]
        if name.endswith(".md") and "/" not in name.strip("/"):
            return [("mem", name[:-3])]
        return [("dir", "~/.claude/projects/" + os.path.basename(PROJ).lower() + "/memory")]
    if not low.startswith(HOME.lower() + "/"):
        return []
    rel = "~" + low[len(HOME):]
    rel = re.sub(r"/+", "/", rel)
    if "/../" in rel or rel.endswith("/.."):
        return []
    # a session's scratch folder is unique to that session, so its files can never recur in another one: one node
    if rel.startswith("~/appdata/local/temp/"):
        return [("dir", "~/scratchpad" if rel.startswith("~/appdata/local/temp/claude/") else "~/temp")]
    parts = rel.split("/")
    out = []
    if len(parts) >= 2:
        base = parts[-1]
        is_file = "." in base[1:] and not base.startswith(".")
        if is_file:
            out.append(("file", rel))
            folder = "/".join(parts[:-1])
        else:
            folder = rel
        if folder and folder != "~":
            out.append(("dir", folder))
        if len(parts) >= 3:
            out.append(("proj", "/".join(parts[:3]) if len(parts) > 3 or not is_file else "/".join(parts[:2])))
        for r in REPOS:
            if rel == r or rel.startswith(r + "/"):
                out.append(("repo", r))
                break
    return out


HEREDOC_RX = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def strip_heredocs(cmd):
    """A shell line without the bodies of its here-documents (a python heredoc's code lines are not commands)."""
    out, end = [], None
    for line in (cmd or "").split("\n"):
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = HEREDOC_RX.search(line)
        if m:
            end = m.group(1)
    return "\n".join(out)


def split_commands(cmd):
    return [c.strip() for c in re.split(r"&&|\|\||;|\n|\|", cmd or "") if c.strip()]


QUOTED_RX = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'", re.S)


def cmd_nodes(cmd):
    """Program and verb of every simple command in a shell line: [('cmd', 'git'), ('cmd', 'git push'), ...].
    Quoted strings are blanked first, so the lines of a python -c "..." body are not read as commands."""
    out = []
    for c in split_commands(QUOTED_RX.sub('""', cmd or "")):
        c = re.sub(r"^(?:[A-Z_][A-Z0-9_]*=\S*\s+)+", "", c)
        c = re.sub(r"^(?:sudo|timeout\s+\d+[smh]?|nice\s+-n\s*\d+|time|exec|start\s+/b)\s+", "", c)
        tok = c.split()
        if not tok:
            continue
        prog = re.sub(r"\.(exe|cmd|bat)$", "", os.path.basename(tok[0].strip("'\"&")).lower())
        if not re.fullmatch(r"[a-z0-9_.+-]{1,24}", prog) or prog in ("cd", "echo", "true", "false"):
            continue
        out.append(("cmd", prog))
        if prog in VERB_PROGS and len(tok) > 1 and re.fullmatch(r"[a-z][a-z-]{1,20}", tok[1]):
            out.append(("cmd", prog + " " + tok[1]))
    return out


def bash_cmd0(cmd):
    c = (cmd or "").strip()
    c = re.sub(r"^(?:cd\s+\S+\s*(?:&&|;)\s*)+", "", c)
    c = re.sub(r"^(?:[A-Z_][A-Z0-9_]*=\S*\s+)+", "", c)
    tok = c.split()
    if not tok:
        return "empty"
    t = os.path.basename(tok[0].strip("'\"")).lower()
    t = re.sub(r"\.exe$", "", t)
    return t if re.fullmatch(r"[a-z0-9_.+-]{1,24}", t) else "other"


def shell_nodes(cmd, cwd):
    """Everything a shell command names besides its paths."""
    cmd = strip_heredocs(cmd)[:6000]
    out = cmd_nodes(cmd)
    for m in REL_SCRIPT_RX.finditer(cmd):
        out += [kn for kn in path_nodes(m.group(1), cwd) if kn[0] in ("file", "dir", "proj", "repo")]
    for m in PY_MOD_RX.finditer(cmd):
        out.append(("cmd", "python -m " + m.group(1).split(".")[0].lower()))
    for m in DIAL_RX.finditer(cmd):
        out.append(("dial", m.group(1)))
    for m in SSH_RX.finditer(cmd):
        for tok in m.group(1).split():
            if tok.startswith("-"):
                continue
            h = tok.split("@")[-1].split(":")[0].strip("'\"").lower()
            if h in HOSTS:
                out.append(("host", HOSTS[h]))
                break
            if "@" in tok and re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,60}", h):
                out.append(("host", h))
                break
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", h):
                out.append(("host", h))
                break
    for m in URL_RX.finditer(cmd):
        out.append(("site", m.group(1).lower()))
    for m in GS_RX.finditer(cmd):
        out.append(("store", "gs://" + m.group(1).lower()))
    for m in RCLONE_RX.finditer(cmd):
        out.append(("store", m.group(1)))
    for m in DB_RX.finditer(cmd):
        out.append(("store", m.group(1).lower()))
    for m in SRV_RX.finditer(cmd):
        out.append(("store", m.group(1)))
    low = cmd.lower()
    for keys, rx, name in SVC:
        if any(k in low for k in keys) and rx.search(cmd):
            out.append(("svc", name))
    for m in TASK_RX.finditer(cmd):
        out.append(("ask", m.group(0)))
    return out


def err_class(tool, text):
    t = text if isinstance(text, str) else json.dumps(text)[:4000]
    for rx, name in ERR_CLASS:
        m = rx.search(t[:4000])
        if m:
            if name is None:
                return "%s:%s" % (tool, m.group(1))
            if name == "exit":
                return "%s:exit%s" % (tool, m.group(1))
            return "%s:%s" % (tool, name)
    return "%s:error" % tool


def result_text(c):
    x = c.get("content")
    if isinstance(x, list):
        return " ".join((y.get("text") or "") for y in x if isinstance(y, dict))
    return x if isinstance(x, str) else ""


def epoch(ts):
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def hook_text(a):
    """The text a hook put in front of the model (plain stdout in content, JSON additionalContext in stdout)."""
    c = a.get("content")
    if isinstance(c, list):
        txt = " ".join(x for x in c if isinstance(x, str))
    else:
        txt = c if isinstance(c, str) else ""
    if not txt:
        so = a.get("stdout")
        if isinstance(so, str) and so.strip():
            try:
                j = json.loads(so)
                txt = ((j.get("hookSpecificOutput") or {}).get("additionalContext") or "") if isinstance(j, dict) else ""
            except Exception:
                txt = so
    return txt


def _first_sight(seen, session, text, ts, window=1800.0):
    """True the first time a prompt's text is seen in a session within `window` seconds: a prompt queued while the agent
    worked and then delivered as a new turn is written twice (18 of 1,231 since 16 Aug), and is counted once."""
    key = (session, text[:300])
    last = seen.get(key)
    seen[key] = ts
    return last is None or abs(ts - last) > window


def complete_end(path, start, step=1 << 20):
    """The offset just after the transcript's last complete line (27 Sep: the capture runs every five minutes now, and a
    line the session is still writing would be read as bad JSON and its offset passed, losing it). A file ending in a
    newline ends there; otherwise the search walks back from the end; with no complete line after start, start."""
    size = os.path.getsize(path)
    if size <= start:
        return size
    with open(path, "rb") as fh:
        fh.seek(size - 1)
        if fh.read(1) == b"\n":
            return size
        hi = size
        while hi > start:
            lo = max(start, hi - step)
            fh.seek(lo)
            k = fh.read(hi - lo).rfind(b"\n")
            if k >= 0:
                return lo + k + 1
            hi = lo
    return start


def scan_file(path, start, emit, stats, end=None):
    tools = {}
    recalls = set()
    prompts_seen = {}
    with open(path, "rb") as fh:
        fh.seek(start)
        pos = start
        for raw in fh:
            if end is not None and pos >= end:
                break                        # a line still being written: the next capture reads it whole
            pos += len(raw)
            if not raw.strip():
                continue
            if (b'"tool_use"' not in raw and b'"tool_result"' not in raw and b'hook_additional_context' not in raw
                    and b'hook_success' not in raw and b'"type":"user"' not in raw and b'"type":"assistant"' not in raw
                    and b'queued_command' not in raw):
                continue
            try:
                o = json.loads(raw)
            except Exception:
                stats["bad_json"] += 1
                continue
            s = norm_session(o.get("sessionId") or "")
            ts = epoch(o.get("timestamp") or "")
            if not s or not ts:
                continue
            t = o.get("type")
            cwd = o.get("cwd") or ""
            if t == "attachment":
                a = o.get("attachment") or {}
                if a.get("type") == "queued_command":
                    # A prompt he typed while the agent was working (27 Sep): the transcript keeps it only as this
                    # attachment (and a queue-operation record), never as a user record, so until 27 Sep the capture
                    # never read it. Since 16 Aug that was 1,213 of his 5,269 prompts and 59 of his 165 corrections.
                    # Task notifications travel the same way; his_text refuses them.
                    p = a.get("prompt")
                    c = his_text(o, p.strip()) if isinstance(p, str) else ""
                    if c and _first_sight(prompts_seen, s, c, ts):
                        for w in words(c, cap=40):
                            emit(ts, s, "kw", w)
                        stats["prompts"] += 1
                        stats["queued_prompts"] += 1
                        for v in his_verdicts(o, c):
                            emit(ts, s, "verdict", v)
                    continue
                if a.get("type") not in ("hook_additional_context", "hook_success"):
                    continue
                txt = hook_text(a)
                if not txt:
                    continue
                for tag in dict.fromkeys(TAG_RX.findall(txt)):
                    emit(ts, s, "hook", tag)
                for slug in dict.fromkeys(SERVED_RX.findall(txt)):
                    emit(ts, s, "served", slug)
                if "[unconscious]" in txt:
                    for slug in dict.fromkeys(SCAR_RX.findall(txt)):
                        if len(slug) >= 8:
                            emit(ts, s, "reflex", slug)
                m = STATE_RX.search(txt)
                if m:
                    for st in dict.fromkeys(x.strip() for x in m.group(1).split(",")):
                        if re.fullmatch(r"[a-z_]{4,40}", st):
                            emit(ts, s, "state", st)
                if "[checklist]" in txt:
                    for m in ASK_HOOK_RX.finditer(txt):
                        emit(ts, s, "ask", m.group(1))
                continue
            msg = o.get("message") or {}
            content = msg.get("content")
            if t == "user":
                if isinstance(content, str):
                    c = his_text(o, content.strip())
                    if c and _first_sight(prompts_seen, s, c, ts):
                        for w in words(c, cap=40):
                            emit(ts, s, "kw", w)
                        stats["prompts"] += 1
                        for v in his_verdicts(o, c):
                            emit(ts, s, "verdict", v)
                elif isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict) or c.get("type") != "tool_result":
                            continue
                        tid = c.get("tool_use_id")
                        if c.get("is_error"):
                            name = tools.get(tid, "tool")
                            emit(ts, s, "err", err_class(name, result_text(c)))
                        elif tid in recalls:
                            for slug in dict.fromkeys(RECALL_RX.findall(result_text(c))):
                                emit(ts, s, "recalled", slug)
                continue
            if t != "assistant" or not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    for w in words(c.get("text"), cap=12):
                        emit(ts, s, "said", w)
                    continue
                if c.get("type") != "tool_use":
                    continue
                name = c.get("name") or "tool"
                inp = c.get("input") or {}
                label = name
                extra = []
                if name == "Bash":
                    label = "Bash:" + bash_cmd0(inp.get("command"))
                elif name in ("Agent", "Task"):
                    label = "Agent:" + str(inp.get("model") or inp.get("subagent_type") or "default")[:24]
                elif name == "Skill":
                    extra.append(("skill", str(inp.get("skill") or "")[:40].lower()))
                elif name in ("WebFetch", "WebSearch"):
                    for m in URL_RX.finditer(str(inp.get("url") or "")):
                        extra.append(("site", m.group(1).lower()))
                tools[c.get("id")] = label
                emit(ts, s, "tool", label)
                writes = name in ("Write", "Edit", "NotebookEdit", "MultiEdit")
                paths = []
                for key in ("file_path", "path", "notebook_path"):
                    if isinstance(inp.get(key), str):
                        paths.append(inp[key])
                if name in ("Bash", "PowerShell"):
                    cmd = inp.get("command") or ""
                    paths += [os.path.expanduser("~").replace(chr(92), "/") + m.group(1) for m in PATH_RX.finditer(cmd)]
                    extra += shell_nodes(cmd, cwd)
                    if "brain.py recall" in cmd or "brain.py neighbors" in cmd:
                        recalls.add(c.get("id"))
                    if "brain.py remember" in cmd:
                        m = re.search(r"brain\.py\s+remember\s+([a-z0-9_]{8,})", cmd)
                        if m:
                            extra.append(("memw", m.group(1)))
                seen = set()
                for pth in paths:
                    for kind, nid in path_nodes(pth, cwd if name in ("Bash", "PowerShell") else None):
                        if kind == "mem" and writes:
                            kind = "memw"
                        if (kind, nid) not in seen:
                            seen.add((kind, nid))
                            emit(ts, s, kind, nid)
                for kind, nid in extra:
                    if nid and (kind, nid) not in seen:
                        seen.add((kind, nid))
                        emit(ts, s, kind, nid)


def seed_from_transcript(path, now=None, window=600.0, k=40, tail_bytes=4 << 20):
    """The nodes a session touched in the last `window` seconds, newest first - what a membrane hook seeds the graph
    with. Reads only the transcript's tail (a hook has a second, not a minute). Returns [(node, age_seconds)]."""
    size = os.path.getsize(path)
    start = max(0, size - tail_bytes)
    got = []

    def emit(ts, s, kind, nid):
        got.append((ts, kind + ":" + nid))
    stats = collections.Counter()
    with open(path, "rb") as fh:
        fh.seek(start)
        if start:
            fh.readline()                   # the first line is cut mid-record
        tmp = fh.read()
    import tempfile
    with tempfile.NamedTemporaryFile("wb", suffix=".jsonl", delete=False) as t:
        t.write(tmp)
        tpath = t.name
    try:
        scan_file(tpath, 0, emit, stats)
    finally:
        os.remove(tpath)
    if not got:
        return []
    now = now if now is not None else max(ts for ts, _n in got)
    out, seen = [], set()
    for ts, node in sorted(got, reverse=True):
        if now - ts > window or len(out) >= k:
            break
        if node not in seen and ts <= now:
            seen.add(node)
            out.append((node, now - ts))
    return out


def polite():
    """BelowNormal, and opted out of Windows power throttling (a throttled windowless job ran 4-10x slower, 25 Sep)."""
    try:
        import ctypes
        import ctypes.wintypes as w
        # the types must be declared: left to guess, ctypes passes GetCurrentProcess()'s pseudo-handle as a 32-bit
        # int and both calls silently fail (measured 26 Sep: the class stayed Normal)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = w.HANDLE
        k32.SetPriorityClass.argtypes = [w.HANDLE, w.DWORD]
        k32.SetProcessInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        h = k32.GetCurrentProcess()
        k32.SetPriorityClass(h, 0x00004000)

        class PPTS(ctypes.Structure):
            _fields_ = [("Version", w.ULONG), ("ControlMask", w.ULONG), ("StateMask", w.ULONG)]
        st = PPTS(1, 0x1, 0x0)
        k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


COMMITTED = "_committed_bytes"      # state key: {output path: its length when the state was last replaced}


def uncommitted_tail(path, committed):
    """The lines a run appended to `path` after the length the last state replace recorded, as a multiset, and what was
    found (28 Sep, fleet03/B12 R12). A run is committed when its state is replaced; a run killed after writing some
    transcripts' events and before that leaves them at the end of the log, and the next run scans those transcripts
    again from the old offsets. Nothing is removed: a torn last line is ended with a newline (the readers skip a line
    that does not parse) and a complete line already in the tail is not written a second time. Lines compare without
    their line ending (the log is written in text mode, CRLF on Windows)."""
    info = {"tail_bytes": 0, "tail_lines": 0, "torn": 0}
    if committed is None or not os.path.exists(path):
        return collections.Counter(), info
    size, committed = os.path.getsize(path), int(committed)
    if size <= committed:
        if size < committed:
            info["shorter_than_committed"] = committed - size   # rewritten elsewhere; nothing here to reconcile
        return collections.Counter(), info
    with open(path, "rb") as fh:
        fh.seek(committed)
        tail = fh.read(size - committed)
    info["tail_bytes"] = len(tail)
    if not tail.endswith(b"\n"):
        with open(path, "ab") as fh:
            fh.write(b"\n")
        info["torn"] = 1
    c = collections.Counter()
    for ln in tail.split(b"\n"):
        ln = ln.rstrip(b"\r")
        if ln:
            c[ln.decode("utf-8", "replace")] += 1
    info["tail_lines"] = sum(c.values())
    return c, info


def _sync(fh):
    fh.flush()
    os.fsync(fh.fileno())


def _save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
        _sync(fh)
    os.replace(tmp, path)


def main():
    polite()
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=40)
    ap.add_argument("--out", default=os.path.join(HERE, "events_touch.jsonl"))
    ap.add_argument("--state", default=os.path.join(HERE, "events_touch_state.json"))
    ap.add_argument("--max-files", type=int, default=0)
    ap.add_argument("--verdicts-out", default=None, help="also append his corrections, repeats and praise here")
    ap.add_argument("--fail-after", type=int, default=0, help=argparse.SUPPRESS)   # tests: die after N transcripts
    ap.add_argument("--fail-torn", action="store_true", help=argparse.SUPPRESS)    # tests: die mid-write, torn line
    a = ap.parse_args()
    try:
        state = json.load(open(a.state, encoding="utf-8"))
    except Exception:
        state = {}
    outs = [a.out] + ([a.verdicts_out] if a.verdicts_out else [])
    keys = {p: os.path.normcase(os.path.abspath(p)) for p in outs}
    committed = state.get(COMMITTED)
    if not isinstance(committed, dict) or any(keys[p] not in committed for p in outs):
        # A state from before 28 Sep, or a first run: what is in the files now counts as committed, and that is saved
        # before anything is scanned, so a run that dies part way through this very run is reconciled by the next.
        committed = dict(committed) if isinstance(committed, dict) else {}
        for p in outs:
            committed.setdefault(keys[p], os.path.getsize(p) if os.path.exists(p) else 0)
        state[COMMITTED] = committed
        _save_state(a.state, state)
    pending, rec = {}, {}
    for p in outs:
        pending[p], rec[p] = uncommitted_tail(p, committed.get(keys[p]))
    cutoff = time.time() - a.days * 86400
    files = [f for f in glob.glob(os.path.join(PROJ, "**", "*.jsonl"), recursive=True)
             if os.sep + "memory" + os.sep not in f and os.path.getmtime(f) >= cutoff]
    files.sort(key=os.path.getmtime)
    if a.max_files:
        files = files[-a.max_files:]
    stats = collections.Counter()
    kinds = collections.Counter()
    t0 = time.time()
    # A transcript's events are held until it scans cleanly and then written together (28 Sep): before, each event
    # was written as it was found, so a transcript that raised part way left its first events in the log and the next
    # run, starting again from the old offset, wrote them a second time.
    buf = {p: [] for p in outs}
    took = []                                          # (path, line) consumed from a tail by the current transcript

    def put(p, line):
        if pending[p].get(line, 0) > 0:
            pending[p][line] -= 1
            took.append((p, line))
            stats["tail_matched"] += 1
        else:
            buf[p].append(line + "\n")

    def emit(ts, s, kind, nid):
        line = json.dumps({"ts": round(ts, 3), "session": s, "kind": kind, "id": nid})
        put(a.out, line)
        if a.verdicts_out and kind == "verdict":
            put(a.verdicts_out, line)
        kinds[kind] += 1

    fhs = {p: open(p, "a", encoding="utf-8") for p in outs}
    try:
        done = 0
        for i, f in enumerate(files):
            start = int(state.get(f, 0))
            if start > os.path.getsize(f):
                start = 0
            size = complete_end(f, start)
            if start >= size:
                continue
            for p in outs:
                buf[p].clear()
            took.clear()
            try:
                scan_file(f, start, emit, stats, end=size)
            except Exception as e:
                for p, line in took:                   # this transcript is scanned again next run
                    pending[p][line] += 1
                stats["tail_matched"] -= len(took)
                stats["file_errors"] += 1
                print("  skip %s: %s" % (os.path.basename(f), type(e).__name__), file=sys.stderr)
                continue
            for p in outs:
                if buf[p]:
                    if a.fail_torn and p == a.out and done + 1 >= max(1, a.fail_after):
                        text = "".join(buf[p])
                        k = text.find("\n", len(text) // 2)
                        fhs[p].write(text[: (k if k > 3 else len(text) // 2 + 3) - 3])   # cut inside a line
                        _sync(fhs[p])
                        os._exit(9)
                    fhs[p].write("".join(buf[p]))
            state[f] = size
            done += 1
            stats["files"] += 1
            stats["bytes"] += size - start
            if a.fail_after and not a.fail_torn and done >= a.fail_after:
                for p in outs:
                    _sync(fhs[p])
                os._exit(9)
            if (i + 1) % 200 == 0:
                print("  %d of %d files, %.0f MB, %.0f s" % (i + 1, len(files), stats["bytes"] / 2**20, time.time() - t0),
                      flush=True)
        for p in outs:                                 # the events are on disk before the state says they were read
            _sync(fhs[p])
    finally:
        for fh in fhs.values():
            fh.close()
    state[COMMITTED] = {keys[p]: os.path.getsize(p) for p in outs}
    _save_state(a.state, state)
    left = sum(sum(v for v in c.values() if v > 0) for c in pending.values())
    tail_note = ""
    if any(r["tail_lines"] or r["torn"] or r.get("shorter_than_committed") for r in rec.values()):
        tail_note = "; an interrupted run's tail: %s, %d of its lines not written again, %d not met again" % (
            {os.path.basename(p): r for p, r in rec.items()}, stats["tail_matched"], left)
    print("scanned %d transcripts (%.0f MB) in %.0f s; events by kind %s; prompts %d; bad json %d; file errors %d%s" % (
        stats["files"], stats["bytes"] / 2**20, time.time() - t0, dict(kinds), stats["prompts"], stats["bad_json"],
        stats["file_errors"], tail_note))


if __name__ == "__main__":
    main()
