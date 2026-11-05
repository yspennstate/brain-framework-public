#!/usr/bin/env python3
"""Privacy-safe categorical liveness counters for Codex warning wrappers."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
METRICS_MODULE = HERE / "error_graph" / "metrics.py"
METRICS_DB = HERE / "error_graph_runtime" / "hook_health.sqlite3"
_SURFACES = {
    "UserPromptSubmit": "prompt",
    "PreToolUse": "pre_action",
    "PostToolUse": "post_action",
    "Stop": "report",
}


def record(event: str, mode: str, status: str, started: float) -> None:
    """Record only allow-listed categories and latency; never prompt, output, or identity."""
    try:
        spec = importlib.util.spec_from_file_location("_codex_warning_metrics", METRICS_MODULE)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        elapsed_ms = max(0.0, (time.perf_counter() - float(started)) * 1000.0)
        module.MetricsStore(str(METRICS_DB)).record(
            _SURFACES.get(str(event), "unknown"), "codex", mode, status, elapsed_ms)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(0)
