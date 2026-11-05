#!/usr/bin/env python3
"""One place that knows how to read a served math-brain block.

WHY THIS EXISTS. On 2026-09-13 I compressed the block - dropped "source-id:" from the card label, dropped
", score N.N" from the header, folded the id onto the hint line - and broke four evaluators across three
parsers. Every one failed by reading ZERO rather than raising: own-result-card 0 of 30, served-theorem 0 of
15, SILENT 15 of 15, and a 64-character hex id being read as content numbers, all on a hook that was serving
the right card first, correctly. The nightly gate runs those four, so it would have reported a healthy brain
as broken. A peer found it and patched all four; the real defect was that four files each owned a private
regex against prose nobody had promised to keep.

So: consumers import this instead of writing their own pattern, and when the block's wording changes again -
it will - this file changes once and every instrument follows. The functions below parse STRUCTURE that
survives rewording (the id's shape, the kind bracket) and accept BOTH the pre- and post-compression forms, so
historical captures stay comparable with today's.

    from math_block_parse import iter_cards, served_ids, block_is_silent

    for card in iter_cards(block):
        card.node_id   # "claim_<64 hex>"    - always, both formats
        card.title     # the card's title
        card.kind      # "theorem", "definition", "concept", ...
        card.text      # the statement text as served (possibly truncated)
        card.hint      # the trust tag
"""
import re
from collections import namedtuple

Card = namedtuple("Card", "node_id title kind text hint")

# The id is the one token with a shape rather than a wording. Everything else is prose.
ID = r"claim_[0-9a-f]{16,}"

# Card header, both generations:
#   new (2026-09-13+):  "- <title> [<kind>]: <text>"
#   old:                "- <title> [<kind>, score 12.3]: <text>"
_HEAD = re.compile(r"^- (?P<title>.+?) \[(?P<kind>[^\]]*?)\]: (?P<text>.*)$", re.M)

# Id-bearing line, both generations:
#   new:  "    (claim_abc... | EXCERPT, incomplete; lemma +proof; draft; unreviewed; unverified)"
#   old:  "    (source-id: claim_abc...)" followed by "    (hint: ...)"
_IDLINE = re.compile(r"^\s*\((?:source-id:\s*)?(?P<id>" + ID + r")(?:\s*\|\s*(?P<hint>.*?))?\)\s*$", re.M)
_HINTLINE = re.compile(r"^\s*\(hint:\s*(?P<hint>.*?)\)\s*$", re.M)


def block_is_silent(block):
    """True when the hook served nothing. An empty block is a real outcome, not a parse failure."""
    return not (block or "").strip()


def iter_cards(block):
    """Every card in a served block, oldest and newest format alike.

    Pairs each card header with the next id-bearing line after it, which is the structure both generations
    share. A card whose id line is missing is still yielded, with node_id None - a caller counting served
    ids should skip those rather than crash, and a caller counting CARDS should not.
    """
    block = block or ""
    heads = list(_HEAD.finditer(block))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(block)
        tail = block[h.end():end]
        m = _IDLINE.search(tail)
        hint = (m.group("hint") if m else None) or ""
        if m and not hint:
            hm = _HINTLINE.search(tail)
            hint = hm.group("hint") if hm else ""
        # the old format put the truncated statement on the header line too, so text is taken the same way
        yield Card(node_id=(m.group("id") if m else None),
                   title=h.group("title").strip(),
                   kind=(h.group("kind") or "").split(",")[0].strip().lower(),
                   text=h.group("text").strip(),
                   hint=hint.strip())


def served_ids(block):
    """The node ids the block actually served, in order. This is what 'did the brain serve X' should ask."""
    return [c.node_id for c in iter_cards(block) if c.node_id]


def card_count(block):
    return sum(1 for _ in iter_cards(block))


def card_is_complete(card):
    """A card that reached the session whole: it names its source (a claim id) and carries its trust tag.

    iter_cards yields a header whose id line is missing (node_id None) or whose tag is empty, so a caller counting
    cards can see it. A caller scoring what the brain DELIVERED must count such a card as a delivery failure, never
    as a hit or a served title: until 30 Sep the hook-path evaluator scored a header-only card as a theorem hit and
    the health check passed eleven header-only blocks (account04/B07, B07-R79-INCOMPLETE-CARD-PASSES-REGRESSION-GATES).
    """
    return bool(card.node_id) and bool(card.hint)


NO_CARD = "no readable card in a non-empty block"
INCOMPLETE_CARD = "incomplete math card"


def delivery_problem(block):
    """'' when the block is silent or every card in it is complete, else a stable reason: NO_CARD (the block has text
    but no card the parser can read - the 13 Sep format change read as silence in four evaluators) or INCOMPLETE_CARD
    (a card without its id or its trust tag). Silence is an outcome; either reason is a failure of delivery."""
    if block_is_silent(block):
        return ""
    cards = list(iter_cards(block))
    if not cards:
        return NO_CARD
    if not all(card_is_complete(c) for c in cards):
        return INCOMPLETE_CARD
    return ""


def _selftest():
    """Positive controls for BOTH formats - and the family, not one member of it.

    The scar this file exists for was made worse by a positive control that covered one of four files and
    passed. So both generations are exercised here, and a caller that imports this gets the whole family
    checked at once by running `python math_block_parse.py`.
    """
    new = ("[math-brain] leads, not verdicts - check each result's hypotheses.\n"
           "- Maximal ergodic theorem [lemma]: Let $(X,\\mathcal B,\\mu)$ be a probability space...\n"
           "    (claim_4e5da40826ced68e04e188001f421a2ff04ef2165ef899ef0c91ed9c71f6fc4c | EXCERPT, incomplete; lemma +proof; draft; unreviewed; unverified)\n"
           "- Tight family of measures [definition]: Let $(S,d)$ be a metric space...\n"
           "    (claim_6b379f9f39b8f899e353d04b96592d438754cd075fffcd3c5f233e7113013891 | definition lead; draft; unreviewed; unverified)\n")
    old = ("[math-brain] the math brain holds material that may bear on this - a hint, not a verdict.\n"
           "- Maximal ergodic theorem [lemma, score 33.5]: Let $(X,\\mathcal B,\\mu)$ be a probability space...\n"
           "    (source-id: claim_4e5da40826ced68e04e188001f421a2ff04ef2165ef899ef0c91ed9c71f6fc4c)\n"
           "    (hint: SOURCE EXCERPT: incomplete statement; lemma with a proof text on file; unverified)\n")
    ok = True
    for label, blk, want_kind in (("new", new, "lemma"), ("old", old, "lemma")):
        cards = list(iter_cards(blk))
        if not cards:
            print("FAIL %s: parsed zero cards" % label); ok = False; continue
        c = cards[0]
        checks = [("id", c.node_id and c.node_id.startswith("claim_")),
                  ("title", c.title == "Maximal ergodic theorem"),
                  ("kind", c.kind == want_kind),
                  ("text", c.text.startswith("Let $(X")),
                  ("hint", "unverified" in c.hint)]
        for name, good in checks:
            if not good:
                print("FAIL %s: %s -> %r" % (label, name, getattr(c, name if name != "id" else "node_id")))
                ok = False
    if len(list(iter_cards(new))) != 2:
        print("FAIL new: expected 2 cards, got %d" % len(list(iter_cards(new)))); ok = False
    if not block_is_silent("") or block_is_silent(new):
        print("FAIL: silence detection"); ok = False
    # prose that merely looks like a card parses as one - it is the card shape, noted so nobody 'fixes' it into a false
    # negative - but it is not a COMPLETE card, so a scorer never counts it as served
    shaped = "[math-brain] leads, not verdicts.\n- see the theorem [it is in the appendix]: for details\n"
    if [card_is_complete(c) for c in iter_cards(shaped)] != [False] or delivery_problem(shaped) != INCOMPLETE_CARD:
        print("FAIL: a card-shaped line with no id line counts as complete"); ok = False
    # completeness, both generations and each way a card can lose its tail (B07-R79)
    header_only = new.split("\n    (claim_4e5d")[0] + "\n"
    no_hint = new.replace(" | EXCERPT, incomplete; lemma +proof; draft; unreviewed; unverified", "")
    old_no_hint = old.replace("    (hint: SOURCE EXCERPT: incomplete statement; lemma with a proof text on file; unverified)\n", "")
    for label, blk, want in (("new", new, ""), ("old", old, ""), ("silent", "", ""),
                             ("header only", header_only, INCOMPLETE_CARD), ("id without hint", no_hint, INCOMPLETE_CARD),
                             ("old id without hint", old_no_hint, INCOMPLETE_CARD),
                             ("no card", "[math-brain] leads, not verdicts.\nsomething else\n", NO_CARD)):
        if delivery_problem(blk) != want:
            print("FAIL delivery_problem(%s) -> %r, wanted %r" % (label, delivery_problem(blk), want)); ok = False
    print("selftest: %s (new %d cards, old %d cards)"
          % ("PASS" if ok else "FAIL", card_count(new), card_count(old)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
