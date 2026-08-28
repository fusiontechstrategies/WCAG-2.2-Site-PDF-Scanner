# Testing

Tests use only synthetic local fixtures and public dependency metadata. No live scan output is stored in this repository.

## 5.0.1 release-readiness validation

The 5.0.1 candidate tree was validated on August 28, 2026. This is release-readiness evidence, not a tag, GitHub release, PyPI publication, or conformance claim.

### Supported Python versions

- The protected workflow covers Python 3.10, 3.12, and 3.14 on Linux plus Python 3.12 on Windows and macOS.
- All 19 core, adoption, source-distribution, and release-asset tests pass on Windows Python 3.10.21, 3.12.10, and 3.14.7. Hosted runs must pass the same suite before merge.
- All four Playwright Chromium regressions pass on each of those three Windows Python versions and remain a separate required hosted gate.
- All 14 embedded offline diagnostics pass from source on each supported Python boundary and from the wheel, source distribution, and pipx installation on Python 3.12.10.
- Ruff, Bandit, bytecode compilation, dependency consistency, and repository punctuation checks pass on the candidate source.

### Reports and installation paths

- The documented five-minute walkthrough completed against the synthetic local page and generated HTML and JSON reports.
- The committed sample HTML and JSON reports reproduced byte for byte from the deterministic fixture builder.
- Browser tests verified named filters, landmarks, live result counts, keyboard operation, synchronized expanded state, and no horizontal page overflow at 1440, 768, and 360 pixels.
- A wheel and normalized source distribution were built twice from the candidate tree into separate fresh directories. Both complete six-asset builds had identical filenames and bytes.
- Twine and both repository distribution inspectors validated package metadata, archive safety, exact runtime bytes, dependency metadata, and wheel RECORD coverage.
- The wheel and source distribution were installed into separate isolated environments. Both reported 5.0.1, loaded runtime bytes matching the standalone and repository source, passed all 14 embedded diagnostics, and had no broken requirements.
- A local pipx installation reported the expected version and passed all 14 embedded diagnostics.
- The standalone runtime, wheel, source distribution, SPDX 2.3 dependency SBOM, SHA-256 checksums, and release evidence form the exact six-asset contract.
- The SPDX document records the exact 16 pinned direct runtime dependencies and is recognized as SPDX JSON by an independent SBOM scanner.
- Release evidence binds archive membership and artifact hashes to an exact 40-character source commit. The final protected-main candidate must be rebuilt with the verified merge commit before tagging.

### Security and repository checks

- pip-audit reported no known vulnerabilities in the runtime, development, or pinned build requirements at test time.
- Gitleaks reported no findings in the complete commit history or the proposed tracked project files.
- Trivy reported no high or critical dependency, secret, or configuration findings in the source and no high or critical vulnerability in the generated SPDX dependency inventory.
- markdownlint, actionlint, YAML parsing, and relative Markdown link checks passed. External HTTP(S) destinations were not part of the relative-link check.
- Every GitHub Action reference is immutable or a protected same-owner reusable workflow reference admitted by repository policy.
- CodeQL, Semgrep 1.175.0 in its hosted Linux container, Trivy, dependency review, and full-history Gitleaks must pass on the exact pull-request and protected-main commits before release approval.

## 5.0.0 release validation

The 5.0.0 release was validated on August 13, 2026.

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
