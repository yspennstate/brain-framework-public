"""Score reflex triggers on a development bank the way the hook matches them (28 Sep, claude-825b1305).

Each bank sentence is wrapped in four inputs the priming hook really sees - an Edit (new_string), a Write (content
mid-file), a Bash heredoc and a raw prompt - serialized with json.dumps(tool_input, ensure_ascii=False) for the tool
calls, and matched with re.findall(trigger, text, re.I) behind the required-literal prefilter derived for that very
trigger (instinct_anchors.anchors_for), as instinct_prime._fire does. A sentence counts as caught when its Edit form
fires; the other forms are reported so a trigger that only works in one serialization is visible.

    python reflex_dev_bank_eval.py [--bank BANK.json] [--cands CANDS.json] [--json OUT.json]

CANDS.json: {"<reflex id>": {"<name>": "<trigger>", ...}, ...}. The live trigger of each reflex is read from
instincts_learned.json and scored as "live".
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
BG = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
sys.path.insert(0, DR)
import instinct_anchors as IA  # noqa: E402

RUNLOG = os.path.expanduser("~/.claude/brain_graph/study/THREE_DAY_RUN_2026-09-27.md")
LEAD = "- 14:2x an earlier entry, unchanged."


def forms(prefix, sentence):
    sep = prefix if prefix else " "
    body = LEAD + sep + sentence
    J = lambda d: json.dumps(d, ensure_ascii=False)
    return {
        "edit": J({"file_path": RUNLOG, "old_string": LEAD, "new_string": body}),
        "write": J({"file_path": RUNLOG, "content": "# Run log\n\n" + body + "\n\n- 14:5x a later entry.\n"}),
        "bash": J({"command": "cat >> {{USERPROFILE}}/run.md <<'EOF'\n" + body + "\nEOF",
                   "description": "Append to the run log"}),
        "prompt": body,
    }


def matcher(trigger):
    anc = IA.anchors_for(trigger)
    table = {IA._key(trigger): anc} if anc else {}
    rx = re.compile(trigger, re.I)

    def fire(text):
        hit = bool(rx.findall(text))
        passed = IA.could_match(trigger, text.lower(), table)
        if hit and not passed:
            raise SystemExit("PREFILTER UNSOUND: anchors %r block a real match of %r" % (anc, trigger[:80]))
        return hit and passed
    return fire, anc


def score(bank_part, trigger):
    fire, anc = matcher(trigger)
    out = {"anchors": anc, "pos": [], "neg": []}
    for kind in ("positive", "negative"):
        for sentence, origin in bank_part[kind]:
            f = forms(bank_part.get("prefix", ""), sentence)
            got = {k: fire(v) for k, v in f.items()}
            out["pos" if kind == "positive" else "neg"].append({"s": sentence, "origin": origin, "fired": got})
    return out


def summary(res):
    p = sum(r["fired"]["edit"] for r in res["pos"])
    n = sum(r["fired"]["edit"] for r in res["neg"])
    per_form = {k: (sum(r["fired"][k] for r in res["pos"]), sum(r["fired"][k] for r in res["neg"]))
                for k in ("edit", "write", "bash", "prompt")}
    lived = [r for r in res["pos"] if r["origin"] == "lived"]
    return {"pos_caught": p, "pos_total": len(res["pos"]), "neg_fired": n, "neg_total": len(res["neg"]),
            "lived_pos_caught": sum(r["fired"]["edit"] for r in lived), "lived_pos_total": len(lived),
            "per_form_pos_neg": per_form}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default=os.path.join(BG, "study", "REFLEX_DEV_BANK_2026-09-28.json"))
    ap.add_argument("--cands", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    raw = io.open(a.bank, "rb").read()
    bank = json.loads(raw.decode("utf-8"))
    learned = json.load(io.open(os.path.join(DR, "instincts_learned.json"), encoding="utf-8"))
    live = {x["id"]: x["trigger"] for x in learned if "id" in x}
    cands, cands_sha = {}, None
    if a.cands:
        craw = io.open(a.cands, "rb").read()
        cands, cands_sha = json.loads(craw.decode("utf-8")), hashlib.sha256(craw).hexdigest()
    report = {"bank": os.path.basename(a.bank), "bank_sha256": hashlib.sha256(raw).hexdigest(),
              "cands": os.path.basename(a.cands) if a.cands else None, "cands_sha256": cands_sha, "reflexes": {}}
    for rid, part in bank.items():
        if not isinstance(part, dict) or "positive" not in part:
            continue
        triggers = {"live": live[rid]}
        triggers.update((cands.get(rid) or {}))
        report["reflexes"][rid] = {}
        for name, trig in triggers.items():
            res = score(part, trig)
            s = summary(res)
            report["reflexes"][rid][name] = {"summary": s, "trigger_sha256": hashlib.sha256(trig.encode()).hexdigest(),
                                             "missed": [r["s"] for r in res["pos"] if not r["fired"]["edit"]],
                                             "false": [r["s"] for r in res["neg"] if r["fired"]["edit"]],
                                             "form_disagree": [r["s"] for r in res["pos"] + res["neg"]
                                                               if len(set(r["fired"].values())) > 1]}
            print("%s / %s: positives %d/%d (lived %d/%d), negatives fired %d/%d; per form (pos,neg) %s; anchors %s" % (
                rid, name, s["pos_caught"], s["pos_total"], s["lived_pos_caught"], s["lived_pos_total"],
                s["neg_fired"], s["neg_total"], s["per_form_pos_neg"], res["anchors"]))
            if not a.quiet:
                for x in report["reflexes"][rid][name]["missed"]:
                    print("    missed:", x)
                for x in report["reflexes"][rid][name]["false"]:
                    print("    FIRED on negative:", x)
                for x in report["reflexes"][rid][name]["form_disagree"]:
                    print("    forms disagree:", x)
    if a.json:
        tmp = a.json + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        os.replace(tmp, a.json)


if __name__ == "__main__":
    main()
