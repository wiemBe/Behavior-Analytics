"""S7 exercises the installer entrypoint with fake Docker, including warning exits."""
from pathlib import Path

import pytest

from tests.unit import test_qradar_dash_installer as base
from tests.unit import test_qradar_dash_upgrade as upgrades

inst = base.inst
no_docker_path = base.no_docker_path
build_path = base.build_path
flow_path = base.flow_path
upgrade = upgrades.upgrade


@pytest.fixture
def features_path(flow_path):
    stub = base.HB_DOCKER_STUB.replace(
        'case "$*" in\n',
        'case "$*" in\n'
        '  *" app.cli.features show")\n'
        '    [ "$(rc show_rc)" = 0 ] || exit 1\n'
        '    cat "$HOME/show_out"; exit 0 ;;\n'
        '  *" app.cli.features apply "*) exit "$(rc apply_rc)" ;;\n'
        '  *" app.cli.features defaults")\n'
        '    [ "$(rc defaults_rc)" = 0 ] || exit 1\n'
        '    echo "Families on by code default: python-fixture"; exit 0 ;;\n',
        1,
    )
    base._stub(Path(flow_path) / "docker", stub)
    return flow_path


def calls(tmp_path, text):
    return [line for line in base._docker_log(tmp_path) if text in line]


def show_fixture(tmp_path):
    # Deliberately differs from both .env and code defaults, including the old wrong key.
    output = ("waf_collection_enabled=off {'source': 'db'}\n"
              "identity_collection_enabled=on {'source': 'db'}\n"
              "qradar_health_collection_enabled=off {'source': 'db'}\n"
              "explanation_enabled=off {'source': 'db'}\n")
    (tmp_path / "show_out").write_text(output)
    return output


@pytest.mark.parametrize("qr_rc", [0, 3])
@pytest.mark.parametrize("apply_rc", [0, 1])
def test_answers_apply_after_history_before_start_even_on_qradar_failure(
    inst, tmp_path, features_path, qr_rc, apply_rc,
):
    expected = show_fixture(tmp_path)
    (tmp_path / "apply_rc").write_text(str(apply_rc))
    result = base._scripted(inst, tmp_path, features_path, qr_rc=qr_rc,
                            HISTORY_BACKFILL="no", FAMILIES="waf,ips", ALERTING="shadow")
    assert result.returncode == (1 if qr_rc else 0), result.stdout + result.stderr
    applied = calls(tmp_path, "app.cli.features apply")
    assert len(applied) == 1
    assert applied[0].endswith("apply --only-differing --families waf,ips "
                               "--alerting shadow --source installer")
    log = base._docker_log(tmp_path)
    history = log.index(calls(tmp_path, "history_backfill decide")[0])
    start = next(i for i, line in enumerate(log)
                 if "up -d --wait --wait-timeout 180 backend" in line)
    assert history < log.index(applied[0]) < start
    assert ("family switches were not applied" in result.stderr) == bool(apply_rc)
    assert expected in result.stdout
    assert "ANOMALY_EXPLANATION_ENABLED" not in result.stdout
    assert "python-fixture" in result.stdout
    assert base._state_exists(inst)


@pytest.mark.parametrize("key,value", [
    ("FAMILIES", "waf,wrong"), ("FAMILIES", "none waf"), ("FAMILIES", "defaults,ips"),
    ("FAMILIES", ",,,"), ("ALERTING", "maybe"),
])
def test_invalid_answers_write_nothing(inst, no_docker_path, key, value):
    result = base.install(inst, inst.answers(**{key: value}), path=no_docker_path)
    base.assert_refused(inst, result, key)


@pytest.mark.parametrize("words", ["none", "defaults", "waf ips", "waf,ips"])
def test_closed_family_words_accept_spaces_or_commas(inst, tmp_path, features_path, words):
    show_fixture(tmp_path)
    result = base._scripted(inst, tmp_path, features_path, FAMILIES=words, ALERTING="defaults")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls(tmp_path, "app.cli.features apply")) == 1
    env = (inst.root / ".env").read_text()
    assert "FAMILIES=" not in env and "ALERTING=" not in env


def test_wizard_defaults_saved_rerun_and_reconfigure(inst, tmp_path, features_path):
    show_fixture(tmp_path)
    first = base._interactive(inst, features_path, base._wizard_replies(inst, "no"))
    assert first.returncode == 0, first.stdout + first.stderr
    assert "15. Which behaviour families" in first.stdout
    assert first.stdout.index("Families on by code default: python-fixture") < first.stdout.index(
        "15. Which behaviour families")
    assert "16. Keep alerting defaults" in first.stdout
    assert "WAF and IPS send the most searches" in first.stdout
    assert "--families defaults --alerting defaults" in calls(tmp_path, "features apply")[0]
    saved = (inst.root / ".secrets/install.answers").read_text()
    assert "FAMILIES=defaults\n" in saved and "ALERTING=defaults\n" in saved
    (tmp_path / "docker.log").unlink()
    again = inst.run("install", "--skip-qradar-check", path=features_path)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "15. Which" not in again.stdout and "16. Keep" not in again.stdout
    assert calls(tmp_path, "features apply") == []
    replies = base._wizard_replies(inst)
    replies[-2:] = ["none", "shadow"]
    configured = base._interactive(inst, features_path, replies, "--reconfigure")
    assert configured.returncode == 0, configured.stdout + configured.stderr
    assert "15. Which" in configured.stdout and "16. Keep" in configured.stdout
    assert "--families none --alerting shadow" in calls(tmp_path, "features apply")[0]


def test_old_installed_site_does_not_ask_families_until_reconfigure(inst, tmp_path, features_path):
    show_fixture(tmp_path)
    assert base._scripted(inst, tmp_path, features_path).returncode == 0
    assert calls(tmp_path, "features apply") == []
    result = base._interactive(inst, features_path, base._wizard_replies(inst))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "15. Which" not in result.stdout
    assert calls(tmp_path, "features apply") == []


def test_show_failure_reports_unknown_without_guesses(inst, tmp_path, features_path):
    show_fixture(tmp_path)
    (tmp_path / "show_rc").write_text("1")
    result = base._scripted(inst, tmp_path, features_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Family switch state could not be read; see Administration → Features." in result.stdout
    assert "waf_collection_enabled=" not in result.stdout
    assert "Searching QRadar" not in result.stdout


def test_upgrade_only_prints_management_hint(upgrade):
    env = upgrade.root / ".env"
    env.write_text(env.read_text() + "WAF_COLLECTION_ENABLED=true\n")
    result = upgrade.run("upgrade", upgrades.TARGET_VERSION)
    assert result.returncode == 0, upgrades.output(result)
    assert ("Family switches are managed under Administration → Features; values from `.env` "
            "stay in force until you set one there.") in result.stdout
    assert "app.cli.features apply" not in upgrade.action_text()
    assert "15. Which" not in result.stdout and "16. Keep" not in result.stdout
    assert "WAF_COLLECTION_ENABLED=true" in env.read_text()


def test_default_hint_failure_still_applies_and_installs(inst, tmp_path, features_path):
    show_fixture(tmp_path)
    (tmp_path / "defaults_rc").write_text("1")
    result = base._scripted(inst, tmp_path, features_path, FAMILIES="none")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "code-default families could not be read" in result.stderr
    assert len(calls(tmp_path, "features apply")) == 1
    assert base._state_exists(inst)


def test_dry_run_does_not_apply_switches(inst, tmp_path, features_path):
    result = base._scripted(inst, tmp_path, features_path, "--dry-run",
                            FAMILIES="none", ALERTING="shadow")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(tmp_path, "features apply") == []
    assert not (inst.root / ".env").exists()


def test_wizard_refuses_bad_family_and_alerting_words(inst, tmp_path, features_path):
    show_fixture(tmp_path)
    replies = base._wizard_replies(inst, "no")
    replies[-2:] = ["waf,nope", "none ips", "none", "bad", "shadow"]
    result = base._interactive(inst, features_path, replies)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unknown family: nope" in result.stdout
    assert "use defaults or none alone" in result.stdout
    assert "must be defaults, shadow or alerting" in result.stdout
    assert "--families none --alerting shadow" in calls(tmp_path, "features apply")[0]
