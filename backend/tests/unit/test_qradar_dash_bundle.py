"""`./qradar-dash bundle` and the receiving side (`install --offline`, `upgrade --offline`).

Docker is a stub on PATH. The stub keeps a small image store under `$HOME/imgstate`: `pull` adds
an image (its ID is derived from the reference), `save` writes the references and IDs into the
archive, `load` reads them back, `image inspect` and `tag` read and write the store. A flag file in
that directory makes the stub fail a call or, on `load`, keep a digest-pinned reference by ID only
(every digest pin, or the secrets helper alone), the way some real image stores do.
"""

# ruff: noqa: F811  (fixtures imported from the installer and upgrade tests are used by name)
from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.unit.test_qradar_dash_installer import (  # noqa: F401  (fixtures)
    INSTALL_FLOW_DOCKER_STUB,
    Install,
    _install_flow,
    _stub,
    build_path,
    flow_path,
    inst,
)
from tests.unit.test_qradar_dash_upgrade import (  # noqa: F401  (fixtures)
    DOCKER_STUB,
    TARGET_VERSION,
    UpgradeFixture,
    output,
    upgrade,
)

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"
COMPOSE_PROD = REPO / "docker-compose.prod.yml"
DEFAULT_PREFIX = "ghcr.io/wiembe/behavior-analytics"
UPGRADE_PREFIX = "registry.example/qradar"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

PRODUCER_TOOLS = [
    "bash", "sh", "cat", "stat", "grep", "sed", "awk", "head", "tail", "sort", "tr", "dirname",
    "readlink", "uname", "mkdir", "rm", "date", "ls", "id", "env", "mktemp", "install", "tar",
    "cp", "mv", "find", "sha256sum", "wc", "chmod",
]

# Runs before the base stub's own cases. Active only when $HOME/imgstate exists.
STORE_PRELUDE = r"""
S="$HOME/imgstate"
if [ -d "$S" ]; then
  db="$S/local.db"; touch "$db"
  flag() { [ -e "$S/$1" ]; }
  lookup() { awk -v k="$1" '$1 == k { print $2; exit }' "$db"; }
  add() { printf '%s %s\n' "$1" "$2" >> "$db"; }
  id_for() { printf 'sha256:%s' "$(printf '%s' "$1" | sha256sum | awk '{print $1}')"; }
  logit() {
    printf '%s\n' "$*" >> "$S/argv.log"
    [ -z "${STUB_ACTIONS:-}" ] || printf 'docker %s\n' "$*" >> "$STUB_ACTIONS"
  }
  case "$1" in
    pull)
      logit "$@"
      if [ -f "$S/pull-fail" ] && grep -qxF -- "$2" "$S/pull-fail"; then
        echo "pull access denied for $2" >&2; exit 1
      fi
      id=$(id_for "$2"); add "$2" "$id"; add "$id" "$id"; echo "pulled $2"; exit 0 ;;
    save)
      logit "$@"
      shift; [ "$1" = -o ] || exit 2
      file=$2; shift 2
      : > "$file"
      flag save-fail && { echo "no space left on device" >&2; exit 1; }
      for ref in "$@"; do
        id=$(lookup "$ref"); [ -n "$id" ] || { echo "No such image: $ref" >&2; exit 1; }
        printf '%s %s\n' "$ref" "$id" >> "$file"
      done
      exit 0 ;;
    load)
      logit "$@"
      flag load-fail && { echo "write error" >&2; exit 1; }
      while read -r ref id; do
        add "$id" "$id"
        if flag load-drop-digest && [[ $ref == *@sha256:* ]]; then
          printf '%s %s\n' "$ref" "$id" >> "$S/dropped"; continue
        fi
        if flag load-drop-helper && [[ $ref == alpine:*@sha256:* ]]; then
          printf '%s %s\n' "$ref" "$id" >> "$S/dropped"; continue
        fi
        if flag load-skew && [[ $ref == */backend:* ]]; then id="${id}00"; fi
        add "$ref" "$id"
      done < "$3"
      exit 0 ;;
    image)
      if [ "$2" = inspect ]; then
        subject=${!#}
        val=$(lookup "$subject")
        if [ -n "$val" ]; then
          logit "$@"
          [ "$3" != --format ] || printf '%s\n' "$val"
          exit 0
        fi
        if [[ $subject != sha256:* ]]; then logit "$@"; exit 1; fi
      fi ;;
    info)
      if [[ $* == *DriverStatus* ]]; then
        logit "$@"
        store=$(cat "$S/store" 2>/dev/null); store=${store:-containerd}
        case "$store" in
          containerd) echo "[[driver-type io.containerd.snapshotter.v1]]" ;;
          classic) echo "[[Backing Filesystem extfs] [Supports d_type true]]" ;;
          *) exit 1 ;;
        esac
        exit 0
      fi ;;
    tag)
      src=$(lookup "$2")
      if [ -n "$src" ]; then
        logit "$@"
        # Docker 29 (containerd store) refuses a digest reference as a tag target.
        if [[ $3 == *@* ]]; then
          echo "refusing to create a tag with a digest reference" >&2; exit 1
        fi
        add "$3" "$src"
        # With the plain tag set, the pinned reference resolves when the digest matches.
        if [ -f "$S/dropped" ] && ! flag plain-no-resolve; then
          while read -r ref id; do
            if [ "${ref%%@*}" = "$3" ] && [ "$id" = "$src" ]; then add "$ref" "$id"; fi
          done < "$S/dropped"
        fi
        exit 0
      fi ;;
  esac
fi
"""

PRODUCER_DOCKER_STUB = (
    "#!/bin/bash\n" + STORE_PRELUDE + 'echo "unexpected docker call: $*" >&2\nexit 97\n'
)


def with_store(base_stub: str) -> str:
    """The base stub with the image-store prelude inserted after its shebang line."""
    first, rest = base_stub.split("\n", 1)
    assert first.startswith("#!")
    return first + "\n" + STORE_PRELUDE + rest


class Producer:
    """A connected host's checkout, with a docker stub and its own home."""

    def __init__(self, tmp_path: Path, *, version: str, prefix: str | None = None) -> None:
        self.version = version
        self.root = tmp_path / f"producer-{version}"
        self.root.mkdir()
        self.home = tmp_path / f"producer-home-{version}"
        (self.home / "imgstate").mkdir(parents=True)
        (self.home / "imgstate" / "strict").write_text("")
        shutil.copy(SCRIPT, self.root / "qradar-dash")
        (self.root / "qradar-dash").chmod(0o755)
        (self.root / "VERSION").write_text(f"{version}\n")
        shutil.copy(REPO / "docker-compose.yml", self.root / "docker-compose.yml")
        shutil.copy(COMPOSE_PROD, self.root / "docker-compose.prod.yml")
        shutil.copytree(REPO / "deploy", self.root / "deploy")
        notes = self.root / "docs" / "releases"
        notes.mkdir(parents=True)
        (notes / f"{version}.md").write_text(f"---\nversion: {version}\nmin_from: 0.1.0\n---\nx\n")
        if prefix is not None:
            (self.root / ".env").write_text(f"QRADAR_DASH_IMAGE_PREFIX={prefix}\nSECRET=hunter2\n")
        self.bin = tmp_path / f"producer-bin-{version}"
        self.bin.mkdir()
        for tool in PRODUCER_TOOLS:
            found = shutil.which(tool)
            assert found, tool
            (self.bin / tool).symlink_to(found)
        _stub(self.bin / "docker", PRODUCER_DOCKER_STUB)
        self.out = tmp_path / f"out-{version}"
        self.out.mkdir()

    def flag(self, name: str, content: str = "") -> None:
        (self.home / "imgstate" / name).write_text(content)

    def docker_calls(self) -> list[str]:
        log = self.home / "imgstate" / "argv.log"
        return log.read_text().splitlines() if log.exists() else []

    def run(
        self, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [shutil.which("bash") or "/bin/bash", str(self.root / "qradar-dash"), *args],
            cwd=cwd or self.root,
            env={"HOME": str(self.home), "PATH": str(self.bin)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            stdin=subprocess.DEVNULL,
        )

    def make(self, *args: str) -> Path:
        result = self.run("bundle", "--output", str(self.out), *args)
        assert result.returncode == 0, result.stdout + result.stderr
        return self.out / f"qradar-dash-{self.version}-bundle.tar"


@pytest.fixture
def producer(tmp_path: Path) -> Producer:
    return Producer(tmp_path, version="0.1.0")


def redis_reference() -> str:
    match = re.search(r"image: (redis:\S+@sha256:[0-9a-f]{64})", COMPOSE_PROD.read_text())
    assert match
    return match.group(1)


def helper_reference() -> str:
    """The secrets-helper reference as pinned in deploy/load-secrets.sh (one place)."""
    match = re.search(
        r"^SECRETS_HELPER_IMAGE=(alpine:\S+@sha256:[0-9a-f]{64})$",
        (REPO / "deploy" / "load-secrets.sh").read_text(),
        flags=re.MULTILINE,
    )
    assert match
    return match.group(1)


def bundle_refs(prefix: str, version: str) -> list[str]:
    """The references the bundle pulls and saves: five services, redis, the helper."""
    return [
        f"{prefix}/{name}:{version}"
        for name in ("postgres", "backend", "frontend", "proxy", "qradar-mcp")
    ] + [redis_reference(), helper_reference()]


def plain_name(reference: str) -> str:
    """The name:tag of a digest pin. `docker tag` may target this, never the digest."""
    plain, separator, digest = reference.partition("@")
    assert separator == "@" and digest.startswith("sha256:") and "@" not in plain
    return plain


def tag_targets(calls: list[str]) -> list[str]:
    """Targets of the stub's `tag` lines. A digest reference is never one of them."""
    return [call.rsplit(" ", 1)[-1] for call in calls if call.startswith("tag ")]


def members(bundle: Path) -> list[str]:
    with tarfile.open(bundle) as tar:
        return tar.getnames()


def rewrite(bundle: Path, changes: dict[str, bytes | None]) -> Path:
    """Copy the bundle with members replaced (bytes), added (bytes) or removed (None)."""
    target = bundle.with_name("changed-" + bundle.name)
    with tarfile.open(bundle) as src, tarfile.open(target, "w") as dst:
        for info in src.getmembers():
            if info.name in changes:
                data = changes.pop(info.name)
                if data is None:
                    continue
                info.size = len(data)
                dst.addfile(info, io.BytesIO(data))
            else:
                dst.addfile(info, src.extractfile(info) if info.isfile() else None)
        for name, data in changes.items():
            assert data is not None
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    return target


# ---------------------------------------------------------------- bundle: the producing side


def test_bundle_pulls_and_saves_the_seven_images_redis_and_helper_by_pinned_digest(
    producer: Producer,
) -> None:
    result = producer.run("bundle", "--output", str(producer.out))
    assert result.returncode == 0, result.stdout + result.stderr

    calls = producer.docker_calls()
    refs = bundle_refs(DEFAULT_PREFIX, "0.1.0")
    assert len(refs) == 7
    assert f"Saving the {len(refs)} images" in result.stdout
    assert [c for c in calls if c.startswith("pull ")] == [f"pull {r}" for r in refs]
    assert any(r.endswith("/proxy:0.1.0") for r in refs)
    assert any(r.endswith("/qradar-mcp:0.1.0") for r in refs)
    assert refs[-2] == redis_reference() and "@sha256:" in refs[-2]
    assert refs[-1] == helper_reference() and "@sha256:" in refs[-1]
    saves = [c for c in calls if c.startswith("save ")]
    assert len(saves) == 1
    assert saves[0].endswith(" ".join(refs))
    # pulls come before the one save
    assert calls.index(saves[0]) > max(calls.index(f"pull {r}") for r in refs)


def test_bundle_has_exactly_the_designed_members_and_correct_sums(producer: Producer) -> None:
    bundle = producer.make()

    names = members(bundle)
    top = {n.split("/")[0] for n in names}
    assert top == {
        "images.tar", "SHA256SUMS", "MANIFEST", "VERSION", "docs", "qradar-dash",
        "docker-compose.yml", "docker-compose.prod.yml", "deploy",
    }
    assert "docs/releases/0.1.0.md" in names
    assert "deploy/load-secrets.sh" in names
    with tarfile.open(bundle) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        script = tar.getmember("qradar-dash")
    assert script.mode & 0o111
    assert files["VERSION"] == b"0.1.0\n"
    sums = dict(
        reversed(line.split("  ", 1)) for line in files["SHA256SUMS"].decode().splitlines()
    )
    assert sorted(sums) == sorted(n for n in files if n != "SHA256SUMS")
    for name, digest in sums.items():
        assert hashlib.sha256(files[name]).hexdigest() == digest, name
    manifest = files["MANIFEST"].decode().splitlines()
    assert "version 0.1.0" in manifest
    assert f"image_prefix {DEFAULT_PREFIX}" in manifest
    images = [line.split() for line in manifest if line.startswith("image ")]
    assert [i[1] for i in images] == bundle_refs(DEFAULT_PREFIX, "0.1.0")
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", i[2]) for i in images)


def test_bundle_never_carries_the_sites_configuration(tmp_path: Path) -> None:
    prod = Producer(tmp_path, version="0.1.0", prefix="registry.example/qradar")
    (prod.root / ".secrets").mkdir()
    (prod.root / ".secrets" / "ghcr.token").write_text("token-material")
    (prod.root / "docker-compose.override.yml").write_text("services: {}\n")

    bundle = prod.make()

    names = members(bundle)
    assert not [n for n in names if n.split("/")[0] in (".env", ".secrets")]
    assert "docker-compose.override.yml" not in names
    with tarfile.open(bundle) as tar:
        blob = b"".join(tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())
    assert b"hunter2" not in blob and b"token-material" not in blob


def test_bundle_refuses_a_config_file_inside_deploy(producer: Producer) -> None:
    (producer.root / "deploy" / ".env").write_text("SECRET=1\n")

    result = producer.run("bundle", "--output", str(producer.out))

    assert result.returncode == 1
    assert "deploy/.env" in result.stderr
    assert list(producer.out.iterdir()) == []


def test_bundle_says_which_prefix_it_used(tmp_path: Path) -> None:
    default = Producer(tmp_path, version="0.1.0")
    result = default.run("bundle", "--output", str(default.out))
    assert f"image prefix {DEFAULT_PREFIX} (the default" in result.stdout

    custom = Producer(tmp_path, version="0.1.1", prefix="registry.example/mirror")
    result = custom.run("bundle", "--output", str(custom.out))
    assert (
        "image prefix registry.example/mirror (QRADAR_DASH_IMAGE_PREFIX in .env)" in result.stdout
    )
    pulls = [c.removeprefix("pull ") for c in custom.docker_calls() if c.startswith("pull ")]
    assert pulls == bundle_refs("registry.example/mirror", "0.1.1")
    # The site prefix covers the five services only. Redis and the helper stay the
    # digest pins from their own files and are not rewritten onto the prefix.
    assert pulls[-2] == redis_reference()
    assert not redis_reference().startswith("registry.example/")
    assert pulls[-1] == helper_reference()
    assert not helper_reference().startswith("registry.example/")


def test_bundle_defaults_to_the_checkout_version_and_current_directory(
    producer: Producer,
) -> None:
    result = producer.run("bundle")

    assert result.returncode == 0, result.stderr
    assert (producer.root / "qradar-dash-0.1.0-bundle.tar").is_file()


def test_bundle_prints_path_size_and_the_next_commands(producer: Producer) -> None:
    result = producer.run("bundle", "--output", str(producer.out))

    target = producer.out / "qradar-dash-0.1.0-bundle.tar"
    assert str(target) in result.stdout
    assert f"{target.stat().st_size} bytes" in result.stdout
    out = result.stdout
    first, _, upgrade_block = out.partition("Upgrade of an installed site")
    assert "First install" in first and upgrade_block
    assert "mkdir qradar-dash-0.1.0" in first
    assert (
        "tar -xf /path/to/qradar-dash-0.1.0-bundle.tar -C qradar-dash-0.1.0 "
        "--exclude=images.tar"
    ) in first
    assert "cd qradar-dash-0.1.0" in first
    assert "./qradar-dash install --offline /path/to/qradar-dash-0.1.0-bundle.tar" in first
    assert "upgrade" not in first.split("First install")[1]
    assert "cd <installation directory>" in upgrade_block
    assert "tar -xf /path/to/qradar-dash-0.1.0-bundle.tar --exclude=images.tar" in upgrade_block
    assert (
        "./qradar-dash upgrade 0.1.0 --offline /path/to/qradar-dash-0.1.0-bundle.tar"
    ) in upgrade_block
    assert "cd qradar-dash-0.1.0" not in upgrade_block
    assert "mkdir" not in upgrade_block and "install --offline" not in upgrade_block


def test_a_failed_save_leaves_no_partial_bundle_and_says_so(producer: Producer) -> None:
    producer.flag("save-fail")

    result = producer.run("bundle", "--output", str(producer.out))

    assert result.returncode == 1
    assert "docker save failed" in result.stderr
    assert "no partial bundle was left" in result.stderr
    assert list(producer.out.iterdir()) == []


def test_a_failed_pull_names_the_image_and_writes_nothing(producer: Producer) -> None:
    refs = bundle_refs(DEFAULT_PREFIX, "0.1.0")
    producer.flag("pull-fail", refs[3] + "\n")

    result = producer.run("bundle", "--output", str(producer.out))

    assert result.returncode == 1
    assert f"could not pull {refs[3]}" in result.stderr
    assert "docker login ghcr.io" in result.stderr
    assert list(producer.out.iterdir()) == []
    assert not [c for c in producer.docker_calls() if c.startswith("save ")]


def test_bundle_never_overwrites_an_existing_bundle(producer: Producer) -> None:
    existing = producer.out / "qradar-dash-0.1.0-bundle.tar"
    existing.write_text("the operator's earlier bundle")

    result = producer.run("bundle", "--output", str(producer.out))

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert existing.read_text() == "the operator's earlier bundle"
    assert producer.docker_calls() == []  # refused before any pull


def test_bundle_version_must_equal_the_checkout_version(producer: Producer) -> None:
    result = producer.run("bundle", "--version", "0.9.0", "--output", str(producer.out))

    assert result.returncode == 1
    assert "this checkout is version 0.1.0, not 0.9.0" in result.stderr
    assert "git checkout v0.9.0" in result.stderr
    assert producer.docker_calls() == []

    ok = producer.run("bundle", "--version", "0.1.0", "--output", str(producer.out))
    assert ok.returncode == 0, ok.stderr


def test_bundle_refuses_a_missing_output_directory_and_missing_release_notes(
    producer: Producer,
) -> None:
    result = producer.run("bundle", "--output", str(producer.out / "absent"))
    assert result.returncode == 1
    assert "mkdir -p" in result.stderr

    (producer.root / "docs" / "releases" / "0.1.0.md").unlink()
    result = producer.run("bundle", "--output", str(producer.out))
    assert result.returncode == 1
    assert "docs/releases/0.1.0.md is missing" in result.stderr
    assert producer.docker_calls() == []


def test_bundle_without_a_pinned_redis_digest_is_refused(producer: Producer) -> None:
    compose = producer.root / "docker-compose.prod.yml"
    compose.write_text(re.sub(r"@sha256:[0-9a-f]{64}", "", compose.read_text()))

    result = producer.run("bundle", "--output", str(producer.out))

    assert result.returncode == 1
    assert "no redis image pinned by digest" in result.stderr
    assert list(producer.out.iterdir()) == []


def test_bundle_without_a_pinned_helper_digest_is_refused(producer: Producer) -> None:
    script = producer.root / "deploy" / "load-secrets.sh"
    script.write_text(script.read_text().replace(helper_reference(), "alpine:3.20"))

    result = producer.run("bundle", "--output", str(producer.out))

    assert result.returncode == 1
    assert "no SECRETS_HELPER_IMAGE pinned by digest" in result.stderr
    assert list(producer.out.iterdir()) == []


# ---------------------------------------------------------------- install --offline


@pytest.fixture
def consumer(inst: Install, tmp_path: Path, flow_path: str) -> Install:
    """The production host's checkout with a docker stub that keeps an image store."""
    state = tmp_path / "imgstate"
    state.mkdir()
    (state / "strict").write_text("")
    _stub(Path(flow_path) / "docker", with_store(INSTALL_FLOW_DOCKER_STUB))
    for tool in ("find", "sha256sum"):  # the bundle check needs them; the base PATH may lack find
        link = Path(flow_path) / tool
        if not link.exists():
            link.symlink_to(shutil.which(tool) or "")
    inst.flow_path = flow_path  # type: ignore[attr-defined]
    return inst


def consumer_calls(inst: Install) -> list[str]:
    log = inst.root.parent / "imgstate" / "argv.log"
    return log.read_text().splitlines() if log.exists() else []


def consumer_flag(inst: Install, name: str, content: str = "") -> None:
    (inst.root.parent / "imgstate" / name).write_text(content)


def offline_install(inst: Install, bundle: Path) -> subprocess.CompletedProcess[str]:
    tmp = inst.root.parent
    for name, value in (("qr_rc", 0), ("up_rc", 0), ("ready_rc", 0), ("version_code", 200)):
        (tmp / name).write_text(f"{value}\n")
    return inst.run(
        "install", "--non-interactive", str(inst.answers()), "--skip-qradar-check",
        "--offline", str(bundle), path=inst.flow_path,  # type: ignore[attr-defined]
    )


def test_the_operators_path_bundle_then_install_offline(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    # exactly the commands the bundle prints for the production host
    extract = subprocess.run(  # noqa: S603
        [shutil.which("tar") or "tar", "-xf", str(bundle), "-C", str(consumer.root),
         "--exclude=images.tar"],
        check=False, capture_output=True, text=True,
    )
    assert extract.returncode == 0, extract.stderr
    assert not (consumer.root / "images.tar").exists()
    assert (consumer.root / "docs" / "releases" / "0.1.0.md").is_file()
    _stub(consumer.root / "deploy" / "load-secrets.sh", "#!/bin/sh\nexit 0\n")

    result = offline_install(consumer, bundle)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bundle images loaded and checked against MANIFEST" in result.stdout
    calls = consumer_calls(consumer)
    assert any(c.startswith("load -i ") for c in calls)
    for ref in bundle_refs(DEFAULT_PREFIX, "0.1.0"):
        assert f"image inspect --format {{{{.Id}}}} {ref}" in calls, ref
    assert (consumer.root / ".qradar-dash" / "state.json").is_file()
    assert not (consumer.root / ".install-tmp").exists()


def test_a_tampered_member_is_refused_and_nothing_is_loaded(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    tampered = rewrite(bundle, {"docker-compose.prod.yml": b"services: {evil: {}}\n"})

    result = offline_install(consumer, tampered)

    assert result.returncode == 1
    assert "the bundle checksums do not match; nothing was loaded" in result.stderr
    assert not [c for c in consumer_calls(consumer) if c.startswith("load")]
    assert not (consumer.root / ".install-tmp").exists()


def test_a_host_without_find_cannot_pass_the_member_check_by_default(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    (Path(consumer.flow_path) / "find").unlink()  # type: ignore[attr-defined]

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "cannot list the members of the bundle" in result.stderr
    assert not [c for c in consumer_calls(consumer) if c.startswith("load")]


@pytest.mark.parametrize(
    ("kind", "name", "needle"),
    [
        (tarfile.SYMTYPE, "link", "holds links"),
        (tarfile.REGTYPE, "../escape", "absolute or parent-directory paths"),
        (tarfile.REGTYPE, "/etc/escape", "absolute or parent-directory paths"),
    ],
)
def test_links_and_escaping_paths_are_refused_before_extraction(
    consumer: Install, producer: Producer, kind: bytes, name: str, needle: str
) -> None:
    bundle = producer.make()
    bad = bundle.with_name("bad.tar")
    shutil.copy(bundle, bad)
    with tarfile.open(bad, "a") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        if kind == tarfile.SYMTYPE:
            info.linkname = "/etc/passwd"
        tar.addfile(info)

    result = offline_install(consumer, bad)

    assert result.returncode == 1
    assert needle in result.stderr
    assert not [c for c in consumer_calls(consumer) if c.startswith("load")]
    assert not (consumer.root / ".install-tmp").exists()


def test_a_member_the_sums_do_not_cover_is_refused(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    extra = rewrite(bundle, {".env": b"SECRET_KEY=planted\n"})

    result = offline_install(consumer, extra)

    assert result.returncode == 1
    assert "holds .env, which SHA256SUMS does not cover" in result.stderr
    assert not [c for c in consumer_calls(consumer) if c.startswith("load")]


def test_a_bundle_of_another_version_is_refused_before_loading(
    consumer: Install, tmp_path: Path
) -> None:
    other = Producer(tmp_path, version="0.3.0")
    bundle = other.make()

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "is version 0.3.0 but this checkout is 0.1.0" in result.stderr
    assert "nothing was loaded" in result.stderr
    assert not [c for c in consumer_calls(consumer) if c.startswith("load")]
    assert not (consumer.root / ".env").exists()  # refused before the first write


def test_a_bundle_for_another_image_prefix_is_refused_before_loading(
    consumer: Install, tmp_path: Path
) -> None:
    other = Producer(tmp_path, version="0.1.0", prefix="registry.example/mirror")
    bundle = other.make()

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "registry.example/mirror" in result.stderr and DEFAULT_PREFIX in result.stderr
    assert not [c for c in consumer_calls(consumer) if c.startswith("load")]
    assert not (consumer.root / ".env").exists()


def test_a_file_that_is_not_a_bundle_is_refused(consumer: Install, tmp_path: Path) -> None:
    junk = tmp_path / "junk.tar"
    junk.write_text("not a tar")

    result = offline_install(consumer, junk)

    assert result.returncode == 1
    assert "cannot read" in result.stderr and "as a tar file" in result.stderr
    assert not (consumer.root / ".env").exists()


def test_a_relative_bundle_path_is_relative_to_where_the_operator_ran_it(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    flow = consumer.flow_path  # type: ignore[attr-defined]
    env = {"HOME": str(consumer.root.parent), "PATH": flow,
           "QRADAR_DASH_MEMINFO_FILE": str(consumer.meminfo)}

    result = subprocess.run(  # noqa: S603
        [shutil.which("bash") or "bash", str(consumer.script), "install", "--non-interactive",
         str(consumer.answers()), "--skip-qradar-check", "--dry-run", "--offline", bundle.name],
        cwd=bundle.parent, env=env, capture_output=True, text=True, timeout=60, check=False,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run: every check passed" in result.stdout


PLAIN_REDIS = "redis:7.4-alpine"


def manifest_text(bundle: Path) -> str:
    with tarfile.open(bundle) as tar:
        member = tar.extractfile("MANIFEST")
        assert member is not None
        return member.read().decode()


def with_manifest(bundle: Path, edit) -> Path:  # type: ignore[no-untyped-def]
    """The bundle with MANIFEST edited and SHA256SUMS recomputed, so only MANIFEST differs."""
    with tarfile.open(bundle) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    files["MANIFEST"] = edit(files["MANIFEST"].decode()).encode()
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
        if name != "SHA256SUMS"
    )
    return rewrite(bundle, {"MANIFEST": files["MANIFEST"], "SHA256SUMS": sums.encode()})


def test_a_load_that_loses_the_redis_digest_gets_the_plain_tag_and_the_pinned_ref_resolves(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "load-drop-digest")

    result = offline_install(consumer, bundle)

    assert result.returncode == 0, result.stdout + result.stderr
    ref = redis_reference()
    helper_plain = plain_name(helper_reference())
    assert plain_name(ref) == PLAIN_REDIS
    assert f"set the tag {PLAIN_REDIS} on it" in result.stdout
    assert f"moves an existing {PLAIN_REDIS}" in result.stdout
    assert f"set the tag {helper_plain} on it" in result.stdout
    assert f"moves an existing {helper_plain}" in result.stdout
    calls = consumer_calls(consumer)
    # load-drop-digest drops every digest pin (redis and the helper). Each is
    # retagged as its plain name only — never a digest tag.
    assert tag_targets(calls) == [PLAIN_REDIS, helper_plain]
    assert not [call for call in calls if call.startswith("tag ") and "@" in call]
    assert f"image inspect --format {{{{.Id}}}} {ref}" in calls
    assert f"image inspect --format {{{{.Id}}}} {helper_reference()}" in calls
    assert (consumer.root / ".qradar-dash" / "state.json").is_file()


def test_a_pinned_ref_that_does_not_resolve_after_the_plain_tag_is_refused(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "load-drop-digest")
    consumer_flag(consumer, "plain-no-resolve")

    result = offline_install(consumer, bundle)

    assert result.returncode == 1, result.stdout
    ref = redis_reference()
    assert f"{ref} does not resolve" in result.stderr
    assert "does not carry the pinned digest on this Docker" in result.stderr
    assert "bundle again" in result.stderr
    assert "docker tag" not in result.stderr  # no advice Docker would refuse
    calls = consumer_calls(consumer)
    # Both digest pins are dropped. Each tag target is the plain name — never a digest tag.
    assert tag_targets(calls) == [PLAIN_REDIS, plain_name(helper_reference())]
    assert not [call for call in calls if call.startswith("tag ") and "@" in call]
    assert not (consumer.root / ".qradar-dash" / "state.json").exists()
    assert not (consumer.root / ".install-tmp").exists()


def test_a_load_that_loses_the_helper_digest_gets_the_plain_tag_and_the_pinned_ref_resolves(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "load-drop-helper")

    result = offline_install(consumer, bundle)

    assert result.returncode == 0, result.stdout + result.stderr
    ref = helper_reference()
    plain = plain_name(ref)
    assert f"docker load kept {ref} only by its ID" in result.stdout
    assert f"set the tag {plain} on it" in result.stdout
    assert f"moves an existing {plain}" in result.stdout
    calls = consumer_calls(consumer)
    # Redis kept its digest, so the only retag is the helper, and it is the plain
    # name — never a digest tag.
    assert tag_targets(calls) == [plain]
    assert not [call for call in calls if call.startswith("tag ") and "@" in call]
    assert f"image inspect --format {{{{.Id}}}} {ref}" in calls
    assert (consumer.root / ".qradar-dash" / "state.json").is_file()


def test_a_pinned_helper_ref_that_does_not_resolve_after_the_plain_tag_is_refused(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "load-drop-helper")
    consumer_flag(consumer, "plain-no-resolve")

    result = offline_install(consumer, bundle)

    assert result.returncode == 1, result.stdout
    ref = helper_reference()
    plain = plain_name(ref)
    assert f"{ref} does not resolve" in result.stderr
    assert "does not carry the pinned digest on this Docker" in result.stderr
    assert f"even with the tag {plain} set" in result.stderr
    assert "bundle again" in result.stderr
    assert "docker tag" not in result.stderr  # no advice Docker would refuse
    calls = consumer_calls(consumer)
    assert tag_targets(calls) == [plain]  # never a digest tag
    assert not [call for call in calls if call.startswith("tag ") and "@" in call]
    assert not (consumer.root / ".qradar-dash" / "state.json").exists()
    assert not (consumer.root / ".install-tmp").exists()


def test_with_the_same_image_store_the_ids_are_compared(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    assert "image_store containerd" in manifest_text(bundle)
    consumer_flag(consumer, "load-skew")  # the receiving store also says containerd

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "backend:0.1.0 is image sha256:" in result.stderr
    assert "the bundle was made from sha256:" in result.stderr
    assert "were not compared" not in result.stdout


def test_with_a_different_image_store_the_ids_are_not_compared_and_the_load_continues(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "store", "classic")
    consumer_flag(consumer, "load-skew")  # IDs differ, as they would across stores

    result = offline_install(consumer, bundle)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Image IDs were not compared" in result.stdout
    assert "bundle host's image store is containerd and this host's is classic" in result.stdout
    assert "SHA256SUMS" in result.stdout
    assert "bundle images loaded and checked against MANIFEST" in result.stdout


def test_a_different_image_store_still_requires_every_reference_to_resolve(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "store", "classic")
    consumer_flag(consumer, "load-drop-digest")
    consumer_flag(consumer, "plain-no-resolve")

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "Image IDs were not compared" in result.stdout
    assert "does not resolve" in result.stderr


def test_a_manifest_without_the_store_line_is_compared_as_before(
    consumer: Install, producer: Producer
) -> None:
    bundle = with_manifest(
        producer.make(),
        lambda text: "".join(
            line for line in text.splitlines(keepends=True) if not line.startswith("image_store")
        ),
    )
    consumer_flag(consumer, "store", "classic")  # would differ, but the bundle does not say
    consumer_flag(consumer, "load-skew")

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "the bundle was made from sha256:" in result.stderr
    assert "were not compared" not in result.stdout


def test_bundle_records_the_image_store_of_the_host_that_made_it(tmp_path: Path) -> None:
    for store in ("containerd", "classic"):
        prod = Producer(tmp_path, version="0.1.0" if store == "containerd" else "0.1.1")
        prod.flag("store", store)
        manifest = manifest_text(prod.make())
        assert f"\nimage_store {store}\n" in manifest
    unknown = Producer(tmp_path, version="0.1.2")
    unknown.flag("store", "fail")
    manifest = manifest_text(unknown.make())
    assert "\nimage_store unknown\n" in manifest


def test_a_failed_docker_load_stops_the_install_and_cleans_up(
    consumer: Install, producer: Producer
) -> None:
    bundle = producer.make()
    consumer_flag(consumer, "load-fail")

    result = offline_install(consumer, bundle)

    assert result.returncode == 1
    assert "docker load failed" in result.stderr
    assert not (consumer.root / ".install-tmp").exists()


# ---------------------------------------------------------------- upgrade --offline [BUNDLE]


@pytest.fixture
def upgrade_store(upgrade: UpgradeFixture, tmp_path: Path) -> UpgradeFixture:
    state = tmp_path / "imgstate"
    state.mkdir()
    (state / "strict").write_text("")
    upgrade._stub("docker", with_store(DOCKER_STUB))
    return upgrade


def upgrade_bundle(tmp_path: Path, *, version: str = TARGET_VERSION) -> Path:
    prod = Producer(tmp_path, version=version, prefix=UPGRADE_PREFIX)
    return prod.make()


def loads(fixture: UpgradeFixture) -> list[str]:
    return [line for line in fixture.action_text().splitlines() if line.startswith("docker load")]


def test_upgrade_with_a_good_bundle_loads_it_before_anything_stops(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    bundle = upgrade_bundle(tmp_path)

    result = upgrade_store.run("upgrade", TARGET_VERSION, "--offline", str(bundle))

    assert result.returncode == 0, output(result)
    actions = upgrade_store.action_text()
    assert len(loads(upgrade_store)) == 1
    assert actions.index("docker load -i") < actions.index(" stop ")
    assert " pull " not in actions
    assert "bundle images loaded and checked against MANIFEST" in result.stdout
    assert upgrade_store.state()["installed_version"] == TARGET_VERSION
    assert not (upgrade_store.root / ".install-tmp").exists()


def test_upgrade_accepts_the_bundle_before_the_version_too(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    bundle = upgrade_bundle(tmp_path)

    result = upgrade_store.run("upgrade", "--offline", str(bundle), TARGET_VERSION)

    assert result.returncode == 0, output(result)
    assert len(loads(upgrade_store)) == 1


def test_upgrade_with_a_bundle_of_another_version_is_refused_before_loading(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    bundle = upgrade_bundle(tmp_path, version="0.3.0")

    result = upgrade_store.run("upgrade", TARGET_VERSION, "--offline", str(bundle))

    assert result.returncode == 1
    assert "is version 0.3.0 but the upgrade target is 0.2.0" in result.stderr
    assert loads(upgrade_store) == []
    assert " stop " not in upgrade_store.action_text()


def test_upgrade_with_a_tampered_bundle_loads_nothing(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    tampered = rewrite(upgrade_bundle(tmp_path), {"docker-compose.yml": b"services: {}\n"})

    result = upgrade_store.run("upgrade", TARGET_VERSION, "--offline", str(tampered))

    assert result.returncode == 1
    assert "checksums do not match; nothing was loaded" in result.stderr
    assert loads(upgrade_store) == []
    assert " stop " not in upgrade_store.action_text()


def test_upgrade_with_a_bundle_whose_redis_digest_cannot_be_restored_is_refused_before_stop(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    bundle = upgrade_bundle(tmp_path)
    (tmp_path / "imgstate" / "load-drop-digest").write_text("")
    (tmp_path / "imgstate" / "plain-no-resolve").write_text("")

    result = upgrade_store.run("upgrade", TARGET_VERSION, "--offline", str(bundle))

    assert result.returncode == 1
    assert f"{redis_reference()} does not resolve" in result.stderr
    assert " stop " not in upgrade_store.action_text()
    tags = [
        line.removeprefix("docker ")
        for line in upgrade_store.action_text().splitlines()
        if line.startswith("docker tag ")
    ]
    # never a digest tag, for redis and for the helper (both pins were dropped)
    assert tag_targets(tags) == [PLAIN_REDIS, plain_name(helper_reference())]
    assert not [line for line in tags if "@" in line]


def test_upgrade_with_a_lost_redis_digest_tags_it_back_and_continues(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    bundle = upgrade_bundle(tmp_path)
    (tmp_path / "imgstate" / "load-drop-digest").write_text("")

    result = upgrade_store.run("upgrade", TARGET_VERSION, "--offline", str(bundle))

    assert result.returncode == 0, output(result)
    helper_plain = plain_name(helper_reference())
    assert f"set the tag {PLAIN_REDIS} on it" in result.stdout
    assert f"set the tag {helper_plain} on it" in result.stdout
    tags = [
        line.removeprefix("docker ")
        for line in upgrade_store.action_text().splitlines()
        if line.startswith("docker tag ")
    ]
    assert tag_targets(tags) == [PLAIN_REDIS, helper_plain]  # never a digest tag
    assert not [line for line in tags if "@" in line]


def test_upgrade_with_a_bundle_for_another_prefix_is_refused_before_loading(
    upgrade_store: UpgradeFixture, tmp_path: Path
) -> None:
    prod = Producer(tmp_path, version=TARGET_VERSION)  # the default prefix
    bundle = prod.make()

    result = upgrade_store.run("upgrade", TARGET_VERSION, "--offline", str(bundle))

    assert result.returncode == 1
    assert UPGRADE_PREFIX in result.stderr and DEFAULT_PREFIX in result.stderr
    assert loads(upgrade_store) == []


def test_offline_upgrade_without_a_bundle_names_the_missing_image_and_the_bundle_argument(
    upgrade: UpgradeFixture,
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, "--offline", STUB_OFFLINE_MISSING="1")

    assert result.returncode == 1
    assert f"image {UPGRADE_PREFIX}/postgres:{TARGET_VERSION} is not loaded on this host" in (
        result.stderr
    )
    assert "pass the bundle: ./qradar-dash upgrade 0.2.0 --offline /path/to/" in result.stderr
    assert "qradar-dash-0.2.0-bundle.tar" in result.stderr
    assert "no container was stopped" in result.stderr
    assert " stop " not in upgrade.action_text()


def test_offline_upgrade_without_the_helper_image_names_it_and_suggests_the_bundle(
    upgrade: UpgradeFixture,
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, "--offline", STUB_HELPER_MISSING="1")

    assert result.returncode == 1
    ref = helper_reference()
    assert f"image {ref} is not loaded on this host" in result.stderr
    assert "pass the bundle: ./qradar-dash upgrade 0.2.0 --offline /path/to/" in result.stderr
    assert "no container was stopped" in result.stderr
    assert " stop " not in upgrade.action_text()


def test_offline_upgrade_reads_the_redis_reference_from_the_compose_file(
    upgrade: UpgradeFixture,
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, "--offline")

    assert result.returncode == 0, output(result)
    assert f"docker image inspect {redis_reference()}" in upgrade.action_text()
    assert f"docker image inspect {helper_reference()}" in upgrade.action_text()
