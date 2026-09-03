# Third-Party Notices

This project is licensed under the MIT License. Its dependencies remain under their own licenses. The table reflects the direct versions pinned in `requirements.txt` on 2026-08-13.

## Runtime-injected component

| Component | Version | Use | License |
|---|---:|---|---|
| axe-core | 4.12.0 | Downloaded from a pinned CDN URL, verified with SHA-256, and injected into sampled browser pages. It is not stored in this repository. | MPL-2.0 |

When using the `--axe-script` option to supply a local axe-core copy, retain the upstream license and notices with that copy.

## Direct Python dependencies

| Package | Version | License |
|---|---:|---|
| aiohttp | 3.14.3 | Apache-2.0 AND MIT |
| beautifulsoup4 | 4.15.0 | MIT |
| charset-normalizer | 3.5.0 | MIT |
| click | 8.4.2 | BSD-3-Clause |
| cssutils | 2.15.0 | LGPL-3.0-or-later |
| defusedxml | 0.7.1 | PSF-2.0 style license |
| fpdf2 | 2.8.8 | LGPL-3.0-only |
| lxml | 6.1.1 | BSD-3-Clause |
| Pillow | 12.3.0 | MIT-CMU |
| pikepdf | 10.11.0 | MPL-2.0 |
| playwright | 1.62.0 | Apache-2.0 |
| pyphen | 0.18.1 | GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1 |
| pyspellchecker | 0.9.0 | MIT |
| questionary | 2.1.1 | MIT |
| rich | 15.0.0 | MIT |

Transitive dependencies are not exhaustively reproduced here. Generate an environment-specific inventory before redistribution:

```powershell
python -m pip install pip-licenses
python -m piplicenses --format=markdown --with-urls --with-license-file
```

## LGPL components

`cssutils` and `fpdf2` are dynamically imported, unmodified libraries. Anyone redistributing an application bundle should retain the applicable license texts and preserve the ability to replace those libraries as required by their licenses.

## Trademarks and independence

Product and standards names belong to their respective owners. This project is independent and is not affiliated with or endorsed by W3C, the US General Services Administration, PDF Association, Deque Systems, or the publishers of referenced accessibility tools.
