"""CS001 - A doubted number is a bug report.

The user says a result makes no sense: what would have to be true for it, and is that true? Re-derive it a second, independent way and hunt the bug before defending it; a figure that contradicts what the user sees in the real system is wrong until shown otherwise.
Why: Confident wrong numbers cost more than a flagged doubt; the surprise is usually the bug.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS001',
 'area': 'numbers',
 'title': 'A doubted number is a bug report',
 'ask': 'The user says a result makes no sense: what would have to be true for it, and is that true?',
 'rule': 'Re-derive it a second, independent way and hunt the bug before defending it; a figure that contradicts '
         'what the user sees in the real system is wrong until shown otherwise.',
 'why': 'Confident wrong numbers cost more than a flagged doubt; the surprise is usually the bug.',
 'events': 'prompt',
 'gate': ['sense', 'credible', "don't see how", 'why would', 'how is it possible', 'how did'],
 'kw2': ["\\b(makes?|making)\\s+(no|zero)\\s+sen[cs]e|does\\s*n[o']?t\\s+make\\s+(any\\s+)?sen[cs]e|not\\s+credible|don'?t\\s+see\\s+how|why\\s+would|how\\s+is\\s+it\\s+possible|how\\s+did\\b"],
 'kw': ['\\d+(\\.\\d+)?\\s*(k\\b|m\\b|%|x\\b|ms\\b)|\\$\\s*\\d',
        '\\b(lose|lost|loss|profit\\w*|returns?|faster|slower|speed\\w*|latency|accuracy|score|multiple|money|cost)\\b'],
 'need': 3,
 'pri': 3,
 'fallback': True,
 'fire': ['p:how did the cache make recall 40x faster? that makes no sense',
          'p:why would the error rate drop to 0.1% overnight, that does not make sense'],
 'quiet': ['p:make the chart more colourful', 'p:the ending of the story does not make sense to me']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
