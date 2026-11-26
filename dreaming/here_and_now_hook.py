"""here_and_now_hook.py - keeps outward text on the current state of the work, not an old snapshot.

Written after a paragraph quoting a dated count of the store (an early snapshot) was copied into an outward
document from an older draft, when the store and its tests had long since moved on. Things change quickly:
write from the live source.

PreToolUse, advisory, never blocks, silent on any error. Two triggers:
  H1  a write or send of outward text (.tex, .html, README.md, a file under Downloads or career, an email script)
      quotes a dated state of our own work - "snapshot of 1 November 2026", "as of 3 Nov", "export of ...",
      "holds 13,344 cards" - with a date more than STALE_DAYS old. One line: read the live source first.
  H2  a Read of a file inside a dated folder (2026-11-10, 2026_11_10, loop_1110 style) when the same parent holds a
      sibling folder with a later date. One line naming the newest sibling.
Each (session, trigger, key) fires once. Plain stdout by default (a hook dispatcher wraps it); --json prints the
Codex hookSpecificOutput form. --selftest runs controls both ways.
"""
import datetime as dt
import json, os, re, sys

STALE_DAYS = 7
STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "here_and_now")
MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
MON3 = {k[:3]: v for k, v in MONTHS.items()}
DOC_EXT = (".tex", ".html", ".htm")
EMAIL_MARK = re.compile(r"EmailMessage|gmail_send_token|messages\(\)\.send|sendmail", re.I)
STATE_PHRASE = re.compile(
    r"(?:snapshot|export|refresh|version|state|count|counts|edition|release)\s+(?:of|on|from|dated|as of)\s+"
    r"(?:the\s+)?(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:,?\s+(20\d\d))?\b"
    r"|\bas of\s+(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:,?\s+(20\d\d))?\b", re.I)
COMPILE = re.compile(r"\b(?:pdflatex|xelatex|lualatex|latexmk)\b[^\n;|&]*?([\w./\\:-]+?)(?:\.tex)?(?=\s|$|;|&|\|)", re.I)
SKIP_PATH = re.compile(r"[\\/](memory|dreaming|brain_graph|audits|journal|transcripts?|logs?)[\\/]|INSTINCTS|MISTAKES|"
                       r"CHANGELOG|HISTORY|_ARCHIVE|NOTES\.md$|STATE\.md$", re.I)
DATED_DIR = re.compile(r"^(.*?)(20\d\d)[-_](\d\d)[-_](\d\d)(.*)$|^(.*?_)(\d\d)(\d\d)$")
LINE_H1 = ("[here-and-now H1] This text cites {what} ({age} days old) as the state of our work. Focus on the "
           "here and now, not the past: things change quickly. Before it goes out, read the live "
           "source (the repo's README/MANIFEST/TESTS.md, the newest result memories, your partner's latest requests on it) and write "
           "from that; keep an old date only when the text is about that date.")
LINE_H2 = ("[here-and-now H2] You are reading {old}, but {newest} beside it is newer. Focus on the here and "
           "now. Check the newer folder is not the current version before you build on this one.")


def parse_date(d, mon, y, today=None):
    m = MONTHS.get(mon.lower()) or MON3.get(mon.lower()[:3])
    if not m:
        return None
    today = today or dt.date.today()
    try:
        if y:
            return dt.date(int(y), m, int(d))
        c = dt.date(today.year, m, int(d))  # no year: the most recent such date not in the future
        return c if c <= today else dt.date(today.year - 1, m, int(d))
    except ValueError:
        return None


def compiled_tex(cmd, cwd):
    """The .tex files a compile command builds, with the \\input/\\include files beside them."""
    out = []
    for m in re.finditer(r"\b(?:pdflatex|xelatex|lualatex|latexmk)\b([^\n;|&]*)", cmd, re.I):
        for tok in m.group(1).split():
            if tok.startswith("-"):
                continue
            tok = tok.strip("'\"")
            if not tok.lower().endswith(".tex"):
                tok += ".tex"
            p = tok if os.path.isabs(tok) or re.match(r"^[A-Za-z]:", tok) else os.path.join(cwd, tok)
            if os.path.isfile(p):
                out.append(p)
    for p in list(out):
        try:
            src = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for inc in re.findall(r"\\(?:input|include)\{([^}]+)\}", src):
            q = os.path.join(os.path.dirname(p), inc if inc.endswith(".tex") else inc + ".tex")
            if os.path.isfile(q):
                out.append(q)
    return out


def outward(tool, inp):
    """Return the outward text this call writes or sends, or ''."""
    path = str(inp.get("file_path") or inp.get("path") or "")
    text = ""
    for k in ("content", "new_string", "command", "input", "patch"):
        v = inp.get(k)
        if isinstance(v, list):
            v = " ".join(map(str, v))
        if isinstance(v, str):
            text += "\n" + v
    if not text:
        return ""
    if path:
        if SKIP_PATH.search(path):
            return ""
        low = path.lower().replace("\\", "/")
        if low.endswith(DOC_EXT) or low.endswith("readme.md") or "/downloads/" in low or "/career/" in low:
            return text
        if low.endswith(".py") and EMAIL_MARK.search(text):
            return text
        return ""
    if EMAIL_MARK.search(text):  # a shell call that sends mail
        return text
    if re.search(r"\*\*\* (?:Update|Add) File: [^\n]*(?:\.tex|\.html?|README\.md)\b", text, re.I):  # Codex apply_patch
        return text
    if re.search(r"(?:^|[\\/\s])[\w./\\-]+\.(?:tex|html)\b", text) and re.search(r"cat\s*>|tee\s|>\s*\S+\.(?:tex|html)", text):
        return text
    return ""


def h1(tool, inp, today, cwd=""):
    text = outward(tool, inp)
    cmd = inp.get("command")
    if isinstance(cmd, str) and COMPILE.search(cmd):  # a compile: read the .tex on disk (sed or a script may have edited it)
        for p in compiled_tex(cmd, cwd or os.getcwd()):
            if not SKIP_PATH.search(p):
                try:
                    text += "\n" + open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    pass
    if not text:
        return None
    for m in STATE_PHRASE.finditer(text):
        g = m.groups()
        d = parse_date(*(g[0:3] if g[0] else g[3:6]), today=today)
        if d and (today - d).days > STALE_DAYS:
            return ("H1:" + m.group(0).lower()[:60],
                    LINE_H1.format(what='"' + m.group(0).strip() + '"', age=(today - d).days))
    return None


def dir_date(name):
    m = DATED_DIR.match(name)
    if not m:
        return None, None
    if m.group(2):
        try:
            return (m.group(1), m.group(5)), dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            return None, None
    try:
        return (m.group(6), ""), dt.date(dt.date.today().year, int(m.group(7)), int(m.group(8)))
    except ValueError:
        return None, None


def h2(tool, inp, today):
    if tool not in ("Read", "read_file", "view"):
        return None
    path = str(inp.get("file_path") or inp.get("path") or "")
    if not path or SKIP_PATH.search(path):
        return None
    p = os.path.normpath(path)
    parts = p.split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        key, d = dir_date(parts[i])
        if not d:
            continue
        parent = os.sep.join(parts[:i]) or os.sep
        if parent.endswith(":"):
            parent += os.sep
        try:
            sibs = os.listdir(parent)
        except OSError:
            return None
        best = None
        for s in sibs:
            k2, d2 = dir_date(s)
            if k2 == key and d2 and d2 > d and os.path.isdir(os.path.join(parent, s)):
                if best is None or d2 > best[1]:
                    best = (s, d2)
        if best:
            return ("H2:" + os.path.join(parent, parts[i]).lower(),
                    LINE_H2.format(old=os.path.join(parent, parts[i]), newest=os.path.join(parent, best[0])))
        return None
    return None


def check(data, today=None, state_dir=STATE_DIR):
    today = today or dt.date.today()
    tool = str(data.get("tool_name") or "")
    inp = data.get("tool_input") or {}
    if not isinstance(inp, dict):
        inp = {"command": str(inp)}
    hit = h1(tool, inp, today, str(data.get("cwd") or "")) or h2(tool, inp, today)
    if not hit:
        return None
    key, line = hit
    if state_dir:
        sid = re.sub(r"[^\w-]", "_", str(data.get("session_id") or "nosession"))[:64]
        sp = os.path.join(state_dir, sid + ".json")
        try:
            seen = set(json.load(open(sp, encoding="utf-8")))
        except Exception:
            seen = set()
        if key in seen:
            return None
        seen.add(key)
        try:
            os.makedirs(state_dir, exist_ok=True)
            json.dump(sorted(seen)[-200:], open(sp, "w", encoding="utf-8"))
        except OSError:
            pass
    return line


def selftest():
    T = dt.date(2026, 11, 19)
    import tempfile
    tmp = tempfile.mkdtemp()
    for n in ("rs_cv_2026-11-10", "rs_cv_2026-11-19", "other_2026-11-01"):
        os.makedirs(os.path.join(tmp, n))
    open(os.path.join(tmp, "rs_cv_2026-11-10", "cv.tex"), "w").close()
    with open(os.path.join(tmp, "cv.tex"), "w", encoding="utf-8") as f:
        f.write("\\input{sec}\n")
    with open(os.path.join(tmp, "sec.tex"), "w", encoding="utf-8") as f:
        f.write("The public snapshot of 1 November 2026 contains 13,344 cards.\n")
    with open(os.path.join(tmp, "ok.tex"), "w", encoding="utf-8") as f:
        f.write("The export of 18 November 2026 holds 89,410 cards.\n")
    cases = [
        # (name, data, expect_fire)
        ("P1 stale snapshot in CV tex", {"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\me\Downloads\career\x\CV.tex",
         "content": "The public snapshot of 1 November 2026 contains 13,344 cards"}}, True),
        ("P2 stale as-of in email script", {"tool_name": "Write", "tool_input": {"file_path": r"C:\tmp\send.py",
         "content": "from email.message import EmailMessage\nbody='counts as of 3 Nov 2026 were'"}}, True),
        ("P3 Edit README export of", {"tool_name": "Edit", "tool_input": {"file_path": r"C:\repo\README.md",
         "old_string": "x", "new_string": "the export of 30 October 2026 holds"}}, True),
        ("P4 Read in older dated folder", {"tool_name": "Read", "tool_input": {"file_path": os.path.join(tmp, "rs_cv_2026-11-10", "cv.tex")}}, True),
        ("P5 Codex apply_patch tex", {"tool_name": "apply_patch", "tool_input": {"input": "*** Update File: paper/main.tex\n+snapshot of 2 Nov 2026"}}, True),
        ("P6 no year, email script", {"tool_name": "Write", "tool_input": {"file_path": r"C:\tmp\send_rscv.py",
         "content": "from email.message import EmailMessage\nbody='Brain counts are from the public snapshot of 1 Nov (13,344 cards'"}}, True),
        ("P7 pdflatex after a sed edit", {"tool_name": "Bash", "cwd": tmp, "tool_input": {"command": "cd x; pdflatex -interaction=nonstopmode " + os.path.join(tmp, "cv.tex")}}, True),
        ("N8 pdflatex on a current tex", {"tool_name": "Bash", "cwd": tmp, "tool_input": {"command": "pdflatex " + os.path.join(tmp, "ok.tex")}}, False),
        ("N7 Codex apply_patch on a .py", {"tool_name": "apply_patch", "tool_input": {"input": "*** Update File: tools/x.py\n+snapshot of 2 Nov 2026"}}, False),
        ("N1 current date", {"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\me\Downloads\a.tex",
         "content": "The export of 18 November 2026 holds 89,410 cards"}}, False),
        ("N2 memory file may cite history", {"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\me\.claude\projects\C--Users-me\memory\m.md",
         "content": "the snapshot of 1 November 2026"}}, False),
        ("N3 plain python no email", {"tool_name": "Write", "tool_input": {"file_path": r"C:\tmp\calc.py",
         "content": "# snapshot of 1 November 2026"}}, False),
        ("N4 Read newest dated folder", {"tool_name": "Read", "tool_input": {"file_path": os.path.join(tmp, "rs_cv_2026-11-19", "x")}}, False),
        ("N5 paper about a date", {"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\me\Downloads\p.tex",
         "content": "On 1 November 2026 the run finished."}}, False),
        ("N6 NOTES.md log", {"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\me\Downloads\career\NOTES.md",
         "content": "snapshot of 1 November 2026"}}, False),
    ]
    ok = True
    for name, data, want in cases:
        data["session_id"] = "selftest"
        got = check(data, today=T, state_dir=None) is not None
        flag = "PASS" if got == want else "FAIL"
        ok &= got == want
        print(flag, name, "fired" if got else "silent")
    # once-per-session
    sd = tempfile.mkdtemp()
    d = dict(cases[0][1], session_id="s1")
    a, b = check(d, today=T, state_dir=sd), check(d, today=T, state_dir=sd)
    once = a is not None and b is None
    print("PASS" if once else "FAIL", "D1 fires once per session")
    ok &= once
    print("ALL PASS" if ok else "SOME FAIL")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        buf = getattr(sys.stdin, "buffer", None)  # bytes when run as a process (no codepage decode); text in-process
        raw = buf.read().decode("utf-8", "replace") if buf is not None else sys.stdin.read()
        data = json.loads(raw or "{}")
        line = check(data)
    except Exception:
        return
    if not line:
        return
    if "--json" in sys.argv:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": line}}))
    else:
        print(line)


if __name__ == "__main__":
    main()
