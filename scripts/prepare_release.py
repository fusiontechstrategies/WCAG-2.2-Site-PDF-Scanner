#!/usr/bin/env python3
"""Validate packages and assemble deterministic WCAG Scanner release assets."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import struct
import tarfile
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

try:
    from scripts.verify_distribution import verify_distribution
except ModuleNotFoundError:
    from verify_distribution import verify_distribution


PROJECT_DISPLAY_NAME = "WCAG 2.2 Site and PDF Scanner"
PROJECT_NAME = "wcag-site-pdf-scanner"
ARCHIVE_NAME = "wcag_site_pdf_scanner"
PROJECT_SLUG = "WCAG-Site-PDF-Scanner"
REPOSITORY_URL = "https://github.com/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner"
RUNTIME_SOURCE = "WCAG_Site_PDF_Scanner.py"
STABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40}$")
RELEASE_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
PINNED_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ReleaseError(RuntimeError):
    """Release input, package content, or output violates the contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_project_version(project_root: Path) -> str:
    text = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
    require(project is not None, "pyproject.toml has no [project] table")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group(0))
    require(match is not None, "[project] has no literal version")
    return match.group(1)


def read_runtime_version(project_root: Path) -> str:
    text = (project_root / RUNTIME_SOURCE).read_text(encoding="utf-8")
    matches = re.findall(r'(?m)^APP_VERSION\s*=\s*"([^"]+)"\s*$', text)
    require(len(matches) == 1, "Runtime must define one APP_VERSION string")
    return matches[0]


def read_release_date(project_root: Path, version: str) -> str:
    changelog = (project_root / "CHANGELOG.md").read_text(encoding="utf-8")
    matches = re.findall(rf"(?m)^## {re.escape(version)} - ([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})$", changelog)
    require(len(matches) == 1, "Changelog must contain one exact stable release heading")
    require(RELEASE_DATE.fullmatch(matches[0]) is not None, "Release date must use YYYY-MM-DD")
    return matches[0]


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_runtime_dependencies(project_root: Path) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    normalized_names: set[str] = set()
    for raw_line in (project_root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PINNED_REQUIREMENT.fullmatch(line)
        require(match is not None, f"Runtime requirement is not an exact package pin: {line!r}")
        name, version = match.groups()
        normalized = normalize_distribution_name(name)
        require(normalized not in normalized_names, f"Duplicate runtime dependency: {name}")
        normalized_names.add(normalized)
        dependencies.append({"name": name, "normalized_name": normalized, "version": version})
    require(bool(dependencies), "At least one runtime dependency is required")
    return sorted(dependencies, key=lambda item: item["normalized_name"])


def expected_asset_names(version: str) -> tuple[str, ...]:
    return (
        f"{PROJECT_SLUG}-v{version}.py",
        f"{ARCHIVE_NAME}-{version}-py3-none-any.whl",
        f"{ARCHIVE_NAME}-{version}.tar.gz",
        f"{PROJECT_SLUG}-v{version}.spdx.json",
        "SHA256SUMS.txt",
        "release-evidence.json",
    )


def validate_source_identity(project_root: Path, version: str, tag: str, source_commit: str) -> str:
    require(STABLE_VERSION.fullmatch(version) is not None, "Release version must be stable X.Y.Z")
    require(COMMIT_ID.fullmatch(source_commit) is not None, "Source commit must be 40 lowercase hex")
    require(tag == f"v{version}", f"Release tag {tag!r} does not match v{version}")
    require(read_project_version(project_root) == version, "Project and requested versions differ")
    require(read_runtime_version(project_root) == version, "Runtime and requested versions differ")
    release_date = read_release_date(project_root, version)
    notes = project_root / ".github" / "release-notes" / f"v{version}.md"
    require(notes.is_file() and not notes.is_symlink(), f"Release notes are missing: {notes}")
    return release_date


def archive_parts(name: str) -> tuple[str, ...]:
    require("\\" not in name, f"Archive member uses a backslash: {name!r}")
    require(not name.startswith("/"), f"Archive member is absolute: {name!r}")
    require(re.match(r"^[A-Za-z]:", name) is None, f"Archive member uses a drive path: {name!r}")
    stripped = name.rstrip("/")
    require(bool(stripped), "Archive contains an empty member name")
    parts = tuple(stripped.split("/"))
    require(all(part not in {"", ".", ".."} for part in parts), f"Unsafe archive path: {name!r}")
    require(tuple(PurePosixPath(*parts).parts) == parts, f"Noncanonical archive path: {name!r}")
    for part in parts:
        require(not part.endswith((" ", ".")), f"Nonportable archive path: {name!r}")
        require(
            all(ord(character) >= 32 and ord(character) != 127 for character in part),
            f"Archive path contains a control character: {name!r}",
        )
        require(
            part.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES,
            f"Archive path uses a reserved Windows name: {name!r}",
        )
    return parts


def record_portable_name(name: str, names: set[str], portable_names: set[str]) -> str:
    archive_parts(name)
    normalized_name = name.rstrip("/")
    require(normalized_name not in names, f"Duplicate archive member: {normalized_name!r}")
    names.add(normalized_name)
    portable = unicodedata.normalize("NFC", normalized_name).casefold()
    require(portable not in portable_names, f"Nonportable duplicate archive member: {normalized_name!r}")
    portable_names.add(portable)
    return normalized_name


def wheel_inventory(path: Path, source_date_epoch: int) -> tuple[list[dict[str, object]], dict[str, bytes]]:
    records: list[dict[str, object]] = []
    values: dict[str, bytes] = {}
    names: set[str] = set()
    portable_names: set[str] = set()
    expected_timestamp = list(time.gmtime(max(source_date_epoch, 315532800))[:6])
    expected_timestamp[5] -= expected_timestamp[5] % 2
    expected_zip_timestamp = tuple(expected_timestamp)
    with zipfile.ZipFile(path) as archive:
        require(archive.comment == b"", "Wheel has a noncanonical archive comment")
        members = archive.infolist()
        require(
            [member.filename for member in members] == sorted(member.filename for member in members),
            "Wheel members are not sorted canonically",
        )
        for member in members:
            name = record_portable_name(member.filename, names, portable_names)
            require(
                member.date_time == expected_zip_timestamp,
                f"Wheel member has a non-reproducible timestamp: {name!r}",
            )
            require(member.flag_bits & 1 == 0, f"Wheel contains an encrypted member: {name!r}")
            require(not stat.S_ISLNK(member.external_attr >> 16), f"Wheel contains a symbolic link: {name!r}")
            require(not member.is_dir(), f"Wheel contains an explicit directory: {name!r}")
            require(member.compress_type == zipfile.ZIP_STORED, f"Wheel member is not stored canonically: {name!r}")
            require(member.create_system == 3, f"Wheel member has a noncanonical creator system: {name!r}")
            require(member.extra == b"" and member.comment == b"", f"Wheel member has extra metadata: {name!r}")
            mode = member.external_attr >> 16
            require(
                stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o644,
                f"Wheel member has a noncanonical mode: {name!r}",
            )
            value = archive.read(member)
            require(len(value) == member.file_size, f"Wheel member is truncated: {name!r}")
            values[name] = value
            records.append({"bytes": len(value), "name": name, "sha256": sha256_bytes(value), "type": "file"})
    return records, values


def validate_wheel_record(values: dict[str, bytes]) -> None:
    record_names = [name for name in values if name.endswith(".dist-info/RECORD")]
    require(len(record_names) == 1, "Wheel must contain exactly one RECORD file")
    record_name = record_names[0]
    try:
        rows = csv.reader(io.StringIO(values[record_name].decode("utf-8"), newline=""))
        entries = list(rows)
    except UnicodeDecodeError as error:
        raise ReleaseError("Wheel RECORD is not UTF-8") from error
    recorded: dict[str, tuple[str, str]] = {}
    for row in entries:
        require(len(row) == 3, "Wheel RECORD contains a malformed row")
        name, digest, size = row
        archive_parts(name)
        require(name not in recorded, f"Wheel RECORD contains a duplicate path: {name!r}")
        recorded[name] = (digest, size)
    require(set(recorded) == set(values), "Wheel RECORD does not cover the exact archive file set")
    for name, value in values.items():
        digest, size = recorded[name]
        if name == record_name:
            require(digest == "" and size == "", "Wheel RECORD must leave its own identity empty")
            continue
        encoded = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode("ascii")
        require(digest == f"sha256={encoded}", f"Wheel RECORD hash mismatch: {name!r}")
        require(size == str(len(value)), f"Wheel RECORD size mismatch: {name!r}")


def sdist_inventory(path: Path, version: str, source_date_epoch: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    names: set[str] = set()
    portable_names: set[str] = set()
    expected_root = f"{ARCHIVE_NAME}-{version}"
    with path.open("rb") as raw_archive:
        gzip_header = raw_archive.read(10)
    require(
        len(gzip_header) == 10 and gzip_header[:4] == b"\x1f\x8b\x08\x00" and gzip_header[8:] == b"\x00\xff",
        "Source-distribution gzip header is not canonical",
    )
    require(
        struct.unpack("<I", gzip_header[4:8])[0] == source_date_epoch,
        "Source-distribution gzip timestamp does not match SOURCE_DATE_EPOCH",
    )
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            name = record_portable_name(member.name, names, portable_names)
            parts = archive_parts(name)
            require(parts[0] == expected_root, f"Unexpected source-distribution root: {name!r}")
            require(member.isfile() or member.isdir(), f"Source distribution contains a link or device: {name!r}")
            require(member.mtime == source_date_epoch, f"Source-distribution member timestamp differs: {name!r}")
            require(
                member.uid == 0 and member.gid == 0 and member.uname == "" and member.gname == "",
                f"Source-distribution member retains owner identity: {name!r}",
            )
            expected_mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
            require(member.mode == expected_mode, f"Source-distribution member has a noncanonical mode: {name!r}")
            if member.isdir():
                records.append({"name": name, "type": "directory"})
                continue
            handle = archive.extractfile(member)
            require(handle is not None, f"Unable to read source-distribution member: {name!r}")
            value = handle.read()
            require(len(value) == member.size, f"Source-distribution member is truncated: {name!r}")
            records.append({"bytes": len(value), "name": name, "sha256": sha256_bytes(value), "type": "file"})
    return records


def dependency_spdx_id(index: int, normalized_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9.-]", "-", normalized_name)
    return f"SPDXRef-Dependency-{index:03d}-{safe_name}"


def build_spdx(
    version: str,
    release_date: str,
    runtime_digest: str,
    dependencies: list[dict[str, str]],
) -> bytes:
    root_id = "SPDXRef-Package"
    packages: list[dict[str, object]] = [
        {
            "SPDXID": root_id,
            "checksums": [{"algorithm": "SHA256", "checksumValue": runtime_digest}],
            "copyrightText": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceLocator": (f"pkg:github/fusiontechstrategies/WCAG-2.2-Site-PDF-Scanner@{version}"),
                    "referenceType": "purl",
                }
            ],
            "filesAnalyzed": False,
            "licenseConcluded": "MIT",
            "licenseDeclared": "MIT",
            "name": PROJECT_NAME,
            "supplier": "Organization: Fusion Technology Strategies",
            "versionInfo": version,
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": root_id,
        }
    ]
    for index, dependency in enumerate(dependencies, start=1):
        package_id = dependency_spdx_id(index, dependency["normalized_name"])
        packages.append(
            {
                "SPDXID": package_id,
                "copyrightText": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceLocator": (f"pkg:pypi/{dependency['normalized_name']}@{dependency['version']}"),
                        "referenceType": "purl",
                    }
                ],
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "name": dependency["name"],
                "supplier": "NOASSERTION",
                "versionInfo": dependency["version"],
            }
        )
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
        )
    document = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": f"{release_date}T00:00:00Z",
            "creators": [
                "Organization: Fusion Technology Strategies",
                "Tool: scripts/prepare_release.py",
            ],
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": (f"{REPOSITORY_URL}/releases/tag/v{version}#spdx-{runtime_digest}"),
        "name": f"{PROJECT_SLUG}-v{version}",
        "packages": packages,
        "relationships": relationships,
        "spdxVersion": "SPDX-2.3",
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_exclusive(path: Path, value: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(value)
    except FileExistsError as error:
        raise ReleaseError(f"Refusing to replace release output: {path}") from error


def prepare_release(
    project_root: Path,
    dist_directory: Path,
    output_directory: Path,
    version: str,
    tag: str,
    source_commit: str,
    source_date_epoch: int,
) -> tuple[Path, ...]:
    project_root = project_root.resolve(strict=True)
    dist_directory = dist_directory.resolve(strict=True)
    output_directory = output_directory.resolve(strict=False)
    release_date = validate_source_identity(project_root, version, tag, source_commit)
    require(315532800 <= source_date_epoch <= 0xFFFFFFFF, "SOURCE_DATE_EPOCH is outside release range")
    require(not output_directory.exists(), "Release output directory already exists")
    require(output_directory.parent.is_dir(), "Release output parent does not exist")

    expected_dist_names = {
        f"{ARCHIVE_NAME}-{version}-py3-none-any.whl",
        f"{ARCHIVE_NAME}-{version}.tar.gz",
    }
    distributions = sorted(dist_directory.iterdir())
    require(
        all(path.is_file() and not path.is_symlink() for path in distributions),
        "Distribution directory must contain only regular files",
    )
    require({path.name for path in distributions} == expected_dist_names, "Unexpected distribution set")
    verify_distribution(dist_directory, project_root)

    wheel = next(path for path in distributions if path.suffix == ".whl")
    sdist = next(path for path in distributions if path.name.endswith(".tar.gz"))
    wheel_members, wheel_values = wheel_inventory(wheel, source_date_epoch)
    validate_wheel_record(wheel_values)
    sdist_members = sdist_inventory(sdist, version, source_date_epoch)
    runtime = project_root / RUNTIME_SOURCE
    require(runtime.is_file() and not runtime.is_symlink(), "Runtime source must be a regular file")
    runtime_value = runtime.read_bytes()
    dependencies = parse_runtime_dependencies(project_root)

    asset_names = expected_asset_names(version)
    output_directory.mkdir()
    standalone_asset = output_directory / asset_names[0]
    wheel_asset = output_directory / asset_names[1]
    sdist_asset = output_directory / asset_names[2]
    spdx_asset = output_directory / asset_names[3]
    checksums_asset = output_directory / asset_names[4]
    evidence_asset = output_directory / asset_names[5]

    write_exclusive(standalone_asset, runtime_value)
    write_exclusive(wheel_asset, wheel.read_bytes())
    write_exclusive(sdist_asset, sdist.read_bytes())
    write_exclusive(
        spdx_asset,
        build_spdx(version, release_date, sha256_bytes(runtime_value), dependencies),
    )

    primary_assets = (standalone_asset, wheel_asset, sdist_asset, spdx_asset)
    checksum_lines = [f"{sha256_file(path)}  {path.name}" for path in primary_assets]
    write_exclusive(checksums_asset, ("\n".join(checksum_lines) + "\n").encode("ascii"))

    evidence_inputs = (*primary_assets, checksums_asset)
    evidence = {
        "archives": {
            wheel_asset.name: wheel_members,
            sdist_asset.name: sdist_members,
        },
        "artifacts": [
            {"bytes": path.stat().st_size, "name": path.name, "sha256": sha256_file(path)} for path in evidence_inputs
        ],
        "dependencies": dependencies,
        "expected_release_assets": list(asset_names),
        "project": PROJECT_DISPLAY_NAME,
        "release_date": release_date,
        "repository": REPOSITORY_URL,
        "runtime_source_sha256": sha256_bytes(runtime_value),
        "schema_version": 1,
        "source_commit": source_commit,
        "source_date_epoch": source_date_epoch,
        "tag": tag,
        "version": version,
    }
    write_exclusive(
        evidence_asset,
        (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    outputs = tuple(output_directory / name for name in asset_names)
    require(all(path.is_file() and not path.is_symlink() for path in outputs), "Release assets are incomplete")
    require({path.name for path in output_directory.iterdir()} == set(asset_names), "Unexpected release asset")
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("--dist-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    try:
        outputs = prepare_release(
            project_root,
            arguments.dist_directory,
            arguments.output_directory,
            arguments.version,
            arguments.tag,
            arguments.source_commit,
            arguments.source_date_epoch,
        )
    except (OSError, ReleaseError, UnicodeError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Release preparation failed: {error}") from error
    for output in outputs:
        print(f"{sha256_file(output)}  {output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
