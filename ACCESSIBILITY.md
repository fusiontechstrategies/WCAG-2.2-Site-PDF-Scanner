# Accessibility statement

Fusion Technology Strategies wants WCAG 2.2 Site and PDF Scanner to be usable by people with a wide range of access needs. WCAG 2.2 Level AA is used as a design reference for the project documentation and generated HTML reports. This statement does not claim formal conformance or certification.

## Scope

This statement covers:

- the command-line and interactive text interfaces
- the documentation and included local walkthrough
- generated HTML, JSON, and CSV reports
- the synthetic sample report committed to this repository

It does not cover the accessibility of third-party terminals, shells, browsers, package registries, or content selected for scanning.

## Current support

- Scanner workflows can be operated from a keyboard through documented command-line arguments.
- Generated HTML reports declare their language and use headings and a main landmark.
- Report filters have accessible names, keyboard focus is visible, expandable findings support keyboard operation, and the filtered-result count is announced as a live update.
- JSON and CSV outputs provide machine-readable alternatives to the visual report. CSV cells are neutralized against spreadsheet formula injection.
- HTML reports are self-contained and do not load remote fonts or presentation assets.
- The included five-minute walkthrough scans only a local synthetic fixture and does not require Chromium.

## Known limitations

- The project has not received an independent accessibility audit.
- Terminal accessibility depends on the terminal, shell, operating system, and assistive technology selected by the user.
- Automated findings cannot establish WCAG, Section 508, or PDF/UA conformance. Manual review, assistive-technology testing, and judgment about the content and its purpose remain necessary.
- The report preview image is illustrative. The linked HTML and JSON fixtures provide the inspectable report content.
- Third-party browser and PDF libraries may introduce behavior outside this project's direct control.

## Feedback

If you encounter an accessibility barrier, open a [GitHub issue](https://github.com/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner/issues) or email [jeff@fusiontsi.com](mailto:jeff@fusiontsi.com). Please describe the interface, command, report section, assistive technology, and operating system involved when you can do so safely.

Do not include confidential scan targets, customer data, credentials, or private report contents in a public issue. Use the process in [SECURITY.md](SECURITY.md) for a security vulnerability.

Last reviewed: September 29, 2026.
