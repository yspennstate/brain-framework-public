"""test_memory_hub_merge.py - merging two computers' copies of a journal hub: origin-only items land after their
predecessor, a closed checkbox from the origin replaces the open one here, no live line is ever dropped, continuation
lines travel with their item, and --apply keeps CRLF and writes a backup under a temporary home."""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import shutil
import tempfile

import memory_hub_merge as H


def test_origin_only_item_goes_after_its_predecessor():
    live = ["# Q", "- a", "- c"]
    origin = ["# Q", "- a", "- b", "- c"]
    out, added, lost, _ = H.merge_items(live, origin)
    assert out == ["# Q", "- a", "- b", "- c"], out
    assert added == ["- b"] and lost == []


def test_live_only_lines_are_kept():
    live = ["# Q", "- a", "- only here"]
    origin = ["# Q", "- a", "- only there, quite different words"]
    out, _, _, _ = H.merge_items(live, origin)
    assert "- only here" in out and "- only there, quite different words" in out


def test_closed_checkbox_from_origin_replaces_open():
    live = ["# P", "- [ ] p12 | rain tomorrow"]
    origin = ["# P", "- [x] p12 | rain tomorrow"]
    out, added, _, replaced = H.merge_items(live, origin)
    assert out == ["# P", "- [x] p12 | rain tomorrow"], out
    assert replaced and added == ["- [x] p12 | rain tomorrow"]


def test_open_in_origin_does_not_reopen_closed_here():
    live = ["# P", "- [x] p12 | rain tomorrow"]
    origin = ["# P", "- [ ] p12 | rain tomorrow"]
    out, _, _, replaced = H.merge_items(live, origin)
    assert out == ["# P", "- [x] p12 | rain tomorrow"] and not replaced


def test_continuation_lines_travel_with_their_item():
    live = ["- a", "  a note"]
    origin = ["- a", "  a note", "  a second note from elsewhere", "- z", "  z detail"]
    out, _, lost, _ = H.merge_items(live, origin)
    assert out == ["- a", "  a note", "  a second note from elsewhere", "- z", "  z detail"], out
    assert lost == []


def test_apply_keeps_crlf_and_backs_up():
    d = tempfile.mkdtemp(prefix="hub_test_")
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = os.environ["USERPROFILE"] = d
    try:
        live, origin = os.path.join(d, "Q.md"), os.path.join(d, "Q.origin.md")
        with open(live, "wb") as fh:
            fh.write(b"# Q\r\n- a\r\n- c\r\n")
        with open(origin, "wb") as fh:
            fh.write(b"# Q\n- a\n- b\n- c\n")
        assert H.main([live, origin]) == 0
        with open(live, "rb") as fh:
            assert fh.read() == b"# Q\r\n- a\r\n- c\r\n"      # dry run writes nothing
        assert H.main([live, origin, "--apply"]) == 0
        with open(live, "rb") as fh:
            assert fh.read() == b"# Q\r\n- a\r\n- b\r\n- c\r\n"
        assert os.path.isfile(os.path.join(H.backup_dir(), "Q.md.prehub"))
        assert H.backup_dir().startswith(d)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
