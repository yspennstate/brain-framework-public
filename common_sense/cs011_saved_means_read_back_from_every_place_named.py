"""CS011 - Saved means read back from every place named.

Is it saved in every place the user named, read back from each one, not just sent? Read the item back from each named place by size, hash or listing, and say which hold it; a delete waits for every copy.
Why: An upload that returned without an error is not a copy until it is read back.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS011',
 'area': 'data',
 'title': 'Saved means read back from every place named',
 'ask': 'Is it saved in every place the user named, read back from each one, not just sent?',
 'rule': 'Read the item back from each named place by size, hash or listing, and say which hold it; a delete waits '
         'for every copy.',
 'why': 'An upload that returned without an error is not a copy until it is read back.',
 'events': 'prompt',
 'gate': ['saved', 'save', 'backed up', 'backup', 'uploading', 'pushed', 'stored'],
 'kw2': ['\\b(saved|backed\\s+up|stored|uploaded|pushed|uploading)\\b(?:[^.?!\\n]){0,40}?\\b(github|bucket|cloud|library|locally|both|every\\w*|drive|server|backup)\\b',
         '\\bin\\s+both\\b|\\b(all|every)\\s+(of\\s+)?(the\\s+)?(places|copies|locations)\\b|\\bonce\\s+(they|it)\\s+(are|is)\\s+saved\\b'],
 'kw': ['\\b(make\\s+sure|check\\s+again|you\\s+sure|verify)\\b',
        '\\b(data|files?|results?|pictures?|everything)\\b'],
 'need': 3,
 'pri': 3,
 'fire': ['p:make sure that we are uploading data to github and the cloud bucket and once they are saved in both '
          'they get deleted from this computer',
          'p:you sure that all of the data is saved locally as well?'],
 'quiet': ['p:save the picture as png', 'p:the file was saved']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
