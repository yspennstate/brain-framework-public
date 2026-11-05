#!/usr/bin/env python3
"""mojibake.py - undo cp1252 double-encoding in text that reached us already mangled.

Measured 2026-08-22: 775 of 18,629 prompt-ledger records (4.16%) carry mojibake - an em-dash
stored as the three characters 'a-circumflex, euro, em-dash' instead of one. 685 of them arrived
through the LIVE hook, 592 in 2026-08 alone. prompt_archive.py itself is clean (fed a real em-dash
on stdin it archives a real em-dash), so the corruption happens upstream, before the hook sees the
prompt. We cannot fix upstream; we can refuse to store the damage.

It matters because his prompts are the substrate the brain retrieves over. A mangled dash splits
one token into three junk tokens, so BM25 and every keyword rule silently score against garbage.

THE REPAIR is the inverse of the damage: UTF-8 bytes were read as cp1252 and re-encoded as UTF-8,
so encoding back to cp1252 and decoding as UTF-8 recovers the original. It is applied only when

  1. the text contains a known mojibake marker (never touch text that looks fine),
  2. the round trip is lossless in both directions - repair(x) must re-mangle back to exactly x,
  3. the repair actually removes markers rather than moving them around.

Anything that fails a check is left alone. A prompt is his words; a speculative rewrite of them is
worse than a visible mangle.
"""
import re

# The high-frequency shapes: an 'A-circumflex/A-tilde' lead byte followed by the second byte of a
# UTF-8 sequence is what cp1252 makes of ordinary punctuation and accents.
_MARKERS = re.compile(u"[\u00c2\u00c3\u00e2][\u0080-\u00bf\u201a-\u20ac\u2020-\u2122]")


def looks_mangled(s):
    return bool(s) and bool(_MARKERS.search(s))


def repair(s):
    """Return the un-mangled string, or the input unchanged if the repair is not provably safe."""
    if not looks_mangled(s):
        return s
    try:
        fixed = s.encode("cp1252", "strict").decode("utf-8", "strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    # must round-trip back to exactly what we were given, or we are guessing
    try:
        if fixed.encode("utf-8").decode("cp1252", "strict") != s:
            return s
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    if len(_MARKERS.findall(fixed)) >= len(_MARKERS.findall(s)):
        return s
    return fixed


def repair_deep(s, rounds=3):
    """Text mangled twice needs unwinding twice. Stops as soon as a round changes nothing."""
    for _ in range(rounds):
        n = repair(s)
        if n == s:
            return s
        s = n
    return s
