"""CS004 - Run it on all the data there is.

The study ran on a slice of the data: why that slice, and what changes when it runs on everything? Count what exists against what was used, run on all of it, and if a part is missing say what and where it would come from instead of shrinking the question.
Why: A result on a convenient slice answers a smaller question than the one asked.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS004',
 'area': 'numbers',
 'title': 'Run it on all the data there is',
 'ask': 'The study ran on a slice of the data: why that slice, and what changes when it runs on everything?',
 'rule': 'Count what exists against what was used, run on all of it, and if a part is missing say what and where it '
         'would come from instead of shrinking the question.',
 'why': 'A result on a convenient slice answers a smaller question than the one asked.',
 'events': 'prompt',
 'gate': ['all of the data',
          'all the data',
          'little data',
          'whole history',
          'whole data',
          'full history',
          'only use'],
 'kw2': ['(on|use|using|with|run\\w*\\s+on)\\s+all\\s+(of\\s+)?the\\s+data|so\\s+little\\s+data|(whole|full|entire)\\s+(data\\s*set|history|record)|why\\s+did\\s+\\w+\\s+only\\s+use'],
 'kw': ['\\b(data|years?|samples?|history|period|dates?|records?|rows?)\\b', '\\bwhy\\b|what\\s+happened|i\\s+want'],
 'need': 4,
 'pri': 2,
 'fire': ['p:why do we have so little data, i want to try this on all of the data',
          'p:why did the study only use 2019-2024, run it on the whole history'],
 'quiet': ['p:save all of the data to the backup drive', 'p:delete all the other temp files']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
