from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from unittest.mock import AsyncMock, patch

import WCAG_Site_PDF_Scanner as scanner


class _FakeContent:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def iter_chunked(self, _size: int):
        yield self.payload


class _FakeResponse:
    def __init__(self, url: str, payload: bytes):
        self.url = url
        self.status = 200
        self.headers = {"Content-Type": "text/html"}
        self.content_length = len(payload)
        self.content = _FakeContent(payload)


class _FakeProgress:
    def update(self, *_args, **_kwargs):
        return None


class CrawlerEncodingRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0

    async def _decode_through_worker(self, payload: bytes) -> str:
        url = "https://example.test/encoding.html"
        report = scanner.AccessibilityReport(target=url)
        crawler = scanner.Crawler(
            start_url=url,
            max_depth=2,
            concurrency=1,
            exclude_patterns=[],
            user_agent="WCAG regression test",
            report=report,
            crawl_delay=0,
            max_urls_to_crawl=2,
        )
        crawler.session = object()
        crawler._can_fetch = AsyncMock(return_value=True)
        await crawler.queue.put((url, 0, url))
        crawler.seen_urls.add(url)

        decoded: list[str] = []

        async def capture_html(html: str, _page_url: str, _depth: int):
            decoded.append(html)

        crawler._process_html = capture_html

        @asynccontextmanager
        async def fake_safe_get(*_args, **_kwargs):
            yield _FakeResponse(url, payload)

        with patch.object(scanner, "_safe_get", fake_safe_get):
            worker = asyncio.create_task(crawler._worker(1, _FakeProgress(), 1))
            await asyncio.wait_for(crawler.queue.join(), timeout=5)
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

        self.assertEqual(len(decoded), 1)
        return decoded[0]

    async def test_utf16_html_is_decoded_through_crawler_worker(self):
        expected = (
            "<!doctype html><html><head><title>UTF-16</title></head><body><p>"
            + "Caf\u00e9 r\u00e9sum\u00e9 \u6771\u4eac. " * 12
            + "</p></body></html>"
        )
        self.assertEqual(await self._decode_through_worker(expected.encode("utf-16")), expected)

    async def test_windows1252_html_is_decoded_through_crawler_worker(self):
        expected = (
            "<!doctype html><html><head><title>Windows-1252</title></head><body><p>"
            + "Caf\u00e9 \u2013 \u201cr\u00e9sum\u00e9\u201d costs \u20ac5. " * 12
            + "</p></body></html>"
        )
        self.assertEqual(await self._decode_through_worker(expected.encode("windows-1252")), expected)


class JsonReportRegressionTests(unittest.TestCase):
    def test_defaultdict_report_fields_serialize_on_python310(self):
        report = scanner.AccessibilityReport(target="https://example.test/")
        report.broken_links["https://example.test/missing"].add("https://example.test/")
        report.fixes_applied["index.html"].append({"status": "fixed"})
        report.compile_summary()

        with tempfile.TemporaryDirectory(prefix="wcag-json-regression-") as temp_dir:
            output_dir = Path(temp_dir)
            scanner.ReportGenerator(report, output_dir).generate_json()
            with (output_dir / "report.json").open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertEqual(
            payload["broken_links"],
            {"https://example.test/missing": ["https://example.test/"]},
        )
        self.assertEqual(payload["fixes_applied"], {"index.html": [{"status": "fixed"}]})
        self.assertIsInstance(report.broken_links, defaultdict)
        self.assertIsInstance(report.fixes_applied, defaultdict)


class LocalPathRegressionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _tool(target: str) -> scanner.A11yPowerTool:
        tool = object.__new__(scanner.A11yPowerTool)
        tool.report = scanner.AccessibilityReport(target=target)
        tool._analyze_page_content = AsyncMock()
        return tool

    async def test_relative_local_file_is_resolved_before_file_uri_conversion(self):
        with tempfile.TemporaryDirectory(prefix="wcag-relative-file-") as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "index.html"
            source.write_text("<!doctype html><html lang='en'><title>Sample</title></html>", encoding="utf-8")
            previous_directory = Path.cwd()
            try:
                os.chdir(temp_path)
                tool = self._tool("index.html")
                await tool._run_for_local_file("index.html")
            finally:
                os.chdir(previous_directory)

            tool._analyze_page_content.assert_awaited_once()
            analyzed_uri = tool._analyze_page_content.await_args.args[1]
            self.assertEqual(analyzed_uri, source.resolve().as_uri())
            self.assertEqual(tool.report.all_files_analyzed, {str(source.resolve())})

    async def test_relative_local_directory_resolves_each_html_file(self):
        with tempfile.TemporaryDirectory(prefix="wcag-relative-directory-") as temp_dir:
            temp_path = Path(temp_dir)
            site_dir = temp_path / "site"
            site_dir.mkdir()
            source = site_dir / "index.html"
            source.write_text("<!doctype html><html lang='en'><title>Sample</title></html>", encoding="utf-8")
            previous_directory = Path.cwd()
            try:
                os.chdir(temp_path)
                tool = self._tool("site")
                await tool._run_for_local_dir("site")
            finally:
                os.chdir(previous_directory)

            tool._analyze_page_content.assert_awaited_once()
            analyzed_uri = tool._analyze_page_content.await_args.args[1]
            self.assertEqual(analyzed_uri, source.resolve().as_uri())
            self.assertEqual(tool.report.all_files_analyzed, {str(source.resolve())})


if __name__ == "__main__":
    unittest.main()
