#!/usr/bin/env python3
"""Normalize a pure-Python wheel for cross-platform reproducible builds."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import re
import stat
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
GENERATED_TEXT_SUFFIXES = (
    ".dist-info/METADATA",
    ".dist-info/WHEEL",
    ".dist-info/entry_points.txt",
    ".dist-info/top_level.txt",
)


class WheelNormalizationError(RuntimeError):
    """A wheel cannot be normalized safely."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WheelNormalizationError(message)


def validate_member_name(name: str) -> str:
    require("\\" not in name, f"Wheel member uses a backslash: {name!r}")
    require(not name.startswith("/"), f"Wheel member is absolute: {name!r}")
    require(re.match(r"^[A-Za-z]:", name) is None, f"Wheel member uses a drive path: {name!r}")
    require(not name.endswith("/"), f"Wheel contains an explicit directory: {name!r}")
    parts = tuple(name.split("/"))
    require(bool(parts) and all(part not in {"", ".", ".."} for part in parts), f"Unsafe wheel path: {name!r}")
    require(tuple(PurePosixPath(*parts).parts) == parts, f"Noncanonical wheel path: {name!r}")
    for part in parts:
        require(not part.endswith((" ", ".")), f"Nonportable wheel path: {name!r}")
        require(
            all(ord(character) >= 32 and ord(character) != 127 for character in part),
            f"Wheel path contains a control character: {name!r}",
        )
        require(
            part.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES,
            f"Wheel path uses a reserved Windows name: {name!r}",
        )
    return name


def sha256_record_digest(value: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def validate_record(values: dict[str, bytes], record_name: str) -> None:
    try:
        rows = list(csv.reader(io.StringIO(values[record_name].decode("utf-8"), newline="")))
    except UnicodeDecodeError as error:
        raise WheelNormalizationError("Wheel RECORD is not UTF-8") from error
    recorded: dict[str, tuple[str, str]] = {}
    for row in rows:
        require(len(row) == 3, "Wheel RECORD contains a malformed row")
        name, digest, size = row
        validate_member_name(name)
        require(name not in recorded, f"Wheel RECORD contains a duplicate path: {name!r}")
        recorded[name] = (digest, size)
    require(set(recorded) == set(values), "Wheel RECORD does not cover the exact file set")
    for name, value in values.items():
        digest, size = recorded[name]
        if name == record_name:
            require(digest == "" and size == "", "Wheel RECORD must leave its own identity empty")
        else:
            require(digest == sha256_record_digest(value), f"Wheel RECORD hash mismatch: {name!r}")
            require(size == str(len(value)), f"Wheel RECORD size mismatch: {name!r}")


def normalize_generated_text(name: str, value: bytes) -> bytes:
    if not name.endswith(GENERATED_TEXT_SUFFIXES):
        return value
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WheelNormalizationError(f"Generated wheel metadata is not UTF-8: {name!r}") from error
    require("\x00" not in text, f"Generated wheel metadata contains a NUL: {name!r}")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def build_record(values: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name in sorted(values):
        if name == record_name:
            writer.writerow((name, "", ""))
        else:
            value = values[name]
            writer.writerow((name, sha256_record_digest(value), str(len(value))))
    return output.getvalue().encode("utf-8")


def expected_timestamp(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    parts = list(time.gmtime(source_date_epoch)[:6])
    parts[5] -= parts[5] % 2
    return tuple(parts)  # type: ignore[return-value]


def read_values(path: Path) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    portable_names: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = validate_member_name(member.filename)
            require(name not in values, f"Wheel contains a duplicate member: {name!r}")
            portable = unicodedata.normalize("NFC", name).casefold()
            require(portable not in portable_names, f"Wheel contains a nonportable duplicate: {name!r}")
            portable_names.add(portable)
            require(member.flag_bits & 1 == 0, f"Wheel contains an encrypted member: {name!r}")
            require(not stat.S_ISLNK(member.external_attr >> 16), f"Wheel contains a symbolic link: {name!r}")
            value = archive.read(member)
            require(len(value) == member.file_size, f"Wheel member is truncated: {name!r}")
            values[name] = value
    return values


def verify_normalized(path: Path, expected_values: dict[str, bytes], timestamp: tuple[int, ...]) -> None:
    with zipfile.ZipFile(path) as archive:
        require(archive.comment == b"", "Wheel has a noncanonical archive comment")
        members = archive.infolist()
        require([member.filename for member in members] == sorted(expected_values), "Wheel members are not sorted")
        for member in members:
            name = member.filename
            require(member.date_time == timestamp, f"Wheel member has an unexpected timestamp: {name!r}")
            require(member.compress_type == zipfile.ZIP_STORED, f"Wheel member is not stored canonically: {name!r}")
            require(member.create_system == 3, f"Wheel member has a noncanonical creator system: {name!r}")
            require(member.extra == b"" and member.comment == b"", f"Wheel member has extra metadata: {name!r}")
            mode = member.external_attr >> 16
            require(
                stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o644, f"Wheel member has a noncanonical mode: {name!r}"
            )
            require(
                archive.read(member) == expected_values[name], f"Wheel member changed during normalization: {name!r}"
            )


def normalize_wheel(path: Path, source_date_epoch: int) -> str:
    require(not path.is_symlink(), "Wheel must not be a symbolic link")
    path = path.resolve(strict=True)
    require(path.is_file() and path.suffix == ".whl", "Wheel path is invalid")
    require(315532800 <= source_date_epoch <= 0xFFFFFFFF, "SOURCE_DATE_EPOCH is outside wheel range")
    timestamp = expected_timestamp(source_date_epoch)
    values = read_values(path)
    record_names = [name for name in values if name.endswith(".dist-info/RECORD")]
    metadata_names = [name for name in values if name.endswith(".dist-info/METADATA")]
    require(len(record_names) == 1, "Wheel must contain exactly one RECORD")
    require(len(metadata_names) == 1, "Wheel must contain exactly one METADATA")
    record_name = record_names[0]
    validate_record(values, record_name)
    for name in tuple(values):
        if name != record_name:
            values[name] = normalize_generated_text(name, values[name])
    values[record_name] = build_record(values, record_name)

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary_path, mode="w", compression=zipfile.ZIP_STORED) as output:
            for name in sorted(values):
                member = zipfile.ZipInfo(name, timestamp)
                member.compress_type = zipfile.ZIP_STORED
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | 0o644) << 16
                member.flag_bits = 0
                member.extra = b""
                member.comment = b""
                output.writestr(member, values[name])
        verify_normalized(temporary_path, values, timestamp)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    arguments = parser.parse_args()
    digest = normalize_wheel(arguments.path, arguments.source_date_epoch)
    print(f"Normalized {arguments.path.name}: sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
