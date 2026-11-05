from pathlib import Path

from math_brain.triggers import ReviewTrigger, load_triggers, match_triggers


def test_trigger_match_is_case_insensitive() -> None:
    trigger = ReviewTrigger("limit", ("limit", "integral"), "check theorem")
    matches = match_triggers("Interchange LIMIT and Integral", [trigger])
    assert matches[0].matched_keywords == ("limit", "integral")


def test_repository_trigger_file_loads() -> None:
    root = Path(__file__).parents[1]
    triggers = load_triggers(root / "config" / "triggers.toml")
    assert len(triggers) >= 8
    assert len({trigger.id for trigger in triggers}) == len(triggers)
