"""The secrets helper script's docker argv: pinned, offline, capability-free.

`deploy/load-secrets.sh` streams the QRadar token, the CA and the proxy TLS key
into their volumes. Every `docker run` it makes is asserted here to use the one
digest-pinned helper image, `--network none`, `--cap-drop ALL` (with only the
capability a copy needs added back), `no-new-privileges`, and a read-only volume
where the step only reads. These tests run the real script against a stub
`docker` on PATH; no Docker is needed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "load-secrets.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

# The constant as it must be written in the script: a tag for readability plus a
# digest. A bump is a reviewed edit of that one line (docs/DEVELOPMENT.md "Base
# image pins"); these tests read the value from the script rather than restating
# the digest, but the form is pinned here.
HELPER_IMAGE_RE = re.compile(r"^alpine:[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$")

DOCKER_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB_DOCKER_CALLS"
case "$1" in
  volume) exit 0 ;;
  run)
    # The load runs stream a tar over stdin; drain it so the host-side tar in
    # load-secrets.sh is never killed by SIGPIPE under pipefail.
    [ "$2" = "-i" ] && cat >/dev/null
    exit 0
    ;;
esac
printf 'unexpected docker call: %s\n' "$*" >&2
exit 97
"""


def helper_image() -> str:
    """The helper reference written in the script (there must be exactly one)."""
    constants = re.findall(
        r"^SECRETS_HELPER_IMAGE=(\S+)\s*$", SCRIPT.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert len(constants) == 1, "deploy/load-secrets.sh must define SECRETS_HELPER_IMAGE once"
    return constants[0]


class LoadSecrets:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        secrets = self.root / ".secrets"
        secrets.mkdir(mode=0o700)
        (secrets / "qradar.sec").write_text("fixture-token\n")
        (secrets / "qradar-ca.pem").write_text("fixture-ca\n")
        (secrets / "tls.crt").write_text("fixture-crt\n")
        (secrets / "tls.key").write_text("fixture-key\n")
        self.script = self.root / "deploy" / "load-secrets.sh"
        self.script.parent.mkdir()
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)

        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for tool in ("bash", "cat", "dirname", "tar"):
            found = shutil.which(tool)
            assert found is not None, tool
            (self.bin / tool).symlink_to(found)
        self.calls = tmp_path / "docker.calls"
        self._stub("docker", DOCKER_STUB)
        self.env = {
            "HOME": str(tmp_path),
            "PATH": str(self.bin),
            "STUB_DOCKER_CALLS": str(self.calls),
        }

    def _stub(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run(self, *args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [shutil.which("bash") or "/bin/bash", str(self.script), *args],
            cwd=self.root,
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def run_heads(self) -> list[str]:
        """The first line of every recorded `docker run` argv.

        A load record carries the container's multi-line `sh -c` script, so it
        spans several lines; everything these tests assert on — the flags, the
        volume, the image — stands before the command, on the first line.
        Continuation lines are indented script text; a record starts with
        `run`/`volume` at the first column.
        """
        if not self.calls.exists():
            return []
        heads: list[str] = []
        current: str | None = None
        for line in self.calls.read_text(encoding="utf-8").splitlines():
            if line.startswith(("run ", "volume ")):
                if current is not None:
                    heads.append(current)
                current = line
            elif current is not None and not line.startswith((" ", "\t")):
                raise AssertionError(f"unrecognized record line: {line!r}")
        if current is not None:
            heads.append(current)
        return [head for head in heads if head.startswith("run ")]


@pytest.fixture
def helper(tmp_path: Path) -> Iterator[LoadSecrets]:
    fixture = LoadSecrets(tmp_path)
    yield fixture


def test_the_helper_constant_is_a_digest_pinned_alpine() -> None:
    assert HELPER_IMAGE_RE.fullmatch(helper_image())


def test_qradar_load_runs_are_pinned_offline_and_capability_free(helper: LoadSecrets) -> None:
    result = helper.run("qradar")

    assert result.returncode == 0, result.stdout + result.stderr
    image = helper_image()
    load, listing = helper.run_heads()
    assert load.startswith(
        f"run -i --rm --network none --cap-drop ALL --cap-add CHOWN "
        f"--security-opt no-new-privileges -v qradar-observability-secrets:/dst {image} "
    ), load
    assert listing.startswith(
        f"run --rm --network none --cap-drop ALL --security-opt no-new-privileges "
        f"-v qradar-observability-secrets:/dst:ro {image} "
    ), listing
    # The step that writes needs its one capability back and the volume
    # read-write; the step that only reads mounts it read-only and adds
    # nothing back.
    assert "--cap-add" not in listing
    assert ":ro" not in load


def test_proxy_load_runs_are_pinned_offline_and_capability_free(helper: LoadSecrets) -> None:
    result = helper.run("proxy")

    assert result.returncode == 0, result.stdout + result.stderr
    image = helper_image()
    load, listing = helper.run_heads()
    assert load.startswith(
        f"run -i --rm --network none --cap-drop ALL --cap-add CHOWN "
        f"--security-opt no-new-privileges -v qradar-observability-proxy-certs:/dst {image} "
    ), load
    assert listing.startswith(
        f"run --rm --network none --cap-drop ALL --security-opt no-new-privileges "
        f"-v qradar-observability-proxy-certs:/dst:ro {image} "
    ), listing
    assert "--cap-add" not in listing


def test_all_runs_both_loaders(helper: LoadSecrets) -> None:
    result = helper.run("all")

    assert result.returncode == 0, result.stdout + result.stderr
    records = helper.run_heads()
    assert len(records) == 4, records
    image = helper_image()
    for record in records:
        assert record.startswith("run ")
        assert "--network none" in record
        assert "--cap-drop ALL" in record
        assert "--security-opt no-new-privileges" in record
        assert image in record
        # No floating reference anywhere: a bare `alpine` token would mean the
        # pin was dropped and the tag started to float.
        assert not re.search(r"(^|\s)alpine(\s|$)", record)


def test_volume_name_overrides_reach_the_runs(helper: LoadSecrets) -> None:
    result = helper.run("proxy", PROXY_CERTS_VOLUME="other-certs")

    assert result.returncode == 0, result.stdout + result.stderr
    load, listing = helper.run_heads()
    assert "-v other-certs:/dst " in load
    assert "-v other-certs:/dst:ro " in listing


def test_a_missing_secret_refuses_before_any_docker_call(helper: LoadSecrets) -> None:
    (helper.root / ".secrets" / "qradar.sec").unlink()

    result = helper.run("qradar")

    assert result.returncode != 0
    assert "missing: .secrets/qradar.sec" in result.stderr
    assert not helper.calls.exists()
