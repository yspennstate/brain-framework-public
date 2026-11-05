from __future__ import annotations

import pytest

from math_brain.random_external_execution_authorization_v4 import (
    MISSING_RANDOM_EXTERNAL_EXECUTION_AUTHORITIES_V4,
    ExecutionAuthorizationUnavailableV4,
    RandomExternalExecutionAuthorizationV4,
    cold_replay_random_external_execution_authorization_v4,
)


def test_nominal_execution_authorization_cannot_be_constructed_or_marker_minted() -> None:
    with pytest.raises(TypeError, match="complete cold-replay builder"):
        RandomExternalExecutionAuthorizationV4()

    assert not hasattr(RandomExternalExecutionAuthorizationV4, "_mint")
    assert not hasattr(RandomExternalExecutionAuthorizationV4, "from_digest")


def test_execution_stays_fail_closed_until_every_preregistered_authority_exists() -> None:
    with pytest.raises(
        ExecutionAuthorizationUnavailableV4,
        match="missing exact authorities",
    ) as captured:
        cold_replay_random_external_execution_authorization_v4()

    message = str(captured.value)
    assert MISSING_RANDOM_EXTERNAL_EXECUTION_AUTHORITIES_V4
    assert all(
        authority in message for authority in MISSING_RANDOM_EXTERNAL_EXECUTION_AUTHORITIES_V4
    )
