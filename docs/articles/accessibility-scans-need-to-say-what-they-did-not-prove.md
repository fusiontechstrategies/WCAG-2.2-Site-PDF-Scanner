# Accessibility scans need to say what they did not prove

An accessibility scanner can find real problems. It can also create a dangerous amount of confidence if the report implies that automation proved conformance.

That distinction shaped the WCAG 2.2 Site and PDF Scanner from the beginning.

The scanner handles websites, local HTML, and PDF documents through one Python application. For web content, it combines source analysis with optional browser testing through Playwright and axe-core. For PDFs, it collects conservative structural evidence about language, titles, headings, figures, tables, links, forms, bookmarks, active content, attachments, and PDF/UA metadata.

Every result is meant to answer three separate questions:

1. What did the scanner actually test?
2. What evidence did it observe?
3. What still needs a person?

The report never treats an automated pass as proof of WCAG, Section 508, or PDF/UA conformance. Manual review, assistive-technology testing, and judgment about the purpose of the content still matter.

There is also a security reason to be careful. Scanning a site or document means processing content you may not control. The default network policy blocks private, loopback, link-local, multicast, reserved, and cloud metadata addresses. Redirects and browser subrequests are checked. Downloads, service workers, HTML, PDFs, URL lists, worker output, and parsing time are bounded. CSV output is neutralized against spreadsheet formulas.

The repository includes a deliberately flawed synthetic page and the report generated from it. You can inspect the HTML, JSON, screenshot, and source page without scanning a live customer site. The five-minute walkthrough uses only those local fixtures and does not require Chromium.

I think this is the right way to evaluate an accessibility tool. Look at the evidence first. Check the limits. Then decide whether its automated findings would help your own manual review process.

Repository: https://github.com/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner

Sample report: https://github.com/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner/tree/main/examples/sample-report

Current release: https://github.com/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner/releases/tag/v5.0.3

Accessibility statement: https://github.com/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner/blob/main/ACCESSIBILITY.md
