"""Controls for doc_expand.py's changed-memory selection and its source digest (27 Sep, claude-825b1305).

A memory edited after its questions were written must get new ones; one merely touched, or edited beyond the part the
question-writer reads, must not. Everything runs in a temporary folder with the Haiku call replaced; nothing live is read
or written.

    python test_doc_expand_changed.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import doc_expand as dx  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def write(mem, slug, desc, body, mtime):
    p = os.path.join(mem, slug + ".md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: project\n---\n\n%s\n" % (slug, desc, body))
    os.utime(p, (mtime, mtime))
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="dx_test_")
    live = (dx.MEM, dx.OUT, dx.ask)
    try:
        mem = os.path.join(tmp, "memory")
        os.makedirs(mem)
        now = time.time()
        t_q = now - 7200                                       # when the stored questions were written
        write(mem, "m_new", "A memory nobody asked about yet", "body text one", now - 3600)
        write(mem, "m_same", "A memory touched but not changed", "body text two", now - 3600)
        write(mem, "m_edit", "A memory whose opening was rewritten", "the new opening says something else", now - 3600)
        write(mem, "m_legacy_edit", "An old row, no digest, file edited later", "body text four", now - 3600)
        write(mem, "m_legacy_old", "An old row, no digest, file older than it", "body text five", t_q - 3600)
        write(mem, "m_settling", "Written a moment ago", "body text six", now - 10)
        long_tail = "x " * 800
        write(mem, "m_tail", "Edited only far past what the writer reads", "start " + long_tail + " tail changed", now - 3600)
        # account01/B02 auto11: an edit made while the questions were being written (file older than the row) and one
        # made 30 s after the row; the digest has to catch both
        t_r = now - 3600
        write(mem, "m_during", "Edited while its questions were being written", "the text after the edit", t_r - 30)
        write(mem, "m_after30", "Edited half a minute after its row", "the text after the edit", t_r + 30)
        dx.MEM = mem
        same_sha = dx.text_sha(dx.note_text("m_same"))
        tail_text_then = dx.note_text("m_tail")                 # the first 700 characters, unchanged by the tail edit
        done = {"m_same": (t_q, same_sha),
                "m_edit": (t_q, dx.text_sha("A memory whose opening was rewritten the old opening")),
                "m_legacy_edit": (t_q, None),
                "m_legacy_old": (t_q, None),
                "m_settling": (t_q, None),
                "m_tail": (t_q, dx.text_sha(tail_text_then)),
                "m_during": (t_r, dx.text_sha("Edited while its questions were being written the text before the edit")),
                "m_after30": (t_r, dx.text_sha("Edited half a minute after its row the text before the edit"))}
        new, changed = dx.new_slugs(done)
        check("11 an edit made while the questions were written is changed", "m_during" in changed, str(changed))
        check("12 an edit 30 s after the row is changed", "m_after30" in changed, str(changed))
        check("1 a memory with no questions is new", new == ["m_new"], str(new))
        check("2 an edited opening is changed", "m_edit" in changed, str(changed))
        check("3 a touched memory with the same text is not changed", "m_same" not in changed, str(changed))
        check("4 a pre-digest row with a later edit is changed", "m_legacy_edit" in changed, str(changed))
        check("5 a pre-digest row newer than its file is not changed", "m_legacy_old" not in changed, str(changed))
        check("6 a file still settling waits", "m_settling" not in changed and "m_settling" not in new, str((new, changed)))
        check("7 an edit past what the writer reads is not changed", "m_tail" not in changed, str(changed))
        # a whole --new run with the model call replaced: rows carry the digest, and bm25's newest-row rule sees them
        out = os.path.join(tmp, "expansions.jsonl")
        with open(out, "w", encoding="utf-8") as fh:
            for slug, (t, sha) in done.items():
                row = {"slug": slug, "q": ["an old question about %s" % slug], "t": t}
                if sha:
                    row["sha"] = sha
                fh.write(json.dumps(row) + "\n")
        dx.OUT = out
        dx.ask = lambda batch: ({s: ["a new question about %s" % s] for s, _t in batch}, 0.0, None)
        dx._run(types.SimpleNamespace(new=True, slugs=None, max=400, batch=40, workers=1))
        rows = [json.loads(l) for l in open(out, encoding="utf-8")]
        newest = {}
        for r in rows:
            newest[r["slug"]] = r
        written = [r for r in rows if r["q"][0].startswith("a new question")]
        check("8 the run wrote the new and the changed memories, nothing else",
              sorted(r["slug"] for r in written) == ["m_after30", "m_during", "m_edit", "m_legacy_edit", "m_new"],
              str([r["slug"] for r in written]))
        check("9 each new row carries the digest of the text the writer saw",
              all(r.get("sha") == dx.text_sha(dx.note_text(r["slug"])) for r in written), str(written))
        new2, changed2 = dx.new_slugs({s: (float(r["t"]), r.get("sha")) for s, r in newest.items()})
        check("10 a second run finds nothing to do", new2 == [] and changed2 == [], str((new2, changed2)))
    finally:
        dx.MEM, dx.OUT, dx.ask = live
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of 12 controls pass" % (12 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
