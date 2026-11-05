from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from proofswarm.tools import lean_verify as lean_module
from proofswarm.tools.lean_verify import compose_lean_source


def test_complete_source_is_never_discarded() -> None:
    source = "theorem wanted : True := by trivial"
    assert source in compose_lean_source(source, "", imports=[])
    with pytest.raises(ValueError, match="cannot be paired"):
        compose_lean_source(source, "theorem dummy : True := by trivial", imports=[])


def test_tactic_replaces_only_an_explicit_placeholder() -> None:
    statement = "theorem arithmetic : 1 + 1 = 2 := by sorry"
    source = compose_lean_source(statement, "by norm_num", imports=["Mathlib"])
    assert "theorem arithmetic" in source
    assert "by norm_num" in source
    assert "sorry" not in source


def test_local_verifier_hashes_the_exact_compiled_bytes(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, bytes] = {}

    def fake_run(command, *, cwd, timeout_seconds):
        captured["source"] = Path(command[-1]).read_bytes()
        assert cwd == tmp_path
        assert timeout_seconds == 180
        return 0, "", None

    monkeypatch.setattr(lean_module, "require_local_compute", lambda: {})
    (tmp_path / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:test\n", encoding="utf-8")
    (tmp_path / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    monkeypatch.setattr(lean_module, "LEAN_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(lean_module.shutil, "which", lambda _name: "lake")
    monkeypatch.setattr(lean_module, "_run_guarded_lean", fake_run)
    result = lean_module.lean_verify(
        "theorem exact_source : 1 + 1 = 2 := by rfl", "", imports=[]
    )
    assert result.ok
    assert result.elaborated
    assert result.sorry_count == 0
    assert result.source_sha256 == hashlib.sha256(captured["source"]).hexdigest()


def test_local_verifier_never_fetches_missing_mathlib(monkeypatch, tmp_path: Path) -> None:
    launched = False

    def fake_run(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("subprocess must not launch")

    (tmp_path / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:test\n", encoding="utf-8")
    monkeypatch.setattr(lean_module, "LEAN_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(lean_module, "require_local_compute", lambda: {})
    monkeypatch.setattr(lean_module.subprocess, "run", fake_run)
    result = lean_module.lean_verify("theorem exact_source : True := by trivial", "", imports=[])
    assert not result.ok
    assert not launched
    assert "not provisioned" in result.errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "theorem incomplete : True := by sorry",
        "axiom fabricated : False",
        "constant fabricated : False",
        "theorem incomplete : True := by admit",
        "theorem bypass : True := by native_decide",
        "theorem bypass : True := by run_tac pure ()",
        "#eval (1 + 1)",
        "run_cmd IO.println \"side effect\"",
        "unsafe def side_effect : Nat := 0",
    ],
)
def test_local_verifier_rejects_trust_bypasses_before_launch(
    source: str, monkeypatch
) -> None:
    launched = False

    def fake_run(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("subprocess must not launch")

    monkeypatch.setattr(lean_module.subprocess, "run", fake_run)
    result = lean_module.lean_verify(source, "", imports=[])
    assert not result.ok
    assert not launched
    assert "forbidden trust or metaprogramming escape" in result.errors[0]


def test_server_composer_also_refuses_to_replace_the_statement() -> None:
    from lean_worker.source_policy import compose_source

    with pytest.raises(ValueError, match="cannot replace"):
        compose_source(
            "theorem wanted : True := by trivial",
            "theorem dummy : True := by trivial",
            [],
        )


def test_lean_source_and_import_counts_are_bounded_before_launch(monkeypatch) -> None:
    launched = False

    def fake_run(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("subprocess must not launch")

    monkeypatch.setattr(lean_module.subprocess, "run", fake_run)
    too_large = "--" + ("x" * lean_module.MAX_LEAN_SOURCE_BYTES)
    result = lean_module.lean_verify(too_large, "", imports=[])
    assert not result.ok
    assert "1 MiB" in result.errors[0]
    assert not launched

    with pytest.raises(ValueError, match="too many Lean imports"):
        compose_lean_source(
            "theorem bounded : True := by trivial", "", imports=["Mathlib"] * 65
        )


def test_guarded_lean_runner_caps_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lean_module, "require_local_compute", lambda: {})
    monkeypatch.setattr(lean_module, "MAX_LEAN_OUTPUT_BYTES", 1024)
    returncode, output, error = lean_module._run_guarded_lean(
        [sys.executable, "-B", "-c", "import sys; sys.stdout.write('x' * 4096)"],
        cwd=tmp_path,
        # This test is about the output cap and asserts nothing about elapsed time; the timeout is
        # only here so a hung child cannot hang the suite. Kept well clear of the cap so a busy
        # machine cannot make the runner report a timeout instead of "output exceeded" - the child
        # writes 4096 bytes and exits, so a generous bound costs nothing.
        timeout_seconds=120,
    )
    assert returncode != 0 or error is not None
    assert error is not None and "output exceeded" in error
    assert len(output.encode("utf-8")) <= 1024
