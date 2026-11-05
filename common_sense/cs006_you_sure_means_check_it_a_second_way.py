"""CS006 - You sure? means check it a second way.

The user asks whether you are sure: have you checked it a second, independent way, or are you about to repeat the first answer? Re-derive it by a different path (another file, the raw record, a hand calculation); if it holds, show the second derivation; if not, say what was wrong.
Why: Repeating an answer with more confidence is not a check.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS006',
 'area': 'asks',
 'title': 'You sure? means check it a second way',
 'ask': 'The user asks whether you are sure: have you checked it a second, independent way, or are you about to '
        'repeat the first answer?',
 'rule': 'Re-derive it by a different path (another file, the raw record, a hand calculation); if it holds, show the '
         'second derivation; if not, say what was wrong.',
 'why': 'Repeating an answer with more confidence is not a check.',
 'events': 'prompt',
 'gate': ['sure', 'certain', 'wrong', 'check again', 'double check', 'double-check'],
 'kw2': ['\\b(are\\s+)?you\\s+(sure|certain)\\b|\\bare\\s+we\\s+sure\\b',
         '\\bi\\s+think\\s+(this|that|it|you)\\s+(is|are)\\s+(\\w+\\s+)?wrong\\b|\\bcheck\\s+(it\\s+|this\\s+|that\\s+)?again\\b|\\bwrong\\s+again\\b'],
 'kw': ['\\bwhy\\s+would\\b|\\bhow\\s+(is|can|could)\\b', '\\?', '^\\s*(are\\s+)?you\\s+sure\\s*\\??\\s*$'],
 'need': 4,
 'pri': 3,
 'fire': ['p:you sure? why would the totals drop so much? i think this is wrong, check again',
          'p:you sure ? i think you are wrong again'],
 'quiet': ['p:make sure the title is bright', 'p:are you sure you can finish part 6 tonight?']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
