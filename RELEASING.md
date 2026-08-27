# Release process

This project has one runtime source file and one version identity. A release is ready only when the source command, installed command, wheel, source distribution, changelog, and tag all describe the same version.

No public PyPI publication workflow is enabled in this repository. Adding one, creating a tag, publishing a GitHub release, or uploading to PyPI requires an explicit release decision.

## Version gate

1. Choose the release version and replace the development version in both `pyproject.toml` and `APP_VERSION` in `WCAG_Site_PDF_Scanner.py`.
2. Convert the relevant changelog section from `Unreleased` to the same version and release date.
3. Confirm that `python WCAG_Site_PDF_Scanner.py --version` and `wcag-site-pdf-scanner --version` report that version.
4. Reject the release if a public artifact with the same version contains different bytes.

Development checkouts use a PEP 440 development version so locally built artifacts cannot be mistaken for the final release.

## Clean build and inspection

Run from a clean checkout with a supported Python version:

```powershell
python -m venv .release-venv
.release-venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m build
python -m twine check dist\*
python scripts\verify_distribution.py dist
```

On macOS or Linux, activate with `source .release-venv/bin/activate` and use `dist/*`.

The verification script rejects missing or extra distributions, inconsistent metadata, unexpected executable code in the wheel, and common secret-bearing file types.

## Isolated install checks

Test both build formats in separate empty environments. Do not rely on dependencies already installed in the build environment.

```powershell
python -m venv .wheel-venv
.wheel-venv\Scripts\python -m pip install dist\*.whl
.wheel-venv\Scripts\wcag-site-pdf-scanner --version
.wheel-venv\Scripts\wcag-site-pdf-scanner diagnostics

python -m venv .sdist-venv
.sdist-venv\Scripts\python -m pip install dist\*.tar.gz
.sdist-venv\Scripts\wcag-site-pdf-scanner --version
.sdist-venv\Scripts\wcag-site-pdf-scanner diagnostics
```

Also run the complete test, lint, dependency-audit, CodeQL, and secret-scan workflows before release approval.

## PyPI trust setup

Before any first publication:

1. Recheck the normalized project name on PyPI. A missing project page is not a reservation guarantee.
2. Create a protected GitHub environment named `pypi` with a required reviewer.
3. Configure a PyPI trusted publisher for this exact repository, workflow filename, and environment.
4. Add a publication workflow with `id-token: write` only after the trusted publisher exists.
5. Pin every third-party action to a full commit SHA.
6. Require an explicit, version-bound approval before the workflow can publish.
7. Prefer PyPI trusted publishing over a long-lived API token.

## Final evidence

Record the exact commit, tag, test run, artifact names, SHA-256 digests, signer or provenance status, and publication URLs. Verify the public files after upload instead of assuming the transfer preserved the locally tested bytes.
