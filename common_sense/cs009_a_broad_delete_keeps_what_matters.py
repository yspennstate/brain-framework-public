"""CS009 - A broad delete keeps what matters.

Before a broad delete: is every file here saved somewhere else, checked, and none of it the only copy? List what goes, prove each copy elsewhere (a repository, a bucket, a backup) by size or hash, and delete only that list.
Why: A recursive delete is the one command that cannot be taken back.
One of the common-sense hooks; the engine is cs_core.py beside this file.
"""
HOOK = {'id': 'CS009',
 'area': 'data',
 'title': 'A broad delete keeps what matters',
 'ask': 'Before a broad delete: is every file here saved somewhere else, checked, and none of it the only copy?',
 'rule': 'List what goes, prove each copy elsewhere (a repository, a bucket, a backup) by size or hash, and delete '
         'only that list.',
 'why': 'A recursive delete is the one command that cannot be taken back.',
 'events': 'tool',
 'gate': ['rm ', 'remove-item', 'del ', 'rmdir', 'rmtree'],
 'cmd': '\\brm\\s+-[a-z]*r|remove-item\\b[^\\n]*-recurse|\\bdel\\s+/s|\\brmdir\\s+/s|shutil\\.rmtree',
 'not': ['scratchpad|[\\\\/]tmp[\\\\/]|[\\\\/]temp[\\\\/]|__pycache__|node_modules|\\.pytest_cache|cs_selftest'],
 'need_tool': 3,
 'pri': 3,
 'fire': ['c:rm -rf /home/user/projects/archive/old_runs', 'c:Remove-Item -Recurse -Force D:\\data\\old_results'],
 'quiet': ['c:rm -f notes.txt', 'c:rm -rf /tmp/build_cache']}


if __name__ == "__main__":
    import cs_core
    cs_core.run(HOOK)
