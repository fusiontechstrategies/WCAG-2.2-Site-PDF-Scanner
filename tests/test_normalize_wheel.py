from __future__ import annotations

import base64
import csv
import hashlib
import io
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import normalize_wheel as normalizer


SOURCE_DATE_EPOCH = 315532800


def record_digest(value: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


class NormalizeWheelTests(unittest.TestCase):
    @staticmethod
    def make_wheel(path: Path, newline: bytes, compression: int, creator_system: int) -> None:
        dist_info = "synthetic-1.0.dist-info"
        record_name = f"{dist_info}/RECORD"
        values = {
            "synthetic.py": b"VALUE = 1\n",
            f"{dist_info}/METADATA": newline.join((b"Metadata-Version: 2.4", b"Name: synthetic", b"Version: 1.0", b"")),
            f"{dist_info}/WHEEL": newline.join(
                (b"Wheel-Version: 1.0", b"Root-Is-Purelib: true", b"Tag: py3-none-any", b"")
            ),
        }
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator=newline.decode("ascii"))
        for name, value in sorted(values.items()):
            writer.writerow((name, record_digest(value), str(len(value))))
        writer.writerow((record_name, "", ""))
        values[record_name] = output.getvalue().encode("utf-8")
        with zipfile.ZipFile(path, mode="w", compression=compression) as archive:
            for name, value in values.items():
                member = zipfile.ZipInfo(name, (2024, 1, 2, 3, 4, 4))
                member.compress_type = compression
                member.create_system = creator_system
                member.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(member, value)

    def test_platform_specific_wheels_normalize_to_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.whl"
            second = root / "second.whl"
            self.make_wheel(first, b"\r\n", zipfile.ZIP_DEFLATED, 0)
            self.make_wheel(second, b"\n", zipfile.ZIP_STORED, 3)
            first_hash = normalizer.normalize_wheel(first, SOURCE_DATE_EPOCH)
            second_hash = normalizer.normalize_wheel(second, SOURCE_DATE_EPOCH)
            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            original = first.read_bytes()
            self.assertEqual(first_hash, normalizer.normalize_wheel(first, SOURCE_DATE_EPOCH))
            self.assertEqual(original, first.read_bytes())
            with zipfile.ZipFile(first) as archive:
                members = archive.infolist()
                metadata = archive.read("synthetic-1.0.dist-info/METADATA")
            self.assertEqual([member.filename for member in members], sorted(member.filename for member in members))
            self.assertNotIn(b"\r", metadata)
            self.assertTrue(all(member.compress_type == zipfile.ZIP_STORED for member in members))
            self.assertTrue(all(member.create_system == 3 for member in members))
            self.assertTrue(all(stat.S_IMODE(member.external_attr >> 16) == 0o644 for member in members))

    def test_record_mismatch_is_rejected_without_changing_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.whl"
            self.make_wheel(path, b"\n", zipfile.ZIP_STORED, 3)
            original = path.read_bytes()
            with zipfile.ZipFile(path) as archive:
                members = [(member, archive.read(member)) for member in archive.infolist()]
            replacement = path.with_suffix(".replacement")
            with zipfile.ZipFile(replacement, mode="w") as archive:
                for member, value in members:
                    archive.writestr(member, b"CHANGED\n" if member.filename == "synthetic.py" else value)
            replacement.replace(path)
            corrupted = path.read_bytes()
            with self.assertRaisesRegex(normalizer.WheelNormalizationError, "hash mismatch"):
                normalizer.normalize_wheel(path, SOURCE_DATE_EPOCH)
            self.assertNotEqual(original, corrupted)
            self.assertEqual(path.read_bytes(), corrupted)

    def test_case_colliding_members_and_invalid_epoch_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collision = root / "collision.whl"
            self.make_wheel(collision, b"\n", zipfile.ZIP_STORED, 3)
            with zipfile.ZipFile(collision, mode="a") as archive:
                archive.writestr("SYNTHETIC.py", b"VALUE = 1\n")
            original = collision.read_bytes()
            with self.assertRaisesRegex(normalizer.WheelNormalizationError, "nonportable duplicate"):
                normalizer.normalize_wheel(collision, SOURCE_DATE_EPOCH)
            self.assertEqual(collision.read_bytes(), original)

            valid = root / "valid.whl"
            self.make_wheel(valid, b"\n", zipfile.ZIP_STORED, 3)
            valid_bytes = valid.read_bytes()
            for epoch in (SOURCE_DATE_EPOCH - 1, 0x100000000):
                with self.subTest(epoch=epoch):
                    with self.assertRaisesRegex(normalizer.WheelNormalizationError, "range"):
                        normalizer.normalize_wheel(valid, epoch)
                    self.assertEqual(valid.read_bytes(), valid_bytes)


if __name__ == "__main__":
    unittest.main()
