#!/usr/bin/env python3
"""ern_labels.py - label the ERN outcomes corpus with reflex families, so miss
budgets extend from named owner corrections to ALL logged real failures.

The outcomes ledger (dreaming/outcomes.jsonl, error_monitor.py) holds every logged
failure signature with the command text that produced it. ern_learn.py aggregates
shapes; nothing maps events to the reflex FAMILY that should have fired - without
that label the miss budget (miss_budget.py) covers only the ~80 owner-named
corrections. This produces ern_labels.tsv with stated coverage.

METHOD, two sources, both conservative:
  rule - definitional signature/content maps only, where the failure IS the family:
         mojibake -> utf8_text_surgery; leaked secret -> secrets_in_git;
         implausible magnitude -> sim_flatters (risk_vs_measured_record secondary);
         ssh + eaten backslash escapes -> the ssh-backslash-eater family;
         a backslashed C:\\ path inside a bash command -> the drive-path family.
         No stretching: a generic traceback or non-zero exit is NOT evidence of any
         existing family and stays unlabeled rather than being force-fitted.
  hand - a stratified sample of the two big generic strata (non-zero exit,
         traceback), read by a person/agent and labeled family-or-none in the HAND
         table below (ts-keyed, reviewable, versioned with this file).

Unlabeled generic events are exactly the candidate-NEW-reflex gap ERN_LEARNING.md
already names (x127 non-zero exits etc.) - the honest statement is that they have
no existing family, not that they are covered.

Usage:
  python ern_labels.py            print rule coverage + the hand-label sample
  python ern_labels.py --write    write ern_labels.tsv (rules + HAND entries)
"""
import io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gfcore

OUTCOMES = os.path.join(DR, "outcomes.jsonl")
OUT = os.path.join(HERE, "ern_labels.tsv")

RULES = (
    # (test on (sig_lower, tool_lower, cmd), label, secondary, why)
    (lambda s, t, c: "mojibake" in s, "utf8_text_surgery", "", "mojibake IS the encoding family"),
    (lambda s, t, c: "leaked secret" in s, "secrets_in_git", "", "secret exposure IS the family"),
    (lambda s, t, c: "implausible magnitude" in s, "sim_flatters", "risk_vs_measured_record",
     "implausible-number signature is the sim-vs-lived family"),
    (lambda s, t, c: ("ssh" in c.lower() and re.search(r"sed\s+.?s/\\+r|\\\\r|\\r\$//", c) is not None),
     "ssh_backslash_escape_eater", "", "ssh + CR-strip escapes: the quoting-eater family"),
    (lambda s, t, c: re.search(r'cd\s+"?C:\\', c) is not None and t == "bash",
     "bash_drive_path", "", "C:\\ path inside a bash command: the drive-path family"),
)

# token-like runs must never be copied into the label file (the outcomes ledger holds
# raw commands; nine events carry a leaked-secret signature) - mask before excerpting
_SECRETISH = re.compile(r"([A-Za-z0-9+/_\-]{24,}|(?i:key|token|secret|passw\w*)\s*[=:]\s*\S+)")


def safe_head(sig, cmd, n=80):
    if "leaked secret" in sig:
        return "[cmd withheld - leaked-secret signature]"
    head = re.sub(r"[\t\r\n]+", " ", cmd)
    return _SECRETISH.sub("[masked]", head)[:n]

# HAND labels: ts -> (label_or_none, note). Filled 2026-07-23 from reading the pass-1
# sample (33 events: 15 non-zero-exit, 15 traceback, 3 traceback+non-zero-exit).
# 'none' = read and judged generic: synthetic probes (python /nope/x.py), dep checks
# doing their job, transient infra, unattributable empty commands. Only entries that
# ATTACH to a family are listed; sampled ts absent here were judged none.
HAND = {
    "1783538617.1327195": ("bash_drive_path", "scp with backslashed C:\\ path in bash"),
    "1783541809.4280841": ("bash_drive_path", "ls -t quoted backslashed C:\\ path in bash"),
    # sample coverage statement: 33/507 generic events hand-read (6.5%); 2/33 (6%)
    # attached to an existing family; the rest are the candidate-new-reflex gap
    # ERN_LEARNING already names (its x127/x115 strata are real generics).
}
HAND_SAMPLED_NONE = 31   # judged-none count from the same pass, for coverage math


def load():
    rows = []
    for ln in io.open(OUTCOMES, encoding="utf-8", errors="replace"):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        sig = str(r.get("sig", "")).lower()
        cmd = str(r.get("cmd", "") or "")
        rows.append((str(r.get("ts", "")), sig, str(r.get("tool", "")), cmd))
    return rows


def stratum(sig):
    keys = []
    for k in ("mojibake", "leaked secret", "implausible magnitude", "syntaxerror",
              "traceback", "non-zero exit"):
        if k in sig:
            keys.append(k.replace(" ", "-"))
    return "+".join(keys) or "other"


def rule_label(sig, tool, cmd):
    for test, label, secondary, why in RULES:
        try:
            if test(sig, (tool or "").lower(), cmd):
                return label, secondary, why
        except Exception:
            continue
    return None


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    write = "--write" in sys.argv
    rows = load()
    model = gfcore._model()
    print("outcomes: %d events" % len(rows))
    labels = []       # (ts, stratum, label, secondary, source, in_model, cmd_head)
    unlabeled = {}
    for ts, sig, tool, cmd in rows:
        st = stratum(sig)
        rl = rule_label(sig, tool, cmd)
        head = safe_head(sig, cmd)
        if rl:
            label, secondary, _why = rl
            labels.append((ts, st, label, secondary, "rule", label in model, head))
        elif ts in HAND:
            hl, _note = HAND[ts]
            if hl and hl != "none":
                labels.append((ts, st, hl, "", "hand", hl in model, head))
            else:
                labels.append((ts, st, "", "", "hand-none", False, head))
        else:
            unlabeled.setdefault(st, []).append((ts, head))

    by_label = {}
    for _ts, _st, label, _sec, src, _im, _h in labels:
        if label:
            by_label[label] = by_label.get(label, 0) + 1
    print("\nRULE+HAND labels: %d events labeled to a family, %d hand-judged generic"
          % (sum(1 for l in labels if l[2]), sum(1 for l in labels if l[4] == "hand-none")))
    for label, n in sorted(by_label.items(), key=lambda kv: -kv[1]):
        print("  %-28s %d  (in model: %s)" % (label, n, "yes" if label in model else "NO"))
    print("\nunlabeled generic strata (the candidate-new-reflex gap, per ERN_LEARNING):")
    for st, evs in sorted(unlabeled.items(), key=lambda kv: -len(kv[1])):
        print("  %-40s %d" % (st, len(evs)))
    total = len(rows)
    lab = sum(1 for l in labels if l[2])
    hand_none = sum(1 for l in labels if l[4] == "hand-none") + HAND_SAMPLED_NONE
    print("\ncoverage: %d/%d events family-labeled (%.0f%%); %d generic events hand-read "
          "and judged none; %d generic unread (the stated boundary of this pass)"
          % (lab, total, 100.0 * lab / max(1, total), hand_none,
             total - len(labels) - HAND_SAMPLED_NONE))

    if not write:
        print("\nHAND-LABEL SAMPLE - first 15 of each big generic stratum; read each, then "
              "fill HAND{ts: (label|'none', note)} in this file and rerun --write:")
        for st in ("non-zero-exit", "traceback", "traceback+non-zero-exit"):
            evs = unlabeled.get(st, [])[:15]
            if not evs:
                continue
            print("\n  stratum %s (showing %d):" % (st, len(evs)))
            for ts, head in evs:
                print("    %s | %s" % (ts, head))
        return 0

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("ts\tstratum\tlabel\tsecondary\tsource\tin_model\tcmd_head\n")
        for ts, st, label, sec, src, im, head in labels:
            f.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (ts, st, label, sec, src, im, head))
    print("\nwrote %s (%d rows)" % (OUT, len(labels)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
