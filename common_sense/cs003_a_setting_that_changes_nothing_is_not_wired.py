"""CS003 - A setting that changes nothing is not wired.

A setting changed and the result did not move at all: does the code read that setting on this path? Grep for the setting, confirm it is read where the result is computed, and diff the two runs; the same result from different settings is a wiring bug until shown otherwise.
Why: Two identical outputs from two configurations usually mean one configuration was never read.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS003',
 'area': 'numbers',
 'title': 'A setting that changes nothing is not wired',
 'ask': 'A setting changed and the result did not move at all: does the code read that setting on this path?',
 'rule': 'Grep for the setting, confirm it is read where the result is computed, and diff the two runs; the same '
         'result from different settings is a wiring bug until shown otherwise.',
 'why': 'Two identical outputs from two configurations usually mean one configuration was never read.',
 'events': 'prompt',
 'gate': ['does nothing',
          'do nothing',
          'no effect',
          'no difference',
          'identical',
          'exactly the same',
          'same result',
          'same number',
          "didn't change",
          'did not change',
          'nothing changed'],
 'kw2': ["(does|do|did)\\s+nothing|no\\s+(effect|difference|change)|identical|exactly\\s+the\\s+same|same\\s+(result|number|output|curve)s?|(did|does)\\s*n[o']?t\\s+change|nothing\\s+changed"],
 'kw': ['\\b(settings?|parameters?|params?|threshold|rules?|knobs?|flags?|switch|sizes?|options?|config\\w*)\\b|%',
        'check\\s+again|\\bwhy\\b|\\bhow\\b'],
 'need': 4,
 'pri': 2,
 'fire': ['p:the threshold 0.5 and threshold 0.9 runs give exactly the same result, why',
          'p:I changed the batch size flag and it does nothing, check again'],
 'quiet': ['p:the two logos look identical, keep the first', 'p:do nothing until I approve']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
