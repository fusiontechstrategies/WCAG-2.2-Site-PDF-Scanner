# Contributing

Contributions should preserve the project's core design: one Python runtime file, conservative accessibility claims, safe defaults, deterministic output, and a flat repository structure.

## Before opening a change

1. Create a virtual environment.
2. Install `requirements-dev.txt`, which includes the runtime and validation dependencies.
3. Install Chromium with `python -m playwright install chromium` when changing browser behavior.
4. Keep all Python runtime and embedded self-test logic in `WCAG_Site_PDF_Scanner.py`.
5. Do not commit generated reports, downloaded PDFs, credentials, production URLs, or live-scan data.

## Required verification

```powershell
python -m py_compile WCAG_Site_PDF_Scanner.py
python WCAG_Site_PDF_Scanner.py diagnostics
ruff check WCAG_Site_PDF_Scanner.py
bandit -q -c pyproject.toml -r WCAG_Site_PDF_Scanner.py
python -m pip_audit -r requirements.txt
```

Changes to crawling, downloads, redirects, browser routing, parsers, or report rendering require focused security tests.

## Accessibility result rules

- Do not label a success criterion as conforming based on one automated rule.
- Analysis failures must be reported as `Analysis error`, never `Pass` or `Not applicable`.
- A tagged PDF is not automatically proof of correct reading order or focus order.
- Thresholds must come from an applicable standard or be clearly labeled as heuristics.
- Results must state the observed evidence and a practical remediation.
- Reports must remain usable with keyboard navigation, zoom, and common screen readers.

## Style

- Support Python 3.10 through 3.14.
- Prefer standard-library features when they remove a large dependency without reducing capability.
- Keep network and file operations bounded.
- Use deterministic sorting for reports and discovery results.
- Add or extend embedded diagnostics for corrected defects.
- Do not use em dashes in code, documentation, or generated text.

## Pull requests

Describe the problem, the behavior change, security impact, test evidence, and any manual evaluation performed. Keep unrelated formatting changes out of focused fixes.
