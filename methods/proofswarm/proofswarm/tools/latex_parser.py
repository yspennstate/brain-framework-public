"""LaTeX paper parser. Extracts theorems/lemmas/propositions/corollaries
+ their proofs from a .tex file, and the bibliography references.

Used by the Paper Parser stage of v2 to seed one ticket per claim.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


CLAIM_KINDS = ("theorem", "lemma", "proposition", "corollary", "definition")

# Common abbreviated env names → canonical kind
ENV_ALIASES = {
    "thm": "theorem", "Thm": "theorem", "theorem": "theorem", "Theorem": "theorem",
    "lem": "lemma", "Lem": "lemma", "lemma": "lemma", "Lemma": "lemma",
    "prop": "proposition", "Prop": "proposition", "proposition": "proposition", "Proposition": "proposition",
    "cor": "corollary", "Cor": "corollary", "corollary": "corollary", "Corollary": "corollary",
    "defn": "definition", "Defn": "definition", "def": "definition", "definition": "definition", "Definition": "definition",
}


@dataclass
class PaperClaim:
    kind: str            # theorem, lemma, proposition, corollary, definition
    label: str | None    # the \label{...} if present
    title: str | None    # optional title in [..] of \begin{...}
    statement: str       # body of the environment
    proof: str | None    # body of the following \begin{proof}...\end{proof}, if any


def _strip_comments(src: str) -> str:
    """Drop %-comments while preserving \\%."""
    out = []
    for line in src.splitlines():
        # Trim everything from an unescaped %
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i-1] != "\\"):
                line = line[:i]
                break
            i += 1
        out.append(line)
    return "\n".join(out)


_BEGIN_RE = re.compile(
    r"\\begin\{(theorem|lemma|proposition|corollary|definition|"
    r"thm|lem|prop|cor|defn|Theorem|Lemma|Proposition|Corollary|Definition|"
    r"Thm|Lem|Prop|Cor|Defn)\}"
    r"(?:\[(?P<title>[^\]]*)\])?",
)
_END_RE = lambda kind: re.compile(rf"\\end\{{{kind}\}}")
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
_PROOF_BEGIN = re.compile(r"\\begin\{proof\}(?:\[(?P<title>[^\]]*)\])?")
_PROOF_END = re.compile(r"\\end\{proof\}")


def parse_paper(tex: str) -> list[PaperClaim]:
    """Extract claims and their proofs.

    Proofs are matched in two ways:
      1. \\begin{proof}[Proof of Theorem~\\ref{X}] — labeled, matched by ref
      2. \\begin{proof} immediately following a claim — proximity-matched
    """
    src = _strip_comments(tex)
    claims: list[PaperClaim] = []
    # First, extract all proofs from the document
    labeled_proofs: dict[str, str] = {}    # label -> proof text
    unlabeled_proofs: list[tuple[int, str]] = []  # (start_pos, proof text)
    proof_pat = re.compile(r"\\begin\{proof\}(?:\[(?P<title>[^\]]*)\])?", re.DOTALL)
    end_proof = re.compile(r"\\end\{proof\}")
    for pm in proof_pat.finditer(src):
        ep = end_proof.search(src, pm.end())
        if not ep:
            continue
        proof_body = src[pm.end():ep.start()].strip()
        proof_title = pm.group("title") or ""
        # Look for \ref{X} in the title
        ref_in_title = re.search(r"\\ref\{([^}]+)\}", proof_title)
        if ref_in_title:
            labeled_proofs[ref_in_title.group(1)] = proof_body
        else:
            unlabeled_proofs.append((pm.start(), proof_body))

    # Now extract claims and attach proofs
    pos = 0
    while pos < len(src):
        m = _BEGIN_RE.search(src, pos)
        if not m:
            break
        raw_kind = m.group(1)
        kind = ENV_ALIASES.get(raw_kind, raw_kind.lower())
        title = m.group("title")
        end_re = _END_RE(raw_kind)
        em = end_re.search(src, m.end())
        if not em:
            break
        body = src[m.end():em.start()].strip()
        label_m = _LABEL_RE.search(body)
        label = label_m.group(1) if label_m else None

        # Try labeled proof match first
        proof_text: str | None = None
        if label and label in labeled_proofs:
            proof_text = labeled_proofs[label]
        else:
            # Fall back to immediately-following \begin{proof}
            rest = src[em.end():]
            pm_anywhere = proof_pat.search(rest)
            if pm_anywhere:
                between = rest[:pm_anywhere.start()]
                if not _BEGIN_RE.search(between):
                    # Make sure it's not a labeled proof for a different claim
                    title_attr = pm_anywhere.group("title") or ""
                    if not re.search(r"\\ref\{", title_attr):
                        pe = end_proof.search(rest, pm_anywhere.end())
                        if pe:
                            proof_text = rest[pm_anywhere.end():pe.start()].strip()
        claims.append(PaperClaim(kind=kind, label=label, title=title,
                                 statement=body, proof=proof_text))
        pos = em.end()
    return claims


def extract_bibliography(tex: str) -> list[str]:
    """Return raw \\bibitem entries."""
    m = re.search(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}",
                  tex, re.DOTALL)
    if not m:
        return []
    block = m.group(0)
    return [b.strip() for b in re.findall(r"\\bibitem\{([^}]+)\}([^\\]+(?:\\[^b][^\\]*)*)",
                                          block)
            if isinstance(b, str)] or re.findall(r"\\bibitem\{[^}]+\}.*?(?=\\bibitem|\\end)",
                                                  block, re.DOTALL)


def parse_file(path: str | Path) -> tuple[list[PaperClaim], list[str]]:
    p = Path(path)
    tex = p.read_text(encoding="utf-8")
    return parse_paper(tex), extract_bibliography(tex)


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    claims, bib = parse_file(sys.argv[1])
    print(f"Parsed {len(claims)} claims, {len(bib)} bib entries")
    for c in claims:
        print(f"  [{c.kind:11s}] label={c.label!r:30s} title={c.title!r:40s} "
              f"stmt_chars={len(c.statement):4d} proof_chars={len(c.proof or ''):4d}")
