from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

import WCAG_Site_PDF_Scanner as scanner
from examples.build_sample_report import build_sample_report


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PublicDocumentationTests(unittest.TestCase):
    @staticmethod
    def _public_markdown_files() -> list[Path]:
        excluded_parts = {".git", ".venv", "__pycache__", "build", "dist", "env", "venv"}
        return [
            path
            for path in sorted(REPOSITORY_ROOT.rglob("*.md"))
            if not excluded_parts.intersection(path.relative_to(REPOSITORY_ROOT).parts)
            and not any(part.endswith(".egg-info") for part in path.relative_to(REPOSITORY_ROOT).parts)
        ]

    def test_public_markdown_contains_no_unicode_em_dash(self):
        files_with_em_dash = []
        for path in self._public_markdown_files():
            if "\N{EM DASH}" in path.read_text(encoding="utf-8"):
                files_with_em_dash.append(str(path.relative_to(REPOSITORY_ROOT)))
        self.assertEqual(files_with_em_dash, [])

    def test_relative_markdown_links_resolve(self):
        missing_links = []
        pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
        for path in self._public_markdown_files():
            for raw_target in pattern.findall(path.read_text(encoding="utf-8")):
                target = raw_target.strip().split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (path.parent / unquote(target)).resolve()
                if not resolved.exists():
                    missing_links.append(f"{path.relative_to(REPOSITORY_ROOT)} -> {raw_target}")
        self.assertEqual(missing_links, [])

    def test_readme_sample_links_follow_the_current_checkout(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("(examples/sample-report/report-preview.png)", readme)
        self.assertIn("](examples/sample-report/report.html)", readme)
        self.assertIn("](examples/sample-report/report.json)", readme)
        self.assertIn("](examples/sample-site/index.html)", readme)
        self.assertNotIn("/main/examples/sample-report/", readme)
        self.assertNotIn("/main/examples/sample-site/", readme)


class PackageMetadataTests(unittest.TestCase):
    def test_source_and_package_versions_match(self):
        pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", pyproject)
        self.assertIsNotNone(project)
        version = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group(0))
        self.assertIsNotNone(version)
        self.assertEqual(version.group(1), scanner.APP_VERSION)


class SampleReportTests(unittest.TestCase):
    def test_committed_reports_match_production_generators(self):
        committed_dir = REPOSITORY_ROOT / "examples" / "sample-report"
        with tempfile.TemporaryDirectory(prefix="wcag-sample-report-") as temp_dir:
            generated_html, generated_json = build_sample_report(Path(temp_dir))
            self.assertEqual(generated_html.read_bytes(), (committed_dir / "report.html").read_bytes())
            self.assertEqual(generated_json.read_bytes(), (committed_dir / "report.json").read_bytes())

    def test_preview_has_documented_viewport(self):
        preview = REPOSITORY_ROOT / "examples" / "sample-report" / "report-preview.png"
        with Image.open(preview) as image:
            self.assertEqual(image.size, (1440, 900))
            self.assertEqual(image.format, "PNG")

    def test_fixture_contains_no_local_or_customer_identifiers(self):
        fixture_dir = REPOSITORY_ROOT / "examples" / "sample-report"
        report_text = "\n".join(
            (fixture_dir / name).read_text(encoding="utf-8") for name in ("report.html", "report.json")
        )
        self.assertIn("https://example.invalid/community-services", report_text)
        self.assertNotIn("Jeffrey", report_text)
        self.assertNotRegex(report_text, r"[A-Za-z]:\\")


if __name__ == "__main__":
    unittest.main()
