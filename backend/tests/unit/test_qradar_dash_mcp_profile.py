"""OPS-23: `install` keeps a profile the operator added, and `upgrade`/`rollback` move qradar-mcp.

Every test runs the `qradar-dash` command against the stubbed Docker of the install and upgrade
suites. Nothing here depends on the host's `docker compose`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit import test_qradar_dash_installer as base_install
from tests.unit import test_qradar_dash_upgrade as base_upgrade
from tests.unit.test_qradar_dash_installer import Install, _build, _env_lines
from tests.unit.test_qradar_dash_upgrade import (
    NEW_IMAGES,
    OLD_IMAGES,
    OLD_VERSION,
    TARGET_VERSION,
    UpgradeFixture,
    output,
)

inst = base_install.inst
build_path = base_install.build_path
upgrade = base_upgrade.upgrade

UP = " up -d --wait --wait-timeout 180 "


def _set_profiles(inst: Install, value: str | None) -> None:
    env = inst.root / ".env"
    lines = [x for x in env.read_text().splitlines() if not x.startswith("COMPOSE_PROFILES=")]
    if value is not None:
        lines.append(f"COMPOSE_PROFILES={value}")
    env.write_text("\n".join(lines) + "\n")


def _profile_lines(inst: Install) -> list[str]:
    return [x for x in _env_lines(inst) if x.startswith("COMPOSE_PROFILES=")]


def _rerun(
    inst: Install, tmp_path: Path, path: str, old: str | None, **answers: str | None
) -> subprocess.CompletedProcess[str]:
    _build(inst, tmp_path, path)
    _set_profiles(inst, old)
    result = inst.run(
        "install", "--non-interactive", str(inst.answers(**answers)), "--build",
        "--skip-qradar-check", path=path,
    )
    assert result.returncode == 1, result.stdout + result.stderr  # the stub build fails, after .env
    return result


def test_install_fresh_writes_the_wizard_profile_only(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _build(inst, tmp_path, build_path)
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=proxy"]


def test_install_fresh_external_proxy_writes_no_profile(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    inst.run(
        "install", "--non-interactive",
        str(inst.answers(EXTERNAL_PROXY="yes", TLS_CERT_FILE=None, TLS_KEY_FILE=None,
                         FORWARDED_ALLOW_IPS="10.1.2.3")),
        "--build", "--skip-qradar-check", path=build_path,
    )
    assert _profile_lines(inst) == ["COMPOSE_PROFILES="]


def test_install_rerun_keeps_mcp_next_to_proxy(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    result = _rerun(inst, tmp_path, build_path, "proxy,mcp")
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=proxy,mcp"]
    assert "kept profiles: mcp" in result.stdout


def test_install_rerun_writes_proxy_first_then_the_kept_profile(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _rerun(inst, tmp_path, build_path, "mcp,proxy")
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=proxy,mcp"]


def test_install_rerun_with_an_external_proxy_answer_yields_mcp(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    result = _rerun(
        inst, tmp_path, build_path, "proxy,mcp",
        EXTERNAL_PROXY="yes", TLS_CERT_FILE=None, TLS_KEY_FILE=None,
        FORWARDED_ALLOW_IPS="10.1.2.3",
    )
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=mcp"]
    assert "kept profiles: mcp" in result.stdout


def test_install_rerun_keeps_an_unknown_profile_once_and_in_order(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _rerun(inst, tmp_path, build_path, "proxy, mcp,lab,mcp")
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=proxy,mcp,lab"]


def test_install_rerun_over_proxy_only_or_no_key_is_unchanged(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    result = _rerun(inst, tmp_path, build_path, "proxy")
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=proxy"]
    assert "kept profiles" not in result.stdout
    _rerun(inst, tmp_path, build_path, None)
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=proxy"]


def test_install_rerun_keeps_the_external_proxy_forwarded_ips_when_only_mcp_was_set(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    ext = {"EXTERNAL_PROXY": "yes", "TLS_CERT_FILE": None, "TLS_KEY_FILE": None}
    for forwarded in ("10.1.2.3", None):
        inst.run(
            "install", "--non-interactive",
            str(inst.answers(FORWARDED_ALLOW_IPS=forwarded, **ext)),
            "--build", "--skip-qradar-check", path=build_path,
        )
        _set_profiles(inst, "mcp")  # an external proxy with the operator's mcp
    assert _profile_lines(inst) == ["COMPOSE_PROFILES=mcp"]
    # RL-1: the operator's address is kept, and the frontend's is listed beside it.
    assert "FORWARDED_ALLOW_IPS=10.1.2.3,172.28.10.11" in _env_lines(inst)


# -------------------------------------------------------------- upgrade and rollback


def _profiles(fx: UpgradeFixture, value: str) -> None:
    env = fx.root / ".env"
    env.write_text(env.read_text().replace("COMPOSE_PROFILES=proxy", f"COMPOSE_PROFILES={value}"))


def _up_lines(fx: UpgradeFixture) -> list[str]:
    return [x for x in fx.action_text().splitlines() if UP in x]


def test_upgrade_with_mcp_recreates_it_in_the_same_up_call(upgrade: UpgradeFixture) -> None:
    _profiles(upgrade, "proxy,mcp")
    result = upgrade.run("upgrade", TARGET_VERSION)
    assert result.returncode == 0, output(result)
    ups = _up_lines(upgrade)
    assert len(ups) == 1
    assert ups[0].split(UP)[1].split() == [
        "qradar-mcp", "backend", "celery-worker", "celery-beat", "frontend", "proxy",
    ]


def test_upgrade_without_mcp_does_not_name_it(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION)
    assert result.returncode == 0, output(result)
    assert [x.split(UP)[1].split() for x in _up_lines(upgrade)] == [
        ["backend", "celery-worker", "celery-beat", "frontend", "proxy"]
    ]


def test_upgrade_with_mcp_and_an_external_proxy_names_mcp_but_not_proxy(
    upgrade: UpgradeFixture,
) -> None:
    _profiles(upgrade, "mcp")
    result = upgrade.run("upgrade", TARGET_VERSION)
    assert result.returncode == 0, output(result)
    assert [x.split(UP)[1].split() for x in _up_lines(upgrade)] == [
        ["qradar-mcp", "backend", "celery-worker", "celery-beat", "frontend"]
    ]


def test_failed_upgrade_with_mcp_rolls_back_with_mcp_in_the_old_start(
    upgrade: UpgradeFixture,
) -> None:
    _profiles(upgrade, "proxy,mcp")
    result = upgrade.run("upgrade", TARGET_VERSION, STUB_UP_FAIL_VERSION=TARGET_VERSION)
    assert result.returncode != 0, output(result)
    assert "was rolled back" in result.stderr
    ups = _up_lines(upgrade)
    assert len(ups) == 2  # the failed target start, then the automatic rollback's old start
    assert all("qradar-mcp" in x.split(UP)[1].split() for x in ups)


def _rollback_ready(fx: UpgradeFixture) -> None:
    backup = fx.make_backup()
    fx._write_env(TARGET_VERSION)
    fx.running_version.write_text(f"{TARGET_VERSION}\n")
    fx.write_state(
        installed=TARGET_VERSION, images=NEW_IMAGES, previous=OLD_IMAGES,
        outcome="success", backup=backup,
    )


def test_manual_rollback_with_mcp_recreates_it_with_the_old_image(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)
    _profiles(upgrade, "proxy,mcp")
    result = upgrade.run("rollback", "--non-interactive", "--yes")
    assert result.returncode == 0, output(result)
    assert upgrade.running_version.read_text().strip() == OLD_VERSION
    ups = _up_lines(upgrade)
    assert len(ups) == 1
    assert "qradar-mcp" in ups[0].split(UP)[1].split()


def test_manual_rollback_without_mcp_does_not_name_it(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)
    result = upgrade.run("rollback", "--non-interactive", "--yes")
    assert result.returncode == 0, output(result)
    ups = _up_lines(upgrade)
    assert len(ups) == 1
    assert "qradar-mcp" not in ups[0]


def test_failed_manual_rollback_with_mcp_still_names_it(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)
    _profiles(upgrade, "proxy,mcp")
    result = upgrade.run(
        "rollback", "--non-interactive", "--yes", STUB_UP_FAIL_VERSION=OLD_VERSION
    )
    assert result.returncode != 0, output(result)
    ups = _up_lines(upgrade)
    assert ups and "qradar-mcp" in ups[0].split(UP)[1].split()
