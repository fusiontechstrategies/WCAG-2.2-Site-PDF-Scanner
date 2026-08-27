# Changelog

## Unreleased

### Added

- Added continuous integration across Python 3.10, 3.12, and 3.14 with offline diagnostics, linting, static security analysis, and dependency auditing
- Added pull-request dependency review with moderate-or-higher vulnerability blocking and explicit license checks
- Added a Dependabot-managed development requirements file with the TOML parser required by Bandit on Python 3.10

### Changed

- Extended Dependabot coverage to GitHub Actions, separated runtime and development update groups, and kept major Python upgrades separate from grouped minor and patch updates

## 5.0.0 - 2026-08-13

### Added

- Unified website, local HTML, PDF, PDF discovery, diagnostics, and interactive workflows in one Python file
- Conservative PDF evidence model with explicit manual-review, not-tested, and analysis-error states
- PDF child-process isolation with configurable timeouts
- Safe remote PDF downloading with SSRF controls, redirect limits, size limits, signatures, and atomic writes
- Structure, language, title, figure, heading, table, link, form, navigation, active-content, attachment, and PDF/UA metadata evidence
- Self-contained PDF HTML, JSON, and spreadsheet-safe CSV reports
- Bounded sitemap and same-site PDF discovery
- Offline embedded self-tests
- Review-first local HTML and CSS remediation with per-finding guidance
- Source-preserving HTML attribute edits, diff preview, unique backups, atomic writes, post-change rescanning, audit records, and rollback
- No artificial website page-count ceiling when `--max-urls` is omitted

### Corrected

- Repaired a crawler state defect that caused queued URLs to be rejected before fetching
- Removed a crawler semaphore deadlock affecting low-concurrency scans
- Separated successfully fetched pages from merely queued URLs
- Suppressed web pass records contradicted by failures for the same criterion and location
- Disabled the obsolete WCAG 2.2 criterion 4.1.1 conformance check while retaining duplicate-ID quality analysis
- Corrected a late-bound table-header closure defect
- Corrected fpdf2 cursor handling that could prevent web PDF summary generation
- Updated web PDF export calls to current fpdf2 fonts and cursor-positioning APIs
- Isolated report-format failures so every requested exporter is attempted and failures return a clear error
- Corrected local linked-stylesheet path handling on Windows and retained real CSS selectors for remediation
- Hardened JUnit XML escaping for hostile targets, markup, CDATA terminators, and invalid control characters
- Added restrictive content security policies to generated HTML reports
- Removed the unsupported numeric compliance score
- Removed automatic NLTK downloads during import
- Removed stale commands, obsolete dependencies, and historical live-scan output

### Hardened

- Added browser subrequest filtering, disabled permissions, downloads, and service workers
- Added top-level redirect scope validation
- Added bounded robots, sitemap, HTML, DOM, URL-list, PDF, and worker-output processing
- Added safe XML parsing
- Removed remote fonts from reports
- Removed search-engine result scraping and command-line API-key handling
- Replaced MD5 caching with SHA-256 naming
- Added preflight destination validation and per-redirect checks for all HTTP workflows
- Blocked numeric IP literal bypasses, DNS rebinding attempts, and cloud metadata destinations
- Corrected capped response reads so chunked bodies are consumed completely
- Added integrity verification for the pinned axe-core payload before browser injection
- Reclassified language and skip-link edits as guided decisions instead of unreviewed automatic changes
- Added containment, template, reparse-point, file-size, encoding, and stale-write guards to remediation

### Changed

- Renamed the application to WCAG 2.2 Site and PDF Scanner
- Consolidated all Python logic into `WCAG_Site_PDF_Scanner.py`
- Consolidated runtime dependencies into one pinned `requirements.txt`
- Preserved a flat root directory

### Verified

- Passed all 14 embedded offline diagnostics
- Passed exact-dependency static, dynamic, axe-core, crawler, PDF, discovery, and report-format tests
- Passed Ruff and Bandit security linting
- Resolved the complete pinned dependency set with no known vulnerabilities reported by pip-audit
- Verified source parsing with Python 3.10, 3.11, 3.12, 3.13, and 3.14
