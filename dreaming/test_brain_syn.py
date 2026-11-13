"""Controls for brain_syn.py, the query-side synonyms brain.py recall reads.

 S1 a seen-as phrase appends the notes' word, and the question's own words stay first
 S2 a question that already holds the word gets nothing added; a question with no phrase is returned unchanged
 S3 the gated rows fire only with both parts (a title abbreviation AND a split word; a bad year)
 S4 numbers and units written apart are rejoined into the indexed spelling
 S5 spelling_fold gives the other spelling of each split word once, none already present
 S6 BRAIN_RECALL_SYN=0 turns expand off
 S7 brain.py's two calls (expand(q, raw), spelling_fold(raw)) exist with those signatures

    python test_brain_syn.py      (under a second; ALL PASS at the end)
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("BRAIN_RECALL_SYN", None)
import brain_syn as S

results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


q = "why is the index too old"
check(S.expand(q) == q + " stale", "S1 'too old' appends 'stale' after the question's words")
check(S.expand("find the temp file", "find the temp file").endswith(" tmp"), "S1 'temp file' appends 'tmp'")
check(S.expand("we ran out of space again") .endswith(" disk"), "S1 'out of space' appends 'disk'")
check(S.expand("the index is stale and too old") == "the index is stale and too old",
      "S2 a question that already says 'stale' gets nothing added")
plain = "how does spreading activation weigh edges"
check(S.expand(plain) == plain, "S2 a question with no phrase is unchanged")
check(S.expand("") == "", "S2 an empty query is unchanged")
check("abbreviation" in S.expand("why does the splitter cut the sentence at St. Louis"),
      "S3 a title abbreviation with a split word adds 'abbreviation'")
check("abbreviation" not in S.expand("ask Dr. Smith about the proof"),
      "S3 a title abbreviation alone adds nothing")
check(S.expand("why did it do badly in 2019").endswith(" weak") and "weak" not in S.expand("badly written note"),
      "S3 'badly in <year>' adds 'weak'; a bare 'badly' does not")
check(S.expand("why did the file grow to 36 MB").endswith(" 36mb"), "S4 '36 MB' rejoined to '36mb'")
check(S.expand("a 1.5 GB cache").endswith(" 1_5gb"), "S4 '1.5 GB' rejoined to '1_5gb'")
check(S.expand("a 40% drop in hits").endswith(" 40pct"), "S4 '40%' rejoined to '40pct'")
check(S.expand("waited 29 minutes").endswith(" 29min"), "S4 '29 minutes' rejoined to '29min'")
check(S.spelling_fold("the neighbours of a catalogue") == ["neighbors", "catalog"],
      "S5 neighbours -> neighbors, catalogue -> catalog")
check(S.spelling_fold("colour and color") == [], "S5 nothing added when both spellings are present")
check(S.spelling_fold("the neighbours neighbours") == ["neighbors"], "S5 each other spelling once")
os.environ["BRAIN_RECALL_SYN"] = "0"
check(S.expand(q) == q, "S6 BRAIN_RECALL_SYN=0 leaves the query alone")
os.environ.pop("BRAIN_RECALL_SYN", None)
check(list(inspect.signature(S.expand).parameters) == ["query", "raw"]
      and list(inspect.signature(S.spelling_fold).parameters) == ["raw"],
      "S7 expand(query, raw) and spelling_fold(raw), as brain.py calls them")

print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
