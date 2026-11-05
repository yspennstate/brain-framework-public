"""CS008 - Change only what was named.

Did the change touch only what the user named, or also the things next to it? Diff the change against the request; revert every edit outside the thing named, and ask before widening the scope.
Why: Unasked edits next to the asked one are the ones nobody reviews.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS008',
 'area': 'asks',
 'title': 'Change only what was named',
 'ask': 'Did the change touch only what the user named, or also the things next to it?',
 'rule': 'Diff the change against the request; revert every edit outside the thing named, and ask before widening '
         'the scope.',
 'why': 'Unasked edits next to the asked one are the ones nobody reviews.',
 'events': 'prompt',
 'gate': ['only change',
          'only fix',
          'only update',
          'only edit',
          'only touch',
          'only modify',
          'did not ask',
          "didn't ask",
          'anything else'],
 'kw2': ['\\bonly\\s+(change|fix|update|edit|touch|modify)\\s+(the\\s+|that\\s+|this\\s+)?\\w+',
         "\\bi\\s+(did\\s+not|didn'?t|never)\\s+ask\\s+(you\\s+)?(to|for)\\s+(make\\s+)?(changes?|touch|edit|modify|that)\\b|\\b(don'?t|do\\s+not)\\s+(change|touch|edit|modify)\\s+anything\\s+else\\b"],
 'kw': ['\\brevert\\w*\\b|\\bback\\b|\\bfix\\b'],
 'not': ['\\bsmall\\b'],
 'need': 3,
 'pri': 3,
 'fire': ['p:no, i did not ask you to make changes like that to the parser, revert it, only change the lexer',
          "p:only fix the title card, don't change anything else"],
 'quiet': ['p:only change the colours if it is small', 'p:the only change in the new version is the font']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
