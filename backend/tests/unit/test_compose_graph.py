"""Compose migrate gate and the macvlan override the deploy script creates.

The production overlay must not drop ``depends_on``. Compose deep-merges that
mapping; ``!override`` replaces it. Docker is consulted only when the daemon
answers. The YAML merge runs either way.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
DB_SERVICES = ("backend", "celery-worker", "celery-beat")


class _Override:
    def __init__(self, value: object) -> None:
        self.value = value


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node) -> _Override:
    if isinstance(node, yaml.SequenceNode):
        value: object = loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    else:
        value = loader.construct_scalar(node)
    return _Override(value)


_ComposeLoader.add_constructor("!override", _construct_override)


def _expand(node: object) -> object:
    """Apply YAML ``<<`` merge keys. Explicit keys replace anchored ones."""
    if isinstance(node, _Override):
        return _Override(_expand(node.value))
    if isinstance(node, list):
        return [_expand(item) for item in node]
    if not isinstance(node, dict):
        return node
    merged: dict[object, object] = {}
    sources = node.get("<<")
    if isinstance(sources, dict):
        sources = [sources]
    if isinstance(sources, list):
        for source in sources:
            expanded = _expand(source)
            if isinstance(expanded, dict):
                merged.update(expanded)
    for key, value in node.items():
        if key == "<<":
            continue
        merged[key] = _expand(value)
    return merged


def _unwrap(node: object) -> object:
    if isinstance(node, _Override):
        return _unwrap(node.value)
    if isinstance(node, list):
        return [_unwrap(item) for item in node]
    if isinstance(node, dict):
        return {key: _unwrap(value) for key, value in node.items()}
    return node


def load_compose(path: Path) -> dict:
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506
    expanded = _expand(loaded)
    if not isinstance(expanded, dict):
        raise AssertionError(f"{path} did not load as a mapping")
    return expanded


def compose_merge(base: dict, overlay: dict) -> dict:
    """Merge two compose documents the way Compose merges ``depends_on``.

    Mappings merge by key. An ``!override`` value replaces the base value.
    Sequences append. A missing overlay key leaves the base value in place,
    which is how an overlay that only sets ``image`` keeps ``depends_on``.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, _Override):
            merged[key] = value.value
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = compose_merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            merged[key] = [*current, *value]
        else:
            merged[key] = value
    return merged


def _migrate_condition(document: dict, service: str) -> str:
    deps = document["services"][service]["depends_on"]
    assert isinstance(deps, dict), service
    migrate = deps["migrate"]
    assert isinstance(migrate, dict), service
    condition = migrate["condition"]
    assert isinstance(condition, str)
    return condition


def test_db_services_wait_for_migrate_in_the_base_file() -> None:
    document = load_compose(REPO / "docker-compose.yml")
    for service in DB_SERVICES:
        deps = document["services"][service]["depends_on"]
        assert deps["postgres"]["condition"] == "service_healthy"
        assert deps["redis"]["condition"] == "service_healthy"
        assert _migrate_condition(document, service) == "service_completed_successfully"
    migrate = document["services"]["migrate"]["depends_on"]
    assert "migrate" not in migrate
    assert migrate["postgres"]["condition"] == "service_healthy"
    assert document["services"]["migrate"]["restart"] == "no"


def test_prod_overlay_does_not_drop_the_migrate_gate() -> None:
    base = load_compose(REPO / "docker-compose.yml")
    overlay = load_compose(REPO / "docker-compose.prod.yml")
    merged = _unwrap(compose_merge(base, overlay))
    assert isinstance(merged, dict)
    for service in DB_SERVICES:
        assert _migrate_condition(merged, service) == "service_completed_successfully"


def test_override_references_the_network_create_vmnet_creates() -> None:
    script = (REPO / "deploy/create-vmnet.sh").read_text(encoding="utf-8")
    names = re.findall(r"^NETWORK=(\S+)", script, re.MULTILINE)
    assert len(names) == 1
    network_name = names[0]
    assert "--ip-range" in script
    example = REPO / "deploy/docker-compose.override.example.yml"
    text = example.read_text(encoding="utf-8")
    # Comments may name the forbidden options. Operative lines must not set them.
    operative = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert ":z" not in operative
    assert "label=disable" not in operative
    assert "label:disable" not in operative
    document = load_compose(example)
    network = document["networks"]["qradar_vm"]
    assert network["external"] is True
    assert network["name"] == network_name
    assert "driver" not in network
    assert "driver_opts" not in network
    assert "ipam" not in network
    # backend and celery-worker open QRadar connections. qradar-mcp is the
    # optional console client. celery-beat only publishes to the broker and
    # migrate only opens Postgres, so neither is attached.
    assert set(document["services"]) == {"backend", "celery-worker", "qradar-mcp"}
    for service in document["services"].values():
        assert service["networks"] == ["qradar_vm"]


def _docker_skip_reason() -> str | None:
    docker = shutil.which("docker")
    if docker is None:
        return "docker is not on PATH"
    try:
        result = subprocess.run(  # noqa: S603
            [docker, "info"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker is not reachable ({exc})"
    if result.returncode != 0:
        return "docker daemon is not reachable"
    return None


def _dummy_env_file(directory: Path) -> Path:
    """Interpolation values only. Not a project ``.env`` and not a secret."""
    path = directory / "compose-config.env"
    path.write_text(
        "\n".join(
            (
                "POSTGRES_USER=compose-config",
                "POSTGRES_PASSWORD=compose-config",
                "POSTGRES_DB=compose-config",
                "QRADAR_CONSOLE_FQDN=console.example.invalid",
                "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api/v1",
                "QRADAR_DASH_VERSION=0.0.0-test",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def _compose_config(*files: str, env_file: Path, profile: str | None = None) -> dict:
    docker = shutil.which("docker")
    assert docker is not None
    env = os.environ.copy()
    env.pop("COMPOSE_FILE", None)
    env.pop("COMPOSE_PROFILES", None)
    env["QRADAR_DASH_VERSION"] = "0.0.0-test"
    # ``env_file: [.env]`` on the backend anchor makes Compose open the project
    # ``.env``. This checkout does not have one. ``--env-file`` supplies
    # interpolation; ``--no-env-resolution`` skips that service env file.
    command = [
        docker,
        "compose",
        "--project-directory",
        str(REPO),
        "--env-file",
        str(env_file),
    ]
    if profile is not None:
        command.extend(["--profile", profile])
    for name in files:
        command.extend(["-f", str(REPO / name)])
    command.extend(["config", "--no-env-resolution"])
    result = subprocess.run(  # noqa: S603
        command,
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-30:])
        raise AssertionError(f"docker compose config rc={result.returncode}\n{tail}")
    loaded = yaml.safe_load(result.stdout)
    assert isinstance(loaded, dict)
    return loaded


def _service_networks(service: dict) -> set[str]:
    networks = service.get("networks") or []
    if isinstance(networks, dict):
        return set(networks)
    return set(networks)


def test_docker_compose_config_keeps_migrate_and_the_external_network(tmp_path: Path) -> None:
    reason = _docker_skip_reason()
    if reason is not None:
        pytest.skip(f"{reason}; the YAML merge test still covers depends_on")
    env_file = _dummy_env_file(tmp_path)
    base = _compose_config("docker-compose.yml", env_file=env_file)
    with_prod = _compose_config(
        "docker-compose.yml", "docker-compose.prod.yml", env_file=env_file
    )
    # qradar-mcp is behind the mcp profile. Without it, config omits the service.
    with_override = _compose_config(
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "deploy/docker-compose.override.example.yml",
        env_file=env_file,
        profile="mcp",
    )
    for document in (base, with_prod, with_override):
        for service in DB_SERVICES:
            assert _migrate_condition(document, service) == "service_completed_successfully"
    assert "qradar_vm" not in _service_networks(base["services"]["backend"])
    script = (REPO / "deploy/create-vmnet.sh").read_text(encoding="utf-8")
    network_name = re.findall(r"^NETWORK=(\S+)", script, re.MULTILINE)[0]
    network = with_override["networks"]["qradar_vm"]
    assert network["external"] is True
    assert network["name"] == network_name
    expected = {
        "backend": {"internal", "edge", "qradar_vm"},
        "celery-worker": {"internal", "qradar_vm"},
        "qradar-mcp": {"internal", "qradar_vm"},
    }
    for name, required in expected.items():
        assert required <= _service_networks(with_override["services"][name])
    for name in ("celery-beat", "migrate"):
        assert "qradar_vm" not in _service_networks(with_override["services"][name])
