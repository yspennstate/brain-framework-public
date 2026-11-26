"""Count the hooks in a brain install, layer by layer.

A hook count means little unless it says what it counts. This script reports four layers separately:

  A  handler commands the harness runs: settings.json hooks, the dispatcher list, Codex hooks.json
  B  hook program files: .py files whose name says hook, guard, reflex or sense, plus every .py
     under a folder whose name contains "hooks" (backups left out)
  C  rules with their own trigger: rows of instincts.tsv, and rule cards in each *hooks* folder
     (files named like K01_..., C12_..., dk004_...), with the number of distinct trigger strings
     when each card carries its own (None when the cards share a trigger core), and rule families
     kept as data, given with --families
  D  fires: rows in every fires.jsonl and instinct_fires*.tsv under the install

--families FILE is a JSON list of {"name", "id_regex", "globs"}: the distinct ids the regex finds in
the files the globs match (relative to --root) are that family's rules. hook_families.example.json
beside this script is a starting point.

With --compare DIR it also says how many of the hook program files in B exist, by file name, in
another tree (for example a checkout of the public framework).

Usage:
  python hook_census.py                       # ~/.claude and ~/.codex
  python hook_census.py --root PATH --codex PATH --families FILE --compare PATH --json out.json
  python hook_census.py --selftest
"""
import argparse
import glob
import io
import json
import os
import re
import shutil
import sys
import tempfile

BACKUP = re.compile(r"\.(bak|orig|cand|pre)\b|bak_|\.precarry|~$")
NAMED = re.compile(r"hook|guard|reflex|sense", re.I)
CARD = re.compile(r"^[A-Za-z]{1,3}\d{2,4}(?:[_.]|$)")
SKIP_DIRS = {"projects", ".git", "node_modules", "cache", "__pycache__", "shell-snapshots", "todos", "file-history"}
TRIGGER_LINE = re.compile(r"(?:TRIGGER|PATTERN|MATCH|RX|REGEX|trigger|pattern)\s*=\s*(r?[\"'].{3,300}?[\"'])")


def read(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def load_json(path):
    try:
        return json.loads(read(path))
    except ValueError:
        return None


def commands(obj):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "command" and isinstance(v, str):
                out.append(v)
            else:
                out += commands(v)
    elif isinstance(obj, list):
        for v in obj:
            out += commands(v)
    return out


def count_lines(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


def hook_dirs(root):
    found = {}
    for base in (root, os.path.join(root, "dreaming")):
        for d in glob.glob(os.path.join(base, "*")):
            if os.path.isdir(d) and "hooks" in os.path.basename(d).lower():
                found[os.path.relpath(d, root).replace("\\", "/")] = d
    return found


def census(root, codex=None, compare=None, family_config=None):
    out = {"root": "<root>"}
    dreaming = os.path.join(root, "dreaming")

    # A
    settings = load_json(os.path.join(root, "settings.json")) or {}
    hooks = settings.get("hooks", {}) if isinstance(settings, dict) else {}
    out["A_settings_commands"] = len(commands(hooks))
    out["A_settings_by_event"] = {k: len(commands(v)) for k, v in hooks.items()}
    lists = sorted(glob.glob(os.path.join(dreaming, "hook_dispatch_list*.json")))
    out["A_dispatcher_entries"] = {os.path.basename(p): len(commands(load_json(p))) for p in lists}
    if codex:
        out["A_codex_commands"] = len(commands(load_json(os.path.join(codex, "hooks.json"))))

    # B
    files = set()
    for p in glob.glob(os.path.join(dreaming, "*.py")):
        b = os.path.basename(p)
        if not BACKUP.search(b) and NAMED.search(b):
            files.add(p)
    per_dir = {}
    for name, d in hook_dirs(root).items():
        fs = [p for p in glob.glob(os.path.join(d, "**", "*.py"), recursive=True)
              if not BACKUP.search(os.path.basename(p))]
        per_dir[name] = len(fs)
        files.update(fs)
    out["B_hook_files"] = len(files)
    out["B_by_folder"] = per_dir

    # C
    rows = [ln.split("\t") for ln in read(os.path.join(dreaming, "instincts.tsv")).splitlines()[1:]
            if ln.strip() and not ln.startswith("#")]
    out["C_reflex_rows"] = len(rows)
    out["C_reflex_distinct_triggers"] = len({r[3] for r in rows if len(r) > 3})
    families = {}
    for name, d in hook_dirs(root).items():
        cards = [p for p in glob.glob(os.path.join(d, "*.py"))
                 if CARD.match(os.path.basename(p)) and not BACKUP.search(os.path.basename(p))]
        if not cards:
            continue
        trig = set()
        for p in cards:
            m = TRIGGER_LINE.findall(read(p))
            if m:
                trig.add(m[0])
        # cards that share one core read their trigger from it, not from a line of their own
        families[name] = {"cards": len(cards), "distinct_triggers": len(trig) if trig else None}
    out["C_rule_card_families"] = families
    data = {}
    for fam in family_config or []:
        rx = re.compile(fam["id_regex"])
        ids = set()
        for g in fam["globs"]:
            for p in glob.glob(os.path.join(root, g), recursive=True):
                if not BACKUP.search(os.path.basename(p)):
                    ids.update(rx.findall(read(p)))
        data[fam["name"]] = len(ids)
    out["C_data_families"] = data
    out["C_rules_total"] = (out["C_reflex_rows"] + sum(f["cards"] for f in families.values())
                            + sum(data.values()))

    # D
    logs = []
    for dp, dns, fns in os.walk(root):
        # transcripts, caches and git data hold no fire logs and can be very large
        dns[:] = [d for d in dns if d not in SKIP_DIRS and dp.count(os.sep) - root.count(os.sep) < 4]
        if "fires.jsonl" in fns:
            logs.append(os.path.join(dp, "fires.jsonl"))
    logs += [p for p in glob.glob(os.path.join(dreaming, "instinct_fires*.tsv")) if "pre_rotate" not in p]
    out["D_fire_rows"] = sum(count_lines(p) for p in logs)
    out["D_fire_logs"] = len(logs)

    if compare:
        names = set()
        for dp, _, fns in os.walk(compare):
            if os.sep + ".git" in dp:
                continue
            names.update(fns)
        have = sorted(os.path.basename(p) for p in files if os.path.basename(p) in names)
        out["compare_hook_files_present"] = len(have)
        out["compare_hook_files_missing"] = len(files) - len(have)
    return out


def selftest():
    tmp = tempfile.mkdtemp(prefix="hook_census_")
    try:
        root = os.path.join(tmp, "claude")
        os.makedirs(os.path.join(root, "dreaming", "kaggle_hooks"))
        with io.open(os.path.join(root, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python a.py"}]}]}}, f)
        with io.open(os.path.join(root, "dreaming", "hook_dispatch_list.json"), "w", encoding="utf-8") as f:
            json.dump({"UserPromptSubmit": [{"hooks": [{"command": "x"}, {"command": "y"}]}]}, f)
        for n in ("reflex_hook.py", "notes.py", "old_hook.py.bak_1"):
            io.open(os.path.join(root, "dreaming", n), "w", encoding="utf-8").write("")
        for n, t in (("K01_a.py", "TRIGGER = r'kaggle'"), ("K02_b.py", "TRIGGER = r'kaggle'"), ("helper.py", "")):
            io.open(os.path.join(root, "dreaming", "kaggle_hooks", n), "w", encoding="utf-8").write(t)
        with io.open(os.path.join(root, "dreaming", "instincts.tsv"), "w", encoding="utf-8") as f:
            f.write("id\tmode\tvalidity\ttrigger\n r1\tfire\tm\tfoo\n r2\tfire\tm\tfoo\n")
        with io.open(os.path.join(root, "dreaming", "kaggle_hooks", "fires.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id": "K01"}\n{"id": "K02"}\n')
        io.open(os.path.join(root, "dreaming", "RULES.md"), "w", encoding="utf-8").write("P0001 x\nP0002 y\nP0001 z\n")
        fam = [{"name": "paper", "id_regex": r"\bP\d{4}\b", "globs": ["dreaming/RULES.md"]}]
        rf = census(root, family_config=fam)
        r = census(root)
        checks = [
            rf["C_data_families"] == {"paper": 2} and rf["C_rules_total"] == 6,
            r["A_settings_commands"] == 1,
            r["A_dispatcher_entries"] == {"hook_dispatch_list.json": 2},
            r["B_hook_files"] == 4,
            r["C_reflex_rows"] == 2 and r["C_reflex_distinct_triggers"] == 1,
            r["C_rule_card_families"] == {"dreaming/kaggle_hooks": {"cards": 2, "distinct_triggers": 1}},
            r["C_data_families"] == {},
            r["C_rules_total"] == 4,
            r["D_fire_rows"] == 2,
        ]
        empty = census(os.path.join(tmp, "nothing"))
        checks.append(empty["B_hook_files"] == 0 and empty["C_rules_total"] == 0)
        print("selftest", "PASS" if all(checks) else "FAIL", checks)
        return 0 if all(checks) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.path.join(os.path.expanduser("~"), ".claude"))
    ap.add_argument("--codex", default=os.path.join(os.path.expanduser("~"), ".codex"))
    ap.add_argument("--compare")
    ap.add_argument("--families", help="JSON list of {name, id_regex, globs} for rules kept as data")
    ap.add_argument("--json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    fams = load_json(a.families) if a.families else None
    if a.families and not isinstance(fams, list):
        print("could not read a list of families from", a.families)
        return 2
    r = census(a.root, a.codex if os.path.isdir(a.codex) else None, a.compare, fams)
    text = json.dumps(r, indent=1)
    if a.json:
        io.open(a.json, "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
