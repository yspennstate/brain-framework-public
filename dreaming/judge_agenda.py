"""judge_agenda.py - judge the dream agenda's reflex fires while awake, and append the verdicts in the judge's format.

The nightly agenda (dream.py, via fire_judge_nightly.py) lists sampled reflex fires in its J, J2 and J3 sections.
On Windows the REM pass is a model that judges them; run_dream.py, the runner everywhere else, has no REM pass, so
the cases wait until an awake agent judges them. Nothing else feeds judged_nightly.jsonl, and the precision table
the priming hook gates on is rebuilt from it.

    python judge_agenda.py show [--full]          the unjudged cases, compact (--full: nothing cut)
    python judge_agenda.py template > v.json      a verdict skeleton for the unjudged cases, to fill in
    python judge_agenda.py write v.json --judge <agent-id> [--dry-run]

v.json maps case_id -> {"domain_at_play": yes|no|unclear, "followed": yes|no|no_opportunity,
"useful": yes|no (J2 and J3 cases only), "why": "one sentence"}. write copies instinct, stratum, path, proxy,
reason and fired_at from the case itself, checks every value, writes nothing when any verdict is malformed or names
a case the agenda does not hold, skips cases the log already has, and makes sure each row starts on its own line.
--agenda and --log point at other files (default: DREAM_AGENDA.md and judged_nightly.jsonl beside this script).
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOMAIN = ("yes", "no", "unclear")
FOLLOWED = ("yes", "no", "no_opportunity")
USEFUL = ("yes", "no")
# the fields each stratum's row copies from its case, in the order of the agenda's row templates (a target case
# carries a proxy too, but its template leaves it out; a suppressed one never fired, so it has no path)
COPIED = {"target": ("instinct", "stratum", "path", "fired_at"),
          "firehose": ("instinct", "stratum", "proxy", "path", "fired_at"),
          "suppressed": ("instinct", "stratum", "reason", "proxy", "fired_at")}


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def cases(agenda):
    """The agenda's judging cases, in order: every fenced json block that carries a case_id and a stratum."""
    try:
        text = io.open(agenda, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    out = []
    for blk in re.findall(r"```json\n(.*?)\n```", text, re.S):
        try:
            c = json.loads(blk)
        except ValueError:
            continue
        if isinstance(c, dict) and c.get("case_id") and c.get("stratum"):
            out.append(c)
    return out


def judged_ids(log):
    ids = set()
    try:
        with io.open(log, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ids.add(json.loads(line).get("case_id"))
                except ValueError:
                    continue
    except OSError:
        pass
    return ids


def _cut(s, n):
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if n is None or len(s) <= n else s[:n].rstrip() + " ..."


def show(cs, done, full=False):
    n = None if full else 300
    todo = [c for c in cs if c["case_id"] not in done]
    print("%d case(s) in the agenda, %d unjudged" % (len(cs), len(todo)))
    for c in todo:
        head = [c["case_id"], c["stratum"], c.get("instinct", "?")]
        for k in ("proxy", "path", "reason"):
            if c.get(k):
                head.append("%s=%s" % (k, c[k]))
        print("\n== " + "  ".join(head) + "  fired " + str(c.get("fired_at", "?")))
        print("   gut: " + _cut(c.get("gut", ""), None if full else 220))
        for k in ("at_fire", "after"):
            v = c.get(k) or []
            for i, x in enumerate(v if isinstance(v, list) else [v]):
                print("   %s[%d]: %s" % (k, i, _cut(x, n)))
    return 0


def template(cs, done):
    t = {}
    for c in cs:
        if c["case_id"] in done:
            continue
        v = {"domain_at_play": "yes|no|unclear", "followed": "yes|no|no_opportunity", "why": ""}
        if c["stratum"] != "target":
            v["useful"] = "yes|no"
        t[c["case_id"]] = v
    print(json.dumps(t, indent=1))
    return 0


def build_rows(cs, done, verdicts, judge):
    """(rows, errors, skipped). rows is empty whenever errors is not."""
    by_id = {c["case_id"]: c for c in cs}
    rows, errors, skipped = [], [], []
    if not isinstance(verdicts, dict):
        return [], ["the verdict file must map case_id to a verdict object"], []
    for cid, v in verdicts.items():
        c = by_id.get(cid)
        if c is None:
            errors.append("%s: not a case in this agenda" % cid)
            continue
        if not isinstance(v, dict):
            errors.append("%s: the verdict is not an object" % cid)
            continue
        if cid in done:
            skipped.append(cid)
            continue
        d, f, u, why = v.get("domain_at_play"), v.get("followed"), v.get("useful"), v.get("why")
        if d not in DOMAIN:
            errors.append("%s: domain_at_play %r is not one of %s" % (cid, d, "/".join(DOMAIN)))
        if f not in FOLLOWED:
            errors.append("%s: followed %r is not one of %s" % (cid, f, "/".join(FOLLOWED)))
        needs_useful = c["stratum"] != "target"
        if needs_useful and u not in USEFUL:
            errors.append("%s: a %s case needs useful yes/no, got %r" % (cid, c["stratum"], u))
        if not isinstance(why, str) or not why.strip():
            errors.append("%s: why is empty" % cid)
        row = {"case_id": cid}
        for k in COPIED.get(c["stratum"], COPIED["firehose"]):
            if c.get(k) not in (None, ""):
                row[k] = c[k]
        row.update({"domain_at_play": d, "followed": f})
        if needs_useful:
            row["useful"] = u
        row.update({"why": (why or "").strip(), "judged_by": "dream", "judge": judge})
        rows.append(row)
    if errors:
        return [], errors, skipped
    return rows, [], skipped


def append_rows(log, rows):
    if not rows:
        return 0
    try:
        with io.open(log, "rb") as fh:
            fh.seek(0, 2)
            if fh.tell():
                fh.seek(-1, 2)
                lead = b"" if fh.read(1) == b"\n" else b"\n"
            else:
                lead = b""
    except OSError:
        lead = b""
    payload = lead + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    with io.open(log, "ab") as fh:
        fh.write(payload)
    return len(rows)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    agenda = _opt(argv, "--agenda", os.path.join(HERE, "DREAM_AGENDA.md"))
    log = _opt(argv, "--log", os.path.join(HERE, "judged_nightly.jsonl"))
    cs, done = cases(agenda), judged_ids(log)
    cmd = argv[0]
    if cmd == "show":
        return show(cs, done, full="--full" in argv)
    if cmd == "template":
        return template(cs, done)
    if cmd == "write":
        if len(argv) < 2:
            print("write needs a verdict file", file=sys.stderr)
            return 2
        judge = _opt(argv, "--judge")
        if not judge:
            print("write needs --judge <agent-id>", file=sys.stderr)
            return 2
        try:
            verdicts = json.load(io.open(argv[1], encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("cannot read %s: %s" % (argv[1], exc), file=sys.stderr)
            return 2
        rows, errors, skipped = build_rows(cs, done, verdicts, judge)
        if errors:
            for e in errors:
                print("ERROR " + e, file=sys.stderr)
            print("nothing written (%d error(s))" % len(errors), file=sys.stderr)
            return 1
        if "--dry-run" in argv:
            for r in rows:
                print(json.dumps(r, ensure_ascii=False))
            print("dry run: %d row(s) would be appended, %d already judged" % (len(rows), len(skipped)))
            return 0
        n = append_rows(log, rows)
        left = len([c for c in cs if c["case_id"] not in done]) - n
        print("appended %d row(s) to %s; %d already judged; %d case(s) still unjudged" % (n, log, len(skipped), left))
        return 0
    print("unknown command %r (show, template, write)" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
