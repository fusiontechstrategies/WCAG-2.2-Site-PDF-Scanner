#!/usr/bin/env python3
"""Normalize a gzip-compressed source distribution for reproducible builds."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import os
import re
import struct
import tarfile
import tempfile
import unicodedata
from io import BytesIO
from pathlib import Path


class SdistNormalizationError(RuntimeError):
    """A source distribution cannot be normalized safely."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SdistNormalizationError(message)


def validate_member_name(name: str) -> None:
    require("\\" not in name, f"Archive member uses a backslash: {name!r}")
    require(not name.startswith("/"), f"Archive member is absolute: {name!r}")
    require(
        re.match(r"^[A-Za-z]:", name) is None,
        f"Archive member uses a drive path: {name!r}",
    )
    parts = name.rstrip("/").split("/")
    require(
        bool(parts) and all(part not in {"", ".", ".."} for part in parts),
        f"Archive member traverses or aliases a path: {name!r}",
    )


def content_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_members(path: Path) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    names: set[str] = set()
    portable_names: set[str] = set()
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            validate_member_name(member.name)
            name = member.name.rstrip("/")
            require(name not in names, f"Archive contains a duplicate member: {name!r}")
            names.add(name)
            portable_name = unicodedata.normalize("NFC", name).casefold()
            require(
                portable_name not in portable_names,
                f"Archive contains a non-portable duplicate member: {name!r}",
            )
            portable_names.add(portable_name)
            require(
                member.isfile() or member.isdir(),
                f"Archive contains a link or device: {member.name!r}",
            )
            value = None
            if member.isfile():
                handle = archive.extractfile(member)
                require(handle is not None, f"Unable to read archive member: {name!r}")
                value = handle.read()
                require(len(value) == member.size, f"Archive member is truncated: {name!r}")
            members.append((member, value))
    return members


def member_identity(
    members: list[tuple[tarfile.TarInfo, bytes | None]],
) -> list[tuple[str, str, int]]:
    return [
        (
            member.name.rstrip("/"),
            "directory" if member.isdir() else content_digest(value or b""),
            0 if value is None else len(value),
        )
        for member, value in members
    ]


def normalize_generated_text(name: str, value: bytes) -> bytes:
    if not (name.endswith("/PKG-INFO") or name.endswith("/setup.cfg")):
        return value
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SdistNormalizationError(f"Generated source metadata is not UTF-8: {name!r}") from error
    require("\x00" not in text, f"Generated source metadata contains a NUL: {name!r}")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def build_stored_gzip(value: bytes, source_date_epoch: int) -> bytes:
    """Build gzip bytes with fixed-size stored DEFLATE blocks and no zlib variance."""
    output = bytearray(b"\x1f\x8b\x08\x00")
    output.extend(struct.pack("<I", source_date_epoch))
    output.extend(b"\x00\xff")
    for offset in range(0, len(value), 0xFFFF):
        block = value[offset : offset + 0xFFFF]
        final = offset + len(block) == len(value)
        output.append(1 if final else 0)
        output.extend(struct.pack("<HH", len(block), len(block) ^ 0xFFFF))
        output.extend(block)
    if not value:
        output.extend(b"\x01\x00\x00\xff\xff")
    output.extend(struct.pack("<II", binascii.crc32(value) & 0xFFFFFFFF, len(value) & 0xFFFFFFFF))
    return bytes(output)


def normalize_sdist(path: Path, source_date_epoch: int) -> str:
    require(not path.is_symlink(), "Source distribution must not be a symbolic link")
    path = path.resolve(strict=True)
    require(path.is_file(), "Source distribution is invalid")
    require(path.name.endswith(".tar.gz"), "Source distribution must end in .tar.gz")
    require(source_date_epoch >= 0, "SOURCE_DATE_EPOCH must not be negative")
    require(
        source_date_epoch <= 0xFFFFFFFF,
        "SOURCE_DATE_EPOCH exceeds the gzip timestamp range",
    )
    members = read_members(path)
    normalized_inputs = [
        (member, None if value is None else normalize_generated_text(member.name, value)) for member, value in members
    ]
    expected_identity = sorted(member_identity(normalized_inputs))

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        raw_tar = BytesIO()
        with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.PAX_FORMAT) as output:
            for original, value in sorted(normalized_inputs, key=lambda item: item[0].name):
                normalized = tarfile.TarInfo(original.name.rstrip("/"))
                normalized.mtime = source_date_epoch
                normalized.uid = 0
                normalized.gid = 0
                normalized.uname = ""
                normalized.gname = ""
                normalized.pax_headers = {}
                if original.isdir():
                    normalized.type = tarfile.DIRTYPE
                    normalized.mode = 0o755
                    normalized.size = 0
                    output.addfile(normalized)
                else:
                    require(value is not None, "Regular archive member has no data")
                    normalized.type = tarfile.REGTYPE
                    # Source-distribution members are data. Canonicalize their
                    # mode so mounted filesystems cannot invent executability.
                    normalized.mode = 0o644
                    normalized.size = len(value)
                    output.addfile(normalized, BytesIO(value))
        temporary_path.write_bytes(build_stored_gzip(raw_tar.getvalue(), source_date_epoch))
        normalized_members = read_members(temporary_path)
        require(
            member_identity(normalized_members) == expected_identity,
            "Normalized archive changed names or file contents",
        )
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    arguments = parser.parse_args()
    digest = normalize_sdist(arguments.path, arguments.source_date_epoch)
    print(f"Normalized {arguments.path.name}: sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
