#!/usr/bin/env python3
"""voice_guard_hook.py - PreToolUse gate for public-facing prose (owner order 2026-09-09).

The owner, 2026-06-10: papers, READMEs, repos and anything published must read as if a human
wrote it - "headline" is an instant tell, no bold-fests, no emoji, state a correction once,
never write a note about sounding human. On 2026-09-09, on his personal website, he added a
second family: self-introspection about the artifact itself ("Every paper on this site will
get...", "the cards below track...", "click Abstract to read...") - prose that narrates the
page instead of saying the thing. The rule: anything that reads as machine-written comes out; the brain's
voice node fires early and is enforced by a hook, so the check does not depend on
anyone remembering it.

The voice_human reflex already existed but was ranked behind other reflexes and capped out
of the fire list on the very session that produced the tells. A rule that must always hold
is a spec for machinery (the ps1_guard precedent), so this hook reads the prose an Edit or
Write is about to put into a prose file and REFUSES the write when it carries a tell, naming
the tell and the line. The agent fixes the sentence and writes again. Nothing is blocked in
code, data, logs or private notes: only prose files (.html .md .tex .txt .rst .bib .markdown
and README-like names), and never paths under the brain's own memory/dreaming/audit trees,
where the notes are for us and quote his words verbatim.

Bypass: put the token  voice-ok  anywhere in the file (an HTML comment, a LaTeX comment)
for a file that legitimately quotes a tell (this hook's own docs, the brain memory).

Fail-open by construction: any internal error -> exit 0, no output. Cost: one regex pass
over the new text. Selftest: python voice_guard_hook.py --selftest
"""
import json
import os
import re
import sys

PROSE_EXT = (".html", ".htm", ".md", ".markdown", ".tex", ".txt", ".rst", ".bib")
SKIP_DIRS = (
    "\\.claude\\", "/.claude/", "\\.codex\\", "/.codex/",
    "brain-repo", "\\audits\\", "/audits/",
    "\\scratchpad\\", "/scratchpad/", "\\Temp\\", "/Temp/", "node_modules",
)

# Hard tells: the words he has named, plus the AI register he keeps pointing at.
WORD_TELLS = [
    r"\bheadline\b", r"\bdelve\b", r"\bdelves\b", r"\bremarkabl[ey]\b", r"\bstrikingly\b",
    r"\bnotably\b", r"\bcrucially\b", r"\bcomprehensive\b", r"\bgame-changing\b",
    r"\bcutting-edge\b", r"\bseamless(?:ly)?\b", r"\brobust and scalable\b",
    r"\bit'?s worth noting\b", r"\bin today'?s\b", r"\bunlock(?:s|ing)?\b", r"\btestament to\b",
    r"\bat its core\b", r"\bin the realm of\b", r"\bnavigat(?:e|ing) the\b", r"\ba tapestry\b",
    r"\bsound(?:s|ing)? human\b", r"\bhuman-sounding\b", r"\bnot (?:like )?an? AI\b",
]
# Self-introspection: prose that narrates the artifact instead of saying the thing.
META_TELLS = [
    r"\bevery (?:paper|page|section|card|entry|item|video|post) (?:on|in|of) this (?:site|page|list|section)\b",
    r"\b(?:the )?cards? below (?:track|show|list|say)s?\b",
    r"\bclick (?:on )?[\"'“]?(?:abstract|summary|here|the button)\b",
    r"\bthis (?:page|site|section) (?:collects|contains|lists|tracks|shows|describes|is a)\b",
    r"\bbelow (?:you will|you'll) find\b",
    r"\b(?:cards?|entries|items) without an? \w+ (?:yet )?say so\b",
    r"\bwhen (?:a|the) \w+ is (?:recorded|added|ready),? the card becomes\b",
    r"\bin this (?:section|page),? (?:we|I) (?:will )?(?:describe|discuss|present|explain)\b",
]
# A paper never narrates its own history (rule of 2026-09-15).
# It states mathematics and attributions; a correction to a citation is silent, the corrected
# citation simply appears. The AI declaration is the ONE place the process may be named, so
# "prepared the first draft of this manuscript" stays legal - see the selftest's negative control.
PAPER_TELLS = [
    r"\bthe first version of (?:this|the present) (?:paper|manuscript|note|article)\b",
    r"\b(?:an|the) (?:earlier|previous|original) (?:record|draft|version) of (?:this|the present)\b",
    r"\bin (?:an|the) (?:earlier|previous) draft\b",
    r"\bwas corrected when\b",
    r"\bwe found no (?:later|other|subsequent)\b",
    r"\bre-?derived line by line\b",
    r"\bthe reviews? found\b",
    r"\breferee agents?\b",
    r"\b(?:verified|checked|re-?derived|confirmed|corrected)[^.]{0,60}before inclusion\b",
    r"\bwas obtained afterwards\b",
    r"\bthis (?:paper|manuscript) originally\b",
]
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2705\u274C\u2714\u2716\u2728\u2B06\u2B07]")


def _is_prose(path):
    p = path.replace("/", os.sep)
    low = p.lower()
    if any(s.lower() in low for s in SKIP_DIRS):
        return False
    base = os.path.basename(low)
    if base.startswith("readme"):
        return True
    return low.endswith(PROSE_EXT)


def _strip_code(text, path):
    """Drop script/style/pre blocks and HTML comments so code inside a page is not judged."""
    if path.lower().endswith((".html", ".htm")):
        text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<pre\b.*?</pre>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    if path.lower().endswith((".md", ".markdown")):
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return text


def find_tells(text, path=""):
    """Return a list of (kind, match, line_no)."""
    hits = []
    body = _strip_code(text, path)
    lines = body.splitlines()
    for i, line in enumerate(lines, 1):
        for pat in WORD_TELLS:
            m = re.search(pat, line, re.I)
            if m:
                hits.append(("word", m.group(0), i))
        for pat in META_TELLS:
            m = re.search(pat, line, re.I)
            if m:
                hits.append(("meta", m.group(0), i))
        for pat in PAPER_TELLS:
            m = re.search(pat, line, re.I)
            if m:
                hits.append(("paper-history", m.group(0), i))
        if EMOJI.search(line):
            hits.append(("emoji", EMOJI.search(line).group(0), i))
    # bold-fest: share of paragraphs (blank-line separated) that open with **bold**
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paras) >= 8:
        bold_open = sum(1 for p in paras if p.startswith("**") or p.startswith("<b>") or p.startswith("<p><b>"))
        if bold_open / len(paras) > 0.35:
            hits.append(("bold-fest", "%d of %d paragraphs open in bold" % (bold_open, len(paras)), 0))
    return hits


def _new_text(data):
    ti = data.get("tool_input", {}) or {}
    name = data.get("tool_name", "")
    if name == "Write":
        return ti.get("content", "") or ""
    if name == "Edit":
        return ti.get("new_string", "") or ""
    if name == "MultiEdit":
        return "\n".join((e.get("new_string", "") or "") for e in (ti.get("edits") or []))
    return ""


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        ti = data.get("tool_input", {}) or {}
        path = ti.get("file_path", "") or ""
        if not path or not _is_prose(path):
            return
        text = _new_text(data)
        if not text or "voice-ok" in text:
            return
        # a file that already carries the bypass token stays bypassed on edits
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    if "voice-ok" in fh.read(200000):
                        return
        except Exception:
            pass
        hits = find_tells(text, path)
        if not hits:
            return
        shown = "; ".join("%s '%s'%s" % (k, m, (" (line %d)" % n) if n else "") for k, m, n in hits[:8])
        reason = (
            "[voice_guard] REFUSED: this prose carries what the owner calls an AI tell - %s. "
            "Rule (owner 2026-06-10 and 2026-09-09, memory research_writing_human_voice_2026_06_10): "
            "public text reads as if a person wrote it. Say the thing; do not narrate the page "
            "('every paper on this site...', 'the cards below...', 'click Abstract'); no 'headline', "
            "no 'delve/remarkably/notably', no emoji, no bold-fests, no note about sounding human. "
            "Rewrite the sentence and write again. If the file must quote a tell on purpose, put "
            "the token voice-ok in it." % shown
        )
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "permissionDecision": "deny",
                                      "permissionDecisionReason": reason}}
        print(json.dumps(out))
    except Exception:
        return


def _selftest():
    ok = True
    good = "A trained network is a stack of matrices. The Marchenko-Pastur law says which part is noise.\n\nThe proof ends in a threshold."
    bad1 = "Every paper on this site will get a short recorded talk. Headline: it works remarkably well."
    bad2 = "Cards without a video yet say so. When a talk is recorded the card becomes the player."
    cases = [
        ("C:/x/site/index.html", good, 0),
        ("C:/x/site/index.html", bad1, 3),
        ("C:/x/site/videos.html", bad2, 2),
        ("C:/x/site/site.js", bad1, None),          # not prose -> not judged
        ("C:/Users/me/.claude/projects/m/memory/note.md", bad1, None),  # brain tree -> skipped
        ("C:/x/README", bad1, 3),
        ("C:/x/site/page.html", "<script>var s='Every paper on this site';</script><p>Plain text.</p>", 0),
        # the 2026-09-15 paper rule, positive controls
        ("C:/x/paper/p.tex", "The first version of this paper presented $W$ as new, and the "
                             "attribution was corrected when \\cite{j} was obtained.", 2),
        ("C:/x/paper/p.tex", "That paper does not mention Problem 11, and we found no later "
                             "paper that draws the conclusion.", 1),
        ("C:/x/paper/p.tex", "The reviews found the following errors.", 1),
        ("C:/x/paper/p.tex", "An earlier record of this computation exists.", 1),
        # negative controls: ordinary attribution, and the AI declaration, which is the one
        # place the process may be named and must keep passing
        ("C:/x/paper/p.tex", "The operator $W$ was introduced by Jamilov, who determined its "
                             "two fixed points.", 0),
        ("C:/x/paper/p.tex", "The software was written and executed by Claude Code and Codex, "
                             "which also prepared the first draft of this manuscript. The author "
                             "reviewed the code, the numbers, and the text.", 0),
    ]
    for path, text, expect in cases:
        if expect is None:
            got = None if not _is_prose(path) else len(find_tells(text, path))
        else:
            got = len(find_tells(text, path))
        res = (got == expect)
        ok = ok and res
        print("%-55s expected %s got %s %s" % (path, expect, got, "OK" if res else "FAIL"))
    # end-to-end through main(): a deny must be printed for a bad Write
    import io
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "C:/x/site/index.html", "content": bad1}})
    sys.stdin = io.StringIO(payload)
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        main()
    finally:
        sys.stdout = old
    denied = '"deny"' in buf.getvalue()
    print("end-to-end deny on bad Write:", "OK" if denied else "FAIL"); ok = ok and denied
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "C:/x/site/index.html", "content": good}})
    sys.stdin = io.StringIO(payload)
    buf = io.StringIO(); sys.stdout = buf
    try:
        main()
    finally:
        sys.stdout = old
    silent = buf.getvalue().strip() == ""
    print("end-to-end silent on good Write:", "OK" if silent else "FAIL"); ok = ok and silent
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()
