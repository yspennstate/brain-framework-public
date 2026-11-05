"""Cwd-independent executable wrapper for the project-memory package."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from project_memory.hourly_runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
