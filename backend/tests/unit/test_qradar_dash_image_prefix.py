"""The image prefix moves to behavior-analytics inside `upgrade` (OPS-17); Docker is stubbed."""

from __future__ import annotations

import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit import test_qradar_dash_installer as base_install
from tests.unit import test_qradar_dash_upgrade as base
from tests.unit.test_qradar_dash_upgrade import (
    OLD_IMAGES,
    OLD_VERSION,
    SECRET_VALUES,
    TARGET_VERSION,
    UpgradeFixture,
    output,
)

OLD = "ghcr.io/wiembe/qradar-dashboard"
NEW = "ghcr.io/wiembe/behavior-analytics"
MOVED = f"Image prefix moved: {OLD} -> {NEW} (the repository was renamed)."

# The shared stub, with two knobs: STUB_MISSING (space separated globs of image references that
# `docker image inspect` does not find) and a log of the prefix `.env` has when a pull runs.
_INSPECT = '  subject=${!#}\n'
_PULL = '  *" pull "*) exit "${STUB_PULL_RC:-0}" ;;\n'
STUB = base.DOCKER_STUB.replace(
    _INSPECT,
    _INSPECT
    + '  for glob in ${STUB_MISSING:-}; do case "$subject" in $glob) exit 1 ;; esac; done\n',
).replace(
    _PULL,
    '  *" pull "*)\n'
    "    printf 'PULL-PREFIX=%s\\n' \"$(sed -n 's/^QRADAR_DASH_IMAGE_PREFIX=//p' .env)\""
    ' >> "$STUB_ACTIONS"\n'
    '    exit "${STUB_PULL_RC:-0}" ;;\n',
)
assert STUB != base.DOCKER_STUB and "PULL-PREFIX" in STUB and "STUB_MISSING" in STUB

inst = base_install.inst
build_path = base_install.build_path


class Moved(UpgradeFixture):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self._stub("docker", STUB)
        self.set_env(prefix=OLD, profiles="proxy")
        secrets = self.root / ".secrets"
        secrets.mkdir(mode=0o700)
        self.answers = secrets / "install.answers"
        self.answers.write_text(f"PUBLIC_HOSTNAME=soc.example.test\nIMAGE_PREFIX={OLD}\n")
        self.answers.chmod(0o600)

    def set_env(self, *, prefix: str | None, profiles: str) -> None:
        env = self.root / ".env"
        lines = [
            line
            for line in env.read_text().splitlines()
            if not line.startswith(("QRADAR_DASH_IMAGE_PREFIX=", "COMPOSE_PROFILES="))
        ]
        lines.append(f"COMPOSE_PROFILES={profiles}")
        if prefix is not None:
            lines.append(f"QRADAR_DASH_IMAGE_PREFIX={prefix}")
        env.write_text("\n".join(lines) + "\n")
        env.chmod(0o600)

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    def env_prefixes(self) -> list[str]:
        return [
            line.split("=", 1)[1]
            for line in self.env_path.read_text().splitlines()
            if line.startswith("QRADAR_DASH_IMAGE_PREFIX=")
        ]

    def tags(self) -> list[str]:
        return [x for x in self.action_text().splitlines() if x.startswith("docker tag ")]

    def unchanged(self, env: bytes, answers: bytes) -> bool:
        return self.env_path.read_bytes() == env and self.answers.read_bytes() == answers


@pytest.fixture
def moved(tmp_path: Path) -> Iterator[Moved]:
    fixture = Moved(tmp_path)
    yield fixture
    for secret in SECRET_VALUES:
        assert secret not in fixture.action_text()


def run(fx: Moved, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return fx.run("upgrade", TARGET_VERSION, *args, **env)


def installed_tags(*services: str) -> list[str]:
    return [
        f"docker tag {OLD}/{s}:{OLD_VERSION} {NEW}/{s}:{OLD_VERSION}" for s in services
    ]


def test_old_prefix_is_tagged_then_rewritten_then_pulled_under_the_new_one(
    moved: Moved,
) -> None:
    before = moved.env_path.read_text().splitlines()

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert result.stdout.count(MOVED) == 1
    assert moved.tags()[:4] == installed_tags("postgres", "backend", "frontend", "proxy")
    assert not any("qradar-mcp" in tag for tag in moved.tags()[:4])
    assert moved.env_prefixes() == [NEW]
    assert moved.answers.read_text() == f"PUBLIC_HOSTNAME=soc.example.test\nIMAGE_PREFIX={NEW}\n"
    assert stat.S_IMODE(moved.env_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(moved.answers.stat().st_mode) == 0o600
    changed = [
        (a, b)
        for a, b in zip(before, moved.env_path.read_text().splitlines(), strict=True)
        if a != b
    ]
    assert changed == [
        ("QRADAR_DASH_VERSION=0.1.0", "QRADAR_DASH_VERSION=0.2.0"),
        (f"QRADAR_DASH_IMAGE_PREFIX={OLD}", f"QRADAR_DASH_IMAGE_PREFIX={NEW}"),
    ]
    actions = moved.action_text()
    assert f"PULL-PREFIX={NEW}\n" in actions
    assert f"docker run --rm --entrypoint cat {NEW}/backend:{TARGET_VERSION}" in actions
    assert OLD not in actions.split(" pull ", 1)[1]
    # tags, then the .env rewrite, then the pull
    order = [
        actions.index(moved.tags()[0]),
        actions.index("env-edit"),
        actions.index("PULL-PREFIX"),
    ]
    assert order == sorted(order)


def test_absent_key_is_written_and_the_installed_images_are_tagged(moved: Moved) -> None:
    moved.set_env(prefix=None, profiles="proxy")

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert MOVED in result.stdout
    assert moved.tags()[:4] == installed_tags("postgres", "backend", "frontend", "proxy")
    assert moved.env_prefixes() == [NEW]
    assert stat.S_IMODE(moved.env_path.stat().st_mode) == 0o600
    assert "PULL-PREFIX=" + NEW in moved.action_text()


def test_custom_prefix_is_left_alone_with_a_note(moved: Moved) -> None:
    moved.set_env(prefix="registry.example/qradar", profiles="proxy")
    moved.answers.write_text("IMAGE_PREFIX=registry.example/qradar\n")
    answers = moved.answers.read_bytes()

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert "registry.example/qradar is not the old default" in result.stdout
    assert "must carry the target's images" in result.stdout
    assert MOVED not in result.stdout
    assert not any(OLD in t or NEW in t for t in moved.tags())
    assert moved.env_prefixes() == ["registry.example/qradar"]
    assert moved.answers.read_bytes() == answers


def test_prefix_that_is_already_new_is_silent_and_untouched(moved: Moved) -> None:
    moved.set_env(prefix=NEW, profiles="proxy")

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert "Image prefix" not in result.stdout
    assert not any(OLD in t for t in moved.tags())
    assert moved.env_prefixes() == [NEW]


def test_checkout_refusal_leaves_env_and_answers_byte_identical(moved: Moved) -> None:
    (moved.root / "VERSION").write_text(f"{OLD_VERSION}\n")
    env, answers = moved.env_path.read_bytes(), moved.answers.read_bytes()

    result = run(moved)

    assert result.returncode == 1
    assert "this checkout is version" in result.stderr
    assert moved.unchanged(env, answers)
    assert moved.tags() == []


@pytest.mark.parametrize(
    "setup",
    ["newer", "failed_outcome", "bad_state"],
)
def test_every_registry_free_refusal_runs_before_the_move(moved: Moved, setup: str) -> None:
    if setup == "newer":
        moved.write_state(installed="0.3.0")
    elif setup == "failed_outcome":
        moved.write_state(outcome="failed")
    else:
        (moved.state_dir / "state.json").write_text("not json\n")
    env, answers = moved.env_path.read_bytes(), moved.answers.read_bytes()

    result = run(moved)

    assert result.returncode == 1, output(result)
    assert moved.unchanged(env, answers)
    assert moved.tags() == []


@pytest.mark.parametrize("service", ["postgres", "backend", "frontend", "proxy"])
def test_an_installed_image_missing_locally_refuses_and_names_it(
    moved: Moved, service: str
) -> None:
    env, answers = moved.env_path.read_bytes(), moved.answers.read_bytes()

    result = run(moved, STUB_MISSING=f"{OLD}/{service}:{OLD_VERSION}")

    assert result.returncode == 1, output(result)
    assert f"{OLD}/{service}:{OLD_VERSION}" in result.stderr
    assert "nothing was changed" in result.stderr
    assert moved.unchanged(env, answers)
    assert moved.tags() == []
    assert " pull " not in moved.action_text()
    assert " stop " not in moved.action_text()


def test_move_then_refused_pull_keeps_the_new_prefix(moved: Moved) -> None:
    result = run(moved, STUB_PULL_RC="19")

    assert result.returncode == 1
    assert "target images are unavailable" in result.stderr
    assert MOVED in result.stdout
    assert moved.env_prefixes() == [NEW]
    assert f"IMAGE_PREFIX={NEW}" in moved.answers.read_text()
    assert moved.tags()[:4] == installed_tags("postgres", "backend", "frontend", "proxy")
    assert " stop " not in moved.action_text()


def test_move_then_auto_rollback_tags_the_old_ids_under_the_new_prefix(moved: Moved) -> None:
    result = run(moved, STUB_MIGRATE_RC="17")

    assert result.returncode != 0
    assert "was rolled back" in result.stderr
    assert moved.env_prefixes() == [NEW]
    tags = moved.tags()
    for service in ("postgres", "backend", "frontend", "proxy"):
        assert f"docker tag {OLD_IMAGES[service]} {NEW}/{service}:{OLD_VERSION}" in tags
    assert not any(t.endswith(f"{OLD}/backend:{OLD_VERSION}") and OLD_IMAGES["backend"] in t
                   for t in tags)
    assert moved.running_version.read_text().strip() == OLD_VERSION


def test_offline_with_the_target_loaded_under_the_new_prefix_succeeds(moved: Moved) -> None:
    result = run(moved, "--offline", STUB_MISSING=f"{OLD}/*:{TARGET_VERSION}")

    assert result.returncode == 0, output(result)
    assert MOVED in result.stdout
    actions = moved.action_text()
    assert f"docker image inspect {NEW}/backend:{TARGET_VERSION}" in actions
    assert " pull " not in actions


def test_offline_with_the_target_under_the_old_prefix_only_is_refused_before_stop(
    moved: Moved,
) -> None:
    result = run(moved, "--offline", STUB_MISSING=f"{NEW}/*:{TARGET_VERSION}")

    assert result.returncode == 1
    assert "target images are unavailable" in result.stderr
    assert " stop " not in moved.action_text()
    assert moved.env_prefixes() == [NEW]  # the move stays; the local tags make it consistent


@pytest.mark.parametrize(
    ("profiles", "expected"),
    [
        ("", ("postgres", "backend", "frontend")),
        ("proxy", ("postgres", "backend", "frontend", "proxy")),
        ("mcp", ("postgres", "backend", "frontend", "qradar-mcp")),
        ("proxy,mcp", ("postgres", "backend", "frontend", "proxy", "qradar-mcp")),
    ],
)
def test_profiles_decide_which_installed_images_are_tagged(
    moved: Moved, profiles: str, expected: tuple[str, ...]
) -> None:
    moved.set_env(prefix=OLD, profiles=profiles)

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert moved.tags()[: len(expected)] == installed_tags(*expected)
    assert len([t for t in moved.tags() if t.startswith(f"docker tag {OLD}/")]) == len(expected)


def test_a_profile_image_missing_only_matters_when_its_profile_is_on(moved: Moved) -> None:
    moved.set_env(prefix=OLD, profiles="")

    result = run(moved, STUB_MISSING=f"{OLD}/proxy:{OLD_VERSION}")

    assert result.returncode == 0, output(result)
    assert moved.env_prefixes() == [NEW]


def test_answers_without_an_image_prefix_entry_are_not_touched(moved: Moved) -> None:
    moved.answers.write_text("PUBLIC_HOSTNAME=soc.example.test\n")
    answers = moved.answers.read_bytes()

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert moved.answers.read_bytes() == answers
    assert moved.env_prefixes() == [NEW]


def test_a_missing_answers_file_is_not_created(moved: Moved) -> None:
    moved.answers.unlink()

    result = run(moved)

    assert result.returncode == 0, output(result)
    assert not moved.answers.exists()
    assert moved.env_prefixes() == [NEW]


def test_a_duplicated_prefix_line_refuses_without_touching_env_or_answers(
    moved: Moved,
) -> None:
    with moved.env_path.open("a") as handle:
        handle.write(f"QRADAR_DASH_IMAGE_PREFIX={OLD}\n")
    env, answers = moved.env_path.read_bytes(), moved.answers.read_bytes()

    result = run(moved)

    assert result.returncode == 1
    assert "more than one QRADAR_DASH_IMAGE_PREFIX line" in result.stderr
    assert moved.unchanged(env, answers)
    assert moved.tags() == []
    assert " pull " not in moved.action_text()
    assert " stop " not in moved.action_text()


def test_a_fresh_install_writes_the_new_prefix(
    inst: base_install.Install, tmp_path: Path, build_path: str
) -> None:
    base_install._build(inst, tmp_path, build_path)

    env = (inst.root / ".env").read_text().splitlines()
    assert f"QRADAR_DASH_IMAGE_PREFIX={NEW}" in env
    assert OLD not in (inst.root / ".env").read_text()
    answers = (inst.root / ".secrets" / "install.answers").read_text()
    assert f"IMAGE_PREFIX={NEW}" in answers
    assert OLD not in answers
