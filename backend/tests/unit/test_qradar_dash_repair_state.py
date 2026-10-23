"""repair-state is exercised through `./qradar-dash repair-state`, with Docker stubbed."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from tests.unit import test_qradar_dash_upgrade as base_upgrade
from tests.unit.test_qradar_dash_upgrade import (
    NEW_IMAGES,
    OLD_IMAGES,
    OLD_VERSION,
    SCRIPT,
    TARGET_VERSION,
    UpgradeFixture,
    output,
)

upgrade = base_upgrade.upgrade

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

REPO = Path(__file__).resolve().parents[3]


def _failed(upgrade: UpgradeFixture) -> None:
    upgrade.write_state(previous=OLD_IMAGES, outcome="failed")


def _unchanged(upgrade: UpgradeFixture, before: str) -> None:
    assert (upgrade.state_dir / "state.json").read_text() == before


def test_each_removed_duplicate_appears_once() -> None:
    text = SCRIPT.read_text()
    assert text.count("command -v docker") == 1
    assert text.count("trap 'exit 129' HUP") == 1
    assert text.count("trap 'exit 130' INT") == 1
    assert text.count("trap 'exit 143' TERM") == 1
    assert text.count('[ "$STATE_LAST_OUTCOME" = failed ]') == 1


def test_the_install_cadence_sentence_matches_the_enum() -> None:
    enums = (REPO / "backend" / "app" / "models" / "enums.py").read_text()
    match = re.search(r"FIVE_MINUTES = (\d+)", enums)
    assert match is not None
    minutes = int(match.group(1)) // 60
    phrase = (
        f"every {minutes} min unless changed under Administration → Search cadence"
    )
    assert "return SearchCadence.FIVE_MINUTES" in enums
    assert "SearchFamily.WAF_COLLECTION" in enums
    assert "SearchFamily.IPS_COLLECTION" in enums
    assert phrase in SCRIPT.read_text()


def test_repair_state_help_exits_zero(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("repair-state", "--help")

    assert result.returncode == 0
    assert "repair-state" in result.stdout


def test_repair_state_rejects_an_unknown_argument(upgrade: UpgradeFixture) -> None:
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run("repair-state", "extra")

    assert result.returncode == 2
    assert "unknown repair-state argument: extra" in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_when_not_installed(upgrade: UpgradeFixture) -> None:
    (upgrade.root / ".env").unlink()

    result = upgrade.run("repair-state")

    assert result.returncode == 1
    assert ".env is missing" in result.stderr
    assert "./qradar-dash install" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_without_docker(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)
    before = (upgrade.state_dir / "state.json").read_text()
    (upgrade.bin_dir / "docker").unlink()

    result = upgrade.run("repair-state")

    assert result.returncode == 1
    assert "Docker is not installed" in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_a_bad_state_field(upgrade: UpgradeFixture) -> None:
    (upgrade.state_dir / "state.json").write_text("{not json\n")

    result = upgrade.run("repair-state")

    assert result.returncode == 1
    assert "field json" in result.stderr
    assert "./qradar-dash status" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_when_last_upgrade_is_missing(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("repair-state")

    assert result.returncode == 1
    assert "no last_upgrade" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_a_rolled_back_outcome(upgrade: UpgradeFixture) -> None:
    upgrade.write_state(previous=OLD_IMAGES, outcome="rolled_back")
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run("repair-state")

    assert result.returncode == 1, output(result)
    assert "last_upgrade.outcome is rolled_back" in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_a_success_record_when_the_new_version_runs(
    upgrade: UpgradeFixture,
) -> None:
    upgrade.write_state(previous=OLD_IMAGES, outcome="success")
    upgrade.running_version.write_text(f"{TARGET_VERSION}\n")
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run("repair-state")

    assert result.returncode == 1, output(result)
    assert "last_upgrade.outcome is success" in result.stderr
    assert f"already {TARGET_VERSION}" in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_a_restored_failure(upgrade: UpgradeFixture) -> None:
    upgrade.write_state(
        previous=OLD_IMAGES,
        outcome="failed",
        restored_from=upgrade.backup_root,
    )
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run("repair-state")

    assert result.returncode == 1, output(result)
    assert "restored_from is" in result.stderr
    assert str(upgrade.backup_root) in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_when_health_is_not_200(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run("repair-state", STUB_READY_CODE="503")

    assert result.returncode == 1, output(result)
    assert "/api/v1/health/ready answered HTTP 503" in result.stderr
    assert "curl exit 0" in result.stderr
    assert "./qradar-dash doctor" in result.stderr
    assert "./qradar-dash logs backend" in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_when_the_health_request_fails(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)

    result = upgrade.run("repair-state", STUB_HEALTH_FAIL_VERSION="all")

    assert result.returncode == 1, output(result)
    assert "HTTP 000" in result.stderr
    assert "curl exit 22" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_when_version_does_not_answer(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)

    result = upgrade.run("repair-state", STUB_VERSION_FAIL="1")

    assert result.returncode == 1, output(result)
    assert "/api/v1/version did not answer" in result.stderr
    assert "./qradar-dash logs backend" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_when_the_image_head_is_missing(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)

    result = upgrade.run("repair-state", STUB_IMAGE_HEAD="missing")

    assert result.returncode == 1, output(result)
    assert "migration_head is missing" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_when_the_database_revision_is_unreadable(
    upgrade: UpgradeFixture,
) -> None:
    _failed(upgrade)

    result = upgrade.run("repair-state", STUB_DB_REVISION="none")

    assert result.returncode == 1, output(result)
    assert "SELECT version_num FROM alembic_version returned nothing" in result.stderr
    assert "./qradar-dash doctor" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_an_alembic_mismatch(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run(
        "repair-state", STUB_DB_REVISION="0031", STUB_IMAGE_HEAD="0032"
    )

    assert result.returncode == 1, output(result)
    assert "database alembic revision is 0031" in result.stderr
    assert "image migration_head is 0032" in result.stderr
    assert "OPS-R11" in result.stderr
    assert "Nothing was written." in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_refuses_a_version_that_is_neither_end(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)

    result = upgrade.run("repair-state", STUB_WRONG_VERSION_FOR=OLD_VERSION)

    assert result.returncode == 1, output(result)
    assert "running version 9.9.9" in result.stderr
    assert f"last_upgrade.from {OLD_VERSION}" in result.stderr
    assert f"last_upgrade.to {TARGET_VERSION}" in result.stderr
    assert "Nothing was written." in result.stderr


def test_repair_state_refuses_when_digests_are_unavailable(upgrade: UpgradeFixture) -> None:
    _failed(upgrade)
    upgrade.running_version.write_text(f"{TARGET_VERSION}\n")
    calls = upgrade.actions.parent / "digest-calls"

    result = upgrade.run(
        "repair-state",
        STUB_DIGEST_CALLS=str(calls),
        STUB_DIGEST_FAIL_FROM="1",
    )

    assert result.returncode == 1, output(result)
    assert "running image digests could not be recorded" in result.stderr
    assert "Nothing was written." in result.stderr
    assert upgrade.state()["last_upgrade"]["outcome"] == "failed"  # type: ignore[index]


def test_repair_state_keeps_the_previous_file_when_the_write_fails(
    upgrade: UpgradeFixture,
) -> None:
    _failed(upgrade)
    upgrade.running_version.write_text(f"{TARGET_VERSION}\n")
    before = (upgrade.state_dir / "state.json").read_text()

    result = upgrade.run("repair-state", STUB_MV_FAIL_STATE="1")

    assert result.returncode == 1, output(result)
    assert "state file write failed; the previous state was kept" in result.stderr
    assert "fix permissions of .qradar-dash" in result.stderr
    assert "./qradar-dash repair-state" in result.stderr
    _unchanged(upgrade, before)


def test_repair_state_records_success_when_the_new_version_is_healthy(
    upgrade: UpgradeFixture,
) -> None:
    _failed(upgrade)
    upgrade.running_version.write_text(f"{TARGET_VERSION}\n")

    result = upgrade.run("repair-state")

    assert result.returncode == 0, output(result)
    state = upgrade.state()
    assert state["installed_version"] == TARGET_VERSION
    assert state["images"] == NEW_IMAGES
    assert state["previous_images"] == OLD_IMAGES
    last = state["last_upgrade"]
    assert last["outcome"] == "success"  # type: ignore[index]
    assert f"Recorded installed_version {TARGET_VERSION}, outcome success" in result.stdout
    assert "The upgrade itself succeeded; do not roll back." in result.stdout


def test_repair_state_records_rolled_back_when_the_old_version_is_healthy(
    upgrade: UpgradeFixture,
) -> None:
    _failed(upgrade)

    result = upgrade.run("repair-state")

    assert result.returncode == 0, output(result)
    state = upgrade.state()
    assert state["installed_version"] == OLD_VERSION
    assert state["images"] == OLD_IMAGES
    assert state["previous_images"] is None
    last = state["last_upgrade"]
    assert last["outcome"] == "rolled_back"  # type: ignore[index]
    assert f"Recorded installed_version {OLD_VERSION}, outcome rolled_back" in result.stdout
    assert "The rollback itself succeeded; do not roll back again." in result.stdout


def test_repair_state_records_a_manual_rollback_whose_failed_write_was_lost(
    upgrade: UpgradeFixture,
) -> None:
    # A manual rollback of a successful upgrade writes "failed" first. When that write itself
    # fails, state.json still holds the upgrade's success record while the old version runs.
    backup = upgrade.make_backup()
    upgrade.write_state(
        installed=TARGET_VERSION,
        images=NEW_IMAGES,
        previous=OLD_IMAGES,
        outcome="success",
        backup=backup,
    )

    result = upgrade.run("repair-state")

    assert result.returncode == 0, output(result)
    state = upgrade.state()
    assert state["installed_version"] == OLD_VERSION
    assert state["images"] == OLD_IMAGES
    assert state["previous_images"] is None
    last = state["last_upgrade"]
    assert last["outcome"] == "rolled_back"  # type: ignore[index]
    assert last["from"] == OLD_VERSION  # type: ignore[index]
    assert last["to"] == TARGET_VERSION  # type: ignore[index]
    assert f"Recorded installed_version {OLD_VERSION}, outcome rolled_back" in result.stdout
