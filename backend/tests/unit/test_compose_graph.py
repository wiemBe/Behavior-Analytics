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


def _stage_compose_project(directory: Path) -> None:
    """Copy the compose files and a dummy ``.env`` the anchor's ``env_file`` can open.

    Values are interpolation stand-ins. Nothing here is a credential. The copy
    is the Compose project, so the test does not read the checkout and does
    not need ``--no-env-resolution`` (Compose 5.6 skips the anchor ``env_file``
    with that flag; the CI runner's Compose still opens it).
    """
    for name in (
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "deploy/docker-compose.override.example.yml",
    ):
        shutil.copyfile(REPO / name, directory / Path(name).name)
    (directory / ".env").write_text(
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


def _compose_config(*files: str, project: Path, profile: str | None = None) -> dict:
    docker = shutil.which("docker")
    assert docker is not None
    env = os.environ.copy()
    for key in (
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_DIRECTORY",
        "COMPOSE_ENV_FILES",
    ):
        env.pop(key, None)
    env.update(
        {
            "POSTGRES_USER": "compose-config",
            "POSTGRES_PASSWORD": "compose-config",
            "POSTGRES_DB": "compose-config",
            "QRADAR_CONSOLE_FQDN": "console.example.invalid",
            "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8000/api/v1",
            "QRADAR_DASH_VERSION": "0.0.0-test",
        }
    )
    # Flags: --project-directory <staged copy>, -f for each copied file,
    # --profile mcp only for the override render, then `config`.
    # No --env-file and no --no-env-resolution. Compose reads <copy>/.env.
    command = [docker, "compose", "--project-directory", str(project)]
    if profile is not None:
        command.extend(["--profile", profile])
    for name in files:
        command.extend(["-f", str(project / Path(name).name)])
    command.append("config")
    result = subprocess.run(  # noqa: S603
        command,
        cwd=project,
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
    _stage_compose_project(tmp_path)
    base = _compose_config("docker-compose.yml", project=tmp_path)
    with_prod = _compose_config(
        "docker-compose.yml", "docker-compose.prod.yml", project=tmp_path
    )
    # qradar-mcp is behind the mcp profile. Without it, config omits the service.
    with_override = _compose_config(
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "deploy/docker-compose.override.example.yml",
        project=tmp_path,
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


# ------------------------------------------------------------ qradar-mcp service (SEC-4)
MCP_SECRETS_MOUNT = "/run/secrets"
# `localhost/` so an unbuilt upstream is never resolved from Docker Hub; an image, not a
# `service:` reference, because Compose 2.x refuses a service of an inactive profile there.
UPSTREAM_IMAGE = "localhost/qradar-mcp-upstream:b8f6a4a3"


def _mcp_volume_entries(service: dict) -> list[dict]:
    """The service's volumes as long-form mappings, whichever form the file used."""
    entries: list[dict] = []
    for item in service.get("volumes", []):
        if isinstance(item, str):
            parts = item.split(":")
            entries.append(
                {
                    "source": parts[0],
                    "target": parts[1],
                    "read_only": len(parts) > 2 and "ro" in parts[2].split(","),
                    "bind": parts[0].startswith((".", "/", "~")),
                }
            )
        else:
            entries.append(
                {
                    "source": item.get("source"),
                    "target": item["target"],
                    "read_only": bool(item.get("read_only")),
                    "bind": item.get("type") == "bind",
                }
            )
    return entries


def _assert_mcp_service(service: dict) -> None:
    entries = _mcp_volume_entries(service)
    # A checkout file is user_home_t: an SELinux-enforcing host cannot read it from a
    # container (OPS-R13). Named volumes only.
    assert [e for e in entries if e["bind"]] == []
    by_target = {e["target"]: e for e in entries}
    assert set(by_target) == {MCP_SECRETS_MOUNT, "/opt/app-root/logs"}
    # The same volume key the backend mounts; the overlay renames the volume behind it.
    assert by_target[MCP_SECRETS_MOUNT]["source"] == "qradar_secrets"
    assert by_target[MCP_SECRETS_MOUNT]["read_only"] is True
    environment = service["environment"]
    if isinstance(environment, list):
        environment = dict(item.split("=", 1) for item in environment)
    assert environment["REQUESTS_CA_BUNDLE"] == f"{MCP_SECRETS_MOUNT}/qradar_ca.pem"
    # FUNCTIONAL_TEST_ENV switches certificate verification off in the pinned server.
    assert "FUNCTIONAL_TEST_ENV" not in environment
    assert "ports" not in service
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]


def test_the_mcp_service_has_no_checkout_mount_and_verifies_tls_in_the_yaml() -> None:
    base = load_compose(REPO / "docker-compose.yml")
    overlay = load_compose(REPO / "docker-compose.prod.yml")
    merged = _unwrap(compose_merge(base, overlay))
    assert isinstance(merged, dict)
    for document in (_unwrap(base), merged):
        assert isinstance(document, dict)
        _assert_mcp_service(document["services"]["qradar-mcp"])
    mcp = base["services"]["qradar-mcp"]
    assert mcp["build"]["context"] == "./deploy/mcp"
    assert mcp["build"]["additional_contexts"] == {"upstream": f"docker-image://{UPSTREAM_IMAGE}"}
    # The upstream build is a build-only service: no profile that `up` ever enables.
    upstream = base["services"]["qradar-mcp-upstream"]
    assert upstream["build"]["context"] == "./qradar-mcp"
    assert "dockerfile" not in upstream or upstream["dockerfile"] == "Dockerfile"
    assert upstream["profiles"] == ["mcp-build"]
    assert upstream["image"] == UPSTREAM_IMAGE


def test_docker_compose_config_shows_the_mcp_service_without_a_bind_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = _docker_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    _stage_compose_project(tmp_path)
    monkeypatch.delenv("SECRETS_VOLUME", raising=False)
    base = _compose_config("docker-compose.yml", project=tmp_path, profile="mcp")
    monkeypatch.setenv("SECRETS_VOLUME", "site-secrets")
    with_prod = _compose_config(
        "docker-compose.yml", "docker-compose.prod.yml", project=tmp_path, profile="mcp"
    )
    _assert_mcp_service(base["services"]["qradar-mcp"])
    _assert_mcp_service(with_prod["services"]["qradar-mcp"])
    assert base["volumes"]["qradar_secrets"]["name"] == "qradar-observability-secrets"
    # The overlay renames the volume; the service follows because it mounts the same key.
    assert with_prod["volumes"]["qradar_secrets"]["name"] == "site-secrets"
    for document in (base, with_prod):
        build = document["services"]["qradar-mcp"]["build"]
        assert build["additional_contexts"] == {"upstream": f"docker-image://{UPSTREAM_IMAGE}"}


def test_the_mcp_dockerfile_adds_only_the_toggle_file_to_the_upstream_build() -> None:
    lines = [
        line.strip()
        for line in (REPO / "deploy/mcp/Dockerfile").read_text(encoding="utf-8").splitlines()
    ]
    instructions = [
        line.split()[0].upper() for line in lines if line and not line.startswith("#")
    ]
    # LABEL continuation lines start with a key, not an instruction; keep the first word.
    assert "RUN" not in instructions
    assert "USER" not in instructions
    assert [i for i in instructions if i in {"FROM", "COPY", "ADD", "ENV", "CMD"}] == [
        "FROM",
        "COPY",
    ]
    assert "FROM upstream" in lines
    copies = [line for line in lines if line.startswith("COPY ")]
    assert len(copies) == 1
    assert copies[0].endswith(
        "feature_toggles.json /opt/app-root/qradar-mcp/feature_toggles.json"
    )
    assert "--chown=1001:1001" in copies[0]
    assert not (REPO / "deploy/mcp/config.json").exists()


def test_the_baked_toggle_file_keeps_every_write_verb_off() -> None:
    import json

    toggles = json.loads((REPO / "deploy/mcp/feature_toggles.json").read_text(encoding="utf-8"))
    assert toggles["verb_toggles"] == {"GET": True, "POST": False, "DELETE": False}


BUILD_CONTEXTS_EXPRESSION = "${{ format(matrix.build-contexts, github.workspace) }}"
# Contexts that exist only once a runner has the job: empty while a matrix expands (REL-1).
RUNNER_ONLY_CONTEXTS = re.compile(r"\b(github\.workspace|runner\.|env\.|steps\.|job\.)")


def _format(template: str, *args: str) -> str:
    """The part of GitHub's ``format()`` these workflows use: ``{0}``, ``{{`` and ``}}``."""
    out = re.sub(r"\{(\d+)\}", lambda m: args[int(m.group(1))], template)
    return out.replace("{{", "{").replace("}}", "}")


def _workflow(name: str) -> dict:
    return yaml.safe_load((REPO / ".github/workflows" / name).read_text(encoding="utf-8"))


def _mcp_entry(workflow: dict, job: str) -> dict:
    include = workflow["jobs"][job]["strategy"]["matrix"]["include"]
    return next(e for e in include if e["name"] == "qradar-mcp")


def test_the_mcp_image_check_mirrors_the_release_matrix_entry_and_expression() -> None:
    release = _workflow("release.yml")
    check = _workflow("mcp-image.yml")
    assert _mcp_entry(check, "build") == {
        k: v for k, v in _mcp_entry(release, "images").items() if k != "base-label"
    }
    release_final = next(
        s for s in release["jobs"]["images"]["steps"] if s.get("name") == "Build and push"
    )
    check_final = next(
        s for s in check["jobs"]["build"]["steps"] if s.get("name") == "Build the derived image"
    )
    for step in (release_final, check_final):
        assert step["with"]["build-contexts"] == BUILD_CONTEXTS_EXPRESSION
        assert step["with"]["context"] == "${{ matrix.context }}"


def test_no_workflow_matrix_value_uses_a_context_that_only_exists_on_the_runner() -> None:
    def walk(value: object):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, str):
            yield value

    checked = 0
    for path in sorted((REPO / ".github/workflows").glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in document.get("jobs", {}).items():
            matrix = job.get("strategy", {}).get("matrix") if isinstance(job, dict) else None
            if not isinstance(matrix, dict):
                continue
            checked += 1
            for text in walk(matrix):
                assert not RUNNER_ONLY_CONTEXTS.search(text), f"{path.name} {job_name}: {text}"
    assert checked >= 3  # release images, mcp-image build, ci integration shards


def test_the_mcp_build_context_template_renders_against_the_workspace() -> None:
    include = _workflow("release.yml")["jobs"]["images"]["strategy"]["matrix"]["include"]
    rendered = {e["name"]: _format(e["build-contexts"], "/w/repo") for e in include}
    assert rendered["qradar-mcp"] == "upstream=oci-layout:///w/repo/.qradar-mcp-upstream"
    assert rendered["backend"] == "repo=."
    assert {rendered[n] for n in ("frontend", "postgres", "proxy")} == {""}
    for entry in include:  # only the qradar-mcp template holds a placeholder
        assert ("{" in entry["build-contexts"]) == (entry["name"] == "qradar-mcp")


def test_the_release_workflow_builds_the_upstream_first_and_passes_it_as_a_context() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    )
    images = workflow["jobs"]["images"]
    entry = next(e for e in images["strategy"]["matrix"]["include"] if e["name"] == "qradar-mcp")
    assert entry["context"] == "deploy/mcp"
    assert entry["build-contexts"].startswith("upstream=oci-layout://")
    steps = images["steps"]
    names = [step.get("name", "") for step in steps]
    upstream = steps[names.index("Build the upstream qradar-mcp")]
    final = next(s for s in steps if s.get("name") == "Build and push")
    assert names.index("Build the upstream qradar-mcp") < names.index("Build and push")
    assert upstream["with"]["context"] == "qradar-mcp"
    assert upstream["with"]["push"] is False
    assert upstream["with"]["outputs"].startswith("type=oci,dest=")
    assert upstream["if"] == "matrix.name == 'qradar-mcp'"
    assert final["with"]["build-contexts"] == BUILD_CONTEXTS_EXPRESSION
    assert final["with"]["context"] == "${{ matrix.context }}"


def test_no_file_points_at_the_inert_mcp_config() -> None:
    needles = ("deploy/mcp/config.json", "qradar-mcp/config.json")
    offenders = []
    for name in (
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "qradar-dash",
        "docs/INSTALL.md",
        "docs/OPERATIONS.md",
        "docs/mcp-capability-matrix.md",
        ".github/workflows/release.yml",
    ):
        text = (REPO / name).read_text(encoding="utf-8")
        offenders += [name for needle in needles if needle in text]
    assert offenders == []
