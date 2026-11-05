"""error_graph's Claude hook under a name of its own (30 Oct 2026, claude-a52370f8).

hook_dispatch_list.json used to launch error_graph/hook.py through an inline `python -c` runpy with the folder
hex-encoded. The dispatcher only runs a named .py file in-process, so error_graph always paid a fresh interpreter:
about 1 s a tool call at load 5 (6-65 ms of that is the hook's own work, hook_health.sqlite3) and 1.5-5 s with
spikes to 17 s at load ~300, where it was the only hook timing out. This file sets the same two variables and calls
the same entry point; it imports error_graph as a package so its engine/events/store modules cannot collide with
another hook's flat `store` (project_memory has one).
"""
import os
import sys

os.environ["ERROR_GRAPH_MODE"] = "shadow"
os.environ["ERROR_GRAPH_RUNTIME_HINT"] = "claude"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from error_graph import hook  # noqa: E402

raise SystemExit(hook.hook_main())
