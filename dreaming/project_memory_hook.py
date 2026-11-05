"""Cwd-independent lifecycle-hook wrapper for project memory."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from project_memory.hook import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
