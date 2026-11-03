"""HLT-1: the per-host table and the HA pair rule in the QRadar health section.

The REST reads are driven through a mocked httpx transport with the shape the lab console
(7.6.0 FP1) returned. The service is driven through the mock provider with fixtures for every
pair state, so the summary counts, the EPS-once rule and the `doctor` lines are asserted on what
a caller receives. HA is proven with fixtures only: the lab console has no HA pair.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.cli import ariel_load as cli
from app.cli.doctor import Status
from app.providers.dto import (
    HEALTH_HOSTS_BOUND,
    HEALTH_SERVERS_BOUND,
    DeploymentHostDTO,
    ManagedServerDTO,
)
from app.providers.mock import MockQRadarProvider
from app.schemas.qradar_health import FieldState, HaPairState
from app.services.qradar_health import (
    classify_pair,
    host_role,
    normalize_status,
    servers_field,
)
from tests.unit.test_ariel_load_qradar import _current, _doctor
from tests.unit.test_qradar_health import (
    _BODIES,
    _instance,
    _rest,
    _Run,
    _unavailable,
)

# ------------------------------------------------------------------- fixtures


def _host(
    host_id: int,
    name: str,
    *,
    primary: int | None,
    secondary: int | None = None,
    average_eps: int | None = 100,
    peak_eps: int | None = 900,
    eps_allocation: int | None = 5000,
) -> DeploymentHostDTO:
    return DeploymentHostDTO(
        host_id=host_id,
        hostname=name,
        private_ip=f"10.0.0.{host_id}",
        version="7.6.0.0",
        status="Active",
        average_eps=average_eps,
        peak_eps=peak_eps,
        eps_allocation=eps_allocation,
        eps_rate_hardware_limit=100000,
        average_fpm=0,
        peak_fpm=0,
        fpm_allocation=200000,
        fpm_rate_hardware_limit=3600000,
        primary_server_id=primary,
        secondary_server_id=secondary,
        cpus=12,
        total_memory_kb=16071800,
    )


def _server(server_id: int, host_id: int, name: str, status: str | None) -> ManagedServerDTO:
    return ManagedServerDTO(
        hostname=name,
        status=status,
        server_id=server_id,
        managed_host_id=host_id,
        private_ip=f"10.0.1.{server_id}",
    )


def _pair(primary_status: str | None, secondary_status: str | None, **host: Any):
    """(hosts, servers) for one managed host that is an HA pair."""
    hosts = [_host(7, "ep-ha", primary=71, secondary=72, **host)]
    servers = [
        _server(71, 7, "ep-ha-a.local", primary_status),
        _server(72, 7, "ep-ha-b.local", secondary_status),
    ]
    return hosts, servers


async def _read(hosts, servers, **faults: Exception):
    provider = MockQRadarProvider()
    provider.health_hosts = hosts
    provider.health_servers = servers
    provider.health_faults.update(faults)
    return await _Run(provider).read()


async def _pair_read(primary_status: str | None, secondary_status: str | None):
    hosts, servers = _pair(primary_status, secondary_status)
    health = await _read(hosts, servers)
    assert health.hosts.value is not None and health.servers.value is not None
    return health.hosts.value.items[0], health.servers.value


# ---------------------------------------------------------------- REST reads

_LAB_HOST = {
    "eps_rate_hardware_limit": 100000,
    "appliance": {"id": "software", "type": "software"},
    "components": ["hostcontext", "magistrate"],
    "average_fpm": 0,
    "public_ip": "",
    "secondary_server_id": None,
    "average_eps": 119,
    "fpm_allocation": 200000,
    "private_ip": "192.168.122.50",
    "hostname": "qradarce2",
    "total_memory": 16071800,
    "primary_server_id": 51,
    "eps_allocation": 5000,
    "id": 53,
    "cpus": 12,
    "peak_eps": 1916,
    "license_serial_number": None,
    "peak_fpm": 0,
    "version": "7.6.0.0",
    "fpm_rate_hardware_limit": 3600000,
    "status": "Active",
}
_LAB_SERVER = {
    "hostname": "qradarce2.local",
    "managed_host_id": 53,
    "server_id": 51,
    "private_ip": "192.168.122.50",
    "status": "ACTIVE",
}


async def test_the_rest_reads_keep_the_lab_fields_and_the_bound_is_explicit() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers.get("Range")
        body = {
            "/api/system/servers": [_LAB_SERVER],
            "/api/config/deployment/hosts": [_LAB_HOST],
        }[request.url.path]
        return httpx.Response(200, json=body)

    provider, _sleep = _rest(handler)
    try:
        servers = await provider.list_managed_servers()
        hosts = await provider.list_deployment_hosts()
    finally:
        await provider.aclose()

    # One row more than the bound is asked for, so a longer answer shows.
    assert seen["/api/config/deployment/hosts"] == f"items=0-{HEALTH_HOSTS_BOUND}"
    assert seen["/api/system/servers"] == f"items=0-{HEALTH_SERVERS_BOUND}"
    [server] = servers
    assert (server.server_id, server.managed_host_id, server.private_ip) == (
        51,
        53,
        "192.168.122.50",
    )
    [host] = hosts
    assert (host.host_id, host.hostname, host.version, host.status) == (
        53,
        "qradarce2",
        "7.6.0.0",
        "Active",
    )
    assert (host.appliance_id, host.appliance_description) == ("software", None)
    assert (host.eps_allocation, host.eps_rate_hardware_limit) == (5000, 100000)
    assert (host.average_eps, host.peak_eps) == (119, 1916)
    assert (host.fpm_allocation, host.fpm_rate_hardware_limit) == (200000, 3600000)
    assert (host.primary_server_id, host.secondary_server_id) == (51, None)
    assert (host.cpus, host.total_memory_kb) == (12, 16071800)
    assert host.public_ip is None  # "" is not a value


async def test_a_field_the_answer_lacks_is_none_not_a_number() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/hosts"):
            return httpx.Response(200, json=[{"id": 9, "hostname": "bare"}])
        return httpx.Response(200, json=[{"hostname": "bare"}])

    provider, _sleep = _rest(handler)
    try:
        [host] = await provider.list_deployment_hosts()
        [server] = await provider.list_managed_servers()
    finally:
        await provider.aclose()
    assert host.model_dump(exclude={"host_id", "hostname"}) == {
        name: None
        for name in DeploymentHostDTO.model_fields
        if name not in {"host_id", "hostname"}
    }
    assert server.server_id is None and server.managed_host_id is None and server.status is None


async def test_the_unchanged_bodies_still_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BODIES[request.url.path])

    provider, _sleep = _rest(handler)
    try:
        assert len(await provider.list_deployment_hosts()) == 1
    finally:
        await provider.aclose()


# --------------------------------------------------------------- pair states


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Active", "ACTIVE"),
        (" standby ", "STANDBY"),
        ("Needs Upgrade", "NEEDS_UPGRADE"),
        ("setting-offline", "SETTING_OFFLINE"),
        ("", None),
        (None, None),
    ],
)
def test_statuses_are_normalized_the_way_ibm_spells_them(raw, normalized) -> None:
    assert normalize_status(raw) == normalized


@pytest.mark.parametrize(
    ("primary", "secondary", "roles_known", "state"),
    [
        ("ACTIVE", "STANDBY", True, HaPairState.HA_HEALTHY),
        ("ACTIVE", "OFFLINE", True, HaPairState.HA_DEGRADED),
        ("ACTIVE", "FAILED", True, HaPairState.HA_DEGRADED),
        ("ACTIVE", "SYNCHRONIZING", True, HaPairState.HA_DEGRADED),
        ("ACTIVE", "NEEDS_UPGRADE", True, HaPairState.HA_DEGRADED),
        ("ACTIVE", "UPGRADING", True, HaPairState.HA_DEGRADED),
        ("ACTIVE", "RESTORING", True, HaPairState.HA_DEGRADED),
        ("ACTIVE", "NEEDS_LICENSE", True, HaPairState.HA_DEGRADED),
        ("FAILED", "ACTIVE", True, HaPairState.HA_FAILED_OVER),
        ("OFFLINE", "ACTIVE", True, HaPairState.HA_FAILED_OVER),
        # A repaired primary comes back as standby while the secondary still runs.
        ("STANDBY", "ACTIVE", True, HaPairState.HA_FAILED_OVER),
        ("FAILED", "FAILED", True, HaPairState.HA_FAILED),
        ("OFFLINE", "STANDBY", True, HaPairState.HA_FAILED),
        ("ACTIVE", "ACTIVE", True, HaPairState.UNKNOWN),
        ("ACTIVE", "ACTIVE_UPGRADING", True, HaPairState.UNKNOWN),
        ("ACTIVE", "UNKNOWN", True, HaPairState.UNKNOWN),
        ("ACTIVE", None, True, HaPairState.UNKNOWN),
        (None, None, True, HaPairState.UNKNOWN),
        # Without roles only a verdict that does not need them is given.
        ("ACTIVE", "STANDBY", False, HaPairState.UNKNOWN),
        ("STANDBY", "ACTIVE", False, HaPairState.UNKNOWN),
        ("FAILED", "OFFLINE", False, HaPairState.HA_FAILED),
    ],
)
def test_the_pair_state_table(primary, secondary, roles_known, state) -> None:
    assert classify_pair(primary, secondary, roles_known=roles_known) is state


async def test_one_host_without_ha_is_single_and_active() -> None:
    health = await _read(
        [_host(53, "qradarce2", primary=51)], [_server(51, 53, "qradarce2.local", "ACTIVE")]
    )
    hosts = health.hosts.value
    assert hosts is not None and (hosts.hosts, hosts.ha_pairs, hosts.ha_problem) == (1, 0, 0)
    [entry] = hosts.items
    assert entry.pair_state is HaPairState.SINGLE
    [member] = entry.members
    assert (member.server_id, member.role, member.hostname) == (
        51,
        "primary",
        "qradarce2.local",
    )
    assert member.status.value == "ACTIVE" and member.private_ip == "10.0.1.51"
    assert (entry.hostname, entry.version, entry.cpus) == ("qradarce2", "7.6.0.0", 12)
    assert entry.eps.value is not None and entry.eps.value.allocation == 5000
    assert entry.eps.value.hardware_limit == 100000 and entry.eps.value.peak == 900
    servers = health.servers.value
    assert servers is not None and servers.all_active and (servers.total, servers.active) == (1, 1)
    assert (servers.ha_pairs, servers.ha_problem, servers.ha_issues) == (0, 0, [])


async def test_a_console_and_two_managed_hosts_are_three_rows() -> None:
    hosts = [
        _host(1, "console", primary=11),
        _host(2, "ep-1", primary=12, average_eps=200),
        _host(3, "ep-2", primary=13, average_eps=300),
    ]
    servers = [
        _server(11, 1, "console.local", "ACTIVE"),
        _server(12, 2, "ep-1.local", "ACTIVE"),
        _server(13, 3, "ep-2.local", "DOWN"),
    ]
    health = await _read(hosts, servers)
    table = health.hosts.value
    assert table is not None and [e.hostname for e in table.items] == ["console", "ep-1", "ep-2"]
    summary = health.servers.value
    assert summary is not None and (summary.total, summary.active) == (3, 2)
    assert [(e.hostname, e.status) for e in summary.not_active] == [("ep-2", "DOWN")]
    assert health.eps.value is not None and health.eps.value.hosts == 3
    assert health.eps.value.average_eps == 100 + 200 + 300


async def test_a_healthy_pair_standby_is_never_not_active_and_eps_counts_once() -> None:
    hosts, servers = _pair("ACTIVE", "STANDBY", average_eps=1000, eps_allocation=8000)
    health = await _read(hosts, servers)
    summary = health.servers.value
    assert summary is not None
    assert (summary.total, summary.active, summary.all_active) == (1, 1, True)
    assert summary.not_active == [] and summary.ha_issues == []
    assert (summary.ha_pairs, summary.ha_problem) == (1, 0)
    # By status counts the members, so a pair shows both.
    assert summary.by_status == {"ACTIVE": 1, "STANDBY": 1}
    table = health.hosts.value
    assert table is not None and (table.ha_pairs, table.ha_problem) == (1, 0)
    [entry] = table.items
    assert entry.pair_state is HaPairState.HA_HEALTHY
    assert [(m.role, m.server_id, m.status.value) for m in entry.members] == [
        ("primary", 71, "ACTIVE"),
        ("secondary", 72, "STANDBY"),
    ]
    # The managed host's allocation and average are counted once, not once per member.
    assert entry.eps.value is not None and entry.eps.value.allocation == 8000
    assert entry.eps.value.average == 1000
    eps = health.eps.value
    assert eps is not None and (eps.hosts, eps.average_eps, eps.allocated_eps) == (1, 1000, 8000)


async def test_a_pair_with_the_standby_offline_is_degraded_and_still_active() -> None:
    entry, summary = await _pair_read("ACTIVE", "OFFLINE")
    assert entry.pair_state is HaPairState.HA_DEGRADED
    assert (summary.total, summary.active, summary.not_active) == (1, 1, [])
    assert (summary.ha_pairs, summary.ha_problem) == (1, 1)
    [issue] = summary.ha_issues
    assert (issue.hostname, issue.status, issue.ha_state) == (
        "ep-ha",
        "ACTIVE / OFFLINE",
        HaPairState.HA_DEGRADED,
    )


async def test_a_failed_over_pair_runs_on_the_secondary() -> None:
    entry, summary = await _pair_read("FAILED", "ACTIVE")
    assert entry.pair_state is HaPairState.HA_FAILED_OVER
    assert [(m.role, m.status.value) for m in entry.members] == [
        ("primary", "FAILED"),
        ("secondary", "ACTIVE"),
    ]
    assert (summary.active, summary.not_active, summary.ha_problem) == (1, [], 1)
    assert summary.ha_issues[0].ha_state is HaPairState.HA_FAILED_OVER


async def test_a_pair_with_no_active_member_is_failed_and_not_active() -> None:
    entry, summary = await _pair_read("FAILED", "OFFLINE")
    assert entry.pair_state is HaPairState.HA_FAILED
    assert (summary.total, summary.active, summary.all_active) == (1, 0, False)
    assert [(e.hostname, e.status, e.ha_state) for e in summary.not_active] == [
        ("ep-ha", "FAILED / OFFLINE", HaPairState.HA_FAILED)
    ]
    assert summary.ha_problem == 1


async def test_a_status_string_outside_ibms_list_is_unknown_and_shown_as_written() -> None:
    entry, summary = await _pair_read("ACTIVE", "Active_Upgrading")
    assert entry.pair_state is HaPairState.UNKNOWN
    assert entry.unclassified == ["ACTIVE", "ACTIVE_UPGRADING"]
    # The primary serves, so the host is active; the pair is still reported.
    assert (summary.active, summary.ha_problem) == (1, 1)
    assert summary.ha_issues[0].ha_state is HaPairState.UNKNOWN


async def test_two_active_members_are_not_healthy_and_not_counted_active() -> None:
    entry, summary = await _pair_read("ACTIVE", "ACTIVE")
    assert entry.pair_state is HaPairState.UNKNOWN
    assert (summary.active, summary.all_active, summary.ha_problem) == (0, False, 1)
    assert summary.not_active[0].status == "ACTIVE / ACTIVE"


async def test_a_second_server_missing_from_system_servers_is_unknown_not_single() -> None:
    hosts = [_host(7, "ep-ha", primary=71, secondary=72)]
    servers = [_server(71, 7, "ep-ha-a.local", "ACTIVE")]
    health = await _read(hosts, servers)
    assert health.hosts.value is not None and health.servers.value is not None
    [entry] = health.hosts.value.items
    assert entry.pair_state is HaPairState.UNKNOWN
    primary, secondary = entry.members
    assert primary.status.value == "ACTIVE"
    assert secondary.role == "secondary" and secondary.server_id == 72
    assert secondary.status.state is FieldState.NOT_COLLECTED
    assert secondary.status.value is None and secondary.status.reason_code == "field_missing"
    summary = health.servers.value
    assert (summary.ha_pairs, summary.ha_problem, summary.unreported) == (1, 1, 1)
    assert summary.ha_issues[0].status == "ACTIVE / no status"


async def test_a_managed_host_with_no_server_record_is_never_counted_active() -> None:
    hosts = [_host(1, "a", primary=11), _host(2, "b", primary=12)]
    servers = [_server(11, 1, "a.local", "ACTIVE")]
    health = await _read(hosts, servers)
    summary = health.servers.value
    assert summary is not None and (summary.total, summary.active) == (2, 1)
    assert summary.all_active is False and summary.unreported == 1
    assert [(e.hostname, e.status) for e in summary.not_active] == [("b", None)]


# ----------------------------------------------------------------- the bound


async def test_more_hosts_than_the_bound_is_partial_with_the_count_read() -> None:
    hosts = [_host(i, f"h{i}", primary=1000 + i) for i in range(1, HEALTH_HOSTS_BOUND + 2)]
    servers = [_server(1000 + i, i, f"h{i}.local", "ACTIVE") for i in range(1, 102)]
    health = await _read(hosts, servers)
    table = health.hosts.value
    assert table is not None and table.truncated is True
    assert table.hosts == HEALTH_HOSTS_BOUND == len(table.items)
    assert health.eps.value is not None and health.eps.value.truncated is True
    assert health.eps.value.hosts == HEALTH_HOSTS_BOUND
    summary = health.servers.value
    assert summary is not None and summary.truncated is False


async def test_a_full_answer_at_the_bound_is_not_partial() -> None:
    hosts = [_host(i, f"h{i}", primary=1000 + i) for i in range(1, HEALTH_HOSTS_BOUND + 1)]
    servers = [_server(1000 + i, i, f"h{i}.local", "ACTIVE") for i in range(1, 101)]
    health = await _read(hosts, servers)
    assert health.hosts.value is not None and health.hosts.value.truncated is False
    assert health.servers.value is not None and health.servers.value.all_active


async def test_a_server_list_cut_at_its_bound_never_claims_all_active() -> None:
    rows = [_server(i, i, f"h{i}", "ACTIVE") for i in range(1, HEALTH_SERVERS_BOUND + 2)]
    value = servers_field(rows[:HEALTH_SERVERS_BOUND], truncated=True).value
    assert value is not None and value.truncated is True
    assert value.active == value.total and value.all_active is False
    health = await _read([_host(1, "h1", primary=1)], rows)
    assert health.servers.value is not None and health.servers.value.truncated is True
    assert health.servers.value.total == HEALTH_SERVERS_BOUND


# ------------------------------------------------- one read failing, the other not


async def test_servers_failed_and_hosts_ok_leaves_the_table_with_unread_members() -> None:
    hosts, _servers = _pair("ACTIVE", "STANDBY")
    health = await _read(hosts, [], list_managed_servers=_unavailable(500))
    assert health.servers.state is FieldState.FAILED
    assert health.servers.reason_code == "upstream_error"
    table = health.hosts.value
    assert table is not None and health.eps.state is FieldState.OK
    [entry] = table.items
    assert entry.eps.state is FieldState.OK
    assert entry.pair_state is HaPairState.UNKNOWN
    for member in entry.members:
        assert member.status.state is FieldState.FAILED
        assert member.status.value is None
        assert member.status.reason_code == "upstream_error"


async def test_hosts_failed_and_servers_ok_still_counts_by_managed_host() -> None:
    _hosts, servers = _pair("ACTIVE", "STANDBY")
    health = await _read([], servers, list_deployment_hosts=_unavailable(500))
    assert health.hosts.state is FieldState.FAILED and health.eps.state is FieldState.FAILED
    summary = health.servers.value
    assert summary is not None
    # Two records with one managed_host_id are one host. Without the deployment record the
    # roles are unknown, so an ACTIVE + STANDBY pair is not called healthy.
    assert summary.total == 1 and summary.active == 1 and summary.ha_pairs == 1
    assert summary.ha_problem == 1
    assert summary.ha_issues[0].ha_state is HaPairState.UNKNOWN
    assert summary.not_active == []


async def test_hosts_failed_and_no_active_member_is_still_failed() -> None:
    _hosts, servers = _pair("FAILED", "FAILED")
    health = await _read([], servers, list_deployment_hosts=_unavailable(500))
    summary = health.servers.value
    assert summary is not None and summary.active == 0
    assert summary.ha_issues[0].ha_state is HaPairState.HA_FAILED


async def test_both_reads_empty_or_failed_say_so() -> None:
    health = await _read([], [])
    assert health.hosts.reason_code == "empty_response"
    assert health.servers.reason_code == "empty_response"
    health = await _read(
        [],
        [],
        list_managed_servers=_unavailable(500),
        list_deployment_hosts=_unavailable(500),
    )
    assert health.hosts.state is FieldState.FAILED and health.servers.state is FieldState.FAILED


async def test_an_unreachable_console_skips_the_hosts_section_too() -> None:
    health = await _read([], [], probe_about=_unavailable())
    assert health.hosts.state is FieldState.NOT_COLLECTED
    assert health.hosts.reason_code == "console_unreachable"


async def test_a_host_without_rates_has_no_rate_part_and_no_zero() -> None:
    bare = DeploymentHostDTO(host_id=1, hostname="bare", primary_server_id=11)
    health = await _read([bare], [_server(11, 1, "bare.local", "ACTIVE")])
    [entry] = health.hosts.value.items  # type: ignore[union-attr]
    assert entry.eps.state is FieldState.NOT_COLLECTED
    assert entry.eps.value is None and entry.eps.reason_code == "field_missing"
    assert entry.fpm.value is None
    assert (entry.version, entry.cpus, entry.total_memory_kb) == (None, None, None)
    # The section-level EPS sum refuses to add a missing figure as 0.
    assert health.eps.state is FieldState.NOT_COLLECTED


async def test_the_section_serializes_with_every_state_and_no_secret() -> None:
    hosts, servers = _pair("ACTIVE", "OFFLINE")
    health = await _read(hosts, servers)
    dumped = health.model_dump(mode="json")
    assert dumped["hosts"]["value"]["items"][0]["pair_state"] == "ha_degraded"
    assert dumped["servers"]["value"]["ha_issues"][0]["ha_state"] == "ha_degraded"
    # The fields the page reads today are still where they were.
    for key in ("total", "active", "unreported", "by_status", "not_active", "all_active"):
        assert key in dumped["servers"]["value"]


# ------------------------------------------------------------------- the doctor


def _doctor_provider(hosts, servers) -> MockQRadarProvider:
    provider = MockQRadarProvider()
    provider.health_hosts = hosts
    provider.health_servers = servers
    return provider


async def _doctor_line(primary: str | None, secondary: str | None):
    hosts, servers = _pair(primary, secondary)
    return await _doctor(_doctor_provider(hosts, servers), _instance()).check_qradar_health()


async def test_the_doctor_does_not_warn_for_the_standby_of_a_healthy_pair() -> None:
    result = await _doctor_line("ACTIVE", "STANDBY")
    assert result.status is Status.PASS and result.runbook is None
    assert "1 of 1 managed hosts ACTIVE" in result.message and "1 HA pair(s)" in result.message
    assert "not ACTIVE" not in result.message and "STANDBY" not in result.message


@pytest.mark.parametrize(
    ("primary", "secondary", "status", "needle"),
    [
        ("ACTIVE", "OFFLINE", Status.WARN, "HA pair ep-ha degraded"),
        ("ACTIVE", "FAILED", Status.WARN, "no failover is possible (ACTIVE / FAILED)"),
        ("OFFLINE", "ACTIVE", Status.WARN, "HA pair ep-ha runs on its secondary"),
        ("ACTIVE", "Mystery", Status.WARN, "HA pair ep-ha is in a state that cannot be"),
        ("FAILED", "FAILED", Status.FAIL, "HA pair ep-ha failed: no member is ACTIVE"),
    ],
)
async def test_the_doctor_names_the_host_of_each_unhealthy_pair(
    primary, secondary, status, needle
) -> None:
    result = await _doctor_line(primary, secondary)
    assert result.status is status and result.runbook == "OPS-R67"
    assert needle in result.message
    # The pair is named once, as a pair, never also as "managed host(s) not ACTIVE".
    assert "managed host(s) not ACTIVE" not in result.message


async def test_a_failed_pair_and_a_down_single_host_are_both_reported() -> None:
    hosts = [_host(7, "ep-ha", primary=71, secondary=72), _host(8, "solo", primary=81)]
    servers = [
        _server(71, 7, "a", "FAILED"),
        _server(72, 7, "b", "FAILED"),
        _server(81, 8, "solo.local", "DOWN"),
    ]
    result = await _doctor(_doctor_provider(hosts, servers), _instance()).check_qradar_health()
    assert result.status is Status.FAIL
    assert "HA pair ep-ha failed" in result.message
    assert "1 managed host(s) not ACTIVE (solo DOWN)" in result.message


async def test_the_doctor_leaves_the_hosts_it_could_not_read_unclaimed() -> None:
    hosts, _servers = _pair("ACTIVE", "STANDBY")
    provider = _doctor_provider(hosts, [])
    provider.health_faults["list_managed_servers"] = _unavailable(500)
    result = await _doctor(provider, _instance()).check_qradar_health()
    assert result.status is Status.PASS
    assert "not read: managed hosts (FAILED)" in result.message
    assert "HA pair" not in result.message


# ----------------------------------------------- round 1: the console role


async def test_the_rest_read_keeps_the_component_list_and_none_when_it_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _LAB_HOST,
                {"id": 2, "hostname": "no-list"},
                {"id": 3, "hostname": "bad-list", "components": "magistrate"},
                {"id": 4, "hostname": "empty", "components": []},
            ],
        )

    provider, _sleep = _rest(handler)
    try:
        hosts = await provider.list_deployment_hosts()
    finally:
        await provider.aclose()
    by_name = {h.hostname: h for h in hosts}
    assert by_name["qradarce2"].components is not None
    assert "magistrate" in by_name["qradarce2"].components
    assert by_name["no-list"].components is None
    assert by_name["bad-list"].components is None
    assert by_name["empty"].components == []


@pytest.mark.parametrize(
    ("components", "role"),
    [
        (["hostcontext", "eventprocessor", "magistrate", "vis"], "console"),
        (["Magistrate"], "console"),
        (["hostcontext", "eventcollector", "eventprocessor"], "managed"),
        ([], None),
        (None, None),
    ],
)
def test_the_role_comes_from_the_magistrate_and_never_from_a_guess(components, role) -> None:
    assert host_role(components) == role


async def test_the_table_marks_the_console_the_managed_host_and_the_unknown() -> None:
    hosts = [
        _host(1, "console", primary=11).model_copy(
            update={"components": ["hostcontext", "magistrate"]}
        ),
        _host(2, "ep", primary=12).model_copy(update={"components": ["eventprocessor"]}),
        _host(3, "unlisted", primary=13),
    ]
    servers = [_server(10 + i, i, f"h{i}", "ACTIVE") for i in (1, 2, 3)]
    health = await _read(hosts, servers)
    assert health.hosts.value is not None
    assert [e.role for e in health.hosts.value.items] == ["console", "managed", None]
    dumped = health.model_dump(mode="json")["hosts"]["value"]["items"]
    assert [i["role"] for i in dumped] == ["console", "managed", None]
    assert all("components" not in i for i in dumped)


# --------------------------------------------------- round 1: members_total


async def test_members_total_counts_members_and_the_other_counts_stay_host_counts() -> None:
    hosts = [_host(1, "console", primary=11), _host(2, "ep", primary=12)]
    hosts.append(_host(7, "ep-ha", primary=71, secondary=72))
    servers = [
        _server(11, 1, "console.local", "ACTIVE"),
        _server(12, 2, "ep.local", "ACTIVE"),
        _server(71, 7, "ha-a", "ACTIVE"),
        _server(72, 7, "ha-b", "STANDBY"),
    ]
    health = await _read(hosts, servers)
    summary = health.servers.value
    assert summary is not None
    assert (summary.total, summary.active, summary.members_total) == (3, 3, 4)
    assert summary.by_status == {"ACTIVE": 3, "STANDBY": 1}
    assert sum(summary.by_status.values()) + summary.unreported == summary.members_total
    assert (summary.ha_pairs, summary.ha_problem) == (1, 0)
    assert health.model_dump(mode="json")["servers"]["value"]["members_total"] == 4


async def test_a_member_whose_record_is_missing_still_counts_in_members_total() -> None:
    hosts = [_host(7, "ep-ha", primary=71, secondary=72)]
    health = await _read(hosts, [_server(71, 7, "ha-a", "ACTIVE")])
    summary = health.servers.value
    assert summary is not None and (summary.total, summary.members_total) == (1, 2)
    assert summary.unreported == 1 and summary.by_status == {"ACTIVE": 1}


async def test_single_hosts_have_one_member_each() -> None:
    health = await _read(
        [_host(1, "a", primary=11), _host(2, "b", primary=12)],
        [_server(11, 1, "a", "ACTIVE"), _server(12, 2, "b", "DOWN")],
    )
    assert health.servers.value is not None
    assert (health.servers.value.total, health.servers.value.members_total) == (2, 2)


# ---------------------------------------------------------- round 1: the CLI


async def _cli_entry(primary: str, secondary: str):
    hosts, servers = _pair(primary, secondary)
    return (await _read(hosts, servers)).model_dump(mode="json")


async def test_the_cli_text_names_a_degraded_pair_with_both_statuses() -> None:
    text = cli.render_current_table(_current(await _cli_entry("ACTIVE", "OFFLINE")))
    assert "managed hosts: 1 of 1 ACTIVE" in text
    assert "  HA pairs: 1, 1 not healthy" in text
    assert "    ep-ha ha_degraded: ACTIVE / OFFLINE" in text


async def test_the_cli_text_names_a_failed_over_pair() -> None:
    text = cli.render_current_table(_current(await _cli_entry("FAILED", "ACTIVE")))
    assert "    ep-ha ha_failed_over: FAILED / ACTIVE" in text


async def test_the_cli_text_names_a_failed_pair_once_in_each_place() -> None:
    text = cli.render_current_table(_current(await _cli_entry("FAILED", "OFFLINE")))
    assert "managed hosts: 0 of 1 ACTIVE (ep-ha FAILED / OFFLINE)" in text
    assert "    ep-ha ha_failed: FAILED / OFFLINE" in text


async def test_the_cli_text_says_nothing_about_a_healthy_pair_but_counts_it() -> None:
    text = cli.render_current_table(_current(await _cli_entry("ACTIVE", "STANDBY")))
    assert "  HA pairs: 1, 0 not healthy" in text
    assert "ha_healthy" not in text and "STANDBY" not in text


async def test_the_cli_text_has_no_ha_line_without_a_pair() -> None:
    entry = (await _read([_host(1, "a", primary=11)], [_server(11, 1, "a", "ACTIVE")])).model_dump(
        mode="json"
    )
    assert "HA pair" not in cli.render_current_table(_current(entry))


async def test_the_cli_text_counts_pairs_beyond_the_listed_issues() -> None:
    entry = await _cli_entry("ACTIVE", "OFFLINE")
    entry["servers"]["value"]["ha_problem"] = 25
    assert "    and 24 more" in cli.render_current_table(_current(entry))


@pytest.mark.parametrize(
    ("primary", "secondary", "state", "status"),
    [
        ("ACTIVE", "OFFLINE", "ha_degraded", "ACTIVE / OFFLINE"),
        ("FAILED", "ACTIVE", "ha_failed_over", "FAILED / ACTIVE"),
    ],
)
def test_the_cli_json_carries_the_issue_with_both_statuses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    primary,
    secondary,
    state,
    status,
) -> None:
    # The command runs its own event loop, so this test is not async.
    current = _current(asyncio.run(_cli_entry(primary, secondary)))

    async def combined(_session: object) -> dict[str, Any]:
        return current

    class _Maker:
        def __call__(self) -> Any:
            class _Ctx:
                async def __aenter__(self) -> None:
                    return None

                async def __aexit__(self, *_a: object) -> None:
                    return None

            return _Ctx()

    async def dispose() -> None:
        return None

    monkeypatch.setattr(cli, "load_current_with_health", combined)
    monkeypatch.setattr("app.core.database.get_sessionmaker", lambda: _Maker())
    monkeypatch.setattr("app.core.database.dispose_engine", dispose)
    assert cli.main(["--current", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)["qradar"]["consoles"][0]["servers"]["value"]
    assert value["ha_problem"] == 1
    assert [(i["hostname"], i["ha_state"], i["status"]) for i in value["ha_issues"]] == [
        ("ep-ha", state, status)
    ]
