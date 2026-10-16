"""Pure support-bundle scrubber tests; no Docker, database, or QRadar is required."""

from __future__ import annotations

import tarfile
from io import BytesIO

import pytest

from app.cli.support_bundle import build_scrubbed_archive, main

SEC_TOKEN = b"b4ae60b5-cf8b-4fa4-9fa1-81cf7b79b337"
OTHER_CORRELATION_ID = b"69fc706e-8905-498f-88cc-4d1026da7df2"
FERNET_TOKEN = b"gAAAAABplanted_url-safe-ciphertext_0123456789="
ENCRYPTION_KEY = b"encryption-key-material-which-must-go"
POSTGRES_PASSWORD = b"database-password-which-must-go"
AUTHORIZATION_VALUE = b"Bearer planted-session-cookie"
PRIVATE_KEY_BODY = b"planted-private-key-material"


def _input_tar() -> BytesIO:
    files = {
        ".support-input/config.env": (
            b"ENVIRONMENT=production\n"
            b"ENCRYPTION_KEY=" + ENCRYPTION_KEY + b"\n"
            b"POSTGRES_PASSWORD=" + POSTGRES_PASSWORD + b"\n"
            b"QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token\n"
        ),
        ".support-input/secrets/qradar.sec": SEC_TOKEN + b"\n",
        "logs/backend.log": (
            b"request marker=" + SEC_TOKEN + b"\n"
            b"Authorization: " + AUTHORIZATION_VALUE + b"\n"
            b"SEC = named-token-value\n"
            b"ciphertext=" + FERNET_TOKEN + b"\n"
            b"-----BEGIN PRIVATE KEY-----\n"
            + PRIVATE_KEY_BODY
            + b"\n-----END PRIVATE KEY-----\n"
            b"-----BEGIN CERTIFICATE-----\ncertificate-body\n"
            b"-----END CERTIFICATE-----\n"
        ),
        "watermarks.txt": (
            b'{"correlation_id":"' + SEC_TOKEN + b'","consecutive_failures":2}\n'
            b'{"correlation_id":"' + OTHER_CORRELATION_ID + b'"}\n'
        ),
    }
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.mode = 0o600
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    stream.seek(0)
    return stream


def _archive_members(archive_bytes: bytes) -> tuple[list[str], bytes]:
    names: list[str] = []
    contents: list[bytes] = []
    with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            names.append(member.name)
            extracted = archive.extractfile(member)
            assert extracted is not None
            contents.append(extracted.read())
    return names, b"\n".join(contents)


def test_archive_scrubs_every_secret_and_keeps_diagnostic_fields() -> None:
    destination = BytesIO()

    build_scrubbed_archive(_input_tar(), destination)

    names, contents = _archive_members(destination.getvalue())
    assert not any(name.startswith(".support-input/") for name in names)
    for planted_secret in (
        SEC_TOKEN,
        FERNET_TOKEN,
        ENCRYPTION_KEY,
        POSTGRES_PASSWORD,
        AUTHORIZATION_VALUE,
        PRIVATE_KEY_BODY,
        b"named-token-value",
    ):
        assert planted_secret not in contents
    assert b"ENCRYPTION_KEY=" not in contents
    assert b"POSTGRES_PASSWORD=" not in contents
    assert b"correlation_id" in contents
    assert OTHER_CORRELATION_ID in contents
    assert b"consecutive_failures" in contents
    assert b"BEGIN CERTIFICATE" in contents
    assert b"QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token" in contents


def _single(files: dict[str, bytes]) -> BytesIO:
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    stream.seek(0)
    return stream


def test_quoted_env_values_are_removed_from_other_files() -> None:
    destination = BytesIO()
    source = _single(
        {
            ".support-input/config.env": b'SMTP_PASSWORD="quoted-smtp-secret"\nKEEP=1\n',
            "logs/a.log": b"smtp login quoted-smtp-secret failed\n",
        }
    )

    build_scrubbed_archive(source, destination)

    _, contents = _archive_members(destination.getvalue())
    assert b"quoted-smtp-secret" not in contents
    assert b"KEEP=1" in contents


def test_short_and_empty_secret_values_do_not_destroy_text() -> None:
    destination = BytesIO()
    source = _single(
        {
            ".support-input/config.env": b"LLM_API_KEY=\nSMTP_PASSWORD=short\n",
            "logs/a.log": b"a short note\n",
        }
    )

    build_scrubbed_archive(source, destination)

    _, contents = _archive_members(destination.getvalue())
    assert b"a short note" in contents
    assert b"<redacted> note" not in contents


def test_missing_config_member_fails_closed_and_writes_nothing() -> None:
    destination = BytesIO()

    with pytest.raises(ValueError, match=r"config\.env"):
        build_scrubbed_archive(_single({"logs/a.log": b"x\n"}), destination)

    assert destination.getvalue() == b""


def test_main_returns_one_and_emits_no_archive_on_garbage_input(
    monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    class FakeStdin:
        buffer = BytesIO(b"not a tar stream")

    monkeypatch.setattr("sys.stdin", FakeStdin())

    assert main() == 1
    captured = capsysbinary.readouterr()
    assert captured.out == b""
    assert b"scrubber failed" in captured.err


def _env_archive(env: bytes, log: bytes = b"nothing\n") -> tuple[bytes, bytes]:
    destination = BytesIO()
    build_scrubbed_archive(
        _single({".support-input/config.env": env, "logs/a.log": log}), destination
    )
    names_contents = _archive_members(destination.getvalue())[1]
    with tarfile.open(fileobj=BytesIO(destination.getvalue()), mode="r:gz") as archive:
        extracted = archive.extractfile("config.env")
        assert extracted is not None
        return extracted.read(), names_contents


def test_export_and_spaced_secret_lines_are_removed() -> None:
    config, contents = _env_archive(
        b"  export POSTGRES_PASSWORD=exported-secret-1\n"
        b"ENCRYPTION_KEY = spaced-secret-2\n"
        b"export QRADAR_API_TOKEN_FILE=/run/secrets/tok\n"
        b"QRADAR_API_TOKEN_FILE=/run/secrets/tok2\n"
    )

    assert b"POSTGRES_PASSWORD" not in config
    assert b"ENCRYPTION_KEY" not in config
    assert b"exported-secret-1" not in contents
    assert b"spaced-secret-2" not in contents
    assert b"export QRADAR_API_TOKEN_FILE=/run/secrets/tok" in config
    assert b"QRADAR_API_TOKEN_FILE=/run/secrets/tok2" in config


def test_commented_secret_lines_are_removed_and_their_values_scrubbed() -> None:
    config, contents = _env_archive(
        b"# POSTGRES_PASSWORD=old-password-value\n"
        b"#ENCRYPTION_KEY=old-encryption-value\n"
        b"#   export SMTP_PASSWORD = old-smtp-value\n"
        b"#RETENTION_ENABLED=false\n"
        b"# --- Core ---\n",
        log=b"login old-password-value old-encryption-value old-smtp-value\n",
    )

    for gone in (b"POSTGRES_PASSWORD", b"ENCRYPTION_KEY", b"SMTP_PASSWORD"):
        assert gone not in config
    for value in (b"old-password-value", b"old-encryption-value", b"old-smtp-value"):
        assert value not in contents
    assert b"#RETENTION_ENABLED=false" in config
    assert b"# --- Core ---" in config


def test_each_key_inside_encryption_keys_json_is_scrubbed_alone() -> None:
    _, contents = _env_archive(
        b'ENCRYPTION_KEYS_JSON={"1":"first-key-material-aaaa","2":"second-key-material-bbb"}\n',
        log=b"rotating with second-key-material-bbb now\n",
    )

    assert b"second-key-material-bbb" not in contents
    assert b"first-key-material-aaaa" not in contents
