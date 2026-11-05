import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


def _load(name, path):
    """Load a module by path, registered under `name`.

    The registration is not decoration. @dataclass resolves its own module through
    sys.modules[cls.__module__]; if the module was executed without being registered
    there, that lookup returns None and the decorator dies with an AttributeError
    about __dict__ before any test runs. scan_secrets.py is a dataclass module, so
    loading it unregistered took this whole file out at collection time -- and with
    it the rest of the directory, including a genuinely failing launcher test.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = Path(__file__).resolve().parents[1] / "sanitized_snapshot.py"
snapshot = _load("sanitized_snapshot_under_test", MODULE)

SCANNER_MODULE = Path(__file__).resolve().parents[2] / "dreaming" / "scan_secrets.py"
scanner = _load("scan_secrets_under_test", SCANNER_MODULE)


class SanitizedSnapshotTests(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        subprocess.check_call(["git", "init", "-b", "main"], cwd=source, stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=source)
        subprocess.check_call(["git", "config", "user.name", "snapshot test"], cwd=source)
        return source

    def test_export_redacts_values_and_excludes_raw_transcripts_and_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            destination = root / "clean"
            source = self.make_source(root)
            (source / "RESTORE.md").write_text("restore\n", encoding="utf-8")
            token = "123456789:q7N_Zp2Lm8Rk4Vx1Tc9Bw6Hs3Fa0Jd5Gy"
            prose_password = "CorrectHorseBattery"
            bearer = "eyJheader.payload.signatureValue"
            (source / "memory.md").write_text(
                f"Telegram token {token}\npassword is {prose_password}\nAuthorization: Bearer {bearer}\n",
                encoding="utf-8",
            )
            raw = source / "11_raw_session_transcripts"
            raw.mkdir()
            (raw / "session.jsonl").write_text(f"raw {token}\n", encoding="utf-8")
            (source / ".env").write_text("API_KEY=real-looking-value-123456789\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "RESTORE.md", "memory.md", "11_raw_session_transcripts/session.jsonl"], cwd=source)
            subprocess.check_call(["git", "add", "-f", ".env"], cwd=source)
            subprocess.check_call(["git", "commit", "-m", "fixture"], cwd=source, stdout=subprocess.DEVNULL)

            report = snapshot.export(source, destination, root / "state.json")
            clean = (destination / "memory.md").read_text(encoding="utf-8")
            self.assertNotIn(token, clean)
            self.assertNotIn(prose_password, clean)
            self.assertNotIn(bearer, clean)
            self.assertIn("<<REDACTED:telegram_token>>", clean)
            self.assertFalse((destination / "11_raw_session_transcripts").exists())
            self.assertFalse((destination / ".env").exists())
            self.assertEqual(report["redactions"]["telegram_token"], 1)
            self.assertEqual(report["redactions"]["plain_password"], 1)
            self.assertEqual(report["redactions"]["authorization_bearer"], 1)
            self.assertEqual(report["excluded"]["raw_transcripts"], 1)
            self.assertEqual(report["excluded"]["sensitive_path"], 1)

    def test_content_fingerprint_changes_when_dirty_bytes_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.make_source(root)
            path = source / "memory.md"
            path.write_text("one\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "memory.md"], cwd=source)
            subprocess.check_call(["git", "commit", "-m", "fixture"], cwd=source, stdout=subprocess.DEVNULL)
            path.write_text("two\n", encoding="utf-8")
            first = snapshot.source_content_fingerprint(source)
            path.write_text("three\n", encoding="utf-8")
            second = snapshot.source_content_fingerprint(source)
            self.assertNotEqual(first, second)

    def test_history_gate_catches_secret_removed_from_current_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.make_source(root)
            path = source / "memory.md"
            path.write_text("password is CorrectHorseBattery\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "memory.md"], cwd=source)
            subprocess.check_call(["git", "commit", "-m", "unsafe"], cwd=source, stdout=subprocess.DEVNULL)
            path.write_text("credential removed\n", encoding="utf-8")
            subprocess.check_call(["git", "commit", "-am", "scrub current tree"], cwd=source, stdout=subprocess.DEVNULL)
            with self.assertRaises(snapshot.SnapshotError):
                snapshot._scan_history(source)

    def test_detectors_cover_unquoted_aws_secret_encrypted_key_and_standalone_jwt(self):
        aws_value = "Nz7qP4mV2sL8dR5k" + "Xc9wT3hY6uJ1fB0aQe/GiKpO"
        encrypted_key = "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----"
        jwt = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"aws_secret_access_key={aws_value}\n{encrypted_key}\n{jwt}\n"
        redacted, counts = snapshot.redact(text)
        self.assertNotIn(aws_value, redacted)
        self.assertNotIn(encrypted_key, redacted)
        self.assertNotIn(jwt, redacted)
        self.assertGreaterEqual(counts["secret_assignment"], 1)
        self.assertGreaterEqual(counts["private_key"], 1)
        self.assertGreaterEqual(counts["jwt"], 1)
        names = {name for name, _ in scanner.scan(text)}
        self.assertTrue({"secret assignment", "private key", "JWT"}.issubset(names))

    def test_external_claim_callback_runs_immediately_before_push(self):
        callback = mock.Mock()
        completed = subprocess.CompletedProcess(["git", "push"], 0, "", "")
        with mock.patch.object(snapshot, "_run", return_value=completed) as command:
            snapshot._push(Path.cwd(), "a" * 40, callback)
        callback.assert_called_once_with()
        self.assertEqual(command.call_args.args[0][:2], ["git", "push"])


if __name__ == "__main__":
    unittest.main()
