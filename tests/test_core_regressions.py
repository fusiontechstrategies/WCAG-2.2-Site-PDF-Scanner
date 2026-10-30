from __future__ import annotations

import asyncio
import json
import os
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

import WCAG_Site_PDF_Scanner as scanner


class CSSImportNetworkRegressionTests(unittest.TestCase):
    def test_css_parser_never_fetches_imports(self):
        requested = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                self.end_headers()
                self.wfile.write(b"body { color: red; }")

            def log_message(self, _format, *_args):
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            analyzer = scanner.CSSAnalyzer(scanner.WCAGLevel.AA)
            analyzer.analyze(
                f'@import url("http://127.0.0.1:{port}/private-style");',
                "synthetic.css",
            )
            self.assertFalse(requested.wait(0.2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class WebCommandCompletenessRegressionTests(unittest.TestCase):
    def test_ci_mode_fails_and_reports_invalid_target(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                scanner.web_command,
                [
                    "does-not-exist-audit.html",
                    "--ci-mode",
                    "--report-formats",
                    "json",
                    "--output-dir",
                    "reports",
                ],
            )
            self.assertEqual(result.exit_code, 1, result.output)
            with Path("reports/report.json").open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertEqual(payload["summary"]["analysis_status"], "incomplete")
        self.assertTrue(payload["analysis_errors"])

    def test_static_site_scope_discovers_and_analyzes_child_page(self):
        requested_paths: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested_paths.append(self.path)
                body = (
                    b"<!doctype html><html lang='en'><title>Child</title><body><main>Child</main></body></html>"
                    if self.path == "/child"
                    else b"<!doctype html><html lang='en'><title>Root</title><body><main><a href='/child'>Child</a></main></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    scanner.web_command,
                    [
                        f"http://127.0.0.1:{port}/",
                        "--allow-private-hosts",
                        "--scope",
                        "site",
                        "--bulk-mode",
                        "static",
                        "--max-urls",
                        "2",
                        "--concurrency",
                        "1",
                        "--analysis-concurrency",
                        "1",
                        "--crawl-delay",
                        "0",
                        "--no-spell-check",
                        "--report-formats",
                        "json",
                        "--output-dir",
                        "reports",
                    ],
                )
                self.assertEqual(result.exit_code, 0, result.output)
                with Path("reports/report.json").open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn("/child", requested_paths)
        self.assertIn(f"http://127.0.0.1:{port}/child", payload["all_urls_crawled"])


class BrowserDnsBindingRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_hostname_request_is_blocked_even_when_python_dns_looks_public(self):
        tool = object.__new__(scanner.A11yPowerTool)
        tool.config = {"allow_private_hosts": False}
        route = SimpleNamespace(
            request=SimpleNamespace(url="http://localhost/private-marker"),
            abort=AsyncMock(),
            continue_=AsyncMock(),
        )

        with patch.object(
            scanner.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("203.0.113.10", 80))],
        ):
            await tool._browser_route_guard(route)

        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_awaited()


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


class TextAnalysisDependencyTests(unittest.TestCase):
    def test_text_analysis_runs_without_nltk_runtime_dependency(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("nltk", requirements.lower())
        self.assertNotIn("textstat", requirements.lower())
        self.assertIn("pyphen==0.18.1", requirements)

        paragraph = "Clear content helps readers understand important public services and complete essential tasks. " * 6
        soup = scanner.BeautifulSoup(f"<html><body><main>{paragraph}</main></body></html>", "lxml")
        issues, passed = scanner.ContentAnalyzer(scanner.WCAGLevel.AAA).analyze(soup, "sample.html")

        self.assertGreater(len(issues) + len(passed), 0)
        self.assertFalse(any("analysis skipped" in result.description.lower() for result in passed))


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
