#!/usr/bin/env python3
"""reflex_audit.py - which gut reflexes are DEAD (never match) or OVER-BROAD (match everything)?

The instinct layer has 38 reflexes, each a regex trigger that fires a felt reflex on a matching prompt
or tool call. Nothing measured whether a trigger actually matches the kind of thing the owner says: a
reflex whose trigger matches NO real prompt is dead weight (it can never fire - exactly the bug found
2026-07-09, where `measure_one_before_promising_n` had qualifier-spacing that stopped it matching any
ETA phrasing); one that matches a huge fraction is noise (it cries on everything, so it means nothing).

A reflex fires on TWO surfaces (instinct_prime hooks both): the prompt (UserPromptSubmit) AND the tool
command (PreToolUse) - so a trigger is tested against both. Testing a tool-triggered reflex (e.g.
figure_filename_clobber, which matches a savefig to figs/scaling.pdf) against prompts ALONE falsely
calls it dead - the owner never TYPES figure-clobber language (caught 2026-07-09: the first version of
this audit made exactly that error). So this replays each trigger against BOTH the prompt archive
(3k+ owner prompts) and a tool-command corpus (outcomes.jsonl cmd fields), and only calls a reflex DEAD
if it matches NEITHER - and, since 07-13, only if the live fire-log ALSO shows zero real fires: both
corpora are samples, and stamp_only_on_delivery proved a trigger can match 0 of each while having 58
logged live fires (it fires on Write/Edit code surfaces neither corpus holds). OVER-BROAD = matches >40% of prompts (cry-wolf on everything the owner says).
This is the #10 shortlist lever (per-reflex precision) on the data that now exists; the true
fired-and-really-dangerous precision still needs a durable fire-log.

Read-only. Fire-mode reflexes are the danger warnings; guide/carry are surfaced differently, excluded.
Usage: python reflex_audit.py
"""
import os, io, re, csv, json

DR = os.path.dirname(os.path.abspath(__file__))
INST = os.path.join(DR, "instincts.tsv")
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
OUTCOMES = os.path.join(DR, "outcomes.jsonl")


def load_owner_prompts(limit=4000):
    out = []
    try:
        for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("origin", "owner") == "owner" and r.get("text"):
                # The audit's question is whether a trigger matches the owner's PHRASING, but the
                # ledger also holds pasted documents (77 prompts >20k chars, max 228k as of 07-13),
                # and a super-linear trigger can burn 40+ CPU-minutes on one of them (hung the
                # 08:29 07-13 dream's post-steps at 100% core; the 03:30 run was still clean).
                # 4k chars keeps every real phrasing intact and bounds the whole replay to seconds.
                out.append(r["text"][:4000])
    except Exception:
        pass
    return out[-limit:]


def load_tool_commands():
    """A real tool-command corpus: the cmd field of every logged felt-error. Small, but it's the
    surface tool-triggered reflexes actually fire on, so it separates 'dead' from 'not prompt-shaped'."""
    out = []
    try:
        for ln in io.open(OUTCOMES, encoding="utf-8", errors="replace"):
            if ln.strip().startswith("{"):
                try:
                    c = json.loads(ln).get("cmd", "")
                    if c:
                        out.append(c)
                except Exception:
                    pass
    except Exception:
        pass
    return out


def main():
    try:
        rows = list(csv.reader(io.open(INST, encoding="utf-8", errors="replace"), delimiter="\t"))
    except Exception:
        print("reflex_audit: no instincts.tsv")
        return
    hdr, rows = rows[0], rows[1:]
    ci = {c: i for i, c in enumerate(hdr)}
    prompts = load_owner_prompts()
    tools = load_tool_commands()
    if len(prompts) < 200:
        print("reflex_audit: only %d owner prompts in the archive - too few to judge trigger coverage." % len(prompts))
        return
    n = len(prompts)

    # Live fire-log ground truth, read ONCE for both the dead-check and the mention/act section
    # below: a reflex with logged real fires is alive no matter what the corpora say. Both corpora
    # are samples of a bigger surface - stamp_only_on_delivery matched 0 prompts and 0 tool cmds
    # while holding 58 live fires (it fires on Write/Edit code text neither corpus contains). The
    # categorical fix is consulting the fires; growing the tool-token list is the treadmill that
    # already missed it once.
    from collections import Counter, defaultdict
    firelog = os.path.join(DR, "instinct_fires.tsv")
    ment, act = Counter(), Counter()
    # SINK-MATCH (added 2026-08-14): the act-proxy below buckets write+act together as
    # "on-referent", which is right for a reflex about taking any action and WRONG for one
    # whose referent is editing a particular file. ps1_hook_edit measured 72.9% `act` fires -
    # shell commands that merely NAME a .ps1 - and so scored as healthy while its heed lift
    # sat at x1.0 for four days. Keep the write targets so the referent can be checked.
    wr_paths = defaultdict(Counter)
    fire_total = 0
    _PATH = re.compile(r'"(?:file_path|path|notebook_path)"\s*:\s*"([^"]+)"')
    try:
        for ln in io.open(firelog, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                fire_total += 1
                (ment if p[2] in ("prompt", "read") else act)[p[3]] += 1
                if p[2] == "write" and len(p) > 4:
                    m = _PATH.search("\t".join(p[4:]))
                    if m:
                        wr_paths[p[3]][m.group(1)] += 1
    except Exception:
        pass
    fired = ment + act  # per-reflex total live fires

    dead, broad, ok, tool_only = [], [], [], []
    for r in rows:
        if len(r) <= ci["trigger"] or r[ci["mode"]] != "fire":
            continue
        rid, trig = r[ci["id"]], r[ci["trigger"]]
        try:
            rx = re.compile(trig, re.I)
        except Exception:
            dead.append((rid, "TRIGGER DOES NOT COMPILE"))
            continue
        hits = sum(1 for p in prompts if rx.search(p))
        tool_hits = sum(1 for c in tools if rx.search(c))
        rate = hits / n
        # A trigger targeting FILE PATHS or SHELL COMMANDS fires on tool calls, not prompts - the owner
        # never TYPES a savefig or a git push. Recognise those by tokens in the trigger source and never
        # assess them against the prompt corpus (they'd falsely read as dead - the figure_filename_clobber
        # case, 2026-07-09). The reliable prompt-side verdicts are over-broad (cry-wolf) and non-compile.
        tool_oriented = bool(re.search(r"\[/\\\\\]|savefig|\\\.\(?:pdf|png|svg|py|md|ps1|json|tsv|"
                                       r"jsonl|tex\)|git |Stop-Process|taskkill|robocopy|scp |ssh |"
                                       r"powershell|--force|push|deploy|rm -rf|Remove-Item", trig, re.I))
        if rate > 0.40:
            broad.append((rid, "%.0f%% match - over-broad, fires on nearly everything (no signal)" % (100 * rate)))
        elif tool_oriented and hits == 0:
            tool_only.append((rid, "tool-triggered (targets paths/commands) - fires on actions, not prompts"))
        elif hits == 0 and tool_hits == 0 and fired.get(rid, 0) > 0:
            tool_only.append((rid, "alive by fire-log: %d live fires on surfaces outside both corpora" % fired[rid]))
        elif hits == 0 and tool_hits == 0:
            dead.append((rid, "prompt-oriented but matches 0 of %d prompts + 0 tool cmds + 0 live fires - trigger likely broken" % n))
        elif hits == 0 and tool_hits > 0:
            tool_only.append((rid, "tool-triggered: %d command matches" % tool_hits))
        else:
            ok.append((rid, rate, hits))

    print("reflex_audit: %d fire-mode reflex triggers vs %d owner prompts + %d tool commands"
          % (len(dead) + len(broad) + len(ok) + len(tool_only), n, len(tools)))
    print("  healthy prompt-match band [0%%<rate<=40%%]: %d reflexes | tool-triggered (fire on commands): %d"
          % (len(ok), len(tool_only)))
    if dead:
        print("  BROKEN (trigger can't match even its own scar - fix it, the danger is real):")
        for rid, why in dead:
            print("    %-30s %s" % (rid, why))
    if tool_only:
        print("  rare/tool-triggered (works, just doesn't show in prompt+error corpora):")
        for rid, why in tool_only:
            print("    %-30s %s" % (rid, why))
    if broad:
        print("  OVER-BROAD (tighten the trigger - it means nothing if it always fires):")
        for rid, why in broad:
            print("    %-30s %s" % (rid, why))
    if ok:
        lo = sorted(ok, key=lambda x: x[1])
        print("  healthiest (rare, so meaningful when they fire): "
              + ", ".join("%s %.0f%%" % (rid, 100 * rate) for rid, rate, _ in lo[:5]))
    print("  read: a DEAD trigger can never protect (it's the false-comfort of a guard that can't fire); "
          "an OVER-BROAD one is cry-wolf. Both are trigger fixes, not reflex retirements - the SCAR behind "
          "each reflex is real; it's the pattern that's wrong.")

    # --- MENTION-vs-ACT false-fire rate (the 2026-07-09 dream's named frontier: "make the fire worth
    # obeying - four of five fires were false, all vocabulary"). instinct_prime now logs each real fire
    # with an act-proxy (prompt/read = MENTION, write/act = ACT). A fire-mode danger reflex firing mostly
    # on MENTIONS (prose, or a read-only audit that merely quotes the vocabulary) is the cry-past-it class.
    total = fire_total  # ment/act already built from the fire-log read above
    if total >= 40:
        print("\n  MENTION-vs-ACT (durable fire-log, %d fires): a danger reflex firing mostly on MENTIONS "
              "(prompt/read) is teaching its reader to click past it - key it on the ACT/SINK instead." % total)
        worst = sorted(((rid, ment[rid], act.get(rid, 0)) for rid in set(ment) | set(act)),
                       key=lambda t: -(t[1] / max(1, t[1] + t[2])) * (t[1] + t[2]))
        for rid, m, a in worst[:6]:
            r = m / max(1, m + a)
            print("    %-30s %3d fires  %.0f%% on mentions%s" % (rid, m + a, 100 * r,
                  "  <- act-key this trigger" if r > 0.7 and (m + a) >= 4 else ""))

        # SINK-MATCH: for a reflex whose referent is EDITING a file, an `act` fire that only
        # mentions the path is as off-referent as a read. Score those against the SINK - did a
        # write actually land on a file the trigger itself matches? Ranked by rate, not volume:
        # the ranking above is -(rate*total) = -mentions, so a low-volume, all-noise reflex can
        # never surface no matter how dead it is. That is how ps1_hook_edit stayed filed under
        # "healthiest" while heed_trend called it decoration.
        sink = []
        trigmap = {r[ci["id"]]: r[ci["trigger"]] for r in rows
                   if len(r) > ci["trigger"] and r[ci["mode"]] == "fire"}
        # ONLY reflexes whose trigger actually names a FILE are in this population. The first
        # version of this block scored every reflex with >=4 write fires and printed a wall of
        # 0.0% "sink-key this trigger" against broker_truth, after_hours_is_live and other
        # purely conceptual reflexes - for those, "did the write land on a file the trigger
        # matches" is a category error, and a detector that answers it fires wrongly on almost
        # everything. That is the cry-wolf failure this whole section exists to find, so the
        # population is gated on the trigger naming an extension (\.ps1, settings\.json, ...).
        # The backslash is REQUIRED. A first cut used \\?\. (optional) and matched every regex
        # wildcard dot followed by letters, so concurrent_races / secrets_in_git / heavy_compute_remote
        # all qualified and printed "sink-key this trigger" at 0.0%. In these triggers a literal
        # file extension is always written escaped (\.ps1, \.vbs, settings\.json), and a bare . is
        # a wildcard - that one character is the whole difference between the two populations.
        PATHY = re.compile(r'\\\.[a-z0-9]{2,5}\b')
        for rid, paths in wr_paths.items():
            trig = trigmap.get(rid)
            if not trig or not PATHY.search(trig):
                continue
            tot = sum(paths.values())
            if tot < 4:
                continue
            try:
                hit = sum(c for pth, c in paths.items() if re.search(trig, pth, re.I))
            except re.error:
                continue
            fires = ment.get(rid, 0) + act.get(rid, 0)
            sink.append((hit / max(1, fires), rid, hit, tot, fires))
        # THE CONTROL FOR THE GATE ABOVE, run every time and able to FAIL. Two earlier versions
        # of this block shipped wrong and were caught only by reading the output: one scored every
        # reflex regardless of referent, one truncated the list and hid the very reflex it was
        # built for. An audit section without a control is the thing this file exists to warn about.
        _POS = [r"(\.ps1\b|wake_brain|sessionstart|\.vbs\b|launcher\b|settings\.json)",  # ps1_hook_edit
                r"\b(\.env|\.pem|id_rsa)\b"]                                             # a real path trigger
        _NEG = [r"\b(sim|backtest|equity[_ ]?curve|sharpe)\b",       # conceptual: bare . absent
                r"after.hours|broker.truth"]                          # wildcard dots, NOT extensions
        _pf = [bool(PATHY.search(t)) for t in _POS]
        _nf = [bool(PATHY.search(t)) for t in _NEG]
        if all(_pf) and not any(_nf):
            print("\n  sink-match gate control: PASS (%d/%d path-referent triggers matched, "
                  "%d/%d conceptual triggers correctly skipped)"
                  % (sum(_pf), len(_POS), len(_NEG) - sum(_nf), len(_NEG)))
        else:
            print("\n  sink-match gate control: FAIL - pos=%s neg=%s. The population gate is wrong, "
                  "so IGNORE the table below rather than acting on it." % (_pf, _nf))

        # AND THE AXIS THAT ACTUALLY MATTERS: the gate control above proves the REGEX separates two
        # populations, which is not the same as proving the reflex I built this for REACHES the table.
        # Both earlier versions passed a regex-shaped check and still hid ps1_hook_edit - once behind
        # a population that was too broad, once behind a head slice. So name it and say plainly
        # whether it arrived.
        _known = [rid for _, rid, _, _, _ in sink]
        if wr_paths.get("ps1_hook_edit"):
            print("  reference case ps1_hook_edit: %s"
                  % ("PRESENT in the table below" if "ps1_hook_edit" in _known else
                     "MISSING despite having write fires - the population gate or the cutoff is "
                     "hiding it, which is the exact failure this section was built to fix"))

        if sink:
            print("\n  SINK-MATCH (write-referent reflexes): of ALL fires, how many were a write "
                  "landing on a file the trigger itself matches? A low rate here means the reflex "
                  "is answering a question nobody asked - the reader is right to skip it.")
            # Print the WHOLE qualifying population, never a head slice. The act-proxy table
            # above truncates at 6 ranked by absolute mentions, and that cutoff is exactly why
            # ps1_hook_edit was invisible for four days while heed_trend called it decoration.
            # A cutoff here would reproduce the same blindness one measure further down.
            for rate, rid, hit, tot, fires in sorted(sink):
                print("    %-30s %5d fires  %4d writes  %3d on-referent  = %.1f%%%s"
                      % (rid, fires, tot, hit, 100 * rate,
                         "  <- sink-key this trigger" if rate < 0.10 else ""))
    else:
        print("\n  MENTION-vs-ACT: only %d logged fires - accrues via instinct_prime's fire-log; re-run "
              "at >=40 to measure the false-fire rate the dream named (act-key the mention-heavy triggers)." % total)


if __name__ == "__main__":
    main()
