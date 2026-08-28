"""Inspect wheel and source-distribution contents before release."""

from __future__ import annotations

import argparse
import email
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_NAME = "wcag-site-pdf-scanner"
MODULE_NAME = "WCAG_Site_PDF_Scanner.py"
BLOCKED_SUFFIXES = {".env", ".key", ".p12", ".pem", ".pfx", ".pyc"}


def _project_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
    if project is None:
        raise ValueError("pyproject.toml has no [project] table")
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group(0))
    if version is None:
        raise ValueError("[project] has no literal version")
    return version.group(1)


def _assert_safe_names(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"archive contains an unsafe path: {name}")
        lower_name = name.lower()
        if any(lower_name.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
            raise ValueError(f"archive contains a blocked file type: {name}")
        if "__pycache__" in path.parts:
            raise ValueError(f"archive contains a Python cache: {name}")


def _runtime_requirements(requirements_path: Path) -> set[str]:
    return {
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-r "))
    }


def _verify_wheel(wheel_path: Path, version: str, repository_root: Path) -> None:
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        _assert_safe_names(names)
        if MODULE_NAME not in names:
            raise ValueError(f"wheel is missing {MODULE_NAME}")
        if archive.read(MODULE_NAME) != (repository_root / MODULE_NAME).read_bytes():
            raise ValueError("wheel runtime module does not match the repository source")
        executable_python = [name for name in names if name.endswith(".py")]
        if executable_python != [MODULE_NAME]:
            raise ValueError(f"wheel has unexpected Python modules: {executable_python}")

        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_point_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entry_point_names) != 1:
            raise ValueError("wheel must contain one METADATA file and one entry_points.txt")

        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
        if metadata.get("Name") != PROJECT_NAME:
            raise ValueError(f"unexpected project name: {metadata.get('Name')}")
        if metadata.get("Version") != version:
            raise ValueError(f"unexpected project version: {metadata.get('Version')}")
        python_specifiers = {
            value.strip() for value in (metadata.get("Requires-Python") or "").split(",") if value.strip()
        }
        if python_specifiers != {">=3.10", "<3.15"}:
            raise ValueError(f"unexpected Python range: {metadata.get('Requires-Python')}")
        if set(metadata.get_all("Requires-Dist", [])) != _runtime_requirements(repository_root / "requirements.txt"):
            raise ValueError("wheel dependency metadata does not match requirements.txt")

        entry_points = archive.read(entry_point_names[0]).decode("utf-8")
        expected_entry_point = "wcag-site-pdf-scanner = WCAG_Site_PDF_Scanner:main"
        if expected_entry_point not in entry_points:
            raise ValueError("wheel is missing the expected console entry point")


def _verify_sdist(sdist_path: Path, repository_root: Path) -> None:
    with tarfile.open(sdist_path, mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        _assert_safe_names(names)
        links = [member.name for member in members if member.issym() or member.islnk()]
        if links:
            raise ValueError(f"source distribution contains archive links: {links}")
        relative_names = {"/".join(PurePosixPath(name).parts[1:]) for name in names}
        required = {
            MODULE_NAME,
            "LICENSE",
            "README.md",
            "RELEASING.md",
            "pyproject.toml",
            "requirements.txt",
            "requirements-build.txt",
            "examples/build_sample_report.py",
            "examples/sample-report/report.html",
            "examples/sample-report/report.json",
            "scripts/normalize_sdist.py",
            "scripts/prepare_release.py",
            "scripts/verify_distribution.py",
            "tests/test_normalize_sdist.py",
            "tests/test_release_assets.py",
        }
        missing = sorted(required - relative_names)
        if missing:
            raise ValueError(f"source distribution is missing: {', '.join(missing)}")
        for relative_path in (
            MODULE_NAME,
            "pyproject.toml",
            "requirements.txt",
            "requirements-build.txt",
            "examples/sample-report/report.html",
            "examples/sample-report/report.json",
            "scripts/normalize_sdist.py",
            "scripts/prepare_release.py",
            "scripts/verify_distribution.py",
            "tests/test_normalize_sdist.py",
            "tests/test_release_assets.py",
        ):
            archived_name = next(name for name in names if "/".join(PurePosixPath(name).parts[1:]) == relative_path)
            archived_file = archive.extractfile(archived_name)
            if archived_file is None or archived_file.read() != (repository_root / relative_path).read_bytes():
                raise ValueError(f"source distribution content differs from the repository: {relative_path}")


def verify_distribution(dist_dir: Path, repository_root: Path) -> tuple[Path, Path]:
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("dist must contain exactly one wheel and one .tar.gz source distribution")
    version = _project_version(repository_root / "pyproject.toml")
    _verify_wheel(wheels[0], version, repository_root)
    _verify_sdist(sdists[0], repository_root)
    return wheels[0], sdists[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    wheel, sdist = verify_distribution(args.dist_dir.resolve(), repository_root)
    print(f"Verified {wheel.name}")
    print(f"Verified {sdist.name}")


if __name__ == "__main__":
    main()
