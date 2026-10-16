"""Build a diagnostic archive while removing credentials from every text member.

Secret values are supplied only through reserved members of the input tar stream. Empty values
and values shorter than eight bytes are deliberately ignored so exact-value replacement cannot
destroy ordinary text. Reserved members are consumed in memory and are never copied to output.
"""

from __future__ import annotations

import re
import sys
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

_RESERVED_PREFIX = ".support-input/"
_CONFIG_MEMBER = f"{_RESERVED_PREFIX}config.env"
_EXACT_SECRET_KEYS = {
    "SECRET_KEY",
    "ENCRYPTION_KEY",
    "ENCRYPTION_KEYS_JSON",
    "POSTGRES_PASSWORD",
    "QRADAR_SEC_TOKEN",
    "OIDC_CLIENT_SECRET",
    "LLM_API_KEY",
    "SMTP_PASSWORD",
}
_KEPT_TOKEN_FILE_KEY = "QRADAR_API_TOKEN_FILE"  # noqa: S105 - a path key, not a credential
_ENV_LINE_RE = re.compile(
    rb"^\s*(?P<comment>#\s*)?(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)
_JSON_STRING_RE = re.compile(rb'"((?:[^"\\]|\\.)*)"')
_AUTHORIZATION_RE = re.compile(rb"(?im)(authorization:)[^\r\n]*")
_SEC_RE = re.compile(rb"(?i)(\bSEC\b\s*[:=])\s*\S+")
_FERNET_RE = re.compile(rb"gAAAAA[A-Za-z0-9_-]+={0,2}")
_PRIVATE_KEY_RE = re.compile(
    rb"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----.*?-----END \1-----",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class ArchiveMember:
    """One regular input member and the metadata retained in the output archive."""

    info: tarfile.TarInfo
    data: bytes


def is_secret_env_key(key: str) -> bool:
    """Return whether an env assignment must be removed from ``config.env``."""
    normalized = key.upper()
    if normalized == _KEPT_TOKEN_FILE_KEY:
        return False
    return (
        normalized in _EXACT_SECRET_KEYS
        or "WEBHOOK" in normalized
        or "PASSWORD" in normalized
        or "TOKEN" in normalized
    )


def filter_config_env(data: bytes) -> tuple[bytes, list[bytes]]:
    """Remove secret assignments and return their values separately for exact scrubbing."""
    kept: list[bytes] = []
    secrets: list[bytes] = []
    for line in data.splitlines(keepends=True):
        candidate = line.rstrip(b"\r\n")
        match = _ENV_LINE_RE.match(candidate)
        if match is None:
            kept.append(line)
            continue
        key = match.group("key").decode("ascii")
        if not is_secret_env_key(key):
            kept.append(line)
            continue
        value = match.group("value").rstrip()
        secrets.append(value)
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in (b'"', b"'"):
            secrets.append(value[1:-1])  # the unquoted form is what appears in logs
        if key.upper() == "ENCRYPTION_KEYS_JSON":
            secrets.extend(m.group(1) for m in _JSON_STRING_RE.finditer(value))
    return b"".join(kept), secrets


def _usable_secret_values(values: Iterable[bytes]) -> list[bytes]:
    unique = {
        value.rstrip(b"\r\n")
        for value in values
        if len(value.rstrip(b"\r\n")) >= 8
    }
    return sorted(unique, key=len, reverse=True)


def scrub_text(data: bytes, secret_values: Iterable[bytes]) -> bytes:
    """Apply the four ordered support-bundle scrub passes to one text payload."""
    for secret in _usable_secret_values(secret_values):
        data = data.replace(secret, b"<redacted>")
    data = _AUTHORIZATION_RE.sub(rb"\1 <redacted>", data)
    data = _SEC_RE.sub(rb"\1 <redacted>", data)
    data = _FERNET_RE.sub(b"<redacted>", data)
    data = _PRIVATE_KEY_RE.sub(b"<redacted>", data)
    return data


def _normalized_name(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name


def _read_members(source: BinaryIO) -> list[ArchiveMember]:
    members: list[ArchiveMember] = []
    with tarfile.open(fileobj=source, mode="r|*") as archive:
        for info in archive:
            name = _normalized_name(info.name)
            if not info.isfile():
                continue
            extracted = archive.extractfile(info)
            if extracted is None:
                raise ValueError(f"could not read archive member {name!r}")
            copied = tarfile.TarInfo(name=name)
            copied.mode = info.mode
            copied.mtime = info.mtime
            copied.uid = info.uid
            copied.gid = info.gid
            copied.uname = info.uname
            copied.gname = info.gname
            members.append(ArchiveMember(copied, extracted.read()))
    return members


def build_scrubbed_archive(source: BinaryIO, destination: BinaryIO) -> None:
    """Read an assembly tar stream and write a gzip-compressed scrubbed tar stream."""
    members = _read_members(source)
    config = next((member for member in members if member.info.name == _CONFIG_MEMBER), None)
    if config is None:
        raise ValueError(f"input archive is missing {_CONFIG_MEMBER}")

    filtered_config, env_secrets = filter_config_env(config.data)
    reserved_secrets = [
        member.data
        for member in members
        if member.info.name.startswith(_RESERVED_PREFIX)
        and member.info.name != _CONFIG_MEMBER
    ]
    secret_values = _usable_secret_values([*env_secrets, *reserved_secrets])

    output_members = [
        member for member in members if not member.info.name.startswith(_RESERVED_PREFIX)
    ]
    config_info = tarfile.TarInfo(name="config.env")
    config_info.mode = 0o600
    config_info.mtime = config.info.mtime
    output_members.append(ArchiveMember(config_info, filtered_config))

    with tarfile.open(fileobj=destination, mode="w:gz") as archive:
        for member in sorted(output_members, key=lambda item: item.info.name):
            scrubbed = scrub_text(member.data, secret_values)
            member.info.size = len(scrubbed)
            archive.addfile(member.info, BytesIO(scrubbed))


def main() -> int:
    """Scrub the tar stream on stdin and emit the finished archive on stdout."""
    try:
        build_scrubbed_archive(sys.stdin.buffer, sys.stdout.buffer)
    except Exception as exc:
        print(
            f"support bundle scrubber failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
