"""Citation-entailment records without redistributing source text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class CitationVerdict(StrEnum):
    SUPPORTS = "supports"
    PARTIAL = "partial"
    CONTRADICTS = "contradicts"
    NOT_FOUND = "not_found"
    INACCESSIBLE = "inaccessible"


@dataclass(frozen=True, slots=True)
class CitationCheck:
    source_id: str
    source_hash: str
    locator: str
    attributed_claim: str
    verdict: CitationVerdict
    assumptions_match: bool
    statement_match: bool
    authorship_match: bool
    #: the source's hypotheses and domain cover every case the attributed claim states. A source
    #: that proves a finite-dimensional, special or asymptotic case of a general, dimension-free or
    #: exact claim does not match: that is partial support or an unsupported generalization
    #: (1000-page report D09-T04). Asked on its own because a reviewer who reads a matching
    #: statement tends to tick statement_match without comparing the ranges.
    scope_match: bool
    checked_by: str
    notes: str = ""

    def __post_init__(self) -> None:
        required = {
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "locator": self.locator,
            "attributed_claim": self.attributed_claim,
            "checked_by": self.checked_by,
        }
        for name, value in (*required.items(), ("notes", self.notes)):
            if not isinstance(value, str):
                raise TypeError(f"citation check {name} must be a string")
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"citation check is missing: {', '.join(missing)}")
        # the record is typed at construction (interactive audit, B100-06, 30 Sep 2026): the string "false" is
        # truthy, so a flag read from text used to make fully_verified true
        if not isinstance(self.verdict, CitationVerdict):
            raise TypeError("citation check verdict must be a CitationVerdict")
        for name, value in (
            ("assumptions_match", self.assumptions_match),
            ("statement_match", self.statement_match),
            ("authorship_match", self.authorship_match),
            ("scope_match", self.scope_match),
        ):
            if type(value) is not bool:
                raise TypeError(f"citation check {name} must be an exact boolean")
        if not _SHA256_RE.fullmatch(self.source_hash):
            raise ValueError("citation check source hash must be sha256: and 64 lowercase hex digits")
        if self.verdict is CitationVerdict.SUPPORTS and not self.scope_match:
            raise ValueError(
                "a source whose scope does not cover the attributed claim is partial support, "
                "not supports"
            )

    @property
    def fully_verified(self) -> bool:
        return (
            self.verdict is CitationVerdict.SUPPORTS
            and self.assumptions_match
            and self.statement_match
            and self.authorship_match
            and self.scope_match
        )
