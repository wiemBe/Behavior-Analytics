"""Image pins and workflow permissions.

Two promises of the release, pinned here so they cannot drift silently:

* every ``FROM`` in the four Dockerfiles the release builds from carries a
  digest (a moved base tag would otherwise change what a version tag builds);
* the workflows hand the ``GITHUB_TOKEN`` read-only by default, and write
  scopes only to the jobs that push images or write the release.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"

# The Dockerfiles the release workflow builds from. qradar-mcp's own Dockerfile
# is IBM's, pinned by submodule commit, and is deliberately not in this list.
DOCKERFILES = [
    REPO / "backend" / "Dockerfile",
    REPO / "frontend" / "Dockerfile",
    REPO / "deploy" / "postgres" / "Dockerfile",
    REPO / "deploy" / "proxy" / "Dockerfile",
]


def workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def write_scopes(permissions: dict[str, str] | str | None) -> set[str]:
    """The scopes a `permissions:` block grants as write (mapping or string form)."""
    if permissions is None:
        return set()
    if isinstance(permissions, str):
        return {"(all)"} if permissions == "write" else set()
    return {scope for scope, value in permissions.items() if value == "write"}


# ---------------------------------------------------------------- the Dockerfiles


def test_every_dockerfile_from_is_digest_pinned() -> None:
    for dockerfile in DOCKERFILES:
        stages: set[str] = set()
        for number, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
            tokens = line.split()
            if not tokens or tokens[0].upper() != "FROM":
                continue
            image = tokens[1]
            if len(tokens) >= 4 and tokens[2].upper() == "AS":
                stages.add(tokens[3])
            if image in stages:
                continue  # a stage that builds FROM another stage needs no digest
            assert re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", image), (
                f"{dockerfile.relative_to(REPO)}:{number}: {image!r} floats on a tag; "
                "pin it TAG@sha256:<digest> (docs/DEVELOPMENT.md \"Base image pins\")"
            )


# ---------------------------------------------------------------- the workflows


def test_release_grants_write_only_to_the_jobs_that_push_or_publish() -> None:
    release = workflow("release.yml")
    # The default every job inherits is read-only.
    assert release.get("permissions") == {"contents": "read"}
    expected = {
        "images": {"packages"},  # pushes the built images to ghcr.io
        "release": {"contents"},  # writes the GitHub Release
    }
    for name, job in release["jobs"].items():
        writes = write_scopes(job.get("permissions"))
        if name in expected:
            assert writes == expected[name], name
        else:
            assert not writes, (
                f"release job {name!r} grants {sorted(writes)} write, "
                "but it neither pushes images nor writes the release"
            )


def test_ci_grants_no_write_scope() -> None:
    ci = workflow("ci.yml")
    assert ci.get("permissions") == {"contents": "read"}
    for name, job in ci["jobs"].items():
        assert not write_scopes(job.get("permissions")), name


def test_mirror_grants_only_the_push_it_performs() -> None:
    mirror = workflow("mirror-images.yml")
    assert mirror.get("permissions") == {"packages": "write"}
    for name, job in mirror["jobs"].items():
        assert write_scopes(job.get("permissions")) in (set(), {"packages"}), name


def test_the_release_resolves_every_pin_it_ships_with() -> None:
    """The digest step names every pin: redis, the four Dockerfiles, the helper."""
    jobs = workflow("release.yml")["jobs"]
    step = next(
        step for step in jobs["redis"]["steps"] if "digests" in step.get("name", "").lower()
    )
    run = step["run"]
    assert "docker buildx imagetools inspect" in run
    for reference in (
        "backend/Dockerfile",
        "frontend/Dockerfile",
        "deploy/postgres/Dockerfile",
        "deploy/proxy/Dockerfile",
        "docker-compose.prod.yml",  # the redis pin
        "deploy/load-secrets.sh",  # SECRETS_HELPER_IMAGE, the secrets helper
    ):
        assert reference in run, f"the digest step no longer checks {reference}"
