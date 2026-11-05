"""Make the vendored proofswarm package importable from the framework tree.

In its own repo proofswarm sits at the root, so `from proofswarm import ...` just
works. Vendored here it lives at methods/proofswarm/, which is not on sys.path, and
a module-level import fails at COLLECTION time - pytest reports an error, not a
failure, and a collection error is easy to read as "no tests" rather than "tests
never ran". One conftest fixes every test in this package instead of asking each
one to repeat a sys.path insert inside its body.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
