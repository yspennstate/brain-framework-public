#!/usr/bin/env python3
"""Check that every name imported from a math_brain module is one that module defines.

The vendored math brain is updated a module at a time from more than one upstream line. On
29 Oct 2026 one such update replaced codex_runner.py and __init__.py with versions that no longer
defined 30 names other files still imported. Five test files then failed to import, pytest stopped
at collection, and the full suite was not run at all. This reads the source with ast, so it costs
well under a second and needs none of the suite's dependencies.

    python scripts/check_mathbrain_imports.py            # exit 1 and a list if any name is missing

It covers `from math_brain.X import a`, `from .X import a` inside the package, `from math_brain
import a`, and attribute use through `import math_brain.X as m`. A module that defines its own
__getattr__ is trusted for names listed in a `_LAZY_EXPORTS`-style table only when the name
appears in it; otherwise any name passes, since it cannot be checked statically.
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "mathbrain")


def _bound_names(stmts):
    names = set()
    for node in stmts:
        for sub in ast.walk(node) if isinstance(node, (ast.If, ast.Try)) else [node]:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(sub.name)
            elif isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for target in targets:
                    names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in sub.names)
    return names


def _string_constants(tree):
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def module_surface(path):
    """(names bound at top level, names a module-level __getattr__ may supply, or None for 'any')."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    names = _bound_names(tree.body)
    lazy = set()
    if "__getattr__" in names:
        # A lazy table is a dict of string tuples; take every string in the module as possibly
        # served. That is generous, but it only ever lets a name through, never blocks one.
        lazy = _string_constants(tree)
    return names, lazy


def audit(mathbrain_dir):
    pkg = os.path.join(mathbrain_dir, "src", "math_brain")
    modules = {}
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            key = "__init__" if fn == "__init__.py" else fn[:-3]
            modules[key] = module_surface(os.path.join(pkg, fn))
    submodules = set(modules) - {"__init__"}
    missing = {}

    def check(module, name, where):
        if name.startswith("__") and name.endswith("__"):
            return  # module attributes such as __file__ and __dict__
        if module not in modules:
            missing.setdefault((module, "(no such module)"), set()).add(where)
            return
        defined, lazy = modules[module]
        if name in defined or name in lazy or (module == "__init__" and name in submodules):
            return
        missing.setdefault((module, name), set()).add(where)

    for directory in (pkg, os.path.join(mathbrain_dir, "tests")):
        if not os.path.isdir(directory):
            continue
        for fn in sorted(os.listdir(directory)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(directory, fn)
            where = os.path.relpath(path, mathbrain_dir)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            alias = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if node.level == 1 and directory == pkg:
                        target = mod or "__init__"
                    elif node.level == 0 and mod == "math_brain":
                        target = "__init__"
                    elif node.level == 0 and mod.startswith("math_brain."):
                        target = mod.split(".", 1)[1]
                    else:
                        continue
                    for a in node.names:
                        if a.name == "*":
                            continue
                        if target == "__init__" and a.name in submodules:
                            alias[a.asname or a.name] = a.name
                        else:
                            check(target, a.name, where)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == "math_brain":
                            alias[a.asname or a.name] = "__init__"
                        elif a.name.startswith("math_brain.") and a.asname:
                            alias[a.asname] = a.name.split(".", 1)[1]
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id in alias):
                    check(alias[node.value.id], node.attr, where)
    return missing


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    target = argv[0] if argv else DEFAULT
    missing = audit(target)
    for (module, name), wheres in sorted(missing.items()):
        print("missing  math_brain.%s.%s  <- %s" % (module, name, ", ".join(sorted(wheres))))
    if missing:
        print("%d imported name(s) are not defined by their module" % len(missing))
        return 1
    print("clean: every name imported from math_brain resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
