"""source_check_0810_formulas.py - the second instrument for the 10 Aug concept replacements (28 Sep, claude-825b1305).

The first pass (source_check_0810.py) found 0 of 25 statements contained in their harvested artifacts. Read by hand, the
reason is the replacement itself: each harvested statement had lost its formulas ("then  is a divisor of ."), and the
10 Aug ingest wrote a replacement with the formulas filled in ("then |H| is a divisor of |G|"). So a replacement is
checked for what it claims to be:
  A. the harvest it replaced is the artifact's own statement (the artifact is content-addressed and pre-dates the batch);
  B. every word of the harvested statement is kept, in order, in the replacement (the replacement only adds);
  C. what it adds is set beside the formulas of the article's lead today ({\\displaystyle ...} of the Wikipedia
     plain-text intro, LaTeX lightly mapped to plain text), with the words each addition sits between, for reading.
A and B are computed; C is printed for a person to judge and the judgement is recorded by hand in the decision file.

    python source_check_0810_formulas.py CONCEPTS_JSON WIKI_CACHE OBJECTS_ROOT OUT_JSON OUT_MD
"""
import difflib
import io
import json
import os
import re
import sys
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s).strip()


TOK = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def toks(s):
    return TOK.findall(norm(s))


DISPLAY = re.compile(r"\{\\displaystyle (.*?)\}\s*\n", re.S)


def lead_formulas(extract):
    """The {\\displaystyle X} bodies of a plain-text intro, in order, and the intro with each formula block (the
    rendered junk lines before it and the LaTeX) replaced by [X]."""
    if not extract:
        return [], ""
    # Line by line (a first version did this with one backtracking regex and ran for minutes on a long intro): a line
    # whose text starts with {\displaystyle is a formula; the rendered pieces before it are indented two or more
    # spaces or blank, and are dropped; every other line is prose.
    forms, out = [], []
    for ln in extract.split("\n"):
        s = ln.strip()
        if s.startswith("{\\displaystyle"):
            f = s[len("{\\displaystyle"):]
            f = f[:-1] if f.endswith("}") else f
            forms.append(f.strip())
            out.append("[" + f.strip() + "]")
        elif not s or ln.startswith("  "):
            continue
        else:
            out.append(s)
    return forms, norm(" ".join(out))


LATEX = [(r"\\left|\\right|\\displaystyle|\\,|\\;|\\!", ""), (r"\\to\b|\\rightarrow\b", "->"), (r"\\leq?\b", "<="),
         (r"\\geq?\b", ">="), (r"\\neq\b", "!="), (r"\\in\b", " in "), (r"\\mathbb\s*\{?(\w)\}?", r"\1"),
         (r"\\mathbf\s*\{?(\w)\}?", r"\1"), (r"\\(?:mathrm|operatorname|text)\s*\{([^}]*)\}", r"\1"),
         (r"\\colon\b", ":"), (r"\\cdot\b|\\times\b", "*"), (r"\\ldots|\\dots|\\cdots", "..."),
         (r"\\\{|\\\}", ""), (r"\\([A-Za-z]+)", r"\1"), (r"[{}]", ""), (r"\s+", "")]


def plain(latex):
    s = latex
    for a, b in LATEX:
        s = re.sub(a, b, s)
    return s


def main():
    concepts, wiki, objroot, out, out_md = sys.argv[1:6]
    rows = json.load(io.open(concepts, encoding="utf-8"))["detail"]
    leads = json.load(io.open(wiki, encoding="utf-8"))
    res, md = [], ["# 10 Aug concept replacements: what each one added, beside the article's formulas today", ""]
    for x in rows:
        art = io.open(os.path.join(objroot, x["artifact_path"].replace("/", os.sep)), encoding="utf-8").read()
        m = re.search(r"```json\s*(\{.*?\})\s*```", art, re.S)
        rec = json.loads(m.group(1)) if m else {}
        art_stmt = rec.get("statement")
        a_ok = art_stmt is not None and norm(art_stmt) == norm(x["replaced_statement"])
        old, new = toks(x["replaced_statement"]), toks(x["statement"])
        sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        removed, added = [], []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("replace", "delete"):
                removed.append(" ".join(old[i1:i2]))
            if op in ("replace", "insert"):
                before = " ".join(new[max(0, j1 - 4):j1])
                after = " ".join(new[j2:j2 + 4])
                added.append({"text": " ".join(new[j1:j2]), "between": [before, after]})
        forms, lead_text = lead_formulas((leads.get(x["replaced_title"]) or {}).get("extract"))
        pforms = [plain(f) for f in forms]
        # the lead as one token list, each [LaTeX] replaced by its plain form: an addition is IN CONTEXT when the two
        # words before it, it, and the two words after it appear together in the lead. Equality alone would pass a
        # swap ("|G| is a divisor of |H|" uses two lead formulas); the context does not (28 Sep, found before use).
        lead_plain = re.sub(r"\[([^\]]*)\]", lambda m: " " + plain(m.group(1)) + " ", lead_text)
        lt = " ".join(toks(lead_plain))
        for a in added:
            p = re.sub(r"\s+", "", a["text"])
            a["equals_a_lead_formula"] = any(p == f for f in pforms if f)
            a["in_a_lead_formula"] = any(p and p in f for f in pforms if f)
            ctx = toks(" ".join(a["between"][0].split()[-2:])) + toks(a["text"]) + toks(" ".join(a["between"][1].split()[:2]))
            a["in_lead_context"] = bool(ctx) and (" " + " ".join(ctx) + " ") in (" " + lt + " ")
        r = {"id": x["id"], "title": x["replaced_title"], "domain": x["domain"],
             "A_replaced_statement_is_the_artifacts": a_ok, "artifact_source": (rec.get("provenance") or {}),
             "B_harvest_words_all_kept_in_order": not removed, "removed": removed, "added": added,
             "lead_formulas_today": forms}
        res.append(r)
        md += ["## %s (%s)" % (x["replaced_title"], x["id"][:18]), "",
               "- A, harvest = artifact: %s; B, every harvested word kept: %s%s" % (
                   a_ok, not removed, "" if not removed else " (removed: %s)" % removed),
               "- added: " + "; ".join("`%s` between \"%s\" and \"%s\"%s" % (
                   a["text"], a["between"][0], a["between"][1],
                   " (in the lead, same place)" if a["in_lead_context"] else " (NOT in the lead at that place: read)")
                   for a in added),
               "- lead formulas today: " + "; ".join("`%s`" % f for f in forms[:12]),
               "- statement: " + x["statement"], "- lead today: " + lead_text[:900], ""]
    for r in res:
        r["additions_needing_a_reader"] = [a["text"] for a in r["added"] if not a["in_lead_context"]]
    json.dump({"rows": res,
               "A_ok": sum(r["A_replaced_statement_is_the_artifacts"] for r in res),
               "B_ok": sum(r["B_harvest_words_all_kept_in_order"] for r in res),
               "additions": sum(len(r["added"]) for r in res),
               "additions_equal_to_a_lead_formula": sum(a["equals_a_lead_formula"] for r in res for a in r["added"]),
               "additions_in_lead_context": sum(a["in_lead_context"] for r in res for a in r["added"]),
               "rows_with_every_addition_in_context": sum(1 for r in res if not r["additions_needing_a_reader"])},
              io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    io.open(out_md, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(len(res), "rows | A", sum(r["A_replaced_statement_is_the_artifacts"] for r in res), "| B",
          sum(r["B_harvest_words_all_kept_in_order"] for r in res), "| additions",
          sum(len(r["added"]) for r in res), "equal to a lead formula",
          sum(a["equals_a_lead_formula"] for r in res for a in r["added"]), "in the lead's context",
          sum(a["in_lead_context"] for r in res for a in r["added"]), "| rows with every addition in context",
          sum(1 for r in res if not r["additions_needing_a_reader"]))


if __name__ == "__main__":
    main()
