from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from math_brain.lean import StatementFidelityReview, check_lean_file


HASH = "sha256:" + "a" * 64


def test_lean_rejects_placeholder_before_invocation(tmp_path: Path) -> None:
    source = tmp_path / "Bad.lean"
    source.write_text("theorem bad : True := by sorry", encoding="utf-8")
    with patch("math_brain.lean.subprocess.run") as run:
        result = check_lean_file(source)
    assert not result.passed
    assert "placeholder" in result.reason
    run.assert_not_called()


def test_lean_rejects_source_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "Outside.lean"
    source.write_text("theorem ok : True := by trivial", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        check_lean_file(source, project_root=project)


def test_lean_records_toolchain_and_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "Good.lean"
    source.write_text("theorem ok : True := by trivial", encoding="utf-8")
    version = Mock(returncode=0, stdout="Lean 4.30.0\n", stderr="")
    check = Mock(returncode=0, stdout="", stderr="")
    with patch("math_brain.lean.subprocess.run", side_effect=[version, check]) as run:
        result = check_lean_file(source, command=("lake", "env", "lean"))
    assert result.passed
    assert result.toolchain_version == "Lean 4.30.0"
    assert result.source_hash.startswith("sha256:")
    assert run.call_args_list[1].args[0][-1] == str(source.resolve())


def test_fidelity_requires_every_dimension() -> None:
    review = StatementFidelityReview(
        HASH,
        HASH,
        "independent reviewer",
        quantifiers_match=True,
        domains_match=True,
        definitions_match=True,
        assumptions_match=True,
        conclusion_match=True,
        boundary_cases_match=False,
        no_extra_axioms=True,
    )
    assert not review.passed
