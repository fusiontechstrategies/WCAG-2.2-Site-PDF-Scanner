# Testing

The 5.0.0 release was validated on August 13, 2026. Tests used only synthetic local fixtures and public dependency metadata. No live scan output is stored in this repository.

## Runtime compatibility

- The complete pinned environment was installed and exercised on Python 3.12.10.
- The source parsed successfully with Python 3.10.20, 3.11.15, 3.12.10, 3.13.15, and 3.14.7.
- Dependency resolution was checked at the supported Python 3.10 and 3.14 boundaries.

## Functional validation

- All 14 embedded offline diagnostics passed.
- A three-page synthetic site was crawled and analyzed with one crawler worker, covering the lowest supported concurrency boundary.
- Full browser analysis completed with Playwright, axe-core, dynamic, static, and validation checks active.
- The downloaded axe-core payload matched its pinned SHA-256 digest before injection.
- Static local HTML analysis detected a deliberately missing image alternative.
- PDF discovery found a linked synthetic PDF through a bounded same-site crawl.
- Local and HTTP PDF workflows analyzed a synthetic document through the isolated worker process.
- The PDF page limit, download limit, input limit, redirect checks, signature validation, and private-address policy were exercised.
- PDF HTML, JSON, and CSV reports contained the same 17 evidence records.
- Generated HTML reports parsed successfully and did not require remote presentation assets.
- All six web export formats completed together: HTML, JSON, CSV, Markdown, PDF, and JUnit XML.
- The web PDF export completed with Python deprecation warnings promoted to errors.
- JUnit XML remained parseable with ampersands, quotes, markup, a CDATA terminator, and an invalid control character in synthetic finding data.
- Web and PDF HTML reports escaped synthetic script, event-handler, SVG, and markup payloads; unsafe report links were not made clickable and restrictive content security policies were present.
- All seven guided remediation handlers were exercised against a synthetic HTML page and the retained changes passed a follow-up static scan.
- A linked local stylesheet was discovered through HTML, retained its original CSS selector in the finding, and passed a guided focus-outline remediation plus follow-up CSS scan.
- Formatting-preserving edits retained the doctype, comments, CRLF newline convention, and a single UTF-8 byte-order mark when present.
- A multi-finding remediation transaction created a unique backup, wrote atomically, verified the targeted reductions, and restored the original bytes through rollback.
- Template-marker refusal and stale-write protection were exercised, including preservation of an external edit made after analysis.
- A synthetic two-file race test restored the file already written by the transaction while preserving an external edit to the file not yet written.
- Post-remediation verification rejected a synthetic change that resolved its target but introduced a new missing-alternative finding.
- Hard-linked source refusal was exercised to prevent edits from crossing an unexpected filesystem alias.

## Security and quality validation

- Ruff completed with no findings under the repository configuration.
- Bandit completed with no findings under the repository configuration.
- pip-audit resolved 42 packages and reported no known vulnerabilities in the pinned environment at test time.
- Secret scanning and targeted credential-pattern checks reported no findings in the final project tree.
- Formula-like CSV values are neutralized by an embedded regression test.
- Direct numeric loopback URLs, AWS IPv4 metadata, and AWS IPv6 metadata addresses are rejected by embedded regression tests.
- Chunked HTTP response bodies and queued-versus-processed crawler state have embedded regression coverage.
- Autocomplete validation, source-preserving attribute edits, and template remediation refusal have embedded regression coverage.

## Interpretation

These results demonstrate exercised code paths and known regression coverage. They do not prove that every website, PDF, browser, parser, assistive technology, or operating-system combination will behave identically. Accessibility conformance still requires manual evaluation and user testing.
