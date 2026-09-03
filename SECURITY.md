# Security Policy

## Supported version

Security fixes are applied to the current release on the default branch. Older copies should be upgraded before use.

## Reporting a vulnerability

Do not publish suspected vulnerabilities in a public issue. Contact the repository owner privately or use a private GitHub Security Advisory when that feature is available. Include:

- The affected version or commit
- A concise description and impact
- Reproduction steps or a minimal proof of concept
- Any suggested mitigation

Do not include credentials, private documents, production URLs, or personal data in a report.

## Threat model

This application processes content that may be hostile:

- Web pages can issue subrequests, execute JavaScript, consume resources, and attempt browser exploitation.
- URLs can target private services or cloud metadata endpoints.
- PDFs can exploit parser defects, use active content, contain attachments, or exhaust memory and CPU.
- Scan results can contain spreadsheet formulas, HTML, sensitive URLs, local paths, text, and screenshots.

## Built-in controls

- Public-address enforcement for HTTP and browser traffic by default
- Link-local and cloud metadata blocking even when private-host scanning is enabled
- Redirect validation and hostname-family checks for top-level web scans
- HTTP, DOM, sitemap, PDF, URL-list, and worker-output size limits
- Explicit browser request routing, disabled permissions, blocked downloads, and blocked service workers
- Pinned axe-core version with SHA-256 integrity verification
- Atomic PDF downloads with signature validation
- Child-process PDF parsing with per-document timeouts
- HTML output escaping and URL-scheme filtering
- Self-contained HTML reports with restrictive content security policies
- CSV formula-injection neutralization
- No automatic language-data downloads or other import-time network activity
- No remote fonts or presentation assets in generated reports
- Review-first remediation with source containment, template and reparse-point refusal, stale-write detection, unique backups, atomic writes, rescanning, and rollback

## Source remediation

Interactive remediation is available only for local HTML, HTM, and CSS files beneath the scanned target. It does not write to remote websites. The engine rejects symbolic links, hard links, Windows reparse points, template markers, non-UTF-8 or null-containing files, files over 5 MB, and targets changed after analysis.

Every proposed source transaction requires explicit approval after a diff preview. The engine creates a unique backup beside each source file, writes atomically, repeats the applicable narrow automated scan, and offers rollback. Backups may contain sensitive source code and should follow the same access and retention controls as the original files.

Verification means only that the applicable automated finding count decreased without a detected regression. It is not a WCAG conformance determination and does not replace manual review, browser testing, assistive-technology testing, or version control.

## Important limitations

A child process is a fault-containment boundary, not a complete security sandbox. A parser exploit may still affect the operating-system account running the scanner. For unknown or high-risk PDFs and websites, run the application inside a disposable virtual machine or container with:

- No production credentials
- No cloud instance role
- No mounted sensitive directories
- Restricted outbound networking
- Tight CPU and memory limits
- A non-administrator account

Playwright's `--no-sandbox` option weakens browser isolation. Do not use it for untrusted websites unless an outer disposable sandbox provides equivalent protection.

## Internal and private-network scans

Private addresses are blocked unless `--allow-private-hosts` is supplied. Enable that option only when the target is authorized. It does not permit link-local, multicast, reserved, unspecified, or cloud metadata addresses.

Use conservative concurrency and rate limiting. Do not scan production systems if the traffic could affect availability.

## Handling generated data

Reports and retained downloads can contain sensitive information. Store them in an access-controlled location, do not commit real scan output, and delete them according to the applicable retention policy.

The default `.gitignore` excludes generated report directories, downloaded PDFs, caches, virtual environments, logs, spreadsheets, and common local data files.

## Dependency maintenance

Before every release:

```powershell
python -m pip install --upgrade pip pip-audit bandit
python -m bandit -q -c pyproject.toml -r WCAG_Site_PDF_Scanner.py
python -m pip_audit -r requirements.txt
python WCAG_Site_PDF_Scanner.py diagnostics
```

Update pins deliberately, reinstall the Playwright browser, and repeat the functional and security checks after each change.
