import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "dreaming"))

import compute_sentinel as cs
import compute_sense


class PressureClassificationTests(unittest.TestCase):
    def classify(self, *, load1=0.0, cpu=0.0, memory=0.0, system=0.0):
        return cs.classify_pressure(load1, cpu, memory, system)

    def test_quiet_box_is_open(self):
        self.assertEqual(self.classify(), ("ok", "open", []))

    def test_each_distress_boundary_is_pinned(self):
        cases = (
            ({"cpu": 40.0}, "cpu_pressure"),
            ({"memory": 15.0}, "memory_pressure"),
            ({"system": 20.0}, "stack_squeezed"),
            ({"load1": 10.0, "cpu": 10.0}, "cpu_pressure"),
        )
        for values, reason in cases:
            with self.subTest(values=values):
                status, mode, reasons = self.classify(**values)
                self.assertEqual(status, "distress")
                self.assertEqual(mode, "throttle")
                self.assertIn(reason, reasons)

    def test_bare_load_warns_without_becoming_distress(self):
        self.assertEqual(
            self.classify(load1=7.0),
            ("warn", "open", ["elevated_pressure"]),
        )

    def test_sustained_cpu_pressure_throttles_before_distress(self):
        self.assertEqual(
            self.classify(cpu=35.0),
            (
                "warn",
                "throttle",
                ["elevated_pressure", "saturation_throttle"],
            ),
        )

    def test_missing_critical_telemetry_warns_and_throttles(self):
        self.assertEqual(
            self.classify(cpu=None),
            ("warn", "throttle", ["telemetry_degraded:psi_cpu"]),
        )

    def test_degraded_telemetry_does_not_hide_measured_distress(self):
        status, mode, reasons = self.classify(cpu=None, system=20.0)
        self.assertEqual(status, "distress")
        self.assertEqual(mode, "throttle")
        self.assertEqual(
            reasons,
            ["stack_squeezed", "telemetry_degraded:psi_cpu"],
        )


class ProtectedHealthTests(unittest.TestCase):
    def test_all_active_is_healthy(self):
        self.assertEqual(
            cs.classify_protected_health({"bot": "active", "observer": "active"}),
            ("ok", []),
        )

    def test_inactive_failed_and_unknown_remain_visible_alerts(self):
        health, alerts = cs.classify_protected_health(
            {
                "observer": "unknown",
                "bot-b": "failed",
                "bot-a": "inactive",
                "healthy": "active",
            }
        )
        self.assertEqual(health, "alert")
        self.assertEqual(
            alerts,
            ["protected_unit_not_active:bot-a,bot-b,observer"],
        )

    def test_protected_alert_does_not_leak_into_pressure_status_or_mode(self):
        pressure = cs.classify_pressure(0.0, 0.0, 0.0, 0.0)
        health = cs.classify_protected_health({"bot": "failed"})
        self.assertEqual(pressure, ("ok", "open", []))
        self.assertEqual(
            health,
            ("alert", ["protected_unit_not_active:bot"]),
        )

    def test_weekend_all_units_down_with_zero_psi_is_not_compute_distress(self):
        pressure = cs.classify_pressure(4.97, 0.0, 0.01, 0.0)
        health = cs.classify_protected_health(
            {unit: "inactive" for unit in cs.UNITS}
        )
        self.assertEqual(pressure, ("ok", "open", []))
        self.assertEqual(health[0], "alert")
        self.assertEqual(
            health[1],
            ["protected_unit_not_active:" + ",".join(sorted(cs.UNITS))],
        )

    def test_stack_pressure_and_protected_alert_coexist_independently(self):
        pressure = cs.classify_pressure(0.0, 0.0, 0.0, 20.0)
        health = cs.classify_protected_health({"bot": "failed"})
        self.assertEqual(pressure, ("distress", "throttle", ["stack_squeezed"]))
        self.assertEqual(
            health,
            ("alert", ["protected_unit_not_active:bot"]),
        )

    def test_unit_observation_is_read_only(self):
        completed = SimpleNamespace(stdout="active\n")
        with mock.patch.object(cs.subprocess, "run", return_value=completed) as run:
            self.assertEqual(cs.unit_state("example-prod-1"), "active")
        run.assert_called_once_with(
            ["systemctl", "is-active", "example-prod-1"],
            capture_output=True,
            text=True,
            timeout=5,
        )


class ProtectedStackInstinctTests(unittest.TestCase):
    def test_protected_alert_has_a_distinct_non_authoritative_message(self):
        signature, context = compute_sense._protected_alert_context(
            {
                "azure_protected_alerts": [
                    "protected_unit_not_active:bot-b",
                    "protected_unit_not_active:bot-a",
                ]
            }
        )
        self.assertEqual(
            signature,
            "protected_unit_not_active:bot-a,protected_unit_not_active:bot-b",
        )
        self.assertIn("This alert alone is not compute distress", context)
        self.assertIn("does not authorize pausing or shedding", context)
        self.assertIn("never weakens any simultaneous", context)
        self.assertIn("never touch, restart, renice, or signal", context)
        self.assertNotIn("PAUSE your current work", context)

    def test_nested_azure_alert_is_supported_during_rollout(self):
        signature, context = compute_sense._protected_alert_context(
            {"azure": {"protected_alerts": ["protected_unit_not_active:observer"]}}
        )
        self.assertEqual(signature, "protected_unit_not_active:observer")
        self.assertIn("OBSERVATION", context)

    def test_stale_protected_telemetry_is_not_presented_as_current(self):
        _, context = compute_sense._protected_alert_context(
            {"azure_protected_alerts": ["protected_telemetry_stale"]}
        )
        self.assertIn("telemetry is stale", context)
        self.assertIn("no last-known unit state is current", context)

    def test_no_protected_alert_stays_silent(self):
        self.assertEqual(compute_sense._protected_alert_context({}), (None, None))

    def test_protected_alert_cannot_replace_deduplicated_distress(self):
        self.assertFalse(compute_sense._protected_alert_can_emit("distress", None))
        self.assertTrue(
            compute_sense._protected_alert_can_emit("distress", "pressure context")
        )
        self.assertTrue(compute_sense._protected_alert_can_emit("ok", None))

    def test_recovery_clears_protected_dedup_for_a_real_recurrence(self):
        seen = {
            "session|protected-health|same-alert": 1,
            "session|distress|cpu": 2,
            "other|protected-health|same-alert": 3,
        }
        compute_sense._clear_protected_dedup(seen, "session")
        self.assertEqual(
            seen,
            {
                "session|distress|cpu": 2,
                "other|protected-health|same-alert": 3,
            },
        )

    def test_degraded_warning_does_not_claim_resource_saturation(self):
        message = compute_sense._warn_notch(
            {"mode": "throttle", "azure": {"telemetry_degraded": True}},
            ["azure_pressure_warn"],
        )
        self.assertIn("telemetry is degraded", message)
        self.assertNotIn("saturated", message)

    def test_measured_saturation_keeps_the_immediate_notch_down(self):
        message = compute_sense._warn_notch(
            {"mode": "throttle", "azure": {}},
            ["saturation_throttle"],
        )
        self.assertIn("CPU/GPU is saturated", message)

    def test_degraded_guidance_does_not_send_heavy_work_to_azure(self):
        guidance = compute_sense._warn_guidance(
            {"azure": {"telemetry_degraded": True}},
            ["azure_pressure_warn"],
        )
        self.assertIn("do not start anything heavy", guidance)
        self.assertIn("until direct telemetry recovers", guidance)
        self.assertNotIn("research slice for anything heavy", guidance)

    def test_unreachable_azure_forbids_new_heavy_work(self):
        state = {"mode": "throttle", "azure": None}
        reasons = ["azure_unreachable"]
        self.assertIn("telemetry is degraded", compute_sense._warn_notch(state, reasons))
        guidance = compute_sense._warn_guidance(state, reasons)
        self.assertIn("do not start anything heavy", guidance)
        self.assertNotIn("research slice for anything heavy", guidance)


if __name__ == "__main__":
    unittest.main()
