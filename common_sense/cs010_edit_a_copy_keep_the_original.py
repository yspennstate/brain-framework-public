"""CS010 - Edit a copy, keep the original.

Is this the user's original document? Copy it and edit the copy, so their version stays untouched. Never edit the user's file in place: copy it (name_v2 or a work folder), change the copy, and give both paths.
Why: An in-place edit of someone's document leaves them nothing to compare against.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS010',
 'area': 'data',
 'title': 'Edit a copy, keep the original',
 'ask': "Is this the user's original document? Copy it and edit the copy, so their version stays untouched.",
 'rule': "Never edit the user's file in place: copy it (name_v2 or a work folder), change the copy, and give both "
         'paths.',
 'why': "An in-place edit of someone's document leaves them nothing to compare against.",
 'events': 'both',
 'gate': ['original', 'copy', 'sed -i', 'downloads', 'documents', '.tex', '.docx'],
 'path': '/(downloads|documents|desktop)/[^|]*\\.(tex|bib|docx?|md|txt)$',
 'cmd': '\\bsed\\s+-i\\b[^;&|\\n]{0,200}?(downloads|documents|\\.tex\\b|\\.docx?\\b)',
 'kw2': ["\\b(don'?t|do\\s+not|never)\\s+(modify|change|edit|touch|overwrite)\\s+(the\\s+|my\\s+)?original\\b",
         '\\bcopy\\s+it\\b(?:[^.?!\\n]){0,40}?\\b(then|and)\\s+(make|do|apply)\\b|\\b(use|keep)\\s+the\\s+original\\b'],
 'kw': ['\\b(latex|code|file|document|paper|draft|slides?|script)\\b|\\.tex\\b|\\\\(documentclass|begin\\{document\\})',
        '\\bcopy\\b'],
 'need': 3,
 'need_tool': 4,
 'pri': 3,
 'fire': ["p:use the original latex code, copy it, don't modify the original, copy it and then make these changes",
          'w:/home/user/Downloads/proposal.tex|\\documentclass{article}\n\\begin{document}Revised.\\end{document}',
          "c:sed -i 's/old/new/g' /home/user/Downloads/paper_v3.tex"],
 'quiet': ['w:/home/user/work/paper/paper.tex|\\documentclass{article}',
           'c:cp /home/user/Downloads/paper.tex /home/user/work/paper_v2.tex',
           'p:copy the picture to the downloads folder']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
