"""Fresh recall test for the memory brain: owner-voice questions, gold memory slugs, hit@1 / hit@5 through brain.py recall.

Authored 2026-09-03 19:0x on the owner's ask "i want another test to see how it is doing" - the other half of
the campaign's object. Each question is worded the way he asks (short, his spelling left alone, no slug words),
and the gold is the memory that answers it. A miss is either recall ranking the wrong memory or the memory
being worded so that his question cannot find it - both are the brain's problem. Re-run for the trend.
"""
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# the Windows path off Windows made every gold look missing, n came out 0 and the gate crashed on it (Mac, 30 Oct)
MEM = os.environ.get("BRAIN_MEM_DIR") or (os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory") if os.name == "nt"
       else os.path.join(os.path.expanduser("~"), ".claude", "projects",
                         os.path.expanduser("~").replace("/", "-"), "memory"))

# The question bank is the user's own: questions worded the way they ask, golds are slugs in their memory folder.
# It lives in a JSON file next to this script (or EVAL_FRESH_BANK): {"bank": [[q, [slug, ...]], ...],
# "bank_v2_extra": [...]}. A fresh install has none, so the lists are empty and main() says how to add one.
BANK_FILE = os.environ.get("EVAL_FRESH_BANK") or os.path.join(HERE, "eval_fresh_bank.json")


def _load_bank(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return [], []
    rows = lambda key: [(q, list(g)) for q, g in d.get(key, [])]
    return rows("bank"), rows("bank_v2_extra")


BANK, BANK_V2_EXTRA = _load_bank(BANK_FILE)


TIMEOUT_MARK = "__RECALL_TIMEOUT__"


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted

# v2 (2026-09-03 20:1x): twenty-five more, same voice, to give the instrument power (one item of 25 is four points).
# Run with --v2 to score all fifty; the first twenty-five alone remain the v1 baseline (5/25 first, 15/25 top five
# before the note demotion; 6 and 18 after).
BANK_V2_EXTRA = [
    # 2026-09-11 evening: six questions on that week's memories, added AFTER the name-coverage boost was measured on
    # the 50 above (a bank that never grows overfits its levers); the gate's n is 56 from here on
    ("why did the math hook go quiet for a few days and how did we find it", ["the_math_hook_went_silent_for_four_days_three_gates_stacked_2026_09_11"]),
    ("the semantic recall was dark, what happened to the embeddings", ["semantic_lane_dark_two_days_encode_box_deleted_2026_09_11"]),
    ("what did the locate test say about the math brain", ["locate_the_fact_bank_l1_the_brain_answers_54_of_60_the_solver_alone_3_2026_09_11"]),
    ("is the closing rebalancer working after the one fill change", ["closing_rebalancer_verified_0911_after_first_full_one_fill_close_0910_2026_09_11"]),
    ("box 3 lanes go to which blob and when does the key expire", ["box3_lanes_ship_to_cncarchive26423_and_the_key_expires_0923_2026_09_10"]),
    ("why did the dream run all day yesterday", ["the_memory_brain_evening_frontmatter_cache_slug_field_and_the_dream_that_rewrote_its_whole_graph_2026_09_11"]),
    ("why did the hopfield capacity number in the bundle have to be fixed", ["an_authors_own_read_is_not_a_review_and_a_name_is_a_family_of_names_2026_09_03"]),
    ("how is the math brain doing on a fresh test", ["fresh_tests_of_the_math_brain_and_the_brain_2026_09_03"]),
    ("the 3cd deck, where did the median book come from", ["the_deck_is_cut_on_the_3cd_plateau_2026_09_03"]),
    ("what did the code audit of the 3cd simulation find", ["the_3cd_code_audit_2026_09_03"]),
    ("box 4 needs 16 cores, what vm size", ["box04_sixteen_cores_needs_easv5_and_scsi_2026_09_03"]),
    ("how do i read the azure bill, which line is which box", ["reading_his_azure_billing_pages_which_box_is_which_2026_09_03"]),
    ("the cpu load of the live trading stack", ["cpu_map_of_the_live_trading_stack_2026_09_03"]),
    ("how to check if the research boxes are running", ["runbook_are_the_research_boxes_running_2026_09_02"]),
    ("how do i use the uvix uvxy pair signal every day", ["how_to_use_the_uvix_uvxy_pair_daily_signal_2026_09_03"]),
    ("why does the drawdown under 3cd happen", ["the_drawdown_under_3cd_is_the_held_shorts_into_the_fall_2026_09_03"]),
    ("does the pre spike cut help", ["the_pre_spike_cut_buys_the_crash_week_2026_09_02", "pre_spike_beta_cut_reaches_the_drawdown_2026_09_02"]),
    ("when should beta be zero", ["when_beta_should_be_zero_2026_09_03"]),
    ("why did the gateways log out after the 3am restart", ["gateways_logged_out_after_the_0300_restart_and_box04_space_2026_09_03"]),
    ("the lanes archive filled box 4 disk, what happened", ["the_rotate_loop_tars_nobody_uploads_ate_box04_disk_2026_09_02"]),
    ("what is the biggest plateau under the current law", ["the_biggest_plateau_under_the_current_law_on_the_full_campaign_2026_09_02"]),
    ("how do i restore a lane series from the blob", ["runbook_restoring_archived_lane_series_from_the_blob_2026_09_02"]),
    ("the feeder thinks the lane is not done, the tombstone is zero bytes", ["feeder_done_test_must_accept_the_offloaders_zero_byte_tombstone_2026_09_02"]),
    ("half size for the first six months, does that fix the young book tail", ["half_size_for_six_months_takes_the_young_book_tail_away_2026_09_03"]),
    ("when does the insurance pay", ["the_insurance_pays_on_spikes_from_calm_not_on_the_crash_met_young_2026_09_03"]),
    ("spike pair size, where is the knee", ["spike_pair_size_knee_at_three_quarters_2026_09_03"]),
    ("what did the msys path rewrite break", ["msys_rewrites_posix_args_handed_to_native_launchers_2026_09_03"]),
    ("the trading box move to gce, where is it", ["trading_gce_built_and_staged_cutover_pending_2026_09_03"]),
    ("what does the version 3 book do differently from version 1", ["version_1_and_3_on_the_plateau_top20_and_median20_2026_09_03", "gist_the_version_3_night_2026_09_03"]),
    ("why did the deep itm pause skip the account solve", ["the_deep_itm_pause_skipped_the_whole_account_solve_2026_09_02"]),
    ("i hold an exclusive claim, do i have to read the inbox", ["scar_holding_a_box_claim_without_reading_the_inbox_2026_09_02"]),
]


TIMEOUT_MARK = "__RECALL_TIMEOUT__"


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def recall(q, k=8):
    # 600 s, not 180. Measured 2026-09-15: three runs of the SAME query at the SAME setting took
    # 19.7 / 84.0 / 189.7 s - a 9.6x spread on identical work. A 180 s cap sits inside that spread,
    # so an A/B arm died at random and the death was attributed to whichever dial was under test.
    # Matches the owner's 09-15 order that every hook gets 600 s for the same reason.
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "brain.py"), "recall", q], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=600).stdout
    except subprocess.TimeoutExpired:
        # A timeout is NOT a miss and NOT a crash. Scoring it as a miss blames the dial under test;
        # raising kills the whole bank mid-run. It is reported separately and left out of the rate.
        return [], TIMEOUT_MARK
    slugs = re.findall(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", out, re.M)
    return slugs[:k], out


def main():
    # 2026-09-11: `--help` used to run the whole 25-question evaluator (no argument parsing) - the fifth time a
    # script here executed work on a help flag. Help is inert; an unknown flag stops instead of falling through.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: eval_brain_recall_fresh.py [--v2]\n  --v2  BANK + BANK_V2_EXTRA; default is BANK alone")
        return 0
    unknown = [a for a in sys.argv[1:] if a not in ("--v2",)]
    if unknown:
        print("unknown argument(s): %s (try --help)" % " ".join(unknown), file=sys.stderr)
        return 2
    v2 = "--v2" in sys.argv
    bank = BANK + BANK_V2_EXTRA if v2 else BANK
    if not bank:
        print("no question bank at %s (write one: {\"bank\": [[question, [gold_slug]]]})" % BANK_FILE)
        return 2
    missing = [s for _, golds in bank for s in golds if not os.path.exists(os.path.join(MEM, s + ".md"))]
    if missing:
        print("gold slugs not on disk (fix the bank):", missing)
    rows = []
    t0 = time.time()
    for q, golds in bank:
        golds = [g for g in golds if os.path.exists(os.path.join(MEM, g + ".md"))]
        if not golds:
            continue
        slugs, raw = recall(q)
        if raw == TIMEOUT_MARK:
            rows.append({"q": q, "golds": golds, "top5": [], "rank": None, "timeout": True})
            continue
        rank = next((i + 1 for i, s in enumerate(slugs) if s in golds), None)
        rows.append({"q": q, "golds": golds, "top5": slugs[:5], "rank": rank})
    timeouts = [r for r in rows if r.get("timeout")]
    rows = [r for r in rows if not r.get("timeout")]
    n = len(rows)
    h1 = sum(1 for r in rows if r["rank"] == 1)
    h5 = sum(1 for r in rows if r["rank"] and r["rank"] <= 5)
    if not n:
        # 2026-10-30 (Mac): every question timed out under load, n was 0 and the summary line died on a
        # ZeroDivisionError, so the gate logged a traceback instead of the cause. Say it and fail.
        print("brain recall fresh bank: n=0 scored, %d of %d question(s) timed out - no rate (%.0fs)"
              % (len(timeouts), len(bank), time.time() - t0))
        return 1
    print(f"brain recall fresh bank: n={n}  hit@1 {h1}/{n} = {100.0*h1/n:.0f}%  hit@5 {h5}/{n} = {100.0*h5/n:.0f}%  ({time.time()-t0:.0f}s)")
    for r in rows:
        mark = "ok " if r["rank"] == 1 else ("~%d " % r["rank"] if r["rank"] else "MISS")
        print(f"  {mark} {r['q'][:62]:62s} -> {r['top5'][0][:60] if r['top5'] else '(nothing)'}")
    # BRAIN_EVAL_TAG keeps two arms of one A/B out of each other's output file. Without it both arms
    # write the same daily name, so arm 2 silently overwrites arm 1 - and an unrelated run earlier the
    # same day is lost too. Sanitised because it becomes a filename.
    tag = re.sub(r"[^A-Za-z0-9_.-]", "", os.environ.get("BRAIN_EVAL_TAG", ""))[:40]
    out = os.path.join(HERE, "state", "brain_recall_fresh_%s%s%s.jsonl"
                       % (time.strftime("%Y%m%d"), "_v2" if v2 else "", ("_" + tag) if tag else ""))
    with io.open(keep_old(out), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if timeouts:
        # Loud on purpose: a run with timeouts is not comparable with one without.
        print("TIMEOUTS: %d question(s) exceeded 600 s and are OUT of the rate above (not scored as misses):" % len(timeouts))
        for r in timeouts:
            print("  TIMEOUT", r["q"][:70])
    print("rows:", out)


if __name__ == "__main__":
    raise SystemExit(main())
