"""CS007 - A fix that made it worse is rolled back first.

Did the last fix make it worse? Then put the last good version back first, and analyse after. Restore the last good version (git, the backup, the previous file), confirm it works again, then study what the fix broke; never stack a new fix on a bad one.
Why: Fixes stacked on a bad fix bury the original fault.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS007',
 'area': 'asks',
 'title': 'A fix that made it worse is rolled back first',
 'ask': 'Did the last fix make it worse? Then put the last good version back first, and analyse after.',
 'rule': 'Restore the last good version (git, the backup, the previous file), confirm it works again, then study '
         'what the fix broke; never stack a new fix on a bad one.',
 'why': 'Fixes stacked on a bad fix bury the original fault.',
 'events': 'prompt',
 'gate': ['previous fix',
          'last fix',
          'go back',
          'bring back',
          'roll back',
          'rollback',
          'roll it back',
          'made it worse',
          'how it was'],
 'kw2': ['\\b(bring|put|set|go|roll|change)\\s+(it\\s+|everything\\s+|them\\s+)?back\\b(?:[^.?!\\n]){0,40}?\\b(previous|how\\s+it\\s+was|before|last\\s+(good|working))\\b|\\bgo\\s+back\\s+to\\s+how\\s+it\\s+was\\b|\\broll\\s*back\\b',
         '\\b(made|make|makes|making)\\s+(it|things|everything)\\s+worse\\b|\\b(mistake|wrong|broke\\w*)\\b(?:[^.?!\\n]){0,30}?\\b(previous|last)\\s+fix\\b|\\b(previous|last)\\s+fix\\b(?:[^.?!\\n]){0,30}?\\b(mistake|wrong|broke\\w*|worse)\\b'],
 'kw': ['\\b(restart|completely\\s+wrong|undo|analysis)\\b'],
 'not': ['\\b(logos?|titles?|banners?|colou?rs?)\\b'],
 'need': 3,
 'pri': 3,
 'fire': ['p:i think you made a big mistake in the previous fix, go back to how it was, restart',
          'p:your fix made it worse, roll it back to the last working version first'],
 'quiet': ['p:bring back the old logo, the new one is worse', 'p:go back to chapter 2 and check the tables']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
