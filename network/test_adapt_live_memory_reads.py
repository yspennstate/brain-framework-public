"""brain.py node reads (memory_reads.tsv, via=node) reach the network as mem: access events."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapt_live  # noqa: E402


def test_node_reads_become_mem_events_and_other_rows_do_not(tmp_path):
    reads = tmp_path / "memory_reads.tsv"
    reads.write_text(
        "alpha_card\t1793877052.4\tsess-a\tnode\t\tcli\n"
        "beta_card\t1793877060.0\tsess-a\tread\t\thook\n"
        "gamma_card\t1793000000.0\tsess-w\twindows_import\t\t\n"
        "short\trow\n"
        "delta_card\tnot-a-time\tsess-a\tnode\t\tcli\n",
        encoding="utf-8",
    )
    out = tmp_path / "events.jsonl"
    assert adapt_live.main(["--memory-reads", str(reads), "--out", str(out)]) == 0
    events = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [(e["kind"], e["id"], e["session"]) for e in events] == [("mem", "alpha_card", "sess-a")]


def test_without_the_file_nothing_breaks(tmp_path):
    out = tmp_path / "events.jsonl"
    assert adapt_live.main(["--memory-reads", str(tmp_path / "absent.tsv"), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == ""
