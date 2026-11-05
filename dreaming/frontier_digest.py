"""frontier_digest.py - what the math brain holds on the open problem a research session is working, delivered to it.

Why (measured 2026-09-24, memory the_math_brain_barely_reaches_the_research_agents_2026_09_24): the math hook fires on
his prompts only, so the quadratic-form session received 4 math cards in 28 hours and the FEI session 1 in 21, while
other agents added dozens of results on the same problems to the store. This module gives a session on a program the
program's frontier once, then, hourly, only what was added since.

Version 2 (24 Sep, after T16; the T16 file is frontier_digest.py.bak_t16). T16's QFORM arms built on a rank-three
"theorem" that an agent had typed the evening before and that was found false the next afternoon; the digest showed it
exactly like Rogozin's published theorem. Three changes:
  - every item says where it comes from: "literature YEAR" (the card carries a citation or a literature/arXiv source),
    "textbook" (an encyclopedia source), "his paper", or "house, unrefereed" with "proof on file" or "no proof on file";
  - supersession is read from the store's SUPERSEDES edges as well as node metadata, as of the digest's moment (the store
    records corrections as edges: the false rank-three card was withdrawn by an edge at 09-23 14:58 his clock, and the
    first version kept showing it after that);
  - a short section after the results lists what was withdrawn as wrong (with the correction's own reason) and
    refutations, then the cards that record a failed route or a refuted citation ("Failed route ...", "REFUTED CITATION
    ... active scar", "Known false ...") - the routes a session should not walk again. A claim filed from a memory is an
    ordinary house result and stays in the list.
The store is resolved when a function is called, so assigning STORE after import now takes effect (the first version bound
it as a default argument at import).

Standard library only (the hooks run under `python -s`); the store is opened read-only.
  python frontier_digest.py --digest FEI [--at "2026-09-23T18:00:00"]   print the digest as of a moment
  python frontier_digest.py --delta QFORM --since "..." [--at "..."]    print what was added in between
"""
import json
import os
import re
import sqlite3
import sys
import time

STORE = os.environ.get("MATHBRAIN_STORE", os.path.expanduser(r"~/math-brain-build/local/population/brain.sqlite3"))
NOTE = "python ~/.claude/dreaming/math_note.py"

# A program: how a session's prompts name it, and how its claims name it. Add a program here when he opens one.
PROGRAMS = {
    "FEI": {"title": "the Fourier entropy-influence conjecture (FEI)",
            "session": re.compile(r"\bFEI\b|entropy[- ]influence", re.I),
            "claim": re.compile(r"\bFEI\b|entropy[- ]influence", re.I)},
    "QFORM": {"title": "the bounded-density conjecture for quadratic forms (QFORM)",
              "session": re.compile(r"quadratic[- ]form|bounded[- ]density|\bQFORM\b", re.I),
              "claim": re.compile(r"quadratic[- ]form|bounded[- ]density|\bQFORM\b", re.I)},
}
DIGEST_MAX_ITEMS = 18
# the harness hands the model at most 10,000 characters of one hook command's output; a delivery naming several
# programs is re-rendered with fewer results each (never fewer than the last step) until it fits under this
SECTION_MAX_CHARS = 9000
ITEM_STEPS = (18, 14, 11, 8)
CORRECTIONS_MAX = 5      # withdrawn-as-wrong and refuted lines after the results
MEMORY_SCARS_MAX = 3     # claims filed from the brain's memories on the program
LINE_CHARS = 240
REASON_CHARS = 110

LIT_PREFIXES = ("literature:", "arxiv:", "doi:")
TEXTBOOK_PREFIXES = ("wikipedia:", "proofwiki:")
OWNER_PREFIXES = ("owner_results",)
# a supersedes edge is a correction, not a routine revision, when its reason says so
CORRECTION_RX = re.compile(r"correct|false|wrong|counterexample|mis-?stated|refut|error|withdr|not (?:true|hold)", re.I)
ARXIV_TITLE_RX = re.compile(r"\barXiv:(\d{2})(\d{2})\.\d{4,5}", re.I)
# cards whose title records a failed route or a refuted citation ("Failed route (FPP chi = 1/3): ...", "REFUTED CITATION
# (our id E0061, active scar): ...", "Known false (ReLU networks): ...")
FAILED_RX = re.compile(r"^\s*(?:failed route|refuted|known false|dead end|not viable|withdrawn)\b|active scar", re.I)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) if isinstance(t, (int, float)) else str(t)[:19]


def programs_of(texts):
    """The programs a session's own words name."""
    joined = "\n".join(texts or [])
    return [p for p, d in PROGRAMS.items() if d["session"].search(joined)]


def _utc_iso(local_iso):
    """Store timestamps are UTC ISO strings; convert a local wall-clock ISO string for comparison."""
    t = time.mktime(time.strptime(local_iso[:19], "%Y-%m-%dT%H:%M:%S"))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))


def _year(text):
    m = re.search(r"\((1[89]\d\d|20\d\d)\)", text or "") or re.search(r"\b(1[89]\d\d|20\d\d)\b", text or "")
    return m.group(1) if m else ""


def provenance(meta, refs, title, body):
    """Where a claim comes from, in a few words. Literature: a citation in the card, a literature/arXiv/DOI source, or a
    title quoting an arXiv theorem. House: everything an agent typed, which no referee has read; a house theorem is a
    claim, not a published result. 'proof on file' when the store holds a proof text or the card carries one."""
    refs = [str(r or "").lower() for r in (refs or [])]
    cit = str(meta.get("citation") or "")
    arx = ARXIV_TITLE_RX.search(title or "")
    if cit or any(r.startswith(LIT_PREFIXES) for r in refs) or arx:
        y = _year(cit) or (("20" + arx.group(1)) if arx else "")
        return "literature" + (" " + y if y else "")
    if any(r.startswith(TEXTBOOK_PREFIXES) for r in refs):
        return "textbook"
    if any(r.startswith(OWNER_PREFIXES) for r in refs):
        return "his paper"
    proof = bool(str(meta.get("proof_text") or "").strip()) or bool(re.search(r"\bProof\b[.:]", body or ""))
    return "house, unrefereed" + (", proof on file" if proof else ", no proof")


def _connect(store):
    return sqlite3.connect("file:%s?mode=ro" % str(store).replace(os.sep, "/"), uri=True)


def _program_rows(con, at_u):
    return con.execute(
        "SELECT id, title, body, metadata_json, created_at FROM nodes WHERE kind='claim' AND created_at <= ? "
        "AND (title LIKE '%FEI%' OR body LIKE '%FEI%' OR body LIKE '%entropy%influence%' OR title LIKE '%entropy%influence%' "
        "OR body LIKE '%quadratic%form%' OR title LIKE '%quadratic%form%' OR body LIKE '%bounded%density%' "
        "OR title LIKE '%bounded%density%' OR body LIKE '%QFORM%' OR title LIKE '%QFORM%')", (at_u,)).fetchall()


def _refs(con, ids):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = "SELECT claim_id, source_ref FROM claim_provenance WHERE claim_id IN (%s)" % ",".join("?" * len(chunk))
        try:
            for cid, ref in con.execute(q, chunk):
                out.setdefault(cid, []).append(ref or "")
        except sqlite3.Error:
            return out
    return out


def _edges(con, kind, at_u):
    try:
        return con.execute("SELECT source_id, target_id, rationale, created_at FROM edges WHERE kind = ? AND "
                           "substr(created_at, 1, 19) <= ?", (kind, at_u)).fetchall()
    except sqlite3.Error:
        return []


def _titles(con, ids):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for nid, title, body in con.execute("SELECT id, title, body FROM nodes WHERE id IN (%s)" % ",".join("?" * len(chunk)),
                                            chunk):
            out[nid] = (title or "").strip() or (body or "").strip()[:LINE_CHARS]
    return out


def scan(program, at=None, since=None, store=None):
    """Everything the digest shows for a program as of `at` (local time): (claims, notes). claims: the current claims,
    newest first, created after `since` if given, minus every claim superseded by metadata or by a SUPERSEDES edge that
    exists at `at`. notes: withdrawn-as-wrong and refuted claims (with the correction's reason) and failed-route cards,
    newest first, each with a 'when' stamp (UTC) - in delta mode only those that appeared after `since`."""
    store = store or STORE
    rx = PROGRAMS[program]["claim"]
    at_u = _utc_iso(at) if at else "9999"
    since_u = _utc_iso(since) if since else ""
    con = _connect(store)
    try:
        rows = _program_rows(con, at_u)
        mine = {}
        superseded = set()
        for nid, title, body, mj, created in rows:
            text = (title or "") + " " + (body or "")[:4000]
            if not rx.search(text):
                continue
            try:
                m = json.loads(mj or "{}")
            except Exception:
                m = {}
            sup = m.get("supersedes")
            if isinstance(sup, str) and sup:
                superseded.add(sup)
            elif isinstance(sup, list):
                superseded.update(x for x in sup if isinstance(x, str))
            mine[nid] = (title, body, m, created)
        refs = _refs(con, mine.keys())
        notes = []
        # SUPERSEDES edges: source supersedes target. Every one removes its target; the ones whose reason says the
        # target was wrong are listed, so the session learns what not to rely on, not just that a card vanished.
        sup_edges = _edges(con, "supersedes", at_u)
        withdrawn = {}
        for src, dst, why, when in sup_edges:
            superseded.add(dst)
            if dst in mine and CORRECTION_RX.search(why or ""):
                withdrawn[dst] = (src, why or "", str(when or "")[:19])
        ref_edges = [e for e in _edges(con, "refutes", at_u) if e[1] in mine or e[0] in mine]
        names = _titles(con, {w[0] for w in withdrawn.values()} | {e[0] for e in ref_edges} | {e[1] for e in ref_edges})
        for dst, (src, why, when) in withdrawn.items():
            notes.append({"when": when, "kind": "withdrawn", "id": dst, "title": (mine[dst][0] or mine[dst][1] or "").strip(),
                          "by": src, "by_title": names.get(src, ""), "why": why})
        for src, dst, why, when in ref_edges:
            notes.append({"when": str(when or "")[:19], "kind": "refuted", "id": dst, "title": names.get(dst, ""),
                          "by": src, "by_title": names.get(src, ""), "why": why or ""})
        out = []
        for nid, (title, body, m, created) in mine.items():
            r = refs.get(nid, [])
            if FAILED_RX.search(title or "") and nid not in superseded:
                # a card that records a failed route or a refuted citation is a warning, not a result: listed apart
                mem = [x for x in r if str(x).startswith("memory:")]
                notes.append({"when": created[:19], "kind": "failed", "id": nid, "title": (title or body or "").strip(),
                              "by": mem[0][len("memory:"):] if mem else "", "by_title": "", "why": ""})
                continue
            out.append({"id": nid, "created": created[:19], "title": (title or "").strip(), "body": (body or "").strip(),
                        "kind": str(m.get("claim_kind") or ""), "truth": str(m.get("truth_status") or ""),
                        "memory_status": str(m.get("memory_status") or ""),
                        "prov": provenance(m, r, title or "", body or "")})
    finally:
        con.close()
    out = [c for c in out if c["id"] not in superseded and (not since_u or c["created"] > since_u)]
    out.sort(key=lambda c: c["created"], reverse=True)
    if since_u:
        notes = [n for n in notes if n["when"] > since_u]
    notes.sort(key=lambda n: n["when"], reverse=True)
    return out, notes


def claims(program, at=None, since=None, store=None):
    """The program's current claims, newest first: created at or before `at` (local time), after `since` if given,
    minus any claim a later claim supersedes (by metadata or by edge, as of `at`). Each: id, created (UTC), title, body,
    kind, truth, memory_status, prov."""
    return scan(program, at=at, since=since, store=store)[0]


def first_line(c, n=LINE_CHARS):
    t = c["title"] or c.get("body", "")
    t = re.sub(r"\s+", " ", t)
    if len(t) > n:
        t = t[:n - 3].rstrip() + "..."
    return t


def local_stamp(utc_iso):
    """'MM-DD HH:MM' on his clock for a UTC store stamp (the store writes UTC; his screen is local time)."""
    try:
        import calendar
        t = calendar.timegm(time.strptime(utc_iso[:19], "%Y-%m-%dT%H:%M:%S"))
        return time.strftime("%m-%d %H:%M", time.localtime(t))
    except Exception:
        return utc_iso[5:16].replace("T", " ")


def _short(text, n):
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


def render(program, items, since=None, total=None, notes=None):
    d = PROGRAMS[program]
    if since:
        head = "[frontier] %s: %d result(s) added to the math brain since %s by the agents working it, newest first." % (
            d["title"], len(items), _iso(since)[5:16].replace("T", " "))
    else:
        head = ("[frontier] %s: the %d newest of the %d results the math brain holds on it, newest first - what the agents "
                "working it have established, including routes that failed. Read any one in full with %s <id>. These are "
                "leads to check, not verdicts." % (d["title"], len(items), total if total is not None else len(items), NOTE))
    head += " Literature = a cited paper; house = an agent's result no referee has read - check it before building on it."
    lines = [head]
    for c in items:
        tag = "; ".join(x for x in (", ".join(y for y in (c["kind"], c["memory_status"] or c["truth"]) if y),
                                    c.get("prov", "")) if x)
        lines.append("  %s %s%s (%s)" % (local_stamp(c["created"]), first_line(c), " [%s]" % tag if tag else "", c["id"]))
    notes = notes or []
    fixes = [n for n in notes if n["kind"] in ("withdrawn", "refuted")][:CORRECTIONS_MAX]
    scars = [n for n in notes if n["kind"] == "failed"][:MEMORY_SCARS_MAX]
    if fixes:
        lines.append(" Withdrawn as wrong or refuted - do not build on these:")
        for n in fixes:
            verb = "withdrawn" if n["kind"] == "withdrawn" else "refuted"
            lines.append("  %s %s (%s) - %s: %s [see %s]" % (local_stamp(n["when"]), _short(n["title"], 90), n["id"], verb,
                                                          _short(n["why"], REASON_CHARS) or "no reason recorded", n["by"]))
    if scars:
        lines.append(" Failed routes and refuted citations on record:")
        for n in scars:
            src = ("; memory %s" % n["by"]) if n["by"] else ""
            lines.append("  %s %s (%s%s)" % (local_stamp(n["when"]), _short(n["title"], 160), n["id"], src))
    return "\n".join(lines) + "\n"


MESH_DB = os.path.join(os.path.expanduser("~"), ".agents", "coordination", "agent_mesh.sqlite3")


def mesh_task(session8, runtime="claude"):
    """The task line the session published on the agent mesh ('QFORM conjecture: ...'). His own words often name the
    problem only as 'the conjecture' or 'that problem' (measured: the quadratic-form session's prompts never say
    'quadratic form'), while the agent's mesh task names it. Read-only; '' on any failure."""
    if not session8:
        return ""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % MESH_DB, uri=True, timeout=0.5)
        try:
            r = con.execute("SELECT task FROM agents WHERE agent_id = ?", ("%s-%s" % (runtime, session8),)).fetchone()
        finally:
            con.close()
        return (r[0] or "") if r else ""
    except Exception:
        return ""


def section(own_texts, st, now=None, every_s=3600, session8=None):
    """For a hook: (text, state) - the frontier of every program the session's own words or its mesh task name, once,
    then hourly only what was added since. st is the hook's per-session state dict (a copy is returned with the
    'frontier' key updated). Silent for a session that names no program, and silent on any error."""
    now = time.time() if now is None else now
    st = dict(st or {})
    fr = dict(st.get("frontier") or {})
    try:
        jobs = []
        at_iso = _iso(now)
        for p in programs_of(list(own_texts or []) + [mesh_task(session8)]):
            last = fr.get(p)
            if last is None:
                jobs.append((p, None))
                fr[p] = now
            elif now - float(last) >= every_s:
                jobs.append((p, _iso(float(last))))
                fr[p] = now
        scans = {p: scan(p, at=at_iso, since=since) for p, since in jobs}
        text = ""
        for k in ITEM_STEPS:
            parts = []
            for p, since in jobs:
                cs, notes = scans[p]
                if since is None:
                    parts.append(render(p, cs[:k], total=len(cs), notes=notes))
                elif cs or notes:
                    parts.append(render(p, cs[:k], since=since, notes=notes))
            text = "".join(parts)
            if len(text) <= SECTION_MAX_CHARS:
                break
    except Exception:
        return "", st
    st["frontier"] = fr
    return text, st


def digest(program, at=None, max_items=DIGEST_MAX_ITEMS):
    cs, notes = scan(program, at=at)
    return render(program, cs[:max_items], total=len(cs), notes=notes)


def delta(program, since, at=None, max_items=DIGEST_MAX_ITEMS):
    cs, notes = scan(program, at=at, since=since)
    return render(program, cs[:max_items], since=since, notes=notes) if (cs or notes) else ""


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    at = a[a.index("--at") + 1] if "--at" in a else None
    if "--digest" in a:
        sys.stdout.write(digest(a[a.index("--digest") + 1], at=at))
    elif "--delta" in a:
        sys.stdout.write(delta(a[a.index("--delta") + 1], a[a.index("--since") + 1], at=at) or "(nothing new)\n")
    else:
        print(__doc__)
