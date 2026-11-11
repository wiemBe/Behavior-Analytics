"""TimescaleDB telemetry is off in both databases this repository runs (FIX-47).

The telemetry reporter is a scheduler job that contacts an external endpoint,
and its extension-version check takes catalog locks that the migration
fixtures' schema drop deadlocks with. Both places that start a database set the
GUC at server level, so it covers every database -- including one created
later -- and an existing volume the init scripts never touch again.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
SETTING = "timescaledb.telemetry_level=off"


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    """Compose's ``!override``: this file only reads places that never use it."""
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_override)


def _load_compose(name: str) -> dict:
    loaded = yaml.load(
        (REPO / name).read_text(encoding="utf-8"),
        Loader=_ComposeLoader,  # noqa: S506 - a SafeLoader with !override only
    )
    assert isinstance(loaded, dict)
    return loaded


def _server_args(command: object) -> list[str]:
    """One compose command as argv, whichever of the two forms the file uses."""
    if isinstance(command, str):
        return shlex.split(command)
    assert isinstance(command, list)
    return [str(part) for part in command]


def test_the_test_database_starts_with_telemetry_off() -> None:
    service = _load_compose("docker-compose.test.yml")["services"]["test-db"]
    # The image's entrypoint is docker-entrypoint.sh; replacing it would skip
    # initdb and the init scripts. `postgres` first keeps its argument handling.
    assert "entrypoint" not in service
    assert _server_args(service["command"]) == ["postgres", "-c", SETTING]


def test_the_production_image_starts_postgres_with_telemetry_off() -> None:
    lines = (
        (REPO / "deploy" / "postgres" / "Dockerfile").read_text(encoding="utf-8").splitlines()
    )
    # A second CMD would silently replace this one (Dockerfile: the last wins).
    cmds = [line for line in lines if line.startswith("CMD ")]
    assert len(cmds) == 1
    assert json.loads(cmds[0][len("CMD ") :]) == ["postgres", "-c", SETTING]
    # The upstream entrypoint still initialises an empty data directory.
    assert not any(line.startswith("ENTRYPOINT") for line in lines)


def test_the_production_compose_does_not_override_the_postgres_command() -> None:
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        # A `command:` at this level replaces the image's, dropping the -c flag.
        assert "command" not in _load_compose(name)["services"]["postgres"], name
