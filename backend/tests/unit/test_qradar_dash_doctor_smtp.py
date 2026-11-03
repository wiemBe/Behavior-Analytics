"""``./qradar-dash doctor --check-smtp`` reaches ``python -m app.cli.doctor`` (MAIL-1).

The wrapper command itself runs, with the fake docker of the other doctor tests on PATH. The
stub accepts any extra in-container doctor argument and logs the call, so the test reads
which arguments the backend received.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_qradar_dash_doctor import DOCKER_STUB, DoctorFixture

CALL = "exec -T backend python -m app.cli.doctor"

# Same stub, with the two in-container doctor patterns widened to prefix globs.
EXTENDED_STUB = DOCKER_STUB.replace(
    '*" exec -T backend python -m app.cli.doctor --json")',
    '*" exec -T backend python -m app.cli.doctor --json"*)',
).replace(
    '*" exec -T backend python -m app.cli.doctor")',
    '*" exec -T backend python -m app.cli.doctor"*)',
)


@pytest.fixture
def doctor(tmp_path: Path) -> DoctorFixture:
    fixture = DoctorFixture(tmp_path)
    fixture._stub("docker", EXTENDED_STUB)
    return fixture


def doctor_calls(fixture: DoctorFixture) -> list[str]:
    return [
        line.split(CALL, 1)[1].strip()
        for line in fixture.calls.read_text().splitlines()
        if CALL in line
    ]


def test_the_stub_widening_took_effect() -> None:
    assert EXTENDED_STUB != DOCKER_STUB and EXTENDED_STUB.count('doctor --json"*)') == 1


def test_check_smtp_alone_reaches_the_backend_doctor(doctor: DoctorFixture) -> None:
    result = doctor.run("--check-smtp")

    assert result.returncode == 0, result.stdout + result.stderr
    assert doctor_calls(doctor) == ["--check-smtp"]


def test_check_smtp_with_json_reaches_the_backend_doctor_in_either_order(
    doctor: DoctorFixture,
) -> None:
    first = doctor.run("--check-smtp", "--json")
    second = doctor.run("--json", "--check-smtp")

    for result in (first, second):
        assert result.returncode == 0, result.stdout + result.stderr
        assert isinstance(json.loads(result.stdout), list)
    assert doctor_calls(doctor) == ["--json --check-smtp", "--json --check-smtp"]


def test_without_the_flag_the_backend_doctor_gets_no_smtp_argument(
    doctor: DoctorFixture,
) -> None:
    doctor.run()
    doctor.run("--json")

    assert doctor_calls(doctor) == ["", "--json"]


def test_the_backend_exit_code_still_decides_with_the_flag(doctor: DoctorFixture) -> None:
    result = doctor.run(
        "--check-smtp",
        STUB_INCONTAINER_OUTPUT="WARN  email  relay r:587: handshake failed (tls_failed)",
        STUB_INCONTAINER_RC="1",
    )

    assert result.returncode == 1
    assert "handshake failed (tls_failed)" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("--check-smtp", "extra"),
        ("--check-smtp", "--check-smtp"),
        ("--json", "--json", "--check-smtp"),
        ("--check-smtp=yes",),
        ("--check",),
        ("--smtp",),
    ],
)
def test_every_other_argument_is_still_rejected_and_nothing_runs(
    doctor: DoctorFixture, args: tuple[str, ...]
) -> None:
    result = doctor.run(*args)

    assert result.returncode == 2
    assert "unknown doctor argument" in result.stderr
    assert "usage: ./qradar-dash doctor [--json] [--check-smtp]" in result.stderr
    assert not doctor.calls.exists() or doctor_calls(doctor) == []


def test_the_usage_text_names_the_flag(doctor: DoctorFixture) -> None:
    result = doctor.run("--help")

    assert result.returncode == 0
    assert "--check-smtp" in result.stdout and "nothing is" in result.stdout
