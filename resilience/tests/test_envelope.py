import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys


MODULE = Path(__file__).resolve().parents[1] / "envelope.py"
SPEC = importlib.util.spec_from_file_location("envelope", MODULE)
envelope = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
# register before exec: @dataclass resolves its own
# module via sys.modules and dies on None
sys.modules[SPEC.name] = envelope
SPEC.loader.exec_module(envelope)


class EnvelopeTests(unittest.TestCase):
    def test_streaming_round_trip_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.bin"
            encrypted = root / "source.bin.enc"
            restored = root / "restored.bin"
            source.write_bytes((b"brain-state\x00" * 600_000) + b"tail")
            key = bytes(range(32))
            aad = envelope.canonical_json_bytes({"run": "test", "artifact": source.name})
            metadata = envelope.encrypt_stream(source, encrypted, key, aad, nonce=b"n" * 12)
            receipt = envelope.decrypt_stream(encrypted, restored, key, aad, metadata)
            self.assertEqual(restored.read_bytes(), source.read_bytes())
            self.assertEqual(receipt["sha256"], metadata["plaintext_sha256"])
            self.assertNotIn(b"brain-state", encrypted.read_bytes())

            ciphertext = bytearray(encrypted.read_bytes())
            ciphertext[len(ciphertext) // 2] ^= 1
            encrypted.write_bytes(ciphertext)
            with self.assertRaises(envelope.EnvelopeError):
                envelope.decrypt_stream(encrypted, root / "tampered.bin", key, aad, metadata)

    def test_wrong_additional_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            encrypted = root / "encrypted"
            source.write_bytes(b"private history")
            metadata = envelope.encrypt_stream(source, encrypted, b"k" * 32, b"right", nonce=b"i" * 12)
            with self.assertRaises(envelope.EnvelopeError):
                envelope.decrypt_stream(encrypted, root / "restored", b"k" * 32, b"wrong", metadata)


if __name__ == "__main__":
    unittest.main()
