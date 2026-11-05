"""Smoke test: run the orchestrator with --mock and verify the dispatch loop works."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mock_orchestrator_runs(tmp_path: Path):
    env = os.environ.copy()
    env.pop("PROOFSWARM_BACKEND", None)
    env["PROOFSWARM_INFERENCE_BACKEND"] = "local"
    env["PROOFSWARM_STORAGE_BACKEND"] = "local"
    env["PROOFSWARM_LOCAL_TICKETS"] = str(tmp_path / "tickets")
    env["PROOFSWARM_TRACE_DIR"] = str(tmp_path / "traces")
    res = subprocess.run(
        [sys.executable, "-m", "proofswarm.orchestrator", "--mock", "--rounds", "5"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120, env=env,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert res.returncode == 0, f"orchestrator exited {res.returncode}\nstdout: {res.stdout}\nstderr: {res.stderr}"
    # The summary JSON should appear in stdout.
    assert "rounds" in res.stdout, "summary missing"
    print(res.stdout)


def test_numerical_sweep_rational():
    """The rational-atomic check should find no counterexamples and produce a stable cert hash."""
    sys.path.insert(0, str(ROOT))
    from proofswarm.tools.numerical import run_numerical_search
    from proofswarm.schemas import NumericalSearchRequest
    req = NumericalSearchRequest(
        family="rational_atomic",
        parameter_grid={"denominators": [5, 6, 7, 8, 9, 10, 11]},
        invariance_tolerance=0.0,
    )
    res = run_numerical_search(req)
    # gcd(q,6)=1 ⟹ joint invariant: q ∈ {5, 7, 11}.  Others (6, 8, 9, 10) should NOT be joint invariant.
    # The sweep should not flag any counterexamples to "joint invariant ⟹ gcd(q,6)=1".
    assert res.counterexamples_found == [], f"unexpected ce: {res.counterexamples_found}"
    assert len(res.certificate_sha256) == 64
