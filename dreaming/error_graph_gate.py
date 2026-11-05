"""Run the error graph's hook only while error_graph_runtime/ENABLED exists.

Codex on Windows runs each hook's commandWindows line from ~/.codex/hooks.json, and its four error-graph lines call this
file in place of error_graph/hook.py. With the flag absent it exits 0 at once and reads nothing, so an event costs one
interpreter start instead of the engine import and its per-session SQLite writes. With the flag present it runs
error_graph/hook.py exactly as before, with the same arguments and the same stdin. The Claude side honours the same
file through the dispatcher's enabled_if key, so one flag turns the graph on or off for both runtimes.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(HERE, "error_graph")
FLAG = os.environ.get("ERROR_GRAPH_GATE_FLAG") or os.path.join(HERE, "error_graph_runtime", "ENABLED")

if not os.path.exists(FLAG):
    sys.exit(0)
sys.argv = [os.path.join(GRAPH, "hook.py")] + sys.argv[1:]
sys.path.insert(0, GRAPH)
runpy.run_path(os.path.join(GRAPH, "hook.py"), run_name="__main__")
