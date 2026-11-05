"""The delivery audit: which stated values survive the injection budget, and an exit code that never fakes a pass."""
import json
from pathlib import Path

from math_brain import delivery_audit as da
from math_brain.models import Claim, ClaimKind
from math_brain.store import ResearchStore


def test_asserted_reads_stated_results_and_not_citations() -> None:
    assert da.ASSERTED.findall("The number of such tilings is exactly 1739.") == ["1739"]
    assert da.ASSERTED.findall("so the constant = 42") == ["42"]
    assert da.ASSERTED.findall("as shown by Janson (2019), see arXiv 1810.08473") == []
    assert da.ASSERTED.findall("the answer is 7") == []          # one digit is BROAD, not ASSERTED


def test_broad_reads_the_forms_asserted_leaves_out() -> None:
    found = da.BROAD.findall("x is -3, p <= 1/2, eps < 1e-5, and the rate is at most 0.25")
    assert found == ["-3", "1/2", "1e-5", "0.25"]
    assert da.BROAD.findall("f -> 3 and a != 4") == []
    assert [da.broad_form(v) for v in found] == ["integer, one digit", "fraction", "scientific or power of ten",
                                                  "decimal"]
    assert da.broad_form("1739") == "integer, two or more digits"


def _store(path: Path, *statements: str) -> Path:
    with ResearchStore(path) as store:
        for s in statements:
            store.add_claim(Claim(s, ClaimKind.THEOREM))
    return path


def _run(store: Path, capsys) -> tuple[int, dict]:
    code = da.main(["--store", str(store), "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_a_missing_store_is_an_error_and_nothing_is_created(tmp_path, capsys) -> None:
    missing = tmp_path / "absent.sqlite3"
    code, out = _run(missing, capsys)
    assert code == da.EXIT_CANNOT_OPEN and out["status"] == "STORE_MISSING"
    assert not missing.exists()


def test_empty_and_out_of_scope_stores_never_read_as_passes(tmp_path, capsys) -> None:
    code, out = _run(_store(tmp_path / "empty.sqlite3"), capsys)
    assert (code, out["status"]) == (da.EXIT_EMPTY, "EMPTY_STORE")
    code, out = _run(_store(tmp_path / "prose.sqlite3", "Every finite group of prime order is cyclic."), capsys)
    assert (code, out["status"]) == (da.EXIT_NONE_IN_SCOPE, "NONE_IN_SCOPE")
    assert out["asserted"]["rate"] is None and out["not_evaluated"] == 1


def test_a_value_past_the_budget_is_counted_lost(tmp_path, capsys) -> None:
    late = ("Counting domino tilings of the 2 by n strip with the boundary rule recorded in the source "
            "and every orientation convention fixed in advance, after the reduction to a transfer matrix "
            "and the check that no state is reachable twice along a cycle, and with the edge cases at the "
            "two ends of the strip handled by hand, the number of tilings for the largest case is exactly 1739.")
    assert late.index("1739") > 320
    code, out = _run(_store(tmp_path / "s.sqlite3", "The count is exactly 1739 for n = 15.", late), capsys)
    assert (code, out["status"]) == (da.EXIT_MEASURED, "MEASURED")
    assert (out["asserted"]["carrying"], out["asserted"]["intact"]) == (2, 1)
    assert out["population"]["nodes"] == 2 and len(out["population"]["digest"]) >= 16
