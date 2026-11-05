"""The exact-name trigger for the main brain (6 Oct 2026): when an agent's prompt or command names
something the brain holds a specific card for, push that one card - a memory's name, a file with a scar, a
command that failed before in the same way, or one of the agent's own results.

Version 1 is the file half. A scar card (a feedback card, or one whose description reads as a mistake) indexes a file
only when the card is ABOUT that file: the file is named in the card's description, or the card names at most
FOCUSED files in all. A command or prompt that names the file exactly gets that one card, once per session. A name more
than MAX_CARDS such cards are about, a generic name (README.md, main.py ...) and a superseded card index nothing. Among
the cards about a file: one that names it in its description first, then the newest, then the most focused. (The first
build ranked his law, rule and order cards first; on the real store that pointed kaggle_ship.py at an order that only
mentions it in passing, and left instinct_prime.py and brain.py with no card at all because dozens of cards mention
them. Cards that only mention a file no longer count against it.)

The index is built ahead (`python exact_name.py build`, incremental by mtime over the memory dir; the hook starts a
windowless rebuild when the index is over two hours old); the hook only reads it.

Show-or-hold from day one: per (session, card) one in HOLD_EVERY held by sha256("exact:session:slug"), never an owner
law, rule or order card; both arms are written to exact_name_ab.tsv (time, session, name, slug, arm) before the context
goes out, and either arm spends the card for the session. Registered outcome: a tool error within the next five calls
of that session on a command naming the same file, read from the outcome log, in both arms the same way."""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}/memory")
LIVE = os.path.expanduser("~/.claude/brain_graph/live")
INDEX = os.path.join(LIVE, "exact_names.json")
CACHE = os.path.join(LIVE, "exact_names_cache.json")
STATE_DIR = os.path.join(LIVE, "exact_name_state")
LOG = os.path.join(HERE, "exact_name_ab.tsv")
HOLD_EVERY = 4
MAX_CARDS = 8
FOCUSED = 3
REBUILD_AFTER = 2 * 3600
EXT = "py|ps1|psm1|sh|bash|vbs|bat|cmd|js|mjs|ts|json|jsonl|tsv|csv|toml|yaml|yml|ini|cfg|md|tex|lean|sql|sqlite|db|txt|html"
NAME_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9_][\w.-]{2,80}\.(?:%s))(?![\w-])" % EXT, re.IGNORECASE)
GENERIC = set(("readme.md memory.md skill.md mistakes.md agents.md claude.md index.md notes.md todo.md changelog.md "
               "license.md __init__.py main.py test.py run.py setup.py utils.py util.py app.py config.py config.json "
               "settings.json package.json index.html requirements.txt").split())
SCAR_RE = re.compile(r"\b(mistake|scar|broke|broken|wrong|bug|fail|failed|failure|refus|lost|leak|stale|never|must not|"
                     r"do not|don't|crash|froze|silently)", re.IGNORECASE)
LAW_RE = re.compile(r"^owner_(law|rule|order)_")
DATE_RE = re.compile(r"(20\d\d)_(\d\d)_(\d\d)")
_IDX = [None]


def _parse(txt):
    head, body = txt[:3000], txt
    if txt.startswith("---"):
        end = txt.find("\n---", 3)
        if end > 0:
            head, body = txt[:end], txt[end + 4:]
    typ = re.search(r"^\s*type:\s*([\w-]+)", head, re.M)
    desc = re.search(r"^description:\s*(.*)$", head, re.M)
    sup = re.search(r"^\s*superseded_by:\s*(\S+)", head, re.M)
    names = sorted(set(n.lower() for n in NAME_RE.findall(body)))
    return [typ.group(1).lower() if typ else "", (desc.group(1).strip().strip("'\"") if desc else "")[:400],
            sup.group(1) if sup else "", names[:200]]


def _rank(card):
    slug, desc, typ, k, about = card
    d = DATE_RE.search(slug)
    return (0 if about else 1, -int("".join(d.groups())) if d else 0, k, 0 if typ == "feedback" else 1)


def build():
    t0 = time.time()
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    new, read = {}, 0
    for e in os.scandir(MEM):
        if not e.name.endswith(".md") or e.name == "MEMORY.md":
            continue
        mt = e.stat().st_mtime_ns
        c = cache.get(e.name)
        if c and c[0] == mt:
            new[e.name] = c
            continue
        try:
            with open(e.path, encoding="utf-8", errors="replace") as f:
                txt = f.read(300000)
        except OSError:
            continue
        read += 1
        new[e.name] = [mt] + _parse(txt)
    os.makedirs(LIVE, exist_ok=True)
    with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(new, f)
    os.replace(CACHE + ".tmp", CACHE)
    by = {}
    for key, (_mt, typ, desc, sup, names) in new.items():
        if sup or not names or not (typ == "feedback" or SCAR_RE.search(desc)):
            continue
        dl = desc.lower()
        for n in names:
            about = n in dl
            if n not in GENERIC and (about or len(names) <= FOCUSED):
                by.setdefault(n, []).append((key[:-3], desc, typ, len(names), about))
    idx = {}
    for n, cards in by.items():
        if len(cards) <= MAX_CARDS:
            best = sorted(cards, key=_rank)[0]
            idx[n] = [best[0], best[1][:260], len(cards)]
    with open(INDEX + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"built": time.time(), "cards": len(new), "read": read, "names": idx}, f)
    os.replace(INDEX + ".tmp", INDEX)
    return {"cards": len(new), "read": read, "names": len(idx), "seconds": round(time.time() - t0, 1)}


def _rebuild_in_background():
    lock = INDEX + ".building"
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 900:
            return
        open(lock, "w").close()
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        import subprocess   # only here: a rebuild is rare, and the hook path should not pay for the module
        subprocess.Popen([exe, os.path.abspath(__file__), "build", "--unlock"], cwd=HERE, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=(0x08000000 | 0x00000008 if os.name == "nt" else 0))   # CREATE_NO_WINDOW | DETACHED_PROCESS
    except Exception:
        pass


def _index():
    if _IDX[0] is None:
        try:
            with open(INDEX, encoding="utf-8") as f:
                d = json.load(f)
            if time.time() - d.get("built", 0) > REBUILD_AFTER:
                _rebuild_in_background()
            _IDX[0] = d.get("names") or {}
        except Exception:
            _rebuild_in_background()
            _IDX[0] = {}
    return _IDX[0]


def arm(session, slug):
    if LAW_RE.match(slug):
        return "shown"
    h = hashlib.sha256(("exact:%s:%s" % (session, slug)).encode("utf-8")).digest()
    return "held" if int.from_bytes(h[:4], "big") % HOLD_EVERY == 0 else "shown"


def line(data, text):
    """One line naming one card, or ''. Fail-open: any error returns ''."""
    try:
        session = str((data or {}).get("session_id") or "")
        if not session or os.environ.get("EXACT_NAME_OFF") == "1":
            return ""
        # what the agent NAMES, not what it writes: the command, the file a tool opens, or his prompt. The first live
        # hour fired on a file name inside the content of a Write (a docstring that mentioned it in passing).
        ti = (data or {}).get("tool_input")
        if isinstance(ti, dict):
            src = ti.get("command") or ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
            text = " ".join(map(str, src)) if isinstance(src, list) else str(src)
        elif (data or {}).get("prompt"):
            text = str(data["prompt"])
        if not text:
            return ""
        names = list(dict.fromkeys(n.lower() for n in NAME_RE.findall(text[:20000])))
        if not names:
            return ""
        idx = _index()
        hits = sorted(((idx[n][2], n, idx[n]) for n in names if n in idx), key=lambda h: h[0])
        if not hits:
            return ""
        os.makedirs(STATE_DIR, exist_ok=True)
        sp = os.path.join(STATE_DIR, re.sub(r"[^\w-]", "_", session)[:80] + ".json")
        try:
            spent = json.load(open(sp, encoding="utf-8"))
        except Exception:
            spent = {}
        for _k, n, (slug, desc, k) in hits:
            if slug in spent or slug in text:
                continue
            a = arm(session, slug)
            spent[slug] = time.time()
            with open(sp + ".tmp", "w", encoding="utf-8") as f:
                json.dump(spent, f)
            os.replace(sp + ".tmp", sp)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write("%.0f\t%s\t%s\t%s\t%s\n" % (time.time(), session, n, slug, a))
            if a == "held":
                return ""
            more = "; %d scar cards are about it" % k if k > 1 else ""
            return ("[exact-name] `%s` has a scar in the brain: %s (card %s%s; recall: python "
                    "~/.claude/dreaming/brain.py recall \"%s\")\n  (hint: exact-name trigger, first trial week - "
                    "unjudged; a card is a question, not a verdict)" % (n, desc.strip()[:240], slug, more, n))
        return ""
    except Exception:
        return ""


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        try:
            print(json.dumps(build()))
        finally:
            if "--unlock" in sys.argv:
                try:
                    os.remove(INDEX + ".building")
                except OSError:
                    pass
