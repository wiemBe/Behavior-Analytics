"""Image pins and workflow permissions.

Three promises of the release, pinned here so they cannot drift silently:

* every ``FROM`` in the four Dockerfiles the release builds from carries a
  digest (a moved base tag would otherwise change what a version tag builds);
* the workflows hand the ``GITHUB_TOKEN`` read-only by default, and write
  scopes only to the jobs that push images or write the release;
* a dispatched run of the release workflow is a dry run: everything builds,
  nothing is pushed, no GitHub Release is written.
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


def test_the_mcp_image_check_is_read_only_pinned_and_never_pushes() -> None:
    check = workflow("mcp-image.yml")
    assert check.get("permissions") == {"contents": "read"}
    for name, job in check["jobs"].items():
        assert not write_scopes(job.get("permissions")), name
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
            with_ = step.get("with", {})
            assert with_.get("push", False) is False
            assert "login" not in step.get("uses", "")
    text = (WORKFLOWS / "mcp-image.yml").read_text(encoding="utf-8")
    assert "upstream=oci-layout://" in text


# ------------------------------------------------------------- the dry run

# The one test for "this run is the release": a tag push. Everywhere the
# workflow asks (the guarded tag comparison, the final build's push, the
# release job's `if`), it asks exactly this, written this way.
RELEASE_ONLY = "github.event_name == 'push'"
PUSH_ONLY_ON_TAG = "${{ github.event_name == 'push' }}"
TAG_GUARD = 'if [ "${{ github.event_name == \'push\' }}" = "true" ]; then'


def _triggers(document: dict[str, Any]) -> Any:
    # `on:` reads as boolean True to yaml.safe_load (YAML 1.1); look under both.
    return document.get("on", document.get(True))


def test_the_release_workflow_triggers_on_the_tag_and_on_demand() -> None:
    # The tag push is the release; a dispatched run (no inputs) is the dry run.
    assert _triggers(workflow("release.yml")) == {
        "push": {"tags": ["v*"]},
        "workflow_dispatch": None,
    }


def test_a_dry_run_builds_everything_and_pushes_nothing() -> None:
    builds = [
        (job_name, step)
        for job_name, job in workflow("release.yml")["jobs"].items()
        for step in job.get("steps", [])
        if "push" in step.get("with", {})
    ]
    assert builds, "the release workflow no longer builds anything"
    pushing = []
    for job_name, step in builds:
        value = step["with"]["push"]
        # A build step either never pushes (the upstream build, the frontend
        # check build) or pushes only when this run is the release.
        assert value is False or value == PUSH_ONLY_ON_TAG, f"{job_name}: {step.get('name')}"
        if value == PUSH_ONLY_ON_TAG:
            pushing.append((job_name, step.get("name")))
    # The final build is the one step that pushes, on the tag push only.
    assert pushing == [("images", "Build and push")]


def test_the_release_job_writes_a_release_only_on_the_tag_push() -> None:
    release = workflow("release.yml")["jobs"]["release"]
    assert release.get("if") == RELEASE_ONLY


def test_verify_compares_the_tag_only_on_the_tag_push() -> None:
    steps = workflow("release.yml")["jobs"]["verify"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    # The pin check and the VERSION/notes checks run on both events.
    for name in (
        "qradar-mcp is at the pinned commit",
        "The tag, VERSION and the release notes agree",
    ):
        assert "if" not in by_name[name], name
    lines = by_name["The tag, VERSION and the release notes agree"]["run"].splitlines()
    guard = lines.index(TAG_GUARD)
    end = lines.index("fi", guard)
    comparison = next(
        i for i, line in enumerate(lines) if '"$GITHUB_REF_NAME" = "v$version"' in line
    )
    assert guard < comparison < end  # a dispatched run has no tag to compare
    # The notes checks and the version output are outside the guard: they run
    # on a dry run too.
    after_guard = lines[end:]
    for both_events in (
        '[ -f "$notes" ]',
        'grep -qx "version: $version"',
        'echo "version=$version" >> "$GITHUB_OUTPUT"',
    ):
        assert any(both_events in line for line in after_guard), both_events


def test_nothing_else_is_conditioned_on_the_event() -> None:
    # A dry run that silently skips a build is the failure this task prevents:
    # apart from the release job and the guarded tag comparison, no job and
    # no step may test which event started the run.
    for job_name, job in workflow("release.yml")["jobs"].items():
        if job_name == "release":
            continue  # pinned by the test above
        assert "github.event" not in job.get("if", ""), job_name
        for step in job.get("steps", []):
            assert "github.event" not in step.get("if", ""), f"{job_name}: {step.get('name')}"
