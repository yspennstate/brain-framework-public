"""Symbolic algebra verification using sympy. Used by the Constant Calculator agent.

Given an algebraic claim like "5*alpha/4 + 4*theta == 29*alpha/20 given theta == alpha/20",
parse and verify symbolically.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class SymbolicCheck:
    claim: str
    context: str
    ok: bool
    actual: str
    error: str | None = None


def check_claim(claim: str, context: str = "") -> SymbolicCheck:
    """Verify a symbolic equality/inequality claim.

    Format: "<expr1> == <expr2>" or "<expr1> < <expr2>" etc., with optional
    context "given x = ..., y = ..." substitutions.
    """
    try:
        import sympy as sp
    except ImportError:
        return SymbolicCheck(claim=claim, context=context, ok=False,
                             actual="sympy not installed",
                             error="install sympy to enable")
    # Parse context as substitutions
    subs = {}
    for kv in context.split(",") if context else []:
        kv = kv.strip()
        if not kv or "=" not in kv or "==" in kv:
            continue
        kv = kv.replace("given", "").strip()
        if "=" in kv:
            key, val = kv.split("=", 1)
            try:
                subs[sp.Symbol(key.strip())] = sp.sympify(val.strip())
            except Exception:
                pass
    # Parse claim — split on relational operator
    import re
    m = re.search(r"(==|>=|<=|<|>|!=)", claim)
    if not m:
        return SymbolicCheck(claim=claim, context=context, ok=False,
                             actual=f"no relational operator found",
                             error="claim must contain ==, <, <=, >, >=, or !=")
    op = m.group(1)
    lhs_raw, rhs_raw = claim.split(op, 1)
    try:
        lhs = sp.sympify(lhs_raw.strip()).subs(subs)
        rhs = sp.sympify(rhs_raw.strip()).subs(subs)
    except Exception as e:
        return SymbolicCheck(claim=claim, context=context, ok=False,
                             actual=f"parse error: {e}", error=str(e))
    diff = sp.simplify(lhs - rhs)
    # For inequalities, try both with and without positive-symbol assumptions, since
    # paper claims like "(m-1)*4*r < 4*m*r" rely on r > 0.
    def _safe_bool(expr) -> tuple[bool | None, str]:
        try:
            return bool(expr), ""
        except TypeError as e:
            # Try with all free symbols positive
            try:
                free = expr.free_symbols
                pos_subs = {s: sp.Symbol(str(s), positive=True) for s in free}
                e2 = expr.subs(pos_subs)
                return bool(e2), "(under positivity)"
            except Exception as e2:
                return None, f"undetermined: {e2}"
    note = ""
    if op == "==":
        ok = bool(diff == 0)
    elif op == "<":
        v, note = _safe_bool(sp.simplify(rhs - lhs) > 0)
        ok = bool(v) if v is not None else False
    elif op == "<=":
        v, note = _safe_bool(sp.simplify(rhs - lhs) >= 0)
        ok = bool(v) if v is not None else False
    elif op == ">":
        v, note = _safe_bool(sp.simplify(lhs - rhs) > 0)
        ok = bool(v) if v is not None else False
    elif op == ">=":
        v, note = _safe_bool(sp.simplify(lhs - rhs) >= 0)
        ok = bool(v) if v is not None else False
    elif op == "!=":
        ok = bool(diff != 0)
    else:
        ok = False
    return SymbolicCheck(
        claim=claim, context=context, ok=ok,
        actual=f"lhs - rhs = {diff} {note}".strip(),
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    # Quick examples from a diffusion-models paper
    cases = [
        ("5*alpha/4 + 4*theta == 29*alpha/20", "given theta = alpha/20"),
        ("(m-1)*4*r < 4*m*r", ""),
        ("alpha/(64*K_alpha*m) == delta_m", "given delta_m = alpha/(64*K_alpha*m)"),
        ("3*alpha/32 < alpha/8", ""),
        ("alpha/16 + 2*alpha/64 == 3*alpha/32", ""),
        # Stress: the perturbation chain in Lemma 6.1
        ("alpha + 4*theta + alpha/4 == 5*alpha/4 + 4*theta", ""),
    ]
    for claim, ctx in cases:
        r = check_claim(claim, ctx)
        marker = "OK" if r.ok else "FAIL"
        print(f"[{marker:4s}] {claim} | {ctx} -> {r.actual}")
