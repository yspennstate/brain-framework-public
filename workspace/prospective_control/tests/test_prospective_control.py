#!/usr/bin/env python3
"""Adversarial behavioral tests for the shadow prospective-control lane."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import prospective_control as pc  # noqa: E402


KEY = b"prospective-control-fixture-key" + b"x" * 32
T0 = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)


def trajectories(
    probabilities: tuple[float, float, float] = (0.6, 0.3, 0.1),
    *,
    expected_marker: str = "The bounded implementation completes cleanly",
    scar_slug: str = "scar_checkout_contamination",
) -> list[dict]:
    return [
        {
            "kind": "expected",
            "probability": probabilities[0],
            "prediction": expected_marker,
            "observable": "The declared verification exits successfully",
            "source_slugs": ["project_current_state"],
        },
        {
            "kind": "scar",
            "probability": probabilities[1],
            "prediction": "A known worktree failure contaminates the result",
            "observable": "The worktree reports unrelated staged or unstaged changes",
            "source_slugs": ["project_current_state", scar_slug],
            "scar_source_slugs": [scar_slug],
            "severity": 3,
            "guard": {
                "guard_id": "verify-clean-worktree",
                "action": "Require a clean scoped diff before the first edit",
                "error_signature": "dirty_worktree",
            },
        },
        {
            "kind": "pivot",
            "probability": probabilities[2],
            "prediction": "A sparse checkout becomes the bounded recovery path",
            "observable": "The full checkout exceeds its declared time budget",
            "source_slugs": ["repo_scale_observation"],
            "trigger": "The isolated full checkout does not finish within its budget",
            "pivot_action": "Create a sparse worktree at the same pinned commit",
        },
    ]


def scenario_input(
    bundle_id: str,
    unit_id: str,
    *,
    expected_marker: str = "The bounded implementation completes cleanly",
    probabilities: tuple[float, float, float] = (0.6, 0.3, 0.1),
    scar_slug: str = "scar_checkout_contamination",
) -> dict:
    return {
        "bundle_id": bundle_id,
        "unit_id": unit_id,
        "unit_source_slug": f"session/{unit_id}",
        "author_id": "fixture-planner",
        "evidence_cutoff_at": (T0 + timedelta(seconds=30)).isoformat(),
        "work_not_started_attestation": True,
        "trajectories": trajectories(
            probabilities, expected_marker=expected_marker, scar_slug=scar_slug
        ),
    }


def resolution_input(
    bundle_id: str,
    *,
    arm: str,
    observed_kind: str = "expected",
    recurred: bool = False,
    critical_harm: bool = False,
    blinded: bool = True,
    work_started_at: datetime | None = None,
) -> dict:
    return {
        "bundle_id": bundle_id,
        "work_started_at": (
            work_started_at or (T0 + timedelta(minutes=2))
        ).isoformat(),
        "observed_kind": observed_kind,
        "adjudicator_id": "fixture-adjudicator",
        "adjudicator_blinded_to_arm": blinded,
        "guarded_error_eligible": True,
        "guarded_error_recurred": recurred,
        "guard_followed": True if arm == "visible" else None,
        "planning_overhead_seconds": 12.0 if arm == "visible" else 0.0,
        "critical_harm": critical_harm,
        "outcome_evidence_slugs": [f"outcome/{bundle_id}"],
    }


def small_gates() -> dict:
    gates = dict(pc.DEFAULT_GATES)
    gates.update(
        {
            "min_resolved_bundles": 4,
            "min_independent_units": 4,
            "min_experiment_days": 1.0,
            "min_eligible_units_per_arm": 2,
            "max_largest_unit_share": 0.5,
            "max_mean_brier": 2.0,
            "max_mean_log_loss": 5.0,
            "max_visible_overhead_seconds": 60.0,
            "min_distinct_scar_sources": 1,
            "min_reused_scar_sources": 1,
            "max_critical_harms": 0,
            "max_late_resolution_fraction": 0.0,
            "recurrence_ci_upper_below": 0.0,
        }
    )
    return gates


class ProspectiveControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = self.root / "shadow.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def init(
        self,
        *,
        end: datetime | None = None,
        max_bundles: int = 120,
        gates: dict | None = None,
        grace: int = 2,
    ) -> None:
        pc.start_experiment(
            self.ledger,
            experiment_id="fixture-experiment",
            end_at=end or (T0 + timedelta(days=2)),
            assignment_key=KEY,
            max_bundles=max_bundles,
            visible_fraction=0.5,
            resolution_grace_days=grace,
            promotion_gates=gates,
            now=T0,
        )

    def units_for(self, arm: str, count: int) -> list[str]:
        found = []
        for index in range(10000):
            unit = f"unit-{index:04d}"
            assignment = pc.assignment_for(KEY, "fixture-experiment", unit, 0.5)
            if assignment["arm"] == arm:
                found.append(unit)
                if len(found) == count:
                    return found
        self.fail(f"could not find {count} deterministic {arm} units")

    def create(self, bundle: str, unit: str, *, at: datetime | None = None, **kwargs):
        return pc.create_scenario(
            self.ledger,
            scenario_input(bundle, unit, **kwargs),
            assignment_key=KEY,
            now=at or (T0 + timedelta(minutes=1)),
        )

    def test_freeze_uses_append_clock_and_canonical_sources(self):
        self.init()
        unit = self.units_for("visible", 1)[0]
        receipt = self.create("bundle-a", unit)
        state = pc.load_state(self.ledger)
        frozen = state.scenarios["bundle-a"]

        self.assertEqual(frozen["created_at"], "2026-07-10T08:01:00.000000Z")
        self.assertEqual(
            frozen["evidence_cutoff_at"], "2026-07-10T08:00:30.000000Z"
        )
        self.assertEqual(
            frozen["trajectories"][1]["source_slugs"],
            ["project_current_state", "scar_checkout_contamination"],
        )
        self.assertEqual(frozen["planning_policy"]["rollout_budget"], 3)
        self.assertAlmostEqual(frozen["planning_policy"]["scar_replay_priority"], 0.9)
        self.assertIsNotNone(receipt["scenario_bundle"])
        self.assertEqual(pc.verify_ledger(self.ledger, assignment_key=KEY)["records"], 2)

        backdated = scenario_input("bundle-b", unit)
        backdated["created_at"] = T0.isoformat()
        with self.assertRaisesRegex(pc.ContractError, "unexpected created_at"):
            pc.create_scenario(
                self.ledger,
                backdated,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=2),
            )

    def test_trajectory_contract_is_exact_and_scar_derived(self):
        self.init()
        unit = self.units_for("visible", 1)[0]

        missing = scenario_input("missing", unit)
        missing["trajectories"] = missing["trajectories"][:2]
        with self.assertRaisesRegex(pc.ContractError, "exactly expected, scar, and pivot"):
            pc.create_scenario(
                self.ledger,
                missing,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=1),
            )

        bad_sum = scenario_input("bad-sum", unit, probabilities=(0.6, 0.3, 0.2))
        with self.assertRaisesRegex(pc.ContractError, "sum to 1"):
            pc.create_scenario(
                self.ledger,
                bad_sum,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=1),
            )

        ungrounded = scenario_input("ungrounded", unit)
        ungrounded["trajectories"][1]["scar_source_slugs"] = ["not_in_sources"]
        with self.assertRaisesRegex(pc.ContractError, "subset"):
            pc.create_scenario(
                self.ledger,
                ungrounded,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=1),
            )

        secret = scenario_input("secret", unit)
        secret["trajectories"][0]["prediction"] = "token=sk-123456789012345678901234"
        with self.assertRaisesRegex(pc.ContractError, "resembles a secret"):
            pc.create_scenario(
                self.ledger,
                secret,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=1),
            )

    def test_hidden_actor_receipt_redacts_arm_content_and_sources(self):
        self.init()
        hidden = self.units_for("hidden", 1)[0]
        marker = "HIDDEN_MARKER must never reach the actor receipt"
        receipt = self.create("sealed-bundle", hidden, expected_marker=marker)
        encoded = json.dumps(receipt, sort_keys=True)
        self.assertIsNone(receipt["scenario_bundle"])
        self.assertNotIn(marker, encoded)
        self.assertNotIn("scar_checkout_contamination", encoded)
        self.assertNotIn('"arm"', encoded)
        self.assertNotIn("hidden", encoded.lower())

        template = pc.adjudication_template(self.ledger, "sealed-bundle")
        template_text = json.dumps(template, sort_keys=True)
        self.assertIn(marker, template_text)
        self.assertNotIn('"assignment"', template_text)
        self.assertNotIn('"visible"', template_text)
        self.assertNotIn('"hidden"', template_text)

    def test_assignment_is_stable_per_unit_and_committed(self):
        self.init()
        unit = self.units_for("visible", 1)[0]
        first = self.create("cluster-a", unit, at=T0 + timedelta(minutes=1))
        second = self.create("cluster-b", unit, at=T0 + timedelta(minutes=2))
        state = pc.load_state(self.ledger)
        self.assertEqual(
            state.scenarios["cluster-a"]["assignment"],
            state.scenarios["cluster-b"]["assignment"],
        )
        self.assertEqual(
            first["assignment_commitment"], second["assignment_commitment"]
        )

        changed_source = scenario_input("cluster-c", unit)
        changed_source["unit_source_slug"] = "session/different-source"
        with self.assertRaisesRegex(pc.ContractError, "unit_source_slug changed"):
            pc.create_scenario(
                self.ledger,
                changed_source,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=3),
            )

    def test_later_resolution_is_blinded_and_cannot_fake_hidden_adherence(self):
        self.init()
        unit = self.units_for("hidden", 1)[0]
        self.create("resolve-me", unit)

        before_freeze = resolution_input(
            "resolve-me",
            arm="hidden",
            work_started_at=T0 + timedelta(seconds=30),
        )
        with self.assertRaisesRegex(pc.ContractError, "after the immutable scenario creation"):
            pc.resolve_scenario(
                self.ledger, before_freeze, now=T0 + timedelta(minutes=3)
            )

        unblinded = resolution_input("resolve-me", arm="hidden", blinded=False)
        with self.assertRaisesRegex(pc.ContractError, "blinded_to_arm must be true"):
            pc.resolve_scenario(
                self.ledger, unblinded, now=T0 + timedelta(minutes=3)
            )

        fake_adherence = resolution_input("resolve-me", arm="hidden")
        fake_adherence["guard_followed"] = True
        with self.assertRaisesRegex(pc.ContractError, "unseen guard"):
            pc.resolve_scenario(
                self.ledger, fake_adherence, now=T0 + timedelta(minutes=3)
            )

        receipt = pc.resolve_scenario(
            self.ledger,
            resolution_input("resolve-me", arm="hidden", recurred=True),
            now=T0 + timedelta(minutes=3),
        )
        self.assertNotIn("arm", receipt)
        state = pc.load_state(self.ledger)
        resolved = state.resolutions["resolve-me"]
        self.assertEqual(resolved["resolved_at"], "2026-07-10T08:03:00.000000Z")
        self.assertFalse(resolved["guard_exposed"])
        with self.assertRaisesRegex(pc.ContractError, "already resolved"):
            pc.resolve_scenario(
                self.ledger,
                resolution_input("resolve-me", arm="hidden", recurred=True),
                now=T0 + timedelta(minutes=4),
            )

    def test_work_must_start_inside_bounded_experiment_window(self):
        self.init(end=T0 + timedelta(hours=1))
        unit = self.units_for("visible", 1)[0]
        self.create("late-work", unit)
        data = resolution_input(
            "late-work",
            arm="visible",
            work_started_at=T0 + timedelta(hours=2),
        )
        with self.assertRaisesRegex(pc.ContractError, "inside the bounded experiment window"):
            pc.resolve_scenario(
                self.ledger,
                data,
                now=T0 + timedelta(hours=2, minutes=1),
            )

    def test_brier_and_log_score_are_proper_multiclass_scores(self):
        self.init()
        unit = self.units_for("visible", 1)[0]
        self.create("scored", unit)
        state = pc.load_state(self.ledger)
        scenario = state.scenarios["scored"]
        resolution = {"observed_kind": "scar"}
        score = pc.score_resolution(scenario, resolution)
        self.assertAlmostEqual(score["brier"], 0.86)
        self.assertAlmostEqual(score["log_loss_nats"], -math.log(0.3))

    def _populate_balanced(
        self,
        *,
        harmful: bool = False,
        equal_recurrence: bool = False,
        reverse_effect: bool = False,
    ):
        visible = self.units_for("visible", 2)
        hidden = self.units_for("hidden", 2)
        allocated = []
        sequence = 0
        for arm, units in (("visible", visible), ("hidden", hidden)):
            for unit in units:
                bundle = f"b-{sequence}"
                create_at = T0 + timedelta(minutes=1, seconds=sequence)
                self.create(
                    bundle,
                    unit,
                    at=create_at,
                    probabilities=(0.8, 0.15, 0.05),
                    scar_slug="scar_shared_regression",
                )
                allocated.append((sequence, arm, bundle))
                sequence += 1
        for sequence, arm, bundle in allocated:
            if reverse_effect:
                recurred = arm == "visible"
            else:
                recurred = True if arm == "hidden" else equal_recurrence
            pc.resolve_scenario(
                self.ledger,
                resolution_input(
                    bundle,
                    arm=arm,
                    recurred=recurred,
                    critical_harm=harmful and sequence == 0,
                    work_started_at=T0 + timedelta(minutes=10, seconds=sequence),
                ),
                now=T0 + timedelta(minutes=20, seconds=sequence),
            )

    def test_unit_balanced_recurrence_and_all_gates_can_yield_candidate(self):
        self.init(
            end=T0 + timedelta(days=1),
            max_bundles=8,
            gates=small_gates(),
            grace=2,
        )
        self._populate_balanced()
        report = pc.experiment_report(self.ledger, now=T0 + timedelta(days=2))
        guarded = report["guarded_error"]
        self.assertEqual(
            guarded["visible_minus_hidden_unit_balanced_risk_difference"], -1.0
        )
        self.assertEqual(guarded["cluster_bootstrap_95_ci"], [-1.0, -1.0])
        self.assertEqual(report["provenance_reuse"]["reused_across_units"], 1)
        self.assertEqual(report["promotion"]["verdict"], "PROMOTION_CANDIDATE")
        self.assertTrue(all(gate["passed"] for gate in report["promotion"]["gates"]))
        self.assertFalse(report["promotion"]["automatic_integration_permitted"])

    def test_promotion_is_data_gated_early_and_holds_on_closed_null_effect(self):
        self.init(
            end=T0 + timedelta(days=1),
            max_bundles=8,
            gates=small_gates(),
            grace=2,
        )
        early = pc.experiment_report(self.ledger, now=T0 + timedelta(hours=1))
        self.assertEqual(early["promotion"]["verdict"], "DATA_GATED")

        self._populate_balanced(equal_recurrence=True)
        closed = pc.experiment_report(self.ledger, now=T0 + timedelta(days=2))
        self.assertEqual(closed["promotion"]["verdict"], "HOLD")
        benefit = next(
            gate
            for gate in closed["promotion"]["gates"]
            if gate["name"] == "guarded_error_recurrence_benefit"
        )
        self.assertFalse(benefit["passed"])

    def test_critical_harm_forces_hold_even_before_window_closes(self):
        self.init(
            end=T0 + timedelta(days=3),
            max_bundles=8,
            gates=small_gates(),
            grace=2,
        )
        self._populate_balanced(harmful=True)
        report = pc.experiment_report(self.ledger, now=T0 + timedelta(hours=2))
        self.assertEqual(report["promotion"]["verdict"], "HOLD")
        self.assertEqual(report["quality"]["critical_harms"], 1)

    def test_adverse_recurrence_signal_forces_early_hold(self):
        self.init(
            end=T0 + timedelta(days=3),
            max_bundles=8,
            gates=small_gates(),
            grace=2,
        )
        self._populate_balanced(reverse_effect=True)
        report = pc.experiment_report(self.ledger, now=T0 + timedelta(hours=2))
        self.assertFalse(report["experiment_closed"])
        self.assertEqual(
            report["guarded_error"]["cluster_bootstrap_95_ci"], [1.0, 1.0]
        )
        self.assertTrue(report["quality"]["adverse_recurrence_signal"])
        self.assertEqual(report["promotion"]["verdict"], "HOLD")

    def test_bounded_creation_rejects_capacity_and_time_overrun(self):
        self.init(
            end=T0 + timedelta(hours=1),
            max_bundles=4,
            gates=small_gates(),
        )
        units = self.units_for("visible", 4)
        for index, unit in enumerate(units):
            self.create(
                f"cap-{index}", unit, at=T0 + timedelta(minutes=index + 1)
            )
        with self.assertRaisesRegex(pc.ContractError, "max_bundles"):
            self.create("cap-over", units[0], at=T0 + timedelta(minutes=10))

        other = self.root / "time-window.jsonl"
        pc.start_experiment(
            other,
            experiment_id="time-window",
            end_at=T0 + timedelta(minutes=5),
            assignment_key=KEY,
            max_bundles=120,
            now=T0,
        )
        data = scenario_input("late-create", "unit-late")
        with self.assertRaisesRegex(pc.ContractError, "window is closed"):
            pc.create_scenario(
                other,
                data,
                assignment_key=KEY,
                now=T0 + timedelta(minutes=6),
            )

    def test_append_clock_cannot_move_behind_ledger_head(self):
        self.init()
        units = self.units_for("visible", 2)
        self.create("later-first", units[0], at=T0 + timedelta(minutes=2))
        with self.assertRaisesRegex(pc.ContractError, "behind the current ledger head"):
            self.create("earlier-second", units[1], at=T0 + timedelta(minutes=1))

    def test_attrition_gates_detect_missing_and_arm_differential_outcomes(self):
        gates = small_gates()
        gates.update(
            {
                "min_resolved_bundles": 3,
                "min_independent_units": 3,
                "min_eligible_units_per_arm": 1,
            }
        )
        self.init(
            end=T0 + timedelta(days=1),
            max_bundles=8,
            gates=gates,
            grace=2,
        )
        visible = self.units_for("visible", 2)
        hidden = self.units_for("hidden", 2)
        allocations = [("visible", *pair) for pair in enumerate(visible)]
        allocations += [("hidden", index + 2, unit) for index, unit in enumerate(hidden)]
        for arm, index, unit in allocations:
            self.create(
                f"attrition-{index}",
                unit,
                at=T0 + timedelta(minutes=1, seconds=index),
                probabilities=(0.8, 0.15, 0.05),
                scar_slug="scar_shared_regression",
            )
        for arm, index, _ in allocations[:3]:
            pc.resolve_scenario(
                self.ledger,
                resolution_input(
                    f"attrition-{index}",
                    arm=arm,
                    recurred=arm == "hidden",
                    work_started_at=T0 + timedelta(minutes=10, seconds=index),
                ),
                now=T0 + timedelta(minutes=20, seconds=index),
            )
        report = pc.experiment_report(self.ledger, now=T0 + timedelta(days=2))
        gate_map = {gate["name"]: gate for gate in report["promotion"]["gates"]}
        self.assertFalse(gate_map["min_resolution_fraction"]["passed"])
        self.assertFalse(gate_map["max_arm_resolution_rate_gap"]["passed"])
        self.assertAlmostEqual(report["quality"]["resolution_fraction"], 0.75)
        self.assertAlmostEqual(report["quality"]["arm_resolution_rate_gap"], 0.5)
        self.assertTrue(report["experiment_closed"])
        self.assertFalse(report["outcome_collection_finalized"])
        self.assertEqual(report["promotion"]["verdict"], "DATA_GATED")

        final = pc.experiment_report(self.ledger, now=T0 + timedelta(days=4))
        self.assertTrue(final["outcome_collection_finalized"])
        self.assertEqual(final["promotion"]["verdict"], "HOLD")

    def test_invalid_signed_gate_fails_before_ledger_creation(self):
        bad = dict(pc.DEFAULT_GATES)
        bad["recurrence_ci_upper_below"] = "not-a-number"
        with self.assertRaisesRegex(pc.ContractError, "finite number"):
            self.init(gates=bad)
        self.assertFalse(self.ledger.exists())

    def test_binary_assignment_key_preserves_boundary_whitespace_bytes(self):
        raw = b" " + b"k" * 30 + b"\n"
        self.assertEqual(len(raw), 32)
        path = self.root / "binary.key"
        path.write_bytes(raw)
        self.assertEqual(pc.read_assignment_key(path), raw)
        self.assertEqual(len(pc.assignment_key_id(raw)), 64)

    def test_hash_chain_detects_edit_and_lock_fails_closed(self):
        self.init()
        unit = self.units_for("visible", 1)[0]
        self.create("tamper", unit)
        original = self.ledger.read_text(encoding="utf-8")
        self.ledger.write_text(
            original.replace(
                "The bounded implementation completes cleanly", "A tampered prediction"
            ),
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaisesRegex(pc.LedgerError, "failed its record hash"):
            pc.verify_ledger(self.ledger)

        locked = self.root / "locked.jsonl"
        pc.start_experiment(
            locked,
            experiment_id="locked-experiment",
            end_at=T0 + timedelta(days=1),
            assignment_key=KEY,
            now=T0,
        )
        lock_path = Path(str(locked) + ".lock")
        lock_path.write_text("fixture lock\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(pc.LedgerError, "inspect the writer"):
                pc.create_scenario(
                    locked,
                    scenario_input("blocked", "unit-blocked"),
                    assignment_key=KEY,
                    now=T0 + timedelta(minutes=1),
                )
        finally:
            lock_path.unlink()

    def test_verify_reports_scope_and_cli_is_explicit(self):
        self.init()
        report = pc.verify_ledger(self.ledger)
        self.assertEqual(report["assignment_status"], "not_checked")
        self.assertIn("archive head_hash", report["integrity_scope"])
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            rc = pc.main(["verify", "--ledger", str(self.ledger)])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])

        with self.assertRaisesRegex(pc.LedgerError, "no experiment"):
            pc.verify_ledger(self.root / "missing.jsonl")

        lock_path = Path(str(self.ledger) + ".lock")
        lock_path.write_text("fixture lock\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(pc.LedgerError, "locked by a writer"):
                pc.verify_ledger(self.ledger)
        finally:
            lock_path.unlink()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                pc.main(["verify"])

    def test_cli_round_trip_uses_explicit_files_and_internal_clocks(self):
        key_path = self.root / "assignment.key"
        key_path.write_bytes(KEY)
        scenario_path = self.root / "scenario.json"
        unit = self.units_for("visible", 1)[0]
        scenario_path.write_text(
            json.dumps(scenario_input("cli-bundle", unit)), encoding="utf-8"
        )

        with mock.patch.object(pc, "_utc_now", return_value=T0):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                rc = pc.main(
                    [
                        "init",
                        "--ledger",
                        str(self.ledger),
                        "--experiment-id",
                        "fixture-experiment",
                        "--end-at",
                        (T0 + timedelta(days=2)).isoformat(),
                        "--assignment-key-file",
                        str(key_path),
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(stdout.getvalue())["shadow_only"])

        with mock.patch.object(pc, "_utc_now", return_value=T0 + timedelta(minutes=1)):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                rc = pc.main(
                    [
                        "create",
                        "--ledger",
                        str(self.ledger),
                        "--input",
                        str(scenario_path),
                        "--assignment-key-file",
                        str(key_path),
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertIsNotNone(json.loads(stdout.getvalue())["scenario_bundle"])

        resolution_path = self.root / "resolution.json"
        resolution_path.write_text(
            json.dumps(resolution_input("cli-bundle", arm="visible")),
            encoding="utf-8",
        )
        with mock.patch.object(pc, "_utc_now", return_value=T0 + timedelta(minutes=3)):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                rc = pc.main(
                    [
                        "resolve",
                        "--ledger",
                        str(self.ledger),
                        "--input",
                        str(resolution_path),
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())["bundle_id"], "cli-bundle")

        with mock.patch.object(pc, "_utc_now", return_value=T0 + timedelta(minutes=4)):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                rc = pc.main(["report", "--ledger", str(self.ledger)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())["promotion"]["verdict"], "DATA_GATED")

    def test_research_translation_is_explicitly_non_biological(self):
        urls = {row["url"] for row in pc.RESEARCH_BASIS}
        self.assertEqual(
            urls,
            {
                "https://www.nature.com/articles/s41593-024-01675-7",
                "https://www.nature.com/articles/s41593-025-01908-3",
                "https://www.nature.com/articles/s41467-025-56731-y",
            },
        )
        self.assertIn("does not claim", pc.__doc__)
        self.assertIn("no hook", pc.__doc__.lower())


if __name__ == "__main__":
    unittest.main()
