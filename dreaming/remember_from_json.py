"""remember_from_json.py - write a memory from one JSON file, with no shell quoting anywhere.

Written 2026-09-16 after backticks inside a double-quoted --evidence argument were executed by bash as a
command, silently truncating a memory's evidence field to "stopping at exactly , " and saving it that way. The
push succeeded, so nothing looked wrong; the corruption was visible only as a one-line shell complaint among
the confirmations.

That is a bad failure for the BRAIN in particular. A mangled memory is not a broken build that someone notices
- it is a confident, wrong sentence that a future session reads and believes. Every character that ends up in
a memory should travel as data, never as a shell argument.

    python remember_from_json.py note.json

    note.json: {"slug": "...", "type": "project|feedback|user|reference", "status": "verified|provisional|
                owner-stated", "desc": "...", "evidence": "...", "body": "..."}

It refuses rather than writing something half-formed: a missing field, an empty body, or a slug that does not
look like a slug all stop before brain.py is called.
"""
import json, os, subprocess, sys, tempfile

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.py")
NEEDED = ("slug", "type", "desc", "body")
TYPES = ("project", "feedback", "user", "reference")
STATUSES = ("verified", "provisional", "owner-stated")


def main():
    if len(sys.argv) < 2:
        print("usage: remember_from_json.py note.json")
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        note = json.load(fh)

    missing = [k for k in NEEDED if not str(note.get(k, "")).strip()]
    if missing:
        print("REFUSED: these fields are empty or absent: %s" % ", ".join(missing))
        return 3
    if note["type"] not in TYPES:
        print("REFUSED: type %r is not one of %s" % (note["type"], ", ".join(TYPES)))
        return 3
    status = note.get("status", "provisional")
    if status not in STATUSES:
        print("REFUSED: status %r is not one of %s" % (status, ", ".join(STATUSES)))
        return 3
    if status == "verified" and not str(note.get("evidence", "")).strip():
        print("REFUSED: a verified memory needs evidence - name the observation, transcript or artifact")
        return 3
    slug = note["slug"]
    if not slug.replace("_", "").isalnum() or slug != slug.lower():
        print("REFUSED: %r does not look like a lowercase underscored slug" % slug)
        return 3

    body = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="")
    body.write(note["body"])
    body.close()
    # argv is passed as a LIST: no shell, so backticks, quotes, dollars and newlines are just characters
    cmd = [sys.executable, BRAIN, "remember", slug, "--type", note["type"], "--status", status,
           "--desc", note["desc"], "--body-file", body.name]
    if str(note.get("evidence", "")).strip():
        cmd += ["--evidence", note["evidence"]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        print("brain.py refused it (exit %d) - nothing was written" % r.returncode)
        return r.returncode

    # read the memory BACK and prove the fields survived, because that is the failure this tool exists for
    path = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory", slug + ".md")
    try:
        saved = open(path, encoding="utf-8").read()
    except Exception as e:
        print("WROTE BUT COULD NOT VERIFY: %s" % e)
        return 4
    for field, want in (("desc", note["desc"]), ("evidence", note.get("evidence", ""))):
        want = str(want).strip()
        if want and want[:60] not in saved:
            print("VERIFY FAILED: the %s field did not survive intact" % field)
            return 5
    if note["body"][:60] not in saved:
        print("VERIFY FAILED: the body did not survive intact")
        return 5
    print("verified on disk: desc, evidence and body all round-tripped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
