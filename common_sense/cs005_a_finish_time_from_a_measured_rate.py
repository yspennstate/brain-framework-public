"""CS005 - A finish time from a measured rate.

The user asks how long: what rate has been measured so far, and how much is left? Answer with done of total, the rate over a stated window and the finish time on the user's clock; say what could change it. No estimate without a measurement behind it.
Why: A guessed finish time is the number most often wrong and most often acted on.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS005',
 'area': 'asks',
 'title': 'A finish time from a measured rate',
 'ask': 'The user asks how long: what rate has been measured so far, and how much is left?',
 'rule': "Answer with done of total, the rate over a stated window and the finish time on the user's clock; say what "
         'could change it. No estimate without a measurement behind it.',
 'why': 'A guessed finish time is the number most often wrong and most often acted on.',
 'events': 'prompt',
 'gate': ['how long', 'when will', 'eta', 'how much longer', 'how much time', 'when does it', 'when is it'],
 'kw2': ['\\bhow\\s+(much\\s+)?long(er)?\\b(?:[^.?!\\n]){0,50}\\b(run\\w*|take|takes|finish\\w*|left|remain\\w*|done|upload\\w*|train\\w*|build\\w*)\\b|\\bwhen\\s+(will|does|is)\\b(?:[^.?!\\n]){0,40}\\b(done|finish\\w*|ready|complete\\w*|end|land)\\b|\\beta\\b'],
 'kw': ['\\b(jobs?|queue|uploads?|batch|models?|downloads?|runs?|running|training|tests?|backfill|builds?)\\b'],
 'not': ['\\bhow\\s+long\\s+should\\b|\\bhow\\s+long\\s+(is|was|are)\\s+(the|a|each|this)\\s+(video|intro|chapter|paper|abstract|talk)\\b'],
 'need': 3,
 'pri': 2,
 'fire': ['p:how long do you think the training will be running for?',
          'p:when will the upload queue be done? how many jobs are left'],
 'quiet': ['p:how long should the intro take before the title?', 'p:when is the meeting tomorrow']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
