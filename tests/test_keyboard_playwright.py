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


if __name__ == "__main__":
    unittest.main()
