"""Run a membrane test suite against a given live_membrane.py: preload it as the module 'live_membrane' so the suite's
own `import live_membrane` gets the copy under test, then run the suite as __main__.

    python run_suite.py PATH_TO_live_membrane.py PATH_TO_test.py [test args...]
"""
import importlib.util
import os
import runpy
import sys

memb, test = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
spec = importlib.util.spec_from_file_location("live_membrane", memb)
mod = importlib.util.module_from_spec(spec)
sys.modules["live_membrane"] = mod
spec.loader.exec_module(mod)
print("[run_suite] live_membrane from", mod.__file__)
sys.argv = [test] + sys.argv[3:]
runpy.run_path(test, run_name="__main__")
