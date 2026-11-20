"""Exercise qradar-dash offline entrypoints with stubbed Docker and QRadar."""

import pytest

from tests.unit import test_qradar_dash_installer as installer
from tests.unit import test_qradar_dash_upgrade as upgrades

inst = installer.inst
build_path = installer.build_path
flow_path = installer.flow_path
hb_path = installer.hb_path
upgrade = upgrades.upgrade


@pytest.mark.parametrize("qr_rc, hb_rc", [(0, 0), (3, 0), (0, 2), (3, 2)])
def test_install_starts_walker_on_success_and_warning_exits(inst, tmp_path, hb_path, qr_rc, hb_rc):
    result = installer._scripted(
        inst, tmp_path, hb_path, qr_rc=qr_rc, hb_rc=hb_rc, HISTORY_BACKFILL="no",
    )
    assert result.returncode == (1 if qr_rc else 0), result.stdout + result.stderr
    starts = [line for line in installer._docker_log(tmp_path)
              if " up -d --wait --wait-timeout 180 backend " in line]
    assert len(starts) == 1
    assert "celery-walker" in starts[0].split()
    assert installer._state_exists(inst)


@pytest.mark.parametrize("qradar_fail", ["0", "1"])
def test_offline_upgrade_starts_walker_even_if_qradar_check_warns(upgrade, qradar_fail):
    result = upgrade.run(
        "upgrade", upgrades.TARGET_VERSION, "--offline", STUB_QRADAR_FAIL=qradar_fail,
    )
    assert result.returncode == 0, upgrades.output(result)
    starts = [line for line in upgrade.action_text().splitlines()
              if " up -d --wait --wait-timeout 180 backend " in line]
    assert len(starts) == 1
    assert "celery-walker" in starts[0].split()
    assert " pull " not in upgrade.action_text()
