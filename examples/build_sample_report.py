"""Regenerate the deterministic, synthetic web-report fixture."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import WCAG_Site_PDF_Scanner as scanner  # noqa: E402


SAMPLE_TARGET = "https://example.invalid/community-services"
SAMPLE_TIMESTAMP = "2026-08-27T12:00:00+00:00"
SAMPLE_DURATION_SECONDS = 0.42


def build_sample_report(output_dir: Path) -> tuple[Path, Path]:
    """Render real HTML and JSON exporters from a synthetic static analysis."""
    source_path = Path(__file__).resolve().parent / "sample-site" / "index.html"
    html_source = source_path.read_text(encoding="utf-8")
    analyzer = scanner.StaticAnalyzer(scanner.WCAGLevel.AA)
    issues, passed_checks = analyzer.analyze(html_source, SAMPLE_TARGET)

    report = scanner.AccessibilityReport(
        target=SAMPLE_TARGET,
        timestamp=SAMPLE_TIMESTAMP,
        wcag_level_tested=scanner.WCAGLevel.AA,
        issues=issues,
        passed_checks=passed_checks,
        analysis_duration=SAMPLE_DURATION_SECONDS,
    )
    report.all_urls_crawled.add(SAMPLE_TARGET)
    report.compile_summary()

    output_dir.mkdir(parents=True, exist_ok=True)
    generator = scanner.ReportGenerator(report, output_dir)
    generator.generate_html()
    generator.generate_json()
    return output_dir / "report.html", output_dir / "report.json"


def render_report_preview(report_path: Path, preview_path: Path) -> None:
    """Capture a fixed-size preview from the generated report when Chromium is available."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.goto(report_path.resolve().as_uri(), wait_until="load")
        page.screenshot(path=str(preview_path), full_page=False)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "sample-report",
        help="Directory that receives report.html and report.json.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also render report-preview.png with the installed Playwright Chromium browser.",
    )
    args = parser.parse_args()
    report_path, _ = build_sample_report(args.output_dir.resolve())
    if args.preview:
        render_report_preview(report_path, args.output_dir.resolve() / "report-preview.png")


if __name__ == "__main__":
    main()
