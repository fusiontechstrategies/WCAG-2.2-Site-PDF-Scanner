# Synthetic sample report

This fixture shows the scanner's real HTML and JSON report formats without scanning a live website or storing customer data.

- The input is [`../sample-site/index.html`](../sample-site/index.html).
- The target uses the reserved `.invalid` domain and is never fetched.
- The static analyzer and production report exporters generate both reports.
- The fixed timestamp and duration keep the fixture deterministic.
- Findings are intentionally present for training. They are not a conformance determination.

Open [`report.html`](report.html) locally for the interactive view, or inspect [`report.json`](report.json) for an automation-friendly example.

Regenerate the fixture from the repository root:

```powershell
python examples/build_sample_report.py
```

Add `--preview` to refresh `report-preview.png` when Playwright Chromium is installed.
