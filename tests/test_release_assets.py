from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import normalize_sdist
from scripts import normalize_wheel
from scripts import prepare_release


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = "5.0.2"
TAG = f"v{VERSION}"
SOURCE_COMMIT = "a" * 40
SOURCE_DATE_EPOCH = 315532800


class ReleasePreparationTests(unittest.TestCase):
    def build_distributions(self, output: Path) -> None:
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = str(SOURCE_DATE_EPOCH)
        # The test invokes the current interpreter with a fixed local build command.
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--wheel",
                "--sdist",
                "--outdir",
                str(output),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        wheel = next(output.glob("*.whl"))
        sdist = next(output.glob("*.tar.gz"))
        normalize_wheel.normalize_wheel(wheel, SOURCE_DATE_EPOCH)
        normalize_sdist.normalize_sdist(sdist, SOURCE_DATE_EPOCH)

    def prepare(self, parent: Path, name: str, dist: Path) -> tuple[Path, ...]:
        return prepare_release.prepare_release(
            PROJECT_ROOT,
            dist,
            parent / name,
            VERSION,
            TAG,
            SOURCE_COMMIT,
            SOURCE_DATE_EPOCH,
        )

    def test_repeat_builds_are_byte_identical_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dist = root / "first-dist"
            second_dist = root / "second-dist"
            first_dist.mkdir()
            second_dist.mkdir()
            self.build_distributions(first_dist)
            self.build_distributions(second_dist)
            expected_sdist_inputs = {
                f"wcag_site_pdf_scanner-{VERSION}/.github/release-notes/{TAG}.md",
                f"wcag_site_pdf_scanner-{VERSION}/.github/workflows/release.yml",
            }
            for dist in (first_dist, second_dist):
                with tarfile.open(next(dist.glob("*.tar.gz")), "r:gz") as archive:
                    self.assertTrue(expected_sdist_inputs.issubset({member.name for member in archive.getmembers()}))
            first = self.prepare(root, "first-release", first_dist)
            second = self.prepare(root, "second-release", second_dist)
            self.assertEqual(prepare_release.expected_asset_names(VERSION), tuple(path.name for path in first))
            self.assertEqual([path.read_bytes() for path in first], [path.read_bytes() for path in second])

    def test_release_assets_bind_source_dependencies_and_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self.build_distributions(dist)
            outputs = self.prepare(root, "release", dist)
            by_name = {path.name: path for path in outputs}

            runtime_name = f"WCAG-Site-PDF-Scanner-v{VERSION}.py"
            self.assertEqual(
                (PROJECT_ROOT / "WCAG_Site_PDF_Scanner.py").read_bytes(), by_name[runtime_name].read_bytes()
            )

            checksum_lines = by_name["SHA256SUMS.txt"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(checksum_lines), 4)
            for line in checksum_lines:
                digest, name = line.split("  ", maxsplit=1)
                self.assertEqual(hashlib.sha256(by_name[name].read_bytes()).hexdigest(), digest)

            spdx_name = f"WCAG-Site-PDF-Scanner-v{VERSION}.spdx.json"
            spdx = json.loads(by_name[spdx_name].read_text(encoding="utf-8"))
            dependencies = prepare_release.parse_runtime_dependencies(PROJECT_ROOT)
            self.assertEqual(spdx["spdxVersion"], "SPDX-2.3")
            self.assertEqual(spdx["packages"][0]["versionInfo"], VERSION)
            self.assertEqual(
                spdx["packages"][0]["externalRefs"][0]["referenceLocator"],
                f"pkg:github/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner@{VERSION}",
            )
            self.assertEqual(len(spdx["packages"]), len(dependencies) + 1)
            self.assertEqual(spdx["packages"][0]["filesAnalyzed"], False)

            evidence = json.loads(by_name["release-evidence.json"].read_text(encoding="utf-8"))
            self.assertEqual(evidence["source_commit"], SOURCE_COMMIT)
            self.assertEqual(evidence["source_date_epoch"], SOURCE_DATE_EPOCH)
            self.assertEqual(evidence["version"], VERSION)
            self.assertEqual(evidence["expected_release_assets"], list(prepare_release.expected_asset_names(VERSION)))
            self.assertEqual(len(evidence["archives"]), 2)
            self.assertGreater(len(next(iter(evidence["archives"].values()))), 1)

    def test_invalid_identity_and_existing_output_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self.build_distributions(dist)
            with self.assertRaises(prepare_release.ReleaseError):
                prepare_release.prepare_release(
                    PROJECT_ROOT,
                    dist,
                    root / "version-mismatch",
                    "5.0.3",
                    TAG,
                    SOURCE_COMMIT,
                    SOURCE_DATE_EPOCH,
                )
            with self.assertRaisesRegex(prepare_release.ReleaseError, "timestamp"):
                prepare_release.prepare_release(
                    PROJECT_ROOT,
                    dist,
                    root / "epoch-mismatch",
                    VERSION,
                    TAG,
                    SOURCE_COMMIT,
                    SOURCE_DATE_EPOCH + 2,
                )
            occupied = root / "occupied"
            occupied.mkdir()
            marker = occupied / "preserve.txt"
            marker.write_text("preserve", encoding="utf-8")
            with self.assertRaises(prepare_release.ReleaseError):
                self.prepare(root, "occupied", dist)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
