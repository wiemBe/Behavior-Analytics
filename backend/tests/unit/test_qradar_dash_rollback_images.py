"""OPS-25 rollback preflight coverage for services absent from the saved image map."""

from __future__ import annotations

from tests.unit import test_qradar_dash_upgrade as base
from tests.unit.test_qradar_dash_upgrade import (
    NEW_IMAGES,
    OLD_IMAGES,
    OLD_VERSION,
    TARGET_VERSION,
    UpgradeFixture,
    output,
)

upgrade = base.upgrade

MCP_REF = f"registry.example/qradar/qradar-mcp:{OLD_VERSION}"
PROXY_REF = f"registry.example/qradar/proxy:{OLD_VERSION}"


def _profiles(fx: UpgradeFixture, value: str) -> None:
    env = fx.root / ".env"
    env.write_text(env.read_text().replace("COMPOSE_PROFILES=proxy", f"COMPOSE_PROFILES={value}"))


def _rollback_ready(
    fx: UpgradeFixture, *, previous: dict[str, str] | None = None
) -> None:
    backup = fx.make_backup()
    fx._write_env(TARGET_VERSION)
    fx.running_version.write_text(f"{TARGET_VERSION}\n")
    fx.write_state(
        installed=TARGET_VERSION,
        images=NEW_IMAGES,
        previous=previous or OLD_IMAGES,
        outcome="success",
        backup=backup,
    )


def test_mcp_without_recorded_image_uses_local_old_version_image(
    upgrade: UpgradeFixture,
) -> None:
    _rollback_ready(upgrade)
    _profiles(upgrade, "proxy,mcp")

    result = upgrade.run("rollback", "--non-interactive", "--yes")

    assert result.returncode == 0, output(result)
    assert f"qradar-mcp: no recorded image; using {MCP_REF} (local)" in result.stdout
    assert " pull " not in upgrade.action_text()


def test_mcp_old_version_image_is_pulled_before_database_swap(
    upgrade: UpgradeFixture,
) -> None:
    _rollback_ready(upgrade)
    _profiles(upgrade, "proxy,mcp")

    result = upgrade.run(
        "rollback", "--non-interactive", "--yes", STUB_MISSING_IMAGE=MCP_REF
    )

    assert result.returncode == 0, output(result)
    assert f"qradar-mcp: no recorded image; using {MCP_REF} (pulled)" in result.stdout
    actions = upgrade.action_text()
    assert actions.index(f"docker pull {MCP_REF}") < actions.index(
        "ALTER DATABASE qradar_obs RENAME TO qradar_obs_broken_"
    )


def test_unpullable_mcp_image_refuses_without_changing_the_site(
    upgrade: UpgradeFixture,
) -> None:
    _rollback_ready(upgrade)
    _profiles(upgrade, "proxy,mcp")
    env_before = (upgrade.root / ".env").read_bytes()
    state_before = (upgrade.state_dir / "state.json").read_bytes()

    result = upgrade.run(
        "rollback",
        "--non-interactive",
        "--yes",
        STUB_MISSING_IMAGE=MCP_REF,
        STUB_PULL_RC="19",
    )

    assert result.returncode == 1, output(result)
    assert (
        "rollback refused: qradar-mcp has no recorded image from the upgrade and "
        f"{MCP_REF} is neither local nor pullable. Next: load the {OLD_VERSION} offline "
        "bundle (docker load -i …/images.tar), or remove mcp from COMPOSE_PROFILES and run "
        "rollback again. Nothing was changed."
    ) in result.stderr
    actions = upgrade.action_text()
    assert "ALTER DATABASE" not in actions
    assert " stop " not in actions
    assert " up " not in actions
    assert (upgrade.root / ".env").read_bytes() == env_before
    assert (upgrade.state_dir / "state.json").read_bytes() == state_before
    assert upgrade.running.read_text() == "1\n"


def test_proxy_without_recorded_image_gets_the_same_preflight(
    upgrade: UpgradeFixture,
) -> None:
    previous = {service: image for service, image in OLD_IMAGES.items() if service != "proxy"}
    _rollback_ready(upgrade, previous=previous)

    result = upgrade.run(
        "rollback", "--non-interactive", "--yes", STUB_MISSING_IMAGE=PROXY_REF
    )

    assert result.returncode == 0, output(result)
    assert f"proxy: no recorded image; using {PROXY_REF} (pulled)" in result.stdout
    actions = upgrade.action_text()
    assert actions.index(f"docker pull {PROXY_REF}") < actions.index("ALTER DATABASE")
