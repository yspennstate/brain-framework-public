"""Dependency-free exact-source composition for the optional Lean HTTP shim."""
from __future__ import annotations

import re


MAX_LEAN_SOURCE_BYTES = 1024 * 1024
MAX_LEAN_IMPORTS = 64
_IMPORT_RE = re.compile(r"[A-Za-z0-9_.]{1,256}\Z")
_FORBIDDEN_TRUST_RE = re.compile(
    r"(?im)^\s*(?:#|axiom\b|axioms\b|constant\b|constants\b|unsafe\b|"
    r"initialize\b|builtin_initialize\b|foreign\b|extern\b|elab\b|"
    r"elab_rules\b|macro\b|macro_rules\b|syntax\b|run_cmd\b)|"
    r"\b(?:sorry|admit|native_decide|unsafeCast|ofReduceBool|run_tac)\b"
)


def forbidden_source_reason(source: str) -> str | None:
    raw = source.encode("utf-8")
    if len(raw) > MAX_LEAN_SOURCE_BYTES:
        return "Lean source exceeds the fixed 1 MiB verification bound"
    if _FORBIDDEN_TRUST_RE.search(source):
        return "submission contains a forbidden trust or metaprogramming escape"
    return None


def compose_source(statement: str, proof: str, imports: list[str]) -> str:
    """Compose a Lean file without ever dropping a separately supplied statement."""

    if not isinstance(statement, str) or not isinstance(proof, str):
        raise ValueError("Lean statement and proof must be text")
    if len(imports) > MAX_LEAN_IMPORTS:
        raise ValueError("too many Lean imports")
    if any(
        not isinstance(item, str) or _IMPORT_RE.fullmatch(item) is None
        for item in imports
    ):
        raise ValueError("Lean imports must be dotted module identifiers")
    body = statement.strip()
    proof_text = proof.strip()
    if not body and not proof_text:
        raise ValueError("Lean source is empty")
    if proof_text:
        if proof_text.startswith("by ") or proof_text.startswith(":= by"):
            if not body:
                raise ValueError("a tactic proof requires a submitted statement")
            placeholder = re.compile(r"(?s)(?::=\s*)?by\s+(?:sorry|admit)\s*\Z")
            if placeholder.search(body) is None:
                raise ValueError("tactic proof requires a terminal by-sorry placeholder")
            replacement = proof_text if proof_text.startswith(":=") else f":= {proof_text}"
            body = placeholder.sub(replacement, body)
        elif body:
            raise ValueError(
                "complete proof source cannot replace a separate submitted statement"
            )
        else:
            body = proof_text
    existing = set(re.findall(r"(?m)^\s*import\s+([A-Za-z0-9_.]+)\s*$", body))
    prefix = "\n".join(f"import {item}" for item in imports if item not in existing)
    return ((prefix + "\n\n") if prefix else "") + body + "\n"
