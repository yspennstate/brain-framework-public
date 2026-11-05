#!/usr/bin/env python3
"""test_hook_stdin_utf8.py - a hook payload is a BYTE protocol. Prove it end to end.

This defect has now happened twice. On 2026-07-23 `prompt_archive.py` read `sys.stdin.read()`,
decoded the wire bytes through cp1252, and stored an em-dash as three junk characters; it was
fixed at that reader. On 2026-07-28 a repair for a separate problem (a bare StringIO restore left
gated hooks unable to read `.buffer`, freezing PROMPTS.jsonl for five days) moved the stdin read UP
into `brain_switch.gate()` - and the cp1252 read came with it. By 2026-08-22, 962 ledger records
(5.2%) carried mojibake, 767 of them from 2026-08 alone, and every OTHER gated brain hook was
receiving the same corrupted text.

Fixing a reader is not fixing the read. These tests pin the invariant at the shared gate, where it
belongs, and they must run in a CHILD PROCESS with a clean environment: on this machine
sys.stdin.encoding is cp1252 and sys.flags.utf8_mode is 0, so a parent that exports
PYTHONIOENCODING=utf-8 makes the whole failure disappear and the test passes vacuously. That is
precisely how the first "the hook preserves UTF-8" check on 2026-08-22 returned a false all-clear.

Run: python test_hook_stdin_utf8.py
"""
import io, json, os, shutil, subprocess, sys, tempfile

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
NONASCII = u"dash \u2014 em, curly \u2019q, shekel \u20aa, accent \u00e9"


def _clean_env(**extra):
    """A child that inherits PYTHONIOENCODING=utf-8 cannot fail this test. Strip it."""
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    env.update(extra)
    return env


def _payload(prompt, session="utf8test01"):
    # ensure_ascii=False puts RAW UTF-8 on the wire. With the default (escaped \uXXXX) the
    # payload is pure ASCII and cp1252 cannot corrupt it - the test would prove nothing.
    return json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                       "session_id": session, "cwd": "C:/tmp"},
                      ensure_ascii=False).encode("utf-8")


def _run(script_body, payload, env=None):
    fd, path = tempfile.mkstemp(suffix=".py", dir=HERE)
    os.close(fd)
    io.open(path, "w", encoding="utf-8", newline="\n").write(script_body)
    try:
        return subprocess.run([sys.executable, path], input=payload,
                              capture_output=True, env=env or _clean_env())
    finally:
        os.unlink(path)


def test_environment_is_actually_hostile():
    """Guard the guard: if stdin already decodes UTF-8 here, the tests below prove nothing."""
    p = _run("import sys\n"
             "sys.stdout.reconfigure(encoding='utf-8')\n"
             "print(sys.stdin.encoding, sys.flags.utf8_mode)\n", b"")
    enc, mode = p.stdout.decode("utf-8", "replace").split()
    if enc.lower().replace("-", "") in ("utf8",) or mode != "0":
        print("SKIP  environment is not hostile (stdin=%s utf8_mode=%s) - "
              "these tests cannot fail here" % (enc, mode))
        return False
    print("ok    environment is hostile as expected (stdin=%s, utf8_mode=%s)" % (enc, mode))
    return True


def test_gate_preserves_non_ascii():
    """brain_switch.gate() must hand the hook back the bytes it was given."""
    body = ("import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "import brain_switch as bsw\n"
            "bsw.gate()\n"
            "obj = json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
            "print(obj['prompt'])\n" % HERE)
    p = _run(body, _payload(NONASCII))
    got = p.stdout.decode("utf-8", "replace").strip()
    assert got == NONASCII, "gate() mangled the payload:\n  sent %r\n  got  %r" % (NONASCII, got)
    print("ok    gate() preserves non-ASCII through a cp1252 stdin")


def test_gate_restored_stdin_still_exposes_buffer():
    """The 2026-07-28 invariant: a text-only restore froze the ledger for five days."""
    body = ("import sys\n"
            "sys.path.insert(0, %r)\n"
            "import brain_switch as bsw\n"
            "bsw.gate()\n"
            "print('has_buffer', hasattr(sys.stdin, 'buffer'))\n" % HERE)
    p = _run(body, _payload("plain ascii"))
    assert "has_buffer True" in p.stdout.decode("utf-8", "replace"), p.stdout[:300]
    print("ok    gate() restores a stdin that still exposes .buffer")


def test_archiver_stores_non_ascii_faithfully():
    """End to end: gate + prompt_archive must land the owner's characters unchanged."""
    tmp = os.path.join(tempfile.gettempdir(), "prompt_archive_utf8_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    env = _clean_env(PROMPT_ARCHIVE_DIR=tmp,
                     PROMPT_ORIGIN_RECENT=os.path.join(tmp, ".recent.json"))
    subprocess.run([sys.executable, os.path.join(HERE, "prompt_archive.py")],
                   input=_payload(NONASCII), capture_output=True, env=env)
    led = os.path.join(tmp, "PROMPTS.jsonl")
    assert os.path.exists(led), "archiver wrote no record"
    rec = json.loads(io.open(led, encoding="utf-8").read().strip())
    assert rec["text"] == NONASCII, "stored %r, sent %r" % (rec["text"], NONASCII)
    shutil.rmtree(tmp, ignore_errors=True)
    print("ok    prompt_archive stores non-ASCII faithfully end to end")


def test_mojibake_repair_is_conservative():
    """The repair must never rewrite text that merely contains accented characters."""
    sys.path.insert(0, HERE)
    import mojibake as mj
    assert mj.repair_deep(u"dash \u00e2\u20ac\u201d em") == u"dash \u2014 em"
    for clean in (u"\u00e9l\u00e8ve fran\u00e7ais", u"na\u00efve r\u00e9sum\u00e9",
                  u"\u00c2ge moyen", u"plain ascii", u"already \u2014 fine"):
        assert mj.repair_deep(clean) == clean, "repair touched clean text: %r" % clean
    print("ok    mojibake repair fixes damage and leaves clean text alone")


if __name__ == "__main__":
    hostile = test_environment_is_actually_hostile()
    test_gate_preserves_non_ascii()
    test_gate_restored_stdin_still_exposes_buffer()
    test_archiver_stores_non_ascii_faithfully()
    test_mojibake_repair_is_conservative()
    print("\nall passed%s" % ("" if hostile else " (BUT the environment was not hostile - "
                                                 "the stdin tests were vacuous)"))
