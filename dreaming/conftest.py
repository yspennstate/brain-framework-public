# Script-style tests (module-level work ending in sys.exit) stop pytest's whole collection; they run one process
# each under tools/run_script_tests.py instead.
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_up = _HERE
while not os.path.exists(os.path.join(_up, "tools", "run_script_tests.py")) and os.path.dirname(_up) != _up:
    _up = os.path.dirname(_up)
sys.path.insert(0, os.path.join(_up, "tools"))
from run_script_tests import is_script_test  # noqa: E402

collect_ignore = [f for f in os.listdir(_HERE)
                  if f.startswith("test_") and f.endswith(".py") and is_script_test(os.path.join(_HERE, f))]
