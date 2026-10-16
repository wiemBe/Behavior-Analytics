"""The version module, GET /api/v1/version, and the three-way version equality."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import version as v
from app.main import create_app

REPO = Path(__file__).resolve().parents[3]


def test_reads_a_good_file(tmp_path: Path) -> None:
    f = tmp_path / "VERSION"
    f.write_text("1.2.3\n")
    assert v.read_version([f]) == "1.2.3"


def test_first_existing_file_wins(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    b.write_text("2.0.0\n")
    assert v.read_version([a, b]) == "2.0.0"


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(v.VersionError, match="not found"):
        v.read_version([tmp_path / "VERSION"])


@pytest.mark.parametrize("text", ["", "latest\n", "0.1\n", "0.1.0\n0.2.0\n", " 0.1.0\n"])
def test_malformed_file_is_an_error(tmp_path: Path, text: str) -> None:
    f = tmp_path / "VERSION"
    f.write_text(text)
    with pytest.raises(v.VersionError, match="SemVer"):
        v.read_version([f])


def test_git_sha_absent_or_invalid_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert v.git_sha() is None
    monkeypatch.setenv("GIT_SHA", "not-a-sha")
    assert v.git_sha() is None
    monkeypatch.setenv("GIT_SHA", "A" * 40)
    assert v.git_sha() == "a" * 40


def test_version_route_is_open_and_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc1234")
    client = TestClient(create_app(), raise_server_exceptions=False)
    resp = client.get("/api/v1/version")  # no token
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"version", "git_sha", "migration_head"}
    assert body["version"] == (REPO / "VERSION").read_text().strip()
    assert body["git_sha"] == "abc1234"
    heads = sorted(
        re.match(r"(\d+)_", p.name).group(1)  # type: ignore[union-attr]
        for p in (REPO / "backend/alembic/versions").glob("0*.py")
    )
    assert body["migration_head"] == heads[-1]


def test_version_route_git_sha_null_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    body = TestClient(create_app()).get("/api/v1/version").json()
    assert body["git_sha"] is None


def test_app_reports_the_version_file() -> None:
    assert create_app().version == (REPO / "VERSION").read_text().strip()


def test_version_file_pyproject_and_package_json_agree() -> None:
    file_version = (REPO / "VERSION").read_text().strip()
    pyproject = tomllib.loads((REPO / "backend/pyproject.toml").read_text())
    package = json.loads((REPO / "frontend/package.json").read_text())
    assert pyproject["project"]["version"] == file_version
    assert package["version"] == file_version
