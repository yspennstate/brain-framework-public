import io
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

import math_brain.lean as lean_module
from math_brain.lean import (
    LocalFormalizationFidelityAuthority,
    LocalLeanEvidenceAuthority,
    StatementFidelityReview,
    bind_formal_statement,
    check_lean_file,
    claim_statement_hash,
)
from math_brain.models import (
    Claim,
    ClaimKind,
    EvidenceAuthorityMode,
    EvidenceAuthorityRole,
    EvidenceKind,
    RecordStatus,
)
from math_brain.store import ResearchStore
from math_brain.verification import (
    EvidenceKeyPolicy,
    EvidenceVerifier,
    derive_and_record_status,
    recompute_current_status,
    register_authenticated_evidence,
)

HASH = "sha256:" + "a" * 64
TOOLCHAIN = "leanprover/lean4:v4.30.0\n"
LAKEFILE = """name = "MathBrain"
version = "0.1.0"
defaultTargets = ["MathBrain"]

[[lean_lib]]
name = "MathBrain"
"""
GOOD_SOURCE = """import MathBrain.Basic
namespace MathBrain
theorem ok : True := by trivial
#print axioms MathBrain.ok
end MathBrain
"""
GOOD_VERSION = b"Lean (version 4.30.0, synthetic-test, Release)\n"


def project(tmp_path: Path, source_text: str = GOOD_SOURCE) -> tuple[Path, Path]:
    root = tmp_path / "formal"
    root.mkdir()
    (root / "lean-toolchain").write_text(TOOLCHAIN, encoding="utf-8")
    (root / "lakefile.toml").write_text(LAKEFILE, encoding="utf-8")
    (root / "MathBrain").mkdir()
    (root / "MathBrain" / "Basic.lean").write_text(
        "namespace MathBrain\ntheorem basic : True := by trivial\nend MathBrain\n",
        encoding="utf-8",
    )
    source = root / "MathBrain.lean"
    source.write_text(source_text, encoding="utf-8")
    return root, source


def compute_state(tmp_path: Path, *, mode: str = "owner_away_open") -> Path:
    path = tmp_path / "compute_state.json"
    path.write_text(
        json.dumps(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "status": "ok",
                "mode": mode,
                "owner_active": False,
                "owner_away": True,
                "hung_windows": 0,
                "governor_suspended": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def native_executable(tmp_path: Path) -> tuple[Path, str]:
    binary_dir = tmp_path / ".elan" / "toolchains" / "leanprover--lean4---v4.30.0" / "bin"
    binary_dir.mkdir(parents=True)
    path = binary_dir / ("lean.exe" if os.name == "nt" else "lean")
    raw = b"MZ\x00\x00synthetic-local-lake-binary"
    path.write_bytes(raw)
    if os.name != "nt":
        path.chmod(0o700)
    return path.resolve(), "sha256:" + sha256(raw).hexdigest()


class ScriptedProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        polls_before_exit: int = 0,
        on_poll: Callable[[int], None] | None = None,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.stdin = None
        self._final_returncode = returncode
        self.returncode: int | None = None
        self.polls_before_exit = polls_before_exit
        self.on_poll = on_poll
        self.poll_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.pid = 999_999

    def poll(self) -> int | None:
        self.poll_calls += 1
        if self.on_poll is not None:
            self.on_poll(self.poll_calls)
        if self.poll_calls > self.polls_before_exit:
            self.returncode = self._final_returncode
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic", timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


class SequenceFactory:
    def __init__(self, processes: list[ScriptedProcess]) -> None:
        self.processes = processes
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, command: tuple[str, ...], **kwargs: object) -> ScriptedProcess:
        self.calls.append((tuple(command), kwargs))
        return self.processes.pop(0)


def clean_run(
    tmp_path: Path,
    *,
    source_text: str = GOOD_SOURCE,
    version: ScriptedProcess | None = None,
    build: ScriptedProcess | None = None,
    final_build: ScriptedProcess | None = None,
    expected_hash: bool = True,
    max_output_bytes: int = lean_module.MAX_OUTPUT_BYTES,
    required_declarations: tuple[str, ...] = (),
):
    root, source = project(tmp_path, source_text)
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)
    factory = SequenceFactory(
        [
            version or ScriptedProcess(stdout=GOOD_VERSION),
            build or ScriptedProcess(),
            final_build or ScriptedProcess(),
        ]
    )
    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest if expected_hash else None,
        required_declarations=required_declarations,
        compute_state_path=state,
        max_output_bytes=max_output_bytes,
        _process_factory=factory,
    )
    return result, factory, root, source, state, executable, digest


@pytest.mark.parametrize(
    "token",
    [
        "sorry",
        "admit",
        "sorryAx",
        "axiom",
        "constant",
        "opaque",
        "unsafe",
        "run_tac",
        "native_decide",
        "bv_decide",
        "run_cmd",
        "include_str",
        "trace",
        "#eval",
        "@[implemented_by foo]",
    ],
)
def test_lean_rejects_trust_escape_before_invocation(tmp_path: Path, token: str) -> None:
    root, source = project(tmp_path, f"theorem bad : True := by {token}\n")
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)

    def forbidden_factory(*args: object, **kwargs: object) -> ScriptedProcess:
        raise AssertionError("trust-escaping source must not be invoked")

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        compute_state_path=state,
        _process_factory=forbidden_factory,
    )
    assert not result.passed
    assert "forbidden trust token" in result.reason
    assert not result.trust_policy_enforced


def test_lean_rejects_source_outside_project(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = tmp_path / "Outside.lean"
    source.write_text("theorem ok : True := by trivial", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        check_lean_file(source, project_root=project_root)


@pytest.mark.parametrize(
    "command",
    [(), ("lean",), ("lake", "env", "lean"), ("lake", "build", "--keep-going")],
)
def test_lean_accepts_only_fixed_direct_compile_plan(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    root, source = project(tmp_path)
    with pytest.raises(ValueError, match="fixed command"):
        check_lean_file(source, project_root=root, command=command)


def test_clean_run_records_exact_snapshot_and_toolchain(tmp_path: Path) -> None:
    result, factory, root, source, _, executable, digest = clean_run(tmp_path)
    assert result.passed, result.reason
    assert result.toolchain_version_verified
    assert result.toolchain_version.startswith("Lean (version 4.30.0")
    assert result.source_hash == "sha256:" + sha256(source.read_bytes()).hexdigest()
    assert result.source_tree_hash.startswith("sha256:")
    assert result.executable_hash == digest
    assert result.direct_toolchain_verified
    assert not result.network_used
    assert result.source_snapshot_enforced
    assert result.trust_policy_enforced
    assert result.cleanup_verified
    assert result.compute_policy_enforced
    assert not result.authority_eligible  # injected process factories are never authorities
    assert factory.calls[0][0] == (str(executable), "--version")
    assert factory.calls[1][0][0:5] == (
        str(executable),
        "--trust=0",
        "-j1",
        "-M1024",
        "-R",
    )
    assert factory.calls[2][0][0:5] == factory.calls[1][0][0:5]
    snapshot = Path(factory.calls[1][1]["cwd"])
    assert factory.calls[1][1]["env"]["LEAN_PATH"] == str(snapshot)
    assert snapshot != root
    assert not snapshot.exists()
    assert result.digest().startswith("sha256:")
    assert result._runtime_seal_is_valid()


def test_named_axiom_report_is_required_for_authority_eligibility(tmp_path: Path) -> None:
    declaration = "MathBrain.ok"
    report = f"'{declaration}' does not depend on any axioms\n".encode()
    result, _, *_ = clean_run(
        tmp_path,
        final_build=ScriptedProcess(stdout=report),
        required_declarations=(declaration,),
    )
    assert result.passed
    assert result.checked_declarations == (declaration,)
    assert result.axiom_report_verified
    assert not result.authority_eligible  # process factory is still injected


def test_missing_canonical_axiom_command_fails_before_launch(tmp_path: Path) -> None:
    root, source = project(
        tmp_path,
        "import MathBrain.Basic\ntheorem ok : True := by trivial\n",
    )
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> ScriptedProcess:
        raise AssertionError("missing declaration binding must fail before launch")

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        required_declarations=("MathBrain.ok",),
        compute_state_path=state,
        _process_factory=forbidden,
    )
    assert not result.passed
    assert "lacks one canonical #print axioms" in result.reason


def test_duplicate_no_axiom_output_cannot_spoof_authority(tmp_path: Path) -> None:
    declaration = "MathBrain.ok"
    line = f"'{declaration}' does not depend on any axioms\n".encode()
    result, _, *_ = clean_run(
        tmp_path,
        final_build=ScriptedProcess(stdout=line + line),
        required_declarations=(declaration,),
    )
    assert result.passed
    assert not result.axiom_report_verified
    assert not result.authority_eligible


def test_positive_axiom_report_cannot_coexist_with_authority(tmp_path: Path) -> None:
    declaration = "MathBrain.ok"
    output = (
        f"'{declaration}' does not depend on any axioms\n"
        f"'{declaration}' depends on axioms: [propext]\n"
    ).encode()
    result, _, *_ = clean_run(
        tmp_path,
        final_build=ScriptedProcess(stdout=output),
        required_declarations=(declaration,),
    )
    assert result.passed
    assert not result.axiom_report_verified
    assert not result.authority_eligible


def test_build_reads_copied_source_not_mutated_original(tmp_path: Path) -> None:
    root, source = project(tmp_path)
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)
    original_source_hash = "sha256:" + sha256(source.read_bytes()).hexdigest()
    observed: list[str] = []
    calls = 0

    def factory(command: tuple[str, ...], **kwargs: object) -> ScriptedProcess:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ScriptedProcess(stdout=GOOD_VERSION)
        if calls == 2:
            source.write_text("axiom swapped : False\n", encoding="utf-8")
            observed.append((Path(kwargs["cwd"]) / "MathBrain.lean").read_text(encoding="utf-8"))
        return ScriptedProcess()

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        compute_state_path=state,
        _process_factory=factory,
    )
    assert result.passed
    assert observed == [GOOD_SOURCE]
    assert result.source_hash == original_source_hash


def test_nonlocal_import_is_discovery_only_and_never_invoked(tmp_path: Path) -> None:
    root, source = project(tmp_path, "import Mathlib\ntheorem ok : True := by trivial\n")
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> ScriptedProcess:
        raise AssertionError("nonlocal imports must fail before launch")

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        compute_state_path=state,
        _process_factory=forbidden,
    )
    assert not result.passed
    assert "imports nonlocal module" in result.reason


def test_executable_pin_mismatch_fails_before_process_creation(tmp_path: Path) -> None:
    root, source = project(tmp_path)
    state = compute_state(tmp_path)
    executable, _ = native_executable(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> ScriptedProcess:
        raise AssertionError("mismatched executable must not launch")

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash="sha256:" + "0" * 64,
        compute_state_path=state,
        _process_factory=forbidden,
    )
    assert not result.passed
    assert "pinned digest" in result.reason
    assert not result.expected_executable_hash_matched


def test_unpinned_diagnostic_can_pass_but_has_no_authority(tmp_path: Path) -> None:
    result, _, *_ = clean_run(tmp_path, expected_hash=False)
    assert result.passed
    assert not result.expected_executable_hash_matched
    assert not result.authority_eligible


def test_closed_compute_gate_prevents_launch(tmp_path: Path) -> None:
    root, source = project(tmp_path)
    state = compute_state(tmp_path, mode="throttle")
    executable, digest = native_executable(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> ScriptedProcess:
        raise AssertionError("closed compute gate must prevent launch")

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        compute_state_path=state,
        _process_factory=forbidden,
    )
    assert not result.passed
    assert "compute policy is closed" in result.reason


def test_output_budget_terminates_hanging_process_tree(tmp_path: Path) -> None:
    version = ScriptedProcess(stdout=GOOD_VERSION)
    build = ScriptedProcess(stdout=b"x" * 4_096, polls_before_exit=10_000)
    result, _, *_ = clean_run(tmp_path, version=version, build=build, max_output_bytes=1_024)
    assert not result.passed
    assert result.output_overflowed
    assert result.forced_termination
    assert build.terminate_calls + build.kill_calls >= 1
    assert "output exceeded" in result.reason


def test_compute_gate_is_rechecked_and_terminates_running_build(tmp_path: Path) -> None:
    root, source = project(tmp_path)
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)

    def close_gate(poll: int) -> None:
        if poll == 3:
            state.write_text(
                json.dumps(
                    {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "status": "ok",
                        "mode": "throttle",
                        "owner_active": True,
                        "owner_away": False,
                        "hung_windows": 0,
                        "governor_suspended": False,
                    }
                ),
                encoding="utf-8",
            )

    build = ScriptedProcess(polls_before_exit=10_000, on_poll=close_gate)
    factory = SequenceFactory([ScriptedProcess(stdout=GOOD_VERSION), build])
    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        compute_state_path=state,
        _process_factory=factory,
    )
    assert not result.passed
    assert result.forced_termination
    assert "yielded to compute policy" in result.reason
    assert build.terminate_calls + build.kill_calls >= 1


def test_version_must_match_pinned_toolchain(tmp_path: Path) -> None:
    result, factory, *_ = clean_run(
        tmp_path, version=ScriptedProcess(stdout=b"Lean (version 4.29.0)\n")
    )
    assert not result.passed
    assert not result.toolchain_version_verified
    assert "does not match" in result.reason
    assert len(factory.calls) == 1


def test_diagnostics_scrub_local_snapshot_paths(tmp_path: Path) -> None:
    root, source = project(tmp_path)
    state = compute_state(tmp_path)
    executable, digest = native_executable(tmp_path)
    calls = 0

    def factory(command: tuple[str, ...], **kwargs: object) -> ScriptedProcess:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ScriptedProcess(stdout=GOOD_VERSION)
        private_path = str(Path(kwargs["cwd"]) / "MathBrain.lean").lower()
        return ScriptedProcess(stdout=f"checked {private_path}\n".encode())

    result = check_lean_file(
        source,
        project_root=root,
        executable=executable,
        expected_executable_hash=digest,
        compute_state_path=state,
        _process_factory=factory,
    )
    assert result.passed
    assert "<local-path>" in result.stdout
    assert str(root) not in result.stdout
    assert str(executable.parent) not in result.stdout


def test_lake_manifest_cannot_add_dependencies_or_scripts(tmp_path: Path) -> None:
    root, source = project(tmp_path)
    (root / "lakefile.toml").write_text(LAKEFILE + "\n[script.evil]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported executable or dependency"):
        check_lean_file(source, project_root=root)


def test_result_invariants_reject_forged_success(tmp_path: Path) -> None:
    result, _, *_ = clean_run(tmp_path)
    with pytest.raises(ValueError, match="return code zero"):
        replace(result, returncode=1)


def sealed_eligible_result(tmp_path: Path, source_text: str = GOOD_SOURCE):
    declaration = "MathBrain.ok"
    report = f"'{declaration}' does not depend on any axioms\n".encode()
    result, _, *_ = clean_run(
        tmp_path,
        source_text=source_text,
        final_build=ScriptedProcess(stdout=report),
        required_declarations=(declaration,),
    )
    eligible = replace(
        result,
        executable_lock_enforced=True,
        tree_guard_enforced=True,
        hidden_window_enforced=True,
        below_normal_priority_enforced=True,
        standard_process_used=True,
    )
    eligible = replace(
        eligible,
        runtime_seal=lean_module._runtime_seal(eligible),
    )
    assert eligible.authority_eligible
    assert eligible._runtime_seal_is_valid()
    return eligible


def bound_fidelity_review(
    tmp_path: Path,
    claim: Claim,
    result: lean_module.LeanResult,
    **overrides: object,
) -> StatementFidelityReview:
    binding = bind_formal_statement(
        tmp_path / "formal",
        "MathBrain.lean",
        "MathBrain.ok",
        start_line=3,
        end_line=3,
    )
    values: dict[str, object] = {
        "claim_digest": claim.digest(),
        "prose_claim_hash": claim_statement_hash(claim),
        "lean_result_digest": result.digest(),
        "source_tree_hash": result.source_tree_hash,
        "statement_bindings": (binding,),
        "reviewer_key_id": "human-fidelity-v1",
        "quantifiers_match": True,
        "domains_match": True,
        "definitions_match": True,
        "assumptions_match": True,
        "conclusion_match": True,
        "boundary_cases_match": True,
        "no_extra_axioms": True,
        "notes": "Compared exact prose, declaration header, assumptions, and conclusion.",
    }
    values.update(overrides)
    return StatementFidelityReview(**values)  # type: ignore[arg-type]


def test_local_authority_signs_only_bound_sealed_lean_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = sealed_eligible_result(tmp_path)
    monkeypatch.setattr(lean_module, "check_lean_file", lambda *args, **kwargs: result)
    key = b"local-lean-test-authority-key-000000000000000000"
    authority = LocalLeanEvidenceAuthority("lean-local-v1", key)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    attempt = authority.verify_and_issue(
        claim,
        tmp_path / "formal" / "MathBrain.lean",
        required_declarations=("MathBrain.ok",),
    )
    assert attempt.issued
    assert attempt.evidence is not None
    assert attempt.attestation is not None
    assert attempt.receipt is not None
    assert attempt.evidence.artifact_hash == result.digest()
    assert attempt.attestation.result_digest == result.digest()
    assert attempt.attestation.request_digest != result.request_digest
    policy = EvidenceKeyPolicy(
        key,
        EvidenceAuthorityRole.AUTOMATED_VERIFIER,
        frozenset({EvidenceKind.LEAN_KERNEL}),
    )
    verifier = EvidenceVerifier({"lean-local-v1": policy})
    assert verifier.verify(
        claim,
        attempt.evidence,
        attempt.attestation,
        attempt.receipt,
    )


def test_local_authority_refuses_tampered_runtime_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = sealed_eligible_result(tmp_path)
    tampered = replace(result, reason="caller changed an otherwise eligible result")
    assert tampered.authority_eligible
    assert not tampered._runtime_seal_is_valid()
    monkeypatch.setattr(lean_module, "check_lean_file", lambda *args, **kwargs: tampered)
    authority = LocalLeanEvidenceAuthority(
        "lean-local-v1",
        b"local-lean-test-authority-key-000000000000000000",
    )
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    attempt = authority.verify_and_issue(
        claim,
        tmp_path / "formal" / "MathBrain.lean",
        required_declarations=("MathBrain.ok",),
    )
    assert not attempt.issued


def test_local_authority_refuses_declaration_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = sealed_eligible_result(tmp_path)
    monkeypatch.setattr(lean_module, "check_lean_file", lambda *args, **kwargs: result)
    authority = LocalLeanEvidenceAuthority(
        "lean-local-v1",
        b"local-lean-test-authority-key-000000000000000000",
    )
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    attempt = authority.verify_and_issue(
        claim,
        tmp_path / "formal" / "MathBrain.lean",
        required_declarations=("MathBrain.different",),
    )
    assert not attempt.issued


def test_creation_policy_never_requests_a_visible_standard_process() -> None:
    launch_configuration, hidden, below_normal = lean_module._creation_options(
        standard_process=True
    )
    assert hidden
    if os.name == "nt":
        flags = launch_configuration["creationflags"]
        assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert flags & getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        assert below_normal
        assert launch_configuration["startupinfo"].wShowWindow == 0
    else:
        assert launch_configuration == {"start_new_session": True}


def test_one_sealed_run_forms_the_lean_route_and_two_source_trees_do_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """account04/B11, B11-R79-CROSS-GENERATION-FORMAL-EVIDENCE-PROMOTES-20260929-01, through the two local
    authorities: the kernel and fidelity records issued for one sealed run promote the claim; a fidelity record
    issued on a revised source tree does not join the first run's kernel record."""
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    lean_key = b"local-lean-test-authority-key-000000000000000000"
    human_key = b"local-human-fidelity-key-000000000000000000000000"
    lean_authority = LocalLeanEvidenceAuthority("lean-local-v1", lean_key)
    human_authority = LocalFormalizationFidelityAuthority("human-fidelity-v1", human_key)
    verifier = EvidenceVerifier(
        {
            "lean-local-v1": EvidenceKeyPolicy(
                lean_key,
                EvidenceAuthorityRole.AUTOMATED_VERIFIER,
                frozenset({EvidenceKind.LEAN_KERNEL}),
            ),
            "human-fidelity-v1": EvidenceKeyPolicy(
                human_key,
                EvidenceAuthorityRole.HUMAN_REVIEWER,
                frozenset({EvidenceKind.FORMALIZATION_FIDELITY}),
            ),
        }
    )

    def sealed_run(folder: str, source_text: str):
        base = tmp_path / folder
        base.mkdir()
        result = sealed_eligible_result(base, source_text=source_text)
        monkeypatch.setattr(lean_module, "check_lean_file", lambda *args, **kwargs: result)
        path = base / "formal" / "MathBrain.lean"
        kernel = lean_authority.verify_and_issue(
            claim, path, required_declarations=("MathBrain.ok",)
        )
        fidelity = human_authority.review_and_issue(
            claim,
            result,
            bound_fidelity_review(base, claim, result),
            path,
            project_root=base / "formal",
        )
        assert kernel.issued and fidelity.issued
        return result, kernel, fidelity

    result_a, kernel_a, fidelity_a = sealed_run("a", GOOD_SOURCE)
    result_b, _kernel_b, fidelity_b = sealed_run("b", GOOD_SOURCE + "-- revised after the kernel run\n")
    assert result_a.source_tree_hash != result_b.source_tree_hash

    def promote(name: str, kernel, fidelity):
        with ResearchStore(tmp_path / name) as store:
            store.add_claim(claim)
            for attempt in (kernel, fidelity):
                register_authenticated_evidence(
                    store, attempt.evidence, attempt.attestation, attempt.receipt, verifier
                )
            applied = derive_and_record_status(
                store,
                claim.id,
                target=RecordStatus.VERIFIED,
                verifier=verifier,
                reviewer="lean-route-test",
            )
            return applied, recompute_current_status(store, claim.id, verifier)

    same, same_status = promote("same.sqlite3", kernel_a, fidelity_a)
    split, split_status = promote("split.sqlite3", kernel_a, fidelity_b)
    assert same.decision.allowed
    assert same_status is RecordStatus.VERIFIED
    assert not split.decision.allowed
    assert "lineage mismatch" in split.decision.rationale
    assert split_status is RecordStatus.UNVERIFIED


def test_fidelity_requires_every_dimension(tmp_path: Path) -> None:
    result = sealed_eligible_result(tmp_path)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    review = bound_fidelity_review(tmp_path, claim, result, boundary_cases_match=False)
    assert not review.passed


def test_fidelity_authority_signs_only_exact_human_review(tmp_path: Path) -> None:
    result = sealed_eligible_result(tmp_path)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    review = bound_fidelity_review(tmp_path, claim, result)
    key = b"local-human-fidelity-key-000000000000000000000000"
    authority = LocalFormalizationFidelityAuthority("human-fidelity-v1", key)
    attempt = authority.review_and_issue(
        claim,
        result,
        review,
        tmp_path / "formal" / "MathBrain.lean",
        project_root=tmp_path / "formal",
    )
    assert attempt.issued
    assert attempt.evidence is not None
    assert attempt.attestation is not None
    assert attempt.receipt is not None
    assert attempt.evidence.kind is EvidenceKind.FORMALIZATION_FIDELITY
    assert attempt.evidence.artifact_hash == review.digest()
    assert attempt.attestation.authority_role is EvidenceAuthorityRole.HUMAN_REVIEWER
    assert attempt.attestation.authority_mode is EvidenceAuthorityMode.LOCAL_HUMAN_REVIEW
    assert attempt.attestation.result_digest == review.digest()
    assert attempt.attestation.source_digest == result.source_tree_hash
    assert not attempt.attestation.network_used
    assert attempt.attestation.marginal_cost_microusd == 0
    verifier = EvidenceVerifier(
        {
            "human-fidelity-v1": EvidenceKeyPolicy(
                key,
                EvidenceAuthorityRole.HUMAN_REVIEWER,
                frozenset({EvidenceKind.FORMALIZATION_FIDELITY}),
            )
        }
    )
    assert verifier.verify(
        claim,
        attempt.evidence,
        attempt.attestation,
        attempt.receipt,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"reviewer_key_id": "different-human-v1"},
        {"quantifiers_match": False},
        {"no_extra_axioms": False},
        {"notes": "   "},
    ],
)
def test_fidelity_authority_refuses_wrong_reviewer_or_failed_dimension(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    result = sealed_eligible_result(tmp_path)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    review = bound_fidelity_review(tmp_path, claim, result, **change)
    authority = LocalFormalizationFidelityAuthority(
        "human-fidelity-v1",
        b"local-human-fidelity-key-000000000000000000000000",
    )
    attempt = authority.review_and_issue(
        claim,
        result,
        review,
        tmp_path / "formal" / "MathBrain.lean",
        project_root=tmp_path / "formal",
    )
    assert not attempt.issued


def test_fidelity_authority_refuses_claim_binding_substitution(tmp_path: Path) -> None:
    result = sealed_eligible_result(tmp_path)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    review = bound_fidelity_review(tmp_path, claim, result)
    substituted = Claim("For every integer n, n + 0 = n.", ClaimKind.THEOREM)
    authority = LocalFormalizationFidelityAuthority(
        "human-fidelity-v1",
        b"local-human-fidelity-key-000000000000000000000000",
    )
    attempt = authority.review_and_issue(
        substituted,
        result,
        review,
        tmp_path / "formal" / "MathBrain.lean",
        project_root=tmp_path / "formal",
    )
    assert not attempt.issued


def test_fidelity_authority_refuses_tampered_runtime_seal(tmp_path: Path) -> None:
    result = sealed_eligible_result(tmp_path)
    tampered = replace(result, reason="caller changed the sealed result")
    assert tampered.authority_eligible
    assert not tampered._runtime_seal_is_valid()
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    review = bound_fidelity_review(tmp_path, claim, tampered)
    authority = LocalFormalizationFidelityAuthority(
        "human-fidelity-v1",
        b"local-human-fidelity-key-000000000000000000000000",
    )
    attempt = authority.review_and_issue(
        claim,
        tampered,
        review,
        tmp_path / "formal" / "MathBrain.lean",
        project_root=tmp_path / "formal",
    )
    assert not attempt.issued


def test_fidelity_authority_refuses_source_or_span_substitution(tmp_path: Path) -> None:
    result = sealed_eligible_result(tmp_path)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    review = bound_fidelity_review(tmp_path, claim, result)
    binding = review.statement_bindings[0]
    tampered_review = replace(
        review,
        statement_bindings=(replace(binding, statement_hash=HASH),),
    )
    authority = LocalFormalizationFidelityAuthority(
        "human-fidelity-v1",
        b"local-human-fidelity-key-000000000000000000000000",
    )
    assert not authority.review_and_issue(
        claim,
        result,
        tampered_review,
        tmp_path / "formal" / "MathBrain.lean",
        project_root=tmp_path / "formal",
    ).issued

    source = tmp_path / "formal" / "MathBrain.lean"
    source.write_text(
        GOOD_SOURCE.replace("theorem ok : True", "theorem ok : False"),
        encoding="utf-8",
    )
    assert not authority.review_and_issue(
        claim,
        result,
        review,
        source,
        project_root=tmp_path / "formal",
    ).issued


def test_lean_and_human_authorities_require_distinct_key_material() -> None:
    shared_key = b"one-key-must-not-cross-authority-boundaries-000000000"
    with pytest.raises(ValueError, match="distinct key material"):
        EvidenceVerifier(
            {
                "lean-local-v1": EvidenceKeyPolicy(
                    shared_key,
                    EvidenceAuthorityRole.AUTOMATED_VERIFIER,
                    frozenset({EvidenceKind.LEAN_KERNEL}),
                ),
                "human-fidelity-v1": EvidenceKeyPolicy(
                    shared_key,
                    EvidenceAuthorityRole.HUMAN_REVIEWER,
                    frozenset({EvidenceKind.FORMALIZATION_FIDELITY}),
                ),
            }
        )


def test_fidelity_review_rejects_non_boolean_checklist_value(tmp_path: Path) -> None:
    result = sealed_eligible_result(tmp_path)
    claim = Claim("For every natural n, n + 0 = n.", ClaimKind.THEOREM)
    with pytest.raises(TypeError, match="exact boolean"):
        bound_fidelity_review(tmp_path, claim, result, quantifiers_match=1)
