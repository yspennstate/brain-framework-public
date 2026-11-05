#!/usr/bin/env python3
"""The task checklist from the command line. Same commands as the hook file it defers to.

    python checklist.py list | show <id> | working <id> "<line>" | note <id> "<text>" | done <id> "<result>" | seen <id|all>
"""
import os, runpy, sys
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_checklist_hook.py")
sys.argv[0] = HOOK
runpy.run_path(HOOK, run_name="__main__")
