"""Fail-closed production authorization boundary for the v4 experiment.

This module intentionally does not yet expose a mint.  The plan preregisters seven
independent readiness authorities, but three exact replay surfaces are still under
construction: curated-lane terminal failure, v4 sham/packet materialization, and a
whole-lane tokenizer attestation.  Treating their config digests as caller labels would
silently weaken the experiment, so execution remains structurally impossible until all
three typed authorities land and this module can cold-replay the complete bundle.
"""

from __future__ import annotations

from typing import NoReturn

RANDOM_EXTERNAL_EXECUTION_AUTHORIZATION_SCHEMA_V4 = (
    "org.math-research-brain/random-external-execution-authorization/v4"
)

MISSING_RANDOM_EXTERNAL_EXECUTION_AUTHORITIES_V4 = (
    "curated_lane_failure_evidence_v4",
    "random_external_sham_materialization_v4",
    "random_external_tokenizer_attestation_v4",
    "external_gold_anchor_acknowledgement_v4",
)


class ExecutionAuthorizationUnavailableV4(RuntimeError):
    """The exact preregistered readiness universe cannot yet be replayed."""


class RandomExternalExecutionAuthorizationV4:
    """Nominal type reserved for a future complete cold-replay result.

    Direct construction is forbidden.  The class deliberately has no private escape
    hatch or marker-based factory: until every missing authority is implemented, there
    is no valid instance and therefore no valid solver execution.
    """

    __slots__ = ()

    def __new__(cls) -> RandomExternalExecutionAuthorizationV4:
        raise TypeError("v4 execution authorization requires the complete cold-replay builder")

    @property
    def content_digest(self) -> str:
        raise ExecutionAuthorizationUnavailableV4(
            "v4 execution authorization has not been implemented"
        )

    def validate_current(self) -> NoReturn:
        raise ExecutionAuthorizationUnavailableV4(
            "v4 execution authorization has not been implemented"
        )


def cold_replay_random_external_execution_authorization_v4() -> NoReturn:
    """Refuse authorization until every exact preregistered authority exists."""

    missing = ", ".join(MISSING_RANDOM_EXTERNAL_EXECUTION_AUTHORITIES_V4)
    raise ExecutionAuthorizationUnavailableV4(
        f"v4 execution authorization is unavailable; missing exact authorities: {missing}"
    )


__all__ = [
    "MISSING_RANDOM_EXTERNAL_EXECUTION_AUTHORITIES_V4",
    "RANDOM_EXTERNAL_EXECUTION_AUTHORIZATION_SCHEMA_V4",
    "ExecutionAuthorizationUnavailableV4",
    "RandomExternalExecutionAuthorizationV4",
    "cold_replay_random_external_execution_authorization_v4",
]
