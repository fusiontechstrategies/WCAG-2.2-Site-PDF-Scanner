# Release process

WCAG 2.2 Site and PDF Scanner releases come from a reviewed, fully tested commit on protected `main`. The runtime, project metadata, wheel, source distribution, changelog, release notes, tag, checksums, SBOM, and release evidence must describe the same stable version.

Creating a tag and publishing a GitHub release each require an explicit maintainer decision. The tag workflow can create only a draft. It contains no release-publication command and no PyPI upload step.

## Exact asset contract

For version `X.Y.Z`, a GitHub release contains only:

1. `WCAG-Site-PDF-Scanner-vX.Y.Z.py`
2. `wcag_site_pdf_scanner-X.Y.Z-py3-none-any.whl`
3. `wcag_site_pdf_scanner-X.Y.Z.tar.gz`
4. `WCAG-Site-PDF-Scanner-vX.Y.Z.spdx.json`
5. `SHA256SUMS.txt`
6. `release-evidence.json`

The standalone asset and packaged module are byte-identical to `WCAG_Site_PDF_Scanner.py` in the tagged commit. The normalized source distribution has deterministic gzip and member metadata. The SPDX 2.3 document records the exact pinned runtime dependency set. SHA-256 covers the runtime, wheel, source distribution, and SBOM. Machine-readable evidence binds those assets, dependency identities, and archive membership to the exact source commit.

Every release asset receives GitHub build-provenance attestation. Existing release assets are never replaced.

## Prepare the candidate

1. Start from current protected `main`.
2. Confirm `pyproject.toml`, `APP_VERSION`, changelog heading, and `.github/release-notes/vX.Y.Z.md` agree on one stable version.
3. Run the complete supported Python, Windows, macOS, Playwright Chromium, package, dependency, CodeQL, Semgrep, Trivy, Gitleaks, and repository-policy gates.
4. Set `SOURCE_DATE_EPOCH` to the candidate commit time.
5. Install the exact tools in `requirements-build.txt` and build with `--no-isolation`.
6. Normalize the source distribution with `scripts/normalize_sdist.py`.
7. Run Twine, `scripts/verify_distribution.py`, and `scripts/prepare_release.py`.
8. Repeat the complete package and release build into new empty directories and require the same six filenames and bytes.
9. Install the wheel and source distribution separately, test the pipx path, run `--version`, and pass all 14 offline diagnostics.
10. Inspect the standalone runtime, both archives, SPDX dependency list, checksums, release evidence, and committed release notes.

The protected CI package job performs this candidate construction, repeat-build comparison, isolated installation, and 30-day evidence preservation on every pull request and protected-main push.

## Candidate commands

Use empty directories and the exact 40-character candidate commit:

```powershell
$candidateCommit = git rev-parse HEAD
$candidateEpoch = git show -s --format=%ct HEAD
$env:SOURCE_DATE_EPOCH = $candidateEpoch

python -m pip install -r requirements-build.txt
python -m build --no-isolation --wheel --sdist --outdir package-dist
python scripts\normalize_sdist.py `
  --source-date-epoch $candidateEpoch `
  package-dist\wcag_site_pdf_scanner-5.0.1.tar.gz
python -m twine check package-dist\*
python scripts\verify_distribution.py package-dist
python scripts\prepare_release.py `
  --version 5.0.1 `
  --tag v5.0.1 `
  --source-commit $candidateCommit `
  --source-date-epoch $candidateEpoch `
  --dist-directory package-dist `
  --output-directory release-assets
```

The builder rejects development or mismatched versions, a malformed commit, a mismatched tag, missing release notes, unexpected distributions, unsafe archive members, incomplete wheel RECORD coverage, non-pinned runtime dependencies, an existing output directory, or an unexpected final asset.

## Draft creation

Tag creation is maintainer-controlled. The tag must be `vX.Y.Z` and resolve to the approved protected-main commit. That target commit must have a valid GitHub verification record.

Pushing the tag starts `.github/workflows/release.yml`. The workflow:

1. Resolves the tag to its exact commit.
2. Confirms the commit is reachable from protected `main` and GitHub-verified.
3. Refuses to continue if a release already exists for the tag.
4. Builds the exact six assets twice and compares every byte.
5. Runs package, archive, metadata, standalone, and offline diagnostic checks.
6. Attests every asset with GitHub provenance.
7. Creates a non-prerelease draft using the committed versioned release notes.
8. Confirms the draft contains exactly the six approved assets.

The workflow has no manual trigger and no publication command.

## Publication review

Before publishing the draft:

- confirm the tag and draft target the approved verified commit
- download all six assets into a new directory
- recompute every SHA-256 digest
- verify every GitHub provenance attestation
- confirm the standalone and packaged runtime bytes match tagged source
- inspect portable wheel and source-distribution membership and exact RECORD coverage
- install the downloaded wheel and source distribution in separate clean environments
- run `--version`, all 14 offline diagnostics, and the synthetic local-site walkthrough
- run the Playwright report checks in a clean browser environment
- confirm the release notes preserve the manual-review and authorization boundaries
- confirm there are zero open code-scanning, Dependabot, or secret-scanning alerts, except any narrowly documented accepted exception

Publish only after every check passes. Then repeat the public download, digest, provenance, installation, diagnostics, and synthetic report checks against the public URLs.

## PyPI remains separate

This repository has no PyPI publication workflow. The normalized project name must be rechecked immediately before any future setup because an unavailable page does not reserve the namespace.

Before any PyPI publication:

1. Secure the PyPI maintainer account with two-factor authentication and store recovery codes outside the repository.
2. Register the exact GitHub repository and future workflow as a pending trusted publisher.
3. Create a protected GitHub `pypi` environment with required maintainer approval.
4. Add the exact immutable PyPA publishing Action to the repository allowlist.
5. Add a reviewed workflow that downloads and revalidates the exact public GitHub distributions before requesting a short-lived OpenID Connect credential.
6. Never add a long-lived PyPI API token.

PyPI setup and publication require a separate explicit decision after the first GitHub release is proven.
