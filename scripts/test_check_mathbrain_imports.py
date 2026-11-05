"""Controls for check_mathbrain_imports.py: it must fail on a missing name and pass on a present one."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_mathbrain_imports as check  # noqa: E402


def _tree(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(tmp_path)


BASE = {
    "src/math_brain/__init__.py": "from .runner import run\n",
    "src/math_brain/runner.py": "def run():\n    pass\nLIMIT = 3\n",
}


def test_clean_tree_passes(tmp_path):
    root = _tree(tmp_path, dict(BASE, **{
        "tests/test_a.py": "from math_brain.runner import run, LIMIT\nimport math_brain.runner as r\nr.run\nr.__file__\n",
        "src/math_brain/user.py": "from .runner import run\nfrom . import runner\n",
    }))
    assert check.audit(root) == {}
    assert check.main([root]) == 0


def test_missing_from_import_is_caught(tmp_path):
    root = _tree(tmp_path, dict(BASE, **{"tests/test_a.py": "from math_brain.runner import run, gone\n"}))
    assert set(check.audit(root)) == {("runner", "gone")}
    assert check.main([root]) == 1


def test_missing_attribute_and_package_export_are_caught(tmp_path):
    root = _tree(tmp_path, dict(BASE, **{
        "tests/test_a.py": "import math_brain.runner as r\nimport math_brain\nr.vanished\nmath_brain.Nope\n",
        "src/math_brain/user.py": "from .runner import also_gone\n",
    }))
    assert set(check.audit(root)) == {("runner", "vanished"), ("__init__", "Nope"), ("runner", "also_gone")}


def test_lazy_table_names_pass_and_others_do_not(tmp_path):
    root = _tree(tmp_path, dict(BASE, **{
        "src/math_brain/__init__.py": (
            "_LAZY = {'runner': ('LIMIT',)}\n"
            "def __getattr__(name):\n    raise AttributeError(name)\n"
        ),
        "tests/test_a.py": "from math_brain import LIMIT, runner\nfrom math_brain import Missing\n",
    }))
    assert set(check.audit(root)) == {("__init__", "Missing")}


def test_unknown_module_is_caught(tmp_path):
    root = _tree(tmp_path, dict(BASE, **{"tests/test_a.py": "from math_brain.nowhere import x\n"}))
    assert set(check.audit(root)) == {("nowhere", "(no such module)")}
