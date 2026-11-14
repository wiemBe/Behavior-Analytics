"""FIX-69: installer fixes from a first pre-production install.

Every test runs the real `qradar-dash` with fakes (docker, openssl, df, containerd) on a PATH in
`tmp_path`. Nothing touches this host's Docker, containerd or TLS tools.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit import test_qradar_dash_doctor as doctor_tests
from tests.unit.test_qradar_dash_installer import (  # noqa: F401
    RECORDED_NO,
    Install,
    _interactive,
    _stub,
    _wizard_replies,
    build_path,
    flow_path,
    hb_path,
    inst,
)

GIB_KIB = 1024 * 1024

DOCKER_STUB = """#!/bin/sh
case "$*" in
  "compose version --short") echo 2.30.0 ;;
  "info --format {{.DockerRootDir}}") cat "$HOME/droot" ;;
  "info --format {{json .DriverStatus}}") cat "$HOME/driverstatus" ;;
esac
exit 0
"""

# df keyed by the path asked about: $HOME/df.map holds "path free_kb device mount" lines; "*" is
# the fallback. Same mount in two lines means the same filesystem.
DF_STUB = """#!/bin/sh
for a in "$@"; do t=$a; done
while read -r path free dev mnt; do
  if [ "$path" = "$t" ] || [ "$path" = "*" ]; then
    printf 'Filesystem 1K-blocks Used Available Use%% Mounted on\\n'
    printf '%s 999999999999 1 %s 1%% %s\\n' "$dev" "$free" "$mnt"
    exit 0
  fi
done < "$HOME/df.map"
exit 1
"""

OPENSSL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/openssl.log"
case "$1" in rand) echo 0123456789abcdef0123456789abcdef01234567; exit 0 ;; esac
mode=$(cat "$HOME/ssl.mode" 2>/dev/null || echo ok)
case " $* " in
  *" -partial_chain "*) partial=1 ;;
  *) partial=0 ;;
esac
if [ "$mode" = mismatch ]; then
  echo "verify error:num=62:Hostname mismatch"
  echo "Verify return code: 62 (Hostname mismatch)"
elif [ "$mode" = selfsigned ] && [ "$partial" = 0 ]; then
  echo "verify error:num=20:unable to get local issuer certificate"
  echo "Verify return code: 20 (unable to get local issuer certificate)"
else
  echo "Verify return code: 0 (ok)"
fi
exit 0
"""

CONTAINERD_STUB = """#!/bin/sh
[ "$1 $2" = "config dump" ] || exit 1
[ -f "$HOME/containerd.out" ] || exit 1
cat "$HOME/containerd.out"
"""

CONTAINERD_DRIVER = '[["driver-type","io.containerd.snapshotter.v1"]]'
OVERLAY_DRIVER = '[["Backing Filesystem","xfs"],["Supports d_type","true"]]'


class Host:
    """A preflight world: docker root, containerd root, df map, driver status, ssl mode."""

    def __init__(self, fixture: Install, tmp_path: Path, path: str) -> None:
        self.inst, self.home, self.path = fixture, tmp_path, path
        self.droot = tmp_path / "docker-root"
        self.croot = tmp_path / "containerd-root"
        self.droot.mkdir()
        self.croot.mkdir()
        bin_dir = Path(path)
        for name, body in (("docker", DOCKER_STUB), ("df", DF_STUB), ("openssl", OPENSSL_STUB)):
            (bin_dir / name).unlink(missing_ok=True)  # may be a symlink to the host's tool
            _stub(bin_dir / name, body)
        (tmp_path / "droot").write_text(f"{self.droot}\n")
        self.driver(OVERLAY_DRIVER)
        self.df(docker=100, containerd=100)
        self.ssl("ok")

    def driver(self, status: str) -> None:
        (self.home / "driverstatus").write_text(status + "\n")

    def containerd(self, dump: bool = True) -> None:
        _stub(Path(self.path) / "containerd", CONTAINERD_STUB)
        if dump:
            (self.home / "containerd.out").write_text(
                f'version = 3\nroot = "{self.croot}"\nstate = "/run/containerd"\n'
            )

    def df(self, docker: int, containerd: int, same_fs: bool = False) -> None:
        cmnt = "/d" if same_fs else "/c"
        (self.home / "df.map").write_text(
            f"{self.droot} {docker * GIB_KIB} dev1 /d\n"
            f"{self.croot} {containerd * GIB_KIB} dev{'1' if same_fs else '2'} {cmnt}\n"
            f"* {999 * GIB_KIB} dev3 /\n"
        )

    def ssl(self, mode: str) -> None:
        (self.home / "ssl.mode").write_text(mode)

    def preflight(self, **answers: str | None) -> subprocess.CompletedProcess[str]:
        return self.inst.run(
            "install", "--non-interactive", str(self.inst.answers(**answers)),
            "--build", "--dry-run", path=self.path,
        )


@pytest.fixture
def host(inst: Install, tmp_path: Path, build_path: str) -> Host:  # noqa: F811
    return Host(inst, tmp_path, build_path)


# ---- 1. trimmed answers -----------------------------------------------------------------


@pytest.mark.parametrize("suffix", [" ", "\t", " \t ", "\r", "  \r"])
def test_non_interactive_values_are_trimmed(host: Host, suffix: str) -> None:
    files = host.inst.files
    host.ssl("ok")
    result = host.preflight(
        PUBLIC_HOSTNAME=f"soc.example.test{suffix}",
        TLS_CERT_FILE=f"{files / 'tls.crt'}{suffix}",
        QRADAR_CA_FILE=f"{suffix}{files / 'ca.pem'}",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Dry run: every check passed" in result.stdout


def test_inner_whitespace_is_still_refused(host: Host) -> None:
    result = host.preflight(PUBLIC_HOSTNAME="soc example.test")
    assert result.returncode == 2
    assert "must be a bare host name" in result.stderr


def test_a_refused_file_path_is_quoted_so_a_hidden_character_shows(host: Host) -> None:
    missing = host.inst.files / "tls crt"
    result = host.preflight(TLS_CERT_FILE=str(missing))
    assert result.returncode == 2
    assert f"TLS_CERT_FILE: file not readable: '{missing}'" in result.stderr
    result = host.preflight(QRADAR_TOKEN_FILE=str(host.inst.files / "nope"))
    assert f"file not readable: '{host.inst.files / 'nope'}'" in result.stderr


def test_the_wizard_trims_a_pasted_answer(inst: Install, tmp_path: Path, hb_path: str) -> None:  # noqa: F811
    (tmp_path / "hb_out").write_text(RECORDED_NO + "\n")
    replies = _wizard_replies(inst, "no")
    replies[0] = "soc.example.test \t"
    replies[2] = f"{replies[2]} "
    replies[7] = f"{replies[7]}\t"
    result = _interactive(inst, hb_path, replies)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "must be a bare host name" not in result.stdout
    assert "file not readable" not in result.stdout
    env = (inst.root / ".env").read_text()
    assert "PUBLIC_ORIGIN=https://soc.example.test\n" in env


def test_the_wizard_still_refuses_inner_whitespace(
    inst: Install, tmp_path: Path, hb_path: str  # noqa: F811
) -> None:
    (tmp_path / "hb_out").write_text(RECORDED_NO + "\n")
    replies = _wizard_replies(inst, "no")
    replies[0:1] = ["soc example", "soc.example.test"]
    result = _interactive(inst, hb_path, replies)
    assert "must be a bare host name" in result.stdout


# ---- 5. question 5 default --------------------------------------------------------------


def test_question_five_defaults_to_the_qradar_url_host(
    inst: Install, tmp_path: Path, hb_path: str  # noqa: F811
) -> None:
    (tmp_path / "hb_out").write_text(RECORDED_NO + "\n")
    replies = _wizard_replies(inst, "no")
    replies[4:6] = ["https://console.example:8443/", ""]
    result = _interactive(inst, hb_path, replies)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "press Enter to use the QRadar URL host" in result.stdout
    assert "Used only by the optional MCP profile" in result.stdout
    assert "5. QRadar console FQDN (host name, optional :port) [console.example]:" in result.stdout
    assert "QRADAR_CONSOLE_FQDN=console.example\n" in (inst.root / ".env").read_text()


# ---- 2. QRadar TLS preflight ------------------------------------------------------------


def test_preflight_accepts_a_self_signed_leaf_with_partial_chain(host: Host) -> None:
    host.ssl("selfsigned")  # the fake answers 20 unless -partial_chain is given
    result = host.inst.run(
        "install", "--non-interactive", str(host.inst.answers()), "--build", "--dry-run",
        path=host.path,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "presents a certificate the CA bundle verifies" in result.stdout
    call = (host.home / "openssl.log").read_text()
    assert "-partial_chain" in call
    assert "-verify_hostname qradar.example.test" in call


def test_preflight_uses_verify_ip_for_an_address(host: Host) -> None:
    result = host.preflight(QRADAR_URL="https://192.0.2.10:8443")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "-verify_ip 192.0.2.10" in (host.home / "openssl.log").read_text()


def test_preflight_fails_a_san_mismatch_naming_the_host(host: Host) -> None:
    host.ssl("mismatch")
    result = host.preflight(QRADAR_URL="https://10.90.34.30")
    assert result.returncode == 1
    assert "not valid for the host '10.90.34.30'" in result.stderr
    assert "Subject Alternative Name" in result.stderr
    assert "does not verify against" not in result.stderr


# ---- 3. disk: the containerd image store ------------------------------------------------


def test_classic_overlay2_checks_only_the_docker_root(host: Host) -> None:
    host.containerd()
    host.df(docker=100, containerd=1)  # would fail if the containerd root were checked
    result = host.preflight()
    assert result.returncode == 0, result.stderr + result.stdout
    assert "containerd" not in result.stdout + result.stderr
    assert "GiB free on the Docker root" in result.stdout


def test_containerd_checks_both_directories_and_says_what_each_holds(host: Host) -> None:
    host.driver(CONTAINERD_DRIVER)
    host.containerd()
    host.df(docker=100, containerd=25)
    result = host.preflight()
    assert result.returncode == 0, result.stderr + result.stdout
    out = result.stdout
    assert f"GiB free on the Docker root ({host.droot}: database, volumes, backup scratch)" in out
    assert f"25 GiB free on the containerd root ({host.croot}, from containerd config dump)" in out
    assert "holds images and layers" in out


def test_a_short_containerd_root_fails_and_names_it(host: Host) -> None:
    host.driver(CONTAINERD_DRIVER)
    host.containerd()
    host.df(docker=100, containerd=19)
    result = host.preflight()
    assert result.returncode == 1
    assert f"19 GiB free on the containerd root ({host.croot}" in result.stderr
    assert "at least 20 GiB" in result.stderr


def test_containerd_default_root_is_used_and_named_without_a_dump(host: Host) -> None:
    host.driver(CONTAINERD_DRIVER)  # no containerd binary on PATH
    (host.home / "df.map").write_text(
        f"{host.droot} {100 * GIB_KIB} dev1 /d\n/var/lib/containerd {30 * GIB_KIB} dev2 /c\n"
        f"* {999 * GIB_KIB} dev3 /\n"
    )
    result = host.preflight()
    assert result.returncode == 0, result.stderr + result.stdout
    assert "containerd root (/var/lib/containerd, from default" in result.stdout


def test_the_same_filesystem_needs_eighty_gib_not_twenty(host: Host) -> None:
    host.driver(CONTAINERD_DRIVER)
    host.containerd()
    host.df(docker=50, containerd=50, same_fs=True)
    result = host.preflight()
    assert result.returncode == 1
    assert "at least 80 GiB is required" in result.stderr
    host.df(docker=90, containerd=90, same_fs=True)
    ok = host.preflight()
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert "shares the filesystem of the Docker root" in ok.stdout


# ---- 3. doctor reports both -------------------------------------------------------------

DOCTOR_PREFIX = r"""#!/bin/bash
if [ "$1" = info ] && [ "$3" = '{{json .DriverStatus}}' ]; then
  printf '%s\n' "$STUB_DRIVER_STATUS"
  exit 0
fi
"""

DOCTOR_DF = r"""#!/bin/bash
for argument in "$@"; do target=$argument; done
if [ "$target" = "$STUB_DOCKER_ROOT" ]; then
  used=$STUB_DOCKER_USED; free=$STUB_DOCKER_FREE_KIB; mnt=/d
elif [ "$target" = "$STUB_CONTAINERD_ROOT" ]; then
  used=$STUB_CONTAINERD_USED; free=$STUB_CONTAINERD_FREE_KIB; mnt=${STUB_CONTAINERD_MOUNT:-/c}
elif [ "$target" = "$STUB_BACKUP_DIR" ]; then
  used=$STUB_BACKUP_USED; free=$STUB_BACKUP_FREE_KIB; mnt=/b
else
  exit 1
fi
printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
printf 'fixture 104857600 1 %s %s%% %s\n' "$free" "$used" "$mnt"
"""


def _doctor(tmp_path: Path, driver: str, **env: str) -> subprocess.CompletedProcess[str]:
    fixture = doctor_tests.DoctorFixture(tmp_path)
    croot = tmp_path / "containerd-root"
    croot.mkdir()
    fixture._stub("docker", DOCTOR_PREFIX + doctor_tests.DOCKER_STUB.split("\n", 1)[1])
    fixture._stub("df", DOCTOR_DF)
    fixture._stub(
        "containerd", f'#!/bin/bash\necho \'root = "{croot}"\'\n'
    )
    base = {
        "STUB_DRIVER_STATUS": driver,
        "STUB_CONTAINERD_ROOT": str(croot),
        "STUB_CONTAINERD_USED": "30",
        "STUB_CONTAINERD_FREE_KIB": str(30 * GIB_KIB),
    }
    return fixture.run(**{**base, **env})


def test_doctor_reports_the_docker_root_and_the_containerd_root(tmp_path: Path) -> None:
    result = _doctor(tmp_path, CONTAINERD_DRIVER)
    lines = result.stdout.splitlines()
    docker = next(x for x in lines if " disk_docker " in x)
    containerd = next(x for x in lines if " disk_containerd " in x)
    assert docker.startswith("PASS") and "Docker root: database, volumes" in docker
    assert containerd.startswith("PASS")
    assert "containerd root" in containerd and "images and layers" in containerd
    assert "containerd-root" in containerd


def test_doctor_fails_a_nearly_full_containerd_root(tmp_path: Path) -> None:
    result = _doctor(tmp_path, CONTAINERD_DRIVER, STUB_CONTAINERD_USED="95")
    line = next(x for x in result.stdout.splitlines() if " disk_containerd " in x)
    assert line.startswith("FAIL") and "OPS-R14" in line


def test_doctor_on_the_same_filesystem_counts_it_once(tmp_path: Path) -> None:
    result = _doctor(tmp_path, CONTAINERD_DRIVER, STUB_CONTAINERD_MOUNT="/d")
    line = next(x for x in result.stdout.splitlines() if " disk_containerd " in x)
    assert "shares the filesystem of the Docker root" in line


def test_doctor_on_classic_overlay2_has_no_containerd_line(tmp_path: Path) -> None:
    result = _doctor(tmp_path, OVERLAY_DRIVER)
    assert " disk_containerd " not in result.stdout
    assert " disk_docker " in result.stdout
