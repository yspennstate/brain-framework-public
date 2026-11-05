"""CS002 - Never invent a constraint nobody gave.

The user says they never asked for this rule: where did it come from - a default, an earlier agent, your own guess? Find it, take it out, list every result it touched and re-run them; a modelling choice nobody asked for is a silent change to the question.
Why: An unrequested assumption buried in code changes every result built on it.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS002',
 'area': 'asks',
 'title': 'Never invent a constraint nobody gave',
 'ask': 'The user says they never asked for this rule: where did it come from - a default, an earlier agent, your '
        'own guess?',
 'rule': 'Find it, take it out, list every result it touched and re-run them; a modelling choice nobody asked for is '
         'a silent change to the question.',
 'why': 'An unrequested assumption buried in code changes every result built on it.',
 'events': 'prompt',
 'gate': ['never told',
          'never said',
          'never asked',
          'did not tell',
          "didn't tell",
          'did not ask',
          "didn't ask",
          'who told'],
 'kw2': ["never\\s+(told|said|asked)|did\\s*n[o']?t\\s+(tell|ask|say)|who\\s+told\\s+you"],
 'kw': ['\\b(that|this|to|for)\\b'],
 'need': 3,
 'pri': 3,
 'fire': ['p:i never told you to reset the counter every night, ever.',
          'p:who told you the queue is first in first out, i did not ask for that'],
 'quiet': ['p:tell me when the upload finishes', 'p:i never got the email, resend it']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
