"""Distinguish typed fingerprints, semantic config changes and raw-byte changes."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import connector as c
import fingerprint_evidence as evidence


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brain-fingerprint-controls-")
        self.profile = Path(self.temp.name)
        self.skill = self.profile / "skills/brain-framework"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "references").mkdir()
        (self.skill / "SKILL.md").write_text("SYNTHETIC fingerprint controls, not admission\n")
        (self.skill / "scripts/fixture.py").write_text("# code digest control\n")
        self.config = self.profile / "config.toml"
        self.config.write_text('[hooks]\nmode = "fixture"\n[sandbox_workspace_write]\nwritable_roots = []\n')
        self.hooks = self.profile / "hooks.json"
        self.hooks.write_text('{"hooks":{"SessionStart":[]}}')
        self.d = {"id": "synthetic", "profile": str(self.profile), "home": str(self.profile / "virtual-home"),
                  "source_revision": "synthetic-test-only", "runtime_code": {}, "enabled": [], "handlers": {},
                  "managed_hook_groups": {}, "hook_config": str(self.hooks)}

    def tearDown(self):
        self.temp.cleanup()

    def observe(self):
        return evidence.inspect(self.d)

    def test_different_kinds_coexist_for_the_exact_same_configuration(self):
        value = self.observe()
        self.assertEqual(value["deployment"]["kind"], "deployment")
        self.assertEqual(value["admission"]["kind"], "admission")
        self.assertNotEqual(value["deployment"]["value"], value["admission"]["value"])
        expected = hashlib.sha256(json.dumps([value["deployment"]["value"], None]).encode()).hexdigest()
        self.assertEqual(value["admission"]["value"], expected)

    def test_setup_evidence_changes_admission_kind_without_changing_deployment_kind(self):
        before = self.observe()
        ledger = self.profile / "brain-framework/setup-evidence.json"
        ledger.parent.mkdir()
        ledger.write_text('{"synthetic":true}')
        after = self.observe()
        self.assertEqual(before["deployment"], after["deployment"])
        self.assertNotEqual(before["admission"], after["admission"])

    def test_toml_serialization_changes_raw_bytes_but_not_bound_semantics(self):
        before = self.observe()
        self.config.write_text('# serialization-only control\n[sandbox_workspace_write]\nwritable_roots=[]\n[hooks]\nmode="fixture"\n')
        after = self.observe()
        self.assertNotEqual(before["raw_config_sha256"], after["raw_config_sha256"])
        self.assertEqual(before["native_hook_trust_semantic_sha256"], after["native_hook_trust_semantic_sha256"])
        self.assertEqual(before["deployment"], after["deployment"])
        self.assertEqual(before["admission"], after["admission"])

    def test_native_trust_semantic_change_reopens_original_fingerprint(self):
        before = self.observe()
        self.config.write_text(self.config.read_text().replace('mode = "fixture"', 'mode = "changed-synthetic-control"'))
        after = self.observe()
        self.assertNotEqual(before["native_hook_trust_semantic_sha256"], after["native_hook_trust_semantic_sha256"])
        self.assertNotEqual(before["deployment"], after["deployment"])

    def test_native_write_scope_change_reopens_original_fingerprint(self):
        before = self.observe()
        self.config.write_text(self.config.read_text().replace('writable_roots = []', 'writable_roots = ["synthetic-extra-scope"]'))
        after = self.observe()
        self.assertNotEqual(before["native_write_scope_semantic_sha256"], after["native_write_scope_semantic_sha256"])
        self.assertNotEqual(before["deployment"], after["deployment"])

    def test_hook_json_serialization_still_reopens_byte_bound_original_qualification(self):
        before = self.observe()
        self.hooks.write_text(json.dumps(json.loads(self.hooks.read_text()), indent=2))
        after = self.observe()
        self.assertEqual(before["effective_hooks_semantic_sha256"], after["effective_hooks_semantic_sha256"])
        self.assertNotEqual(before["effective_hooks_raw_sha256"], after["effective_hooks_raw_sha256"])
        self.assertNotEqual(before["deployment"], after["deployment"])

    def test_package_code_change_is_not_normalized_out(self):
        before = self.observe()
        path = self.skill / "scripts/fixture.py"
        path.write_text(path.read_text() + "# changed code\n")
        self.assertNotEqual(before["deployment"], self.observe()["deployment"])

    def test_read_only_inspection_changes_no_files(self):
        before = {str(p.relative_to(self.profile)): p.read_bytes() for p in self.profile.rglob("*") if p.is_file()}
        self.observe()
        after = {str(p.relative_to(self.profile)): p.read_bytes() for p in self.profile.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
