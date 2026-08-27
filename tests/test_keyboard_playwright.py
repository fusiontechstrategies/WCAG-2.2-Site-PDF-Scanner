from __future__ import annotations

import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

import WCAG_Site_PDF_Scanner as scanner


PAGE_HTML = b"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Keyboard regression</title></head>
<body tabindex="-1">
  <a id="first" href="#target">Skip to target</a>
  <button id="second" type="button">Open</button>
  <input id="third" aria-label="Search">
  <button id="excluded" type="button" tabindex="-1">Excluded</button>
  <main id="target">Target</main>
</body>
</html>
"""

SAMPLE_REPORT = Path(__file__).resolve().parents[1] / "examples" / "sample-report" / "report.html"


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE_HTML)))
        self.end_headers()
        self.wfile.write(PAGE_HTML)

    def log_message(self, _format, *_args):
        return None


class KeyboardNavigationPlaywrightTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)

    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        self.page = await self.browser.new_page()
        port = self.server.server_address[1]
        await self.page.goto(f"http://127.0.0.1:{port}/", wait_until="load")

    async def asyncTearDown(self):
        await self.page.close()
        await self.browser.close()
        await self.playwright.stop()

    async def test_keyboard_check_executes_and_records_expected_pass(self):
        analyzer = scanner.DynamicAnalyzer(scanner.WCAGLevel.AA)
        analyzer.page = self.page
        analyzer.url = self.page.url
        analyzer.issues = []
        analyzer.passed = []

        with tempfile.TemporaryDirectory(prefix="wcag-keyboard-regression-") as temp_dir:
            analyzer.screenshot_dir = Path(temp_dir)
            await analyzer._check_2_1_1_keyboard_navigation()

        keyboard_issues = [issue for issue in analyzer.issues if issue.criterion in {"2.1.1", "2.1.2"}]
        keyboard_passes = [passed for passed in analyzer.passed if passed.criterion == "2.1.1"]
        self.assertEqual(keyboard_issues, [])
        self.assertEqual(len(keyboard_passes), 1)
        self.assertEqual(keyboard_passes[0].elements_checked, 3)


class SampleReportPlaywrightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        self.page = await self.browser.new_page(viewport={"width": 1440, "height": 900})
        self.page_errors: list[str] = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        await self.page.goto(SAMPLE_REPORT.as_uri(), wait_until="load")

    async def asyncTearDown(self):
        await self.page.close()
        await self.browser.close()
        await self.playwright.stop()

    async def test_report_landmarks_filters_and_live_count_are_named(self):
        self.assertEqual(await self.page.locator("main").count(), 1)
        self.assertEqual(await self.page.locator("h1").count(), 1)
        self.assertGreaterEqual(await self.page.locator("h2").count(), 3)

        for label in (
            "Search findings",
            "Filter by severity",
            "Filter by WCAG level",
            "Filter by finding status",
            "Filter by WCAG principle",
            "Filter by analysis source",
        ):
            self.assertEqual(await self.page.get_by_label(label, exact=True).count(), 1)

        search = self.page.get_by_label("Search findings", exact=True)
        await search.fill("no-such-finding")
        self.assertEqual(await self.page.locator("#resultCount").inner_text(), "0 of 4 shown")
        self.assertEqual(await self.page.locator("#resultCount").get_attribute("aria-live"), "polite")
        self.assertEqual(self.page_errors, [])

    async def test_finding_expansion_supports_keyboard_and_bulk_controls(self):
        headers = self.page.locator(".finding-header")
        self.assertEqual(await headers.count(), 4)
        first = headers.first
        detail_id = await first.get_attribute("aria-controls")
        detail = self.page.locator(f"#{detail_id}")

        await first.focus()
        await first.press("Enter")
        self.assertEqual(await first.get_attribute("aria-expanded"), "true")
        self.assertEqual(await detail.get_attribute("aria-hidden"), "false")
        self.assertTrue(await detail.is_visible())

        await first.press("Space")
        self.assertEqual(await first.get_attribute("aria-expanded"), "false")
        self.assertEqual(await detail.get_attribute("aria-hidden"), "true")
        self.assertFalse(await detail.is_visible())

        await self.page.locator("#expandAll").click()
        self.assertEqual(await headers.evaluate_all("els => els.map(el => el.getAttribute('aria-expanded'))"), ["true"] * 4)
        await self.page.locator("#collapseAll").click()
        self.assertEqual(await headers.evaluate_all("els => els.map(el => el.getAttribute('aria-expanded'))"), ["false"] * 4)
        self.assertEqual(self.page_errors, [])

    async def test_report_has_no_horizontal_page_overflow_at_common_widths(self):
        for width in (1440, 768, 360):
            await self.page.set_viewport_size({"width": width, "height": 900})
            dimensions = await self.page.evaluate(
                "() => ({scroll: document.documentElement.scrollWidth, viewport: window.innerWidth})"
            )
            self.assertLessEqual(dimensions["scroll"], dimensions["viewport"] + 1, f"overflow at {width}px")
        self.assertEqual(self.page_errors, [])


if __name__ == "__main__":
    unittest.main()
