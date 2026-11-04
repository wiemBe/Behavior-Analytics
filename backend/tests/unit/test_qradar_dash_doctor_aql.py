"""``./qradar-dash doctor --aql`` reaches ``python -m app.cli.doctor`` (FIX-24, AQL-1).

The wrapper command itself runs, with the fake docker of the other doctor tests on PATH. The
stub accepts any extra in-container doctor argument and logs the call, so the test reads
which arguments the backend received.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_qradar_dash_doctor import DoctorFixture
from tests.unit.test_qradar_dash_doctor_smtp import EXTENDED_STUB

CALL = "exec -T backend python -m app.cli.doctor"
USAGE = "usage: ./qradar-dash doctor [--json] [--check-smtp] [--aql]"


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


def test_aql_alone_reaches_the_backend_doctor(doctor: DoctorFixture) -> None:
    result = doctor.run("--aql")

    assert result.returncode == 0, result.stdout + result.stderr
    assert doctor_calls(doctor) == ["--aql"]


def test_aql_with_check_smtp_reaches_the_backend_doctor_in_the_text_branch(
    doctor: DoctorFixture,
) -> None:
    result = doctor.run("--check-smtp", "--aql")

    assert result.returncode == 0, result.stdout + result.stderr
    assert doctor_calls(doctor) == ["--check-smtp --aql"]


def test_aql_with_json_reaches_the_backend_doctor_in_either_order(
    doctor: DoctorFixture,
) -> None:
    first = doctor.run("--aql", "--json")
    second = doctor.run("--json", "--aql", "--check-smtp")

    for result in (first, second):
        assert result.returncode == 0, result.stdout + result.stderr
        assert isinstance(json.loads(result.stdout), list)
    assert doctor_calls(doctor) == ["--json --aql", "--json --check-smtp --aql"]


def test_without_the_flag_the_backend_doctor_gets_no_aql_argument(
    doctor: DoctorFixture,
) -> None:
    doctor.run()
    doctor.run("--json")
    doctor.run("--check-smtp")

    assert doctor_calls(doctor) == ["", "--json", "--check-smtp"]


@pytest.mark.parametrize(
    "args",
    [
        ("--aql", "--aql"),
        ("--aql", "--json", "--aql"),
        ("--json", "--aql", "--aql", "--check-smtp"),
        ("--aql=yes",),
        ("--aq",),
        ("--aql", "extra"),
    ],
)
def test_every_other_argument_is_still_rejected_and_nothing_runs(
    doctor: DoctorFixture, args: tuple[str, ...]
) -> None:
    result = doctor.run(*args)

    assert result.returncode == 2
    assert "unknown doctor argument" in result.stderr
    assert USAGE in result.stderr
    assert not doctor.calls.exists() or doctor_calls(doctor) == []


def test_the_usage_text_names_the_flag(doctor: DoctorFixture) -> None:
    result = doctor.run("--help")

    assert result.returncode == 0
    usage = result.stdout
    assert "--aql" in usage and "runbook OPS-R72" in usage
    # The header comment of the script names the flag too, next to the other two.
    header = doctor.script.read_text().splitlines()[:10]
    assert "#   ./qradar-dash doctor [--json] [--check-smtp] [--aql]" in header
