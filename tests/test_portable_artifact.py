"""Transfer acceptance: exact bytes, immutable source, hostile metadata and retries."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "dreaming/portable_artifact.py"
spec = importlib.util.spec_from_file_location("portable_artifact", MODULE)
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)
REVISION = "1" * 40


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.destination = self.root / "restored"
        self.files = {"src/package/__init__.py": b"VALUE = 42\n",
                      "data/notation.txt": "∀x∈ℝ\r\nx² ≥ 0\nשלום\r\n".encode(),
                      "data/empty.bin": b"", "data/binary.bin": bytes(range(256)) * 8}
        self.receipt = artifact.build(self.files, self.bundle, REVISION, chunk_size=64)
        self.expected = self.receipt["manifest_sha256"]

    def restore(self):
        return artifact.restore(self.bundle, self.destination, self.expected, REVISION)

    def change_manifest(self, edit):
        value = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
        edit(value)
        raw = artifact.canonical(value)
        (self.bundle / "manifest.json").write_bytes(raw)
        self.expected = hashlib.sha256(raw).hexdigest()

    def rejected_without_destination(self, exception=ValueError):
        with self.assertRaises(exception):
            self.restore()
        self.assertFalse(self.destination.exists())

    def test_exact_unicode_binary_empty_and_mixed_newlines(self):
        result = self.restore()
        self.assertEqual(result["status"], "SOURCE_BYTES_RESTORED")
        self.assertFalse(result["full_brain_connected"])
        for name, expected in self.files.items():
            self.assertEqual((self.destination / name).read_bytes(), expected)

    def test_build_is_reproducible(self):
        second = self.root / "second"
        result = artifact.build(dict(reversed(list(self.files.items()))), second, REVISION, 64)
        self.assertEqual(result, self.receipt)
        for path in self.bundle.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())

    def test_existing_destination_is_untouched(self):
        self.destination.mkdir()
        sentinel = self.destination / "authentic.txt"
        sentinel.write_bytes(b"keep original")
        with self.assertRaises(FileExistsError):
            self.restore()
        self.assertEqual(list(self.destination.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"keep original")

    def test_fragment_fills_only_missing_verified_files_and_resumes(self):
        keys = sorted(self.files)
        first, second = self.root / "fragment-a", self.root / "fragment-b"
        a = artifact.build({keys[0]: self.files[keys[0]]}, first, REVISION)
        b = artifact.build({k: self.files[k] for k in keys[1:]}, second, REVISION)
        result = artifact.hydrate(first, a["manifest_sha256"], self.bundle, self.expected, self.destination, REVISION)
        self.assertFalse(result["reference_files_complete"])
        self.assertEqual(result["reference_files_present"], 1)
        result = artifact.hydrate(second, b["manifest_sha256"], self.bundle, self.expected, self.destination, REVISION)
        self.assertTrue(result["reference_files_complete"])
        repeated = artifact.hydrate(first, a["manifest_sha256"], self.bundle, self.expected, self.destination, REVISION)
        self.assertEqual(repeated["added"], [])
        self.assertFalse(repeated["runtime_executed"])
        self.assertFalse(repeated["full_brain_connected"])
        self.assertEqual({k: (self.destination / k).read_bytes() for k in keys}, self.files)

    def test_generated_file_bundles_hydrate_the_complete_original(self):
        output = self.root / "by-file"
        result = artifact.fragment_source(self.bundle, self.expected, REVISION, output)
        index = json.loads((output / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(result["files"], len(self.files))
        self.assertEqual(index["reference_manifest_sha256"], self.expected)
        for entry in reversed(index["files"]):
            restored = artifact.hydrate(output / entry["bundle_path"], entry["manifest_sha256"],
                                       self.bundle, self.expected, self.destination, REVISION)
        self.assertTrue(restored["reference_files_complete"])
        self.assertEqual({k: (self.destination / k).read_bytes() for k in self.files}, self.files)

    def test_fragment_cannot_authorize_itself(self):
        other = self.root / "unapproved"
        receipt = artifact.build({"extra.py": b"VALUE = 1\n"}, other, REVISION)
        with self.assertRaisesRegex(ValueError, "not authorized"):
            artifact.hydrate(other, receipt["manifest_sha256"], self.bundle, self.expected, self.destination, REVISION)
        self.assertFalse(self.destination.exists())

    def test_fragment_wrong_bytes_under_correct_filename_rejected(self):
        other = self.root / "altered"
        receipt = artifact.build({"src/package/__init__.py": b"VALUE = 0\n"}, other, REVISION)
        with self.assertRaisesRegex(ValueError, "not authorized"):
            artifact.hydrate(other, receipt["manifest_sha256"], self.bundle, self.expected, self.destination, REVISION)
        self.assertFalse(self.destination.exists())

    def test_conflicting_existing_source_blocks_all_fragment_writes(self):
        self.destination.mkdir()
        wrong = self.destination / "data/notation.txt"
        wrong.parent.mkdir()
        wrong.write_bytes(b"original user contents")
        with self.assertRaisesRegex(ValueError, "existing source differs"):
            artifact.hydrate(self.bundle, self.expected, self.bundle, self.expected, self.destination, REVISION)
        self.assertEqual(wrong.read_bytes(), b"original user contents")
        self.assertFalse((self.destination / "src").exists())

    def test_partial_transfer_can_resume_by_replacing_missing_immutable_part(self):
        part = self.bundle / "part-00001.hex"
        original = part.read_bytes()
        part.unlink()
        self.rejected_without_destination(FileNotFoundError)
        part.write_bytes(original)
        self.assertEqual(self.restore()["files"], len(self.files))

    def test_corrupted_part_is_rejected(self):
        part = self.bundle / "part-00000.hex"
        raw = part.read_bytes()
        part.write_bytes(b"A" + raw[1:])
        self.rejected_without_destination()

    def test_swapped_parts_are_rejected(self):
        first, second = self.bundle / "part-00000.hex", self.bundle / "part-00001.hex"
        a, b = first.read_bytes(), second.read_bytes()
        first.write_bytes(b)
        second.write_bytes(a)
        self.rejected_without_destination()

    def test_expected_manifest_digest_is_external(self):
        raw = (self.bundle / "manifest.json").read_bytes()
        (self.bundle / "manifest.json").write_bytes(raw + b" ")
        self.rejected_without_destination()

    def test_wrong_revision_rejected_even_with_correct_manifest_hash(self):
        self.change_manifest(lambda m: m.update(source_revision="2" * 40))
        self.rejected_without_destination()

    def test_file_git_identity_checked_independently_of_sha256(self):
        self.change_manifest(lambda m: m["files"][0].update(git_blob_sha1="0" * 40))
        self.rejected_without_destination()

    def test_manifest_must_be_an_object(self):
        raw = b"[]\n"
        (self.bundle / "manifest.json").write_bytes(raw)
        self.expected = hashlib.sha256(raw).hexdigest()
        self.rejected_without_destination()

    def test_paths_reject_traversal_windows_aliases_and_devices(self):
        for name in ("../escape", "/absolute", "C:/escape", "safe\\escape", "a//b",
                     "a/./b", "a./b", "a /b", "nul.txt", "COM1", "x\x00y"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                artifact.safe_relative(name)

    def test_file_directory_and_case_collisions_rejected(self):
        for names in (["Data/x", "data/X"], ["data", "data/x"]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                artifact.checked_paths(names)

    def test_manifest_traversal_rejected_before_writes(self):
        self.change_manifest(lambda m: m["files"][0].update(path="../escape"))
        self.rejected_without_destination()

    def test_gaps_overlap_and_reordering_rejected(self):
        self.change_manifest(lambda m: m["files"][1].update(offset=0))
        self.rejected_without_destination()

    def test_boolean_does_not_count_as_part_index(self):
        self.change_manifest(lambda m: m["parts"][1].update(index=True))
        self.rejected_without_destination()

    def test_decompression_bound_rejects_more_output_than_declared(self):
        # Correct compressed hash, but a false tiny claimed expansion bound.
        def edit(m):
            m["data_bytes"] = 1
            m["files"] = [{"path": "one", "offset": 0, "size": 1,
                           "sha256": artifact.digest(b"a"), "git_blob_sha1": artifact.git_blob(b"a")}]
        self.change_manifest(edit)
        self.rejected_without_destination()

    def test_trailing_compressed_stream_rejected(self):
        manifest = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
        packed = b"".join(bytes.fromhex((self.bundle / p["path"]).read_text(encoding="ascii"))
                          for p in manifest["parts"]) + b"trailing"
        encoded = packed.hex().encode("ascii") + b"\n"
        (self.bundle / "part-00000.hex").write_bytes(encoded)
        def edit(m):
            m["packed_bytes"] = len(packed)
            m["packed_sha256"] = artifact.digest(packed)
            m["parts"] = [{"index": 0, "path": "part-00000.hex", "offset": 0,
                           "size": len(packed), "sha256": artifact.digest(packed),
                           "encoded_size": len(encoded), "encoded_sha256": artifact.digest(encoded)}]
        self.change_manifest(edit)
        self.rejected_without_destination()

    def test_linked_destination_parent_is_rejected(self):
        link = self.root / "linked"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            if os.name != "nt" or not hasattr(Path, "is_junction"):
                self.skipTest("OS does not permit creating a symlink")
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(self.root)],
                                    capture_output=True, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            if result.returncode or not link.is_junction():
                self.skipTest("OS does not permit creating a symlink or junction")
        self.destination = link / "escape"
        self.rejected_without_destination()

    def test_git_source_reads_committed_bytes_not_dirty_checkout(self):
        repo = self.root / "repo"
        repo.mkdir()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  check=True, creationflags=flags).stdout
        git("init", "-q")
        (repo / "module.py").write_bytes(b"ORIGINAL = True\n")
        git("add", "module.py")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "source fixture")
        revision = git("rev-parse", "HEAD").decode().strip()
        (repo / "module.py").write_bytes(b"DIRTY = True\n")
        self.assertEqual(artifact.git_files(repo, revision, ["module.py"]),
                         {"module.py": b"ORIGINAL = True\n"})
        with self.assertRaises(ValueError):
            artifact.git_files(repo, revision, ["missing.py"])
        self.assertEqual((repo / "module.py").read_bytes(), b"DIRTY = True\n")

    def test_cli_failure_never_claims_full_runtime(self):
        result = subprocess.run([sys.executable, str(MODULE), "restore", "--bundle", str(self.bundle),
                                 "--manifest-sha256", "0" * 64, "--revision", REVISION,
                                 "--destination", str(self.destination)], capture_output=True,
                                encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stderr)["full_brain_connected"])
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
