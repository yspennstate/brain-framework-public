"""Run a script with some modules preloaded from given files, so its own imports get the copies under test.

    python run_with.py name=path [name=path ...] -- SCRIPT.py [args ...]
"""
import importlib.util
import os
import runpy
import sys

args = sys.argv[1:]
cut = args.index("--")
pre, rest = args[:cut], args[cut + 1:]
sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
for item in pre:
    name, path = item.split("=", 1)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    print("[run_with] %s from %s" % (name, mod.__file__))
sys.argv = rest
runpy.run_path(rest[0], run_name="__main__")
