# Script-style tests (module-level work ending in sys.exit) stop pytest's whole collection; they run one process
# each under tools/run_script_tests.py instead.
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_up = _HERE
while not os.path.exists(os.path.join(_up, "tools", "run_script_tests.py")) and os.path.dirname(_up) != _up:
    _up = os.path.dirname(_up)
sys.path.insert(0, os.path.join(_up, "tools"))
try:
    from run_script_tests import is_script_test  # noqa: E402
except ImportError:          # an installed brain ships without the repo's tools/ folder: the same test, inline
    def is_script_test(path):
        """Ends the process itself and has no unittest or pytest test function: a module-level sys.exit / raise
        SystemExit, or one inside a module-level if/try/for/with block (not in a def, a class or the __main__ block),
        which runs at import."""
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return False
        if "unittest.TestCase" in src or "\ndef test_" in src:
            return False
        if any(l.startswith(("sys.exit(", "raise SystemExit")) for l in src.splitlines()):
            return True
        import ast
        try:
            stack = list(ast.parse(src).body)
        except SyntaxError:
            return False
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test):
                continue
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "exit" \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "sys":
                return True
            if isinstance(n, ast.Raise) and n.exc is not None:
                e = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
                if isinstance(e, ast.Name) and e.id == "SystemExit":
                    return True
            stack.extend(ast.iter_child_nodes(n))
        return False

collect_ignore = [f for f in os.listdir(_HERE)
                  if f.startswith("test_") and f.endswith(".py") and is_script_test(os.path.join(_HERE, f))]
