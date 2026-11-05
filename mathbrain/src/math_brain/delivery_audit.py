"""How much of what the store holds actually survives to the injection block?

A node can be retrieved, selected, and still arrive useless. The injection budget is a few
hundred characters per node, the Assumes clause is appended whole after the statement is cut,
and whatever sits past the cut is simply not delivered. So a node whose point is a value - a
count, a constant, a bound, an identity - loses its point if that value is not near the front.

This was not hypothetical. On 2026-08-30 two thousand enumeration nodes were ingested opening
with provenance and stating their value at character 240; the value reached the rendered block
in 0 of 8 probed packets. Rewriting the statement to lead with the value took the same items to
7 of 8. Nothing about retrieval or selection changed.

This audits the whole store for that failure at once, so the next instance is found before it
costs a run rather than after. It measures DELIVERY, not storage: for every node it renders at
the live budget and asks whether the numbers in the body survive.

    python -m math_brain.delivery_audit
    python -m math_brain.delivery_audit --budget 320 --worst 15
    python -m math_brain.delivery_audit --json

What the numbers cover. The main rate counts only ASSERTED values: a positive number of two or
more digits stated as a result (see the pattern below). Signed values, single digits, decimals
below ten, fractions, scientific notation and bounds are counted on a separate BROAD line, so the
main rate stays comparable with every earlier run. A node whose body states no value either
pattern reads (prose, a symbolic result such as O(n log n)) is NOT EVALUATED - neither a pass nor
a failure. A value surviving as a string says nothing about whether its hypotheses, sign
conventions or quantifiers survived with it, and rendering is not use by the receiver. Every node
is rendered with no query, so the text is the head of the body; the live hook serves the passage
that matches the question, so these rates are delivery when the question does not point at the
value.

The store is opened read-only. A missing, unreadable or unsupported store is an error (exit 2)
and nothing is created; an empty store (exit 3) and a store with nodes but none in scope (exit 4)
are reported as such, never as an audit that passed. Exit 0 means the main rate was measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from math_brain.rendering import claim_hypotheses, claim_view, render_parts
from math_brain.store import STORE_SCHEMA_VERSION, ResearchStore, StoreSchemaError

# Two directories up from this file is the checkout root, so the default store
# resolves in any clone without a home directory appearing in the source.
STORE = Path(os.environ.get(
    "MATH_BRAIN_STORE",
    str(Path(__file__).resolve().parents[2] / "local" / "population" /
        "brain.sqlite3")))

EXIT_MEASURED, EXIT_CANNOT_OPEN, EXIT_EMPTY, EXIT_NONE_IN_SCOPE = 0, 2, 3, 4

# An ASSERTED value, not any number. The first version of this counted every two-digit run in
# the body and reported that 3.0 percent of nodes delivered all of them - a number that says
# nothing, because bodies are long, the budget is 320 characters, and most of what it counted
# was arXiv identifiers and publication years. A citation year is not a value a node exists to
# carry, and losing it past the cut is not a delivery failure.
#
# So: match only a value the node states as a result - "is exactly 1739", "= 1739", "equals
# 1739", "is 1739". That is the thing whose loss makes the node useless, and it is the thing
# that was actually lost on the enumeration nodes.
ASSERTED = re.compile(
    r"(?:\b(?:is|are|was|were|equals?|gives?|yields?|returns?)\s+"
    # The trailing guard must not reject a sentence-final value. An earlier (?![\w.]) here
    # excluded every "... is exactly 1739." - the period disqualified the match - so the store
    # that HAD the defect reported only one node asserting a value at all, and the negative
    # control silently proved nothing. Caught by running the control, not by reading the code.
    r"(?:exactly\s+|precisely\s+)?|=\s*)(\d{2,}(?:\.\d+)?)(?!\d)")
ASSERTED_CONTRACT = ("a positive value of two or more digits, optionally with a decimal part, "
                     "after is/are/was/were/equals/gives/yields/returns (optionally 'exactly' or "
                     "'precisely') or after '='")

# The same idea over the representations ASSERTED leaves out (interactive audit IA-20260929-MB-05):
# a sign (-, U+2212, +), one digit, a decimal, a fraction, scientific notation or a power of ten,
# and a bound introduced by <, >, <=, >=, U+2264 or U+2265 or by 'at most' / 'at least'. An arrow
# (->, =>) and an inequality (!=) are not a stated value.
BROAD = re.compile(
    r"(?:\b(?:is|are|was|were|equals?|gives?|yields?|returns?)\s+"
    r"(?:exactly\s+|precisely\s+|at\s+most\s+|at\s+least\s+)?"
    r"|(?:(?<![-=<>!])(?:<=|>=|<|>)|(?<![<>!=])=|\u2264|\u2265)\s*)"
    r"([-\u2212+]?"
    r"(?:\d+(?:\.\d+)?[eE][-+\u2212]?\d+"
    r"|(?:\d+(?:\.\d+)?\s*(?:x|\u00d7|\\times)\s*)?10\^\{?[-\u2212]?\d+\}?"
    r"|\d+\s*/\s*\d+"
    r"|\d*\.\d+"
    r"|\d+))(?!\w)")
BROAD_CONTRACT = ("a value after the ASSERTED verbs (also 'at most' / 'at least'), after '=', "
                  "or after <, >, <=, >=, U+2264, U+2265; signed or not; an integer of any "
                  "length, a decimal, a fraction a/b, scientific notation, or a power of ten")


# A delivered number says nothing about the conditions it holds under (30 Sep 2026,
# 1000-page report D10: "grade sign, domain, quantifier ... not only retained numbers").
# This line counts the claims that list hypotheses and asks whether every one of them
# reaches the rendered text.
ASSUMPTIONS_CONTRACT = ("a claim with at least one hypothesis among its own assumptions "
                        "(every assumption but the ledger's record lines, "
                        "rendering.claim_hypotheses) counts as delivered when every "
                        "hypothesis, whitespace-normalised, appears in the text rendered "
                        "at the budget")


def broad_form(value: str) -> str:
    """The representation class of one BROAD value."""
    v = value.lstrip("-\u2212+")
    if "^" in v or "e" in v.lower():
        return "scientific or power of ten"
    if "/" in v:
        return "fraction"
    if "." in v:
        return "decimal"
    return "integer, one digit" if len(v) == 1 else "integer, two or more digits"


def population_digest(nodes: list) -> str:
    """One hash over every node's id and content digest: a result names the population it read."""
    h = hashlib.sha256()
    for node_id, digest in sorted((n.id, n.digest()) for n in nodes):
        h.update(f"{node_id}\t{digest}\n".encode())
    return h.hexdigest()


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _claim_parts(store: ResearchStore, nodes: list) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Each claim node's own statement and assumptions, which the hook renders it from.

    See rendering.claim_view. A claim row this store cannot read is left out, and its node
    is rendered as it stands."""
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for n in nodes:
        if not (n.metadata or {}).get("claim_kind"):
            continue
        try:
            claim = store.get_claim(n.id)
        except (ValueError, TypeError, KeyError):
            continue
        if claim is not None:
            out[n.id] = (claim.statement, tuple(claim.assumptions))
    return out


def _fail(args: argparse.Namespace, status: str, message: str) -> int:
    print(f"delivery audit: {message}", file=sys.stderr)
    if args.json:
        print(json.dumps({"tool": "math_brain.delivery_audit", "status": status,
                          "store": str(args.store), "error": message}, indent=2))
    return EXIT_CANNOT_OPEN


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(STORE))
    ap.add_argument("--budget", type=int, default=320, help="max_text_chars at the live hook")
    ap.add_argument("--worst", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="print one machine-readable result")
    args = ap.parse_args(argv)

    try:
        with ResearchStore.open_read_only(args.store) as store:
            nodes = list(store.iter_nodes())
            parts = _claim_parts(store, nodes)
    except FileNotFoundError:
        return _fail(args, "STORE_MISSING", f"no store at {args.store}; nothing was created")
    except StoreSchemaError as exc:
        return _fail(args, "STORE_UNSUPPORTED",
                     f"{args.store} cannot be read as a store of schema version "
                     f"{STORE_SCHEMA_VERSION}: {exc}")
    except (OSError, sqlite3.Error) as exc:
        return _fail(args, "STORE_UNREADABLE", f"{args.store} could not be read: {exc}")

    total = carrying = intact = 0
    b_carrying = b_intact = not_evaluated = 0
    lost_rows = []
    by_kind = Counter()
    kind_lost = Counter()
    form_stated = Counter()
    form_delivered = Counter()
    a_carrying = a_intact = 0
    for n in nodes:
        body = n.body or ""
        total += 1
        # rendered as the live hook renders it: a claim through its own statement and assumptions
        statement, assumptions = parts.get(n.id, ("", ()))
        view = claim_view(n, statement, assumptions) if assumptions else n
        hypotheses = claim_hypotheses(assumptions)
        if hypotheses:
            a_carrying += 1
            _, text = render_parts(view, max_text_chars=args.budget, query="")
            if all(h in _norm(text) for h in hypotheses):
                a_intact += 1
        # The audit is about nodes that HAVE a value to lose. A prose node with no number is
        # not passing or failing this check, it is not in scope, and counting it as a pass
        # would dilute the rate into meaninglessness.
        want = set(ASSERTED.findall(body))
        b_want = set(BROAD.findall(body))
        if not want and not b_want:
            not_evaluated += 1
            continue
        _, text = render_parts(view, max_text_chars=args.budget, query="")
        if b_want:
            b_carrying += 1
            b_got = set(BROAD.findall(text))
            if b_want <= b_got:
                b_intact += 1
            for v in b_want:
                form_stated[broad_form(v)] += 1
                if v[:1] in "-\u2212+":
                    form_stated["signed (any form)"] += 1
                if v in b_got:
                    form_delivered[broad_form(v)] += 1
                    if v[:1] in "-\u2212+":
                        form_delivered["signed (any form)"] += 1
        if not want:
            continue
        carrying += 1
        got = set(ASSERTED.findall(text))
        missing = want - got
        kind = str(getattr(n, "kind", "") or "?")
        by_kind[kind] += 1
        if not missing:
            intact += 1
        else:
            kind_lost[kind] += 1
            # position of the first number in the body, which is the thing that decides it
            m = ASSERTED.search(body)
            lost_rows.append(
                (len(missing), m.start() if m else -1, n.title or "", sorted(missing)[:4]))

    if not total:
        status, code = "EMPTY_STORE", EXIT_EMPTY
    elif not carrying:
        status, code = "NONE_IN_SCOPE", EXIT_NONE_IN_SCOPE
    else:
        status, code = "MEASURED", EXIT_MEASURED

    if args.json:
        print(json.dumps({
            "tool": "math_brain.delivery_audit",
            "status": status,
            "store": str(args.store),
            "schema_version": STORE_SCHEMA_VERSION,
            "population": {"nodes": total, "digest": population_digest(nodes)},
            "budget": args.budget,
            "rendering": "no query: the head of each body, not the question-matched passage",
            "asserted": {"contract": ASSERTED_CONTRACT, "carrying": carrying, "intact": intact,
                         "rate": round(intact / carrying, 6) if carrying else None,
                         "by_kind": {k: {"carrying": c, "lost": kind_lost[k]}
                                     for k, c in by_kind.most_common()}},
            "broad": {"contract": BROAD_CONTRACT, "carrying": b_carrying, "intact": b_intact,
                      "rate": round(b_intact / b_carrying, 6) if b_carrying else None,
                      "values_by_form": {f: {"stated": c, "delivered": form_delivered[f]}
                                         for f, c in sorted(form_stated.items())}},
            "not_evaluated": not_evaluated,
            "assumptions": {"contract": ASSUMPTIONS_CONTRACT, "carrying": a_carrying,
                            "intact": a_intact,
                            "rate": round(a_intact / a_carrying, 6) if a_carrying else None},
        }, indent=2))
        return code

    print(f"store: {total} nodes, {carrying} assert a value")
    if not total:
        print("  EMPTY: the store holds no nodes, so nothing was audited")
        return code
    if not carrying:
        print("  NONE IN SCOPE: no node asserts a value the ASSERTED pattern reads; "
              "nothing was measured")
    else:
        print(f"  asserted value delivered at a {args.budget}-char budget: {intact}/{carrying} "
              f"= {intact/carrying:.3f}")
        print(f"  losing at least one:                                  {carrying-intact}")
    print(f"  scope: {ASSERTED_CONTRACT}")
    print("  rendered with no query (the head of each body), not the question-matched passage")
    if b_carrying:
        print(f"  broad values (signed, one digit, decimal, fraction, scientific, bounds) "
              f"delivered: {b_intact}/{b_carrying} = {b_intact/b_carrying:.3f}")
        for f, c in sorted(form_stated.items()):
            print(f"    {f:32s} {form_delivered[f]:5d} / {c:5d} values")
    print(f"  not evaluated (no value either pattern reads): {not_evaluated} nodes")
    if a_carrying:
        print(f"  claims whose every hypothesis is delivered: {a_intact}/{a_carrying} "
              f"= {a_intact/a_carrying:.3f}")
        print(f"    scope: {ASSUMPTIONS_CONTRACT}")
    if not carrying:
        return code
    print("\n  by kind (lost / carrying):")
    for k, c in by_kind.most_common():
        print(f"    {k:28s} {kind_lost[k]:5d} / {c:5d}")

    lost_rows.sort(key=lambda r: (-r[0], -r[1]))
    print(f"\n  worst {min(args.worst, len(lost_rows))}, by count of numbers lost then by how "
          f"late the first number appears:")
    for nmiss, pos, title, sample in lost_rows[: args.worst]:
        print(f"    -{nmiss:3d} first digit at char {pos:5d}  {title[:70]}")
        print(f"          missing e.g. {sample}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
