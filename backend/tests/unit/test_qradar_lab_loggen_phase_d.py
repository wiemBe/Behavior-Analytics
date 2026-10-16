"""Vendor-native Phase D IPS profiles for the isolated QRadar lab."""

from __future__ import annotations

import csv
import importlib.util
import io
import ipaddress
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).parents[3] / "tools" / "qradar_lab_loggen.py"
SPEC = importlib.util.spec_from_file_location("qradar_lab_loggen_phase_d", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
loggen = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = loggen
SPEC.loader.exec_module(loggen)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
PROTECTED_NETWORK = ipaddress.ip_network("10.10.20.0/24")


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def options(profile: str, scenario: str, *, count: int = 4_000, seed: int = 42):
    return loggen.Options(
        profiles=(profile,),
        scenario=scenario,
        output_format="vendor",
        count=count,
        seed=seed,
        dry_run=True,
        stdout=True,
    )


def messages(profile: str, scenario: str, *, count: int = 4_000) -> list[str]:
    opts = options(profile, scenario, count=count)
    loggen.validate_options(opts)
    generator = loggen.EventGenerator(opts, clock=lambda: NOW)
    return [generator.generate(index) for index in range(count)]


def pan_fields(message: str) -> dict[str, str]:
    row = next(csv.reader([loggen.syslog_body(message)]))
    assert len(row) == len(loggen.PAN_THREAT_FIELD_NAMES)
    return dict(zip(loggen.PAN_THREAT_FIELD_NAMES, row, strict=True))


def ftd_fields(message: str) -> dict[str, str]:
    body = loggen.syslog_body(message)
    _, separator, payload = body.partition("%FTD-1-430001: ")
    assert separator
    return dict(item.split(": ", 1) for item in payload.split(", "))


def parsed(profile: str, scenario: str, *, count: int = 4_000) -> list[dict[str, str]]:
    parser = pan_fields if profile == "pan_threat" else ftd_fields
    return [parser(message) for message in messages(profile, scenario, count=count)]


def value(rows: list[dict[str, str]], profile: str, pan: str, ftd: str) -> list[str]:
    key = pan if profile == "pan_threat" else ftd
    return [row[key] for row in rows]


def is_blocked(profile: str, row: dict[str, str]) -> bool:
    if profile == "pan_threat":
        return row["action"] in {
            "drop", "reset-client", "reset-server", "reset-both", "block-ip"
        }
    return row["InlineResult"] == "Blocked"


# Palo Alto Networks, "Threat Log Fields" (PAN-OS 11.1), "Format:" entry, copied
# for the first 36 fields (through Direction). The page publishes no example
# line, so the order is pinned here by position instead of by a reference row.
PAN_DOCUMENTED_PREFIX = (  # noqa: SIM905
    "FUTURE_USE, Receive Time, Serial Number, Type, Threat/Content Type, FUTURE_USE, "
    "Generated Time, Source Address, Destination Address, NAT Source IP, NAT Destination IP, "
    "Rule Name, Source User, Destination User, Application, Virtual System, Source Zone, "
    "Destination Zone, Inbound Interface, Outbound Interface, Log Action, FUTURE_USE, "
    "Session ID, Repeat Count, Source Port, Destination Port, NAT Source Port, "
    "NAT Destination Port, Flags, IP Protocol, Action, URL/Filename, Threat ID, Category, "
    "Severity, Direction"
).split(", ")
PAN_DOCUMENTED_TOTAL = 123  # documented field count through Cluster Name


def test_pan_renderer_follows_the_documented_field_order() -> None:
    message = messages("pan_threat", "ips-baseline", count=1)[0]
    generated = next(csv.reader([loggen.syslog_body(message)]))

    assert len(generated) == PAN_DOCUMENTED_TOTAL == len(loggen.PAN_THREAT_FIELD_NAMES)
    assert len(PAN_DOCUMENTED_PREFIX) == 36
    # Every documented slot is at its documented index: FUTURE_USE slots carry
    # the names' reserved positions, the rest are mapped by position.
    expected = {
        1: "receive_time", 2: "serial_number", 3: "type", 4: "subtype",
        6: "generated_time", 7: "source_address", 8: "destination_address",
        11: "rule_name", 14: "application", 20: "log_action", 22: "session_id",
        24: "source_port", 25: "destination_port", 29: "ip_protocol", 30: "action",
        32: "threat_id", 33: "category", 34: "severity", 35: "direction",
        107: "high_resolution_timestamp", 122: "cluster_name",
    }
    for position, name in expected.items():
        assert loggen.PAN_THREAT_FIELD_NAMES[position] == name
    assert [position for position, doc in enumerate(PAN_DOCUMENTED_PREFIX)
            if doc == "FUTURE_USE"] == [0, 5, 21]
    for position in (0, 5, 21):
        assert loggen.PAN_THREAT_FIELD_NAMES[position].startswith("future_use")


def test_pan_renderer_value_shapes() -> None:
    row = pan_fields(messages("pan_threat", "ips-baseline", count=1)[0])
    assert re.fullmatch(r"\d{4}/\d\d/\d\d \d\d:\d\d:\d\d", row["receive_time"])
    assert row["generated_time"] == row["receive_time"]
    assert row["type"] == "THREAT"
    assert row["subtype"] in {"vulnerability", "spyware", "url"}
    assert row["action"] in {
        "alert", "allow", "drop", "reset-client", "reset-server", "reset-both", "block-ip"
    }
    assert re.fullmatch(r".+\(\d+\)", row["threat_id"])
    assert row["severity"] in {"informational", "low", "medium", "high", "critical"}
    assert row["direction"] in {"client-to-server", "server-to-client"}
    assert row["ip_protocol"] in {"tcp", "udp"}


# Cisco Secure Firewall Threat Defense Syslog Messages, "Intrusion Event Field
# Descriptions" (430001). Keys the page lists, as emitted on the wire.
CISCO_DOCUMENTED_KEYS = {
    "AccessControlRuleName", "ACPolicy", "ApplicationProtocol", "Classification", "Client",
    "DeviceUUID", "DstIP", "DstPort", "EgressInterface", "EgressZone", "FirstPacketSecond",
    "GID", "IngressInterface", "IngressZone", "InlineResult", "IntrusionPolicy", "Message",
    "NAPPolicy", "Priority", "Protocol", "Revision", "SID", "SrcIP", "SrcPort", "User",
}
# Keys named by the T-029 spec or by operator knowledge but absent from that page.
FTD_UNVERIFIED_KEYS = {
    "EventPriority", "InstanceID", "ConnectionID", "AccessControlRuleAction",
    "IngressVRF", "EgressVRF",
}


def test_ftd_renderer_keys_are_documented_or_declared_unverified() -> None:
    message = messages("ftd_ips", "ips-baseline", count=1)[0]
    assert loggen.syslog_body(message).startswith("%FTD-1-430001: EventPriority: ")
    event = ftd_fields(message)
    assert set(event) <= CISCO_DOCUMENTED_KEYS | FTD_UNVERIFIED_KEYS
    assert CISCO_DOCUMENTED_KEYS - {"User"} <= set(event)
    assert set(loggen.FTD_REQUIRED_KEYS) <= set(event)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", event["FirstPacketSecond"])
    assert event["Protocol"] in {"tcp", "udp"}
    assert event["InlineResult"] in {"Blocked", "Would block", "Allowed"}
    assert event["Message"].endswith(f"({event['GID']}:{event['SID']}:{event['Revision']})")


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
def test_ips_baseline_has_stable_cardinalities_and_action_share(profile: str) -> None:
    rows = parsed(profile, "ips-baseline")
    ports = value(rows, profile, "destination_port", "DstPort")
    sources = value(rows, profile, "source_address", "SrcIP")
    signatures = value(rows, profile, "threat_id", "SID")
    assert len(set(ports)) == 12
    assert len(set(sources)) == 30
    assert len(set(signatures)) == 10
    assert sum(is_blocked(profile, row) for row in rows) / len(rows) == pytest.approx(0.10)


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
def test_ips_port_sweep_is_one_source_over_sequential_ports(profile: str) -> None:
    rows = parsed(profile, "ips-port-sweep")
    ports = [int(item) for item in value(rows, profile, "destination_port", "DstPort")]
    sources = value(rows, profile, "source_address", "SrcIP")
    destinations = value(rows, profile, "destination_address", "DstIP")
    assert ports[:2_048] == list(range(1_024, 3_072))
    assert len(set(ports)) == 2_048
    assert len(set(sources)) == 1
    assert len(set(destinations)) == 1


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
def test_ips_new_port_follows_a_baseline_that_never_used_it(profile: str) -> None:
    rows = parsed(profile, "ips-new-port")
    ports = value(rows, profile, "destination_port", "DstPort")
    baseline, anomaly = ports[:2_000], ports[2_000:]
    assert str(loggen.IPS_NEW_PORT) not in set(baseline)
    assert set(anomaly) == {str(loggen.IPS_NEW_PORT)}


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
def test_ips_source_concentration_exceeds_sixty_percent(profile: str) -> None:
    rows = parsed(profile, "ips-source-concentration")[2_000:]
    sources = value(rows, profile, "source_address", "SrcIP")
    assert Counter(sources).most_common(1)[0][1] / len(sources) > 0.60


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
def test_ips_signature_burst_multiplies_one_signature_by_ten(profile: str) -> None:
    rows = parsed(profile, "ips-signature-burst")
    signatures = value(rows, profile, "threat_id", "SID")
    target = signatures[2_000]
    baseline_rate = signatures[:2_000].count(target) / 2_000
    anomaly_rate = signatures[2_000:].count(target) / 2_000
    assert anomaly_rate / baseline_rate >= 10


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
def test_ips_block_shift_moves_from_ten_to_sixty_percent(profile: str) -> None:
    rows = parsed(profile, "ips-block-shift")
    baseline = sum(is_blocked(profile, row) for row in rows[:2_000]) / 2_000
    anomaly = sum(is_blocked(profile, row) for row in rows[2_000:]) / 2_000
    assert baseline == pytest.approx(0.10)
    assert anomaly == pytest.approx(0.60)


@pytest.mark.parametrize("profile", loggen.VENDOR_IPS_PROFILES)
@pytest.mark.parametrize("scenario", loggen.VENDOR_IPS_SCENARIOS)
def test_vendor_ips_addresses_stay_inside_the_safety_networks(
    profile: str, scenario: str
) -> None:
    rows = parsed(profile, scenario, count=200)
    sources = value(rows, profile, "source_address", "SrcIP")
    destinations = value(rows, profile, "destination_address", "DstIP")
    assert all(
        any(ipaddress.ip_address(address) in network for network in DOCUMENTATION_NETWORKS)
        for address in sources
    )
    assert all(ipaddress.ip_address(address) in PROTECTED_NETWORK for address in destinations)


def test_vendor_ips_rate_and_address_overrides_fail_closed() -> None:
    with pytest.raises(ValueError, match="allow-high-rate"):
        loggen.validate_options(options("ftd_ips", "ips-baseline").__class__(
            **{**options("ftd_ips", "ips-baseline").__dict__, "eps": 100.1}
        ))
    with pytest.raises(ValueError, match="RFC 5737"):
        loggen.validate_options(loggen.Options(
            profiles=("ftd_ips",), scenario="ips-baseline", output_format="vendor",
            fixed_source_ip="8.8.8.8",
        ))
    with pytest.raises(ValueError, match=r"10\.10\.20\.0/24"):
        loggen.validate_options(loggen.Options(
            profiles=("pan_threat",), scenario="ips-baseline", output_format="vendor",
            fixed_destination_ip="10.10.21.5",
        ))


def test_vendor_ips_seed_is_deterministic() -> None:
    one = messages("ftd_ips", "ips-source-concentration", count=100)
    two = messages("ftd_ips", "ips-source-concentration", count=100)
    assert one == two


def test_vendor_ips_dry_run_stdout_never_constructs_a_socket() -> None:
    fake = FakeTime()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run constructed a network sender")

    output = io.StringIO()
    count = loggen.run(
        options("ftd_ips", "ips-baseline", count=20),
        sender_factory=forbidden,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=output,
        stderr=io.StringIO(),
    )
    assert count == 20
    assert len(output.getvalue().splitlines()) == 20


def test_vendor_ips_headers_use_the_stable_identity() -> None:
    assert re.match(r"<134>Oct 12 12:00:00 pa-fw-01 ", messages(
        "pan_threat", "ips-baseline", count=1
    )[0])
    assert re.match(r"<134>Oct 12 12:00:00 ftd-01 ", messages(
        "ftd_ips", "ips-baseline", count=1
    )[0])


def test_vendor_ips_profiles_are_opt_in_and_not_mixed() -> None:
    assert not set(loggen.VENDOR_IPS_PROFILES) & set(loggen.DEFAULT_PROFILES)
    assert loggen.Options().profiles == loggen.DEFAULT_PROFILES
    with pytest.raises(ValueError, match="only pan_threat and ftd_ips"):
        loggen.validate_options(loggen.Options(
            profiles=("ips", "ftd_ips"), scenario="ips-baseline", output_format="vendor",
        ))
    with pytest.raises(ValueError, match="--format vendor"):
        loggen.validate_options(loggen.Options(
            profiles=("ftd_ips",), scenario="ips-baseline", output_format="leef",
        ))
    with pytest.raises(ValueError, match="need --types"):
        loggen.validate_options(loggen.Options(
            profiles=("ips",), scenario="ips-baseline", output_format="vendor",
        ))


# ---------------------------------------------------------------- defect D-1 ---
# With two vendor profiles `_scenario_event` round-robins them and hands each
# one `local_index = index // 2`, so the change phase must be half of that
# profile's own share -- not half of the whole run. Every scenario below is
# gated on that phase; `ips-port-sweep` is not and is intentionally absent.

MIXED_PROFILES = ("pan_threat", "ftd_ips")
GATED_SCENARIOS = (
    "ips-new-port",
    "ips-source-concentration",
    "ips-signature-burst",
    "ips-block-shift",
)
# 9000 events over two profiles is 4500 each, so the change starts at 2250.
TWO_PROFILE_COUNT = 9_000
TWO_PROFILE_HALF = TWO_PROFILE_COUNT // len(MIXED_PROFILES)
TWO_PROFILE_TRANSITION = TWO_PROFILE_HALF // 2


def _vendor_row(message: str) -> tuple[str, dict[str, str]]:
    if loggen.syslog_body(message).startswith("%FTD-1-430001: "):
        return "ftd_ips", ftd_fields(message)
    return "pan_threat", pan_fields(message)


def run_rows(
    scenario: str,
    *,
    count: int = 0,
    duration: float | None = None,
    eps: float = loggen.DEFAULT_EPS,
    emit: int | None = None,
    profiles: tuple[str, ...] = MIXED_PROFILES,
) -> dict[str, list[dict[str, str]]]:
    """Build a vendor run in memory, grouped per profile in send order.

    ``emit`` overrides how many messages are built, which the zero-planned
    fallback needs in order to run past a zero ``--count``.
    """
    opts = loggen.Options(
        profiles=profiles,
        scenario=scenario,
        output_format="vendor",
        count=count,
        duration=duration,
        eps=eps,
        seed=42,
        dry_run=True,
        stdout=True,
    )
    loggen.validate_options(opts)
    generator = loggen.EventGenerator(opts, clock=lambda: NOW)
    planned = count
    if planned == 0 and duration is not None:
        planned = int(duration * eps)
    total = planned if emit is None else emit
    grouped: dict[str, list[dict[str, str]]] = {profile: [] for profile in profiles}
    for index in range(total):
        profile, row = _vendor_row(generator.generate(index))
        grouped[profile].append(row)
    return grouped


def change_share(scenario: str, profile: str, rows: list[dict[str, str]]) -> float:
    """Fraction of ``rows`` carrying the scenario's changed shape."""
    if scenario == "ips-new-port":
        ports = value(rows, profile, "destination_port", "DstPort")
        return sum(port == str(loggen.IPS_NEW_PORT) for port in ports) / len(rows)
    if scenario == "ips-signature-burst":
        signature = loggen.IPS_SIGNATURES[0]
        target = (
            f"{signature.name}({signature.pan_id})" if profile == "pan_threat"
            else str(signature.sid)
        )
        return sum(item == target for item in value(rows, profile, "threat_id", "SID")) / len(rows)
    if scenario == "ips-source-concentration":
        sources = value(rows, profile, "source_address", "SrcIP")
        return Counter(sources).most_common(1)[0][1] / len(rows)
    if scenario == "ips-block-shift":
        return sum(is_blocked(profile, row) for row in rows) / len(rows)
    raise AssertionError(f"no change shape defined for {scenario}")


@pytest.mark.parametrize("scenario", GATED_SCENARIOS)
@pytest.mark.parametrize("profile", MIXED_PROFILES)
def test_two_profiles_enter_the_change_phase_in_each_profile(
    scenario: str, profile: str
) -> None:
    rows = run_rows(scenario, count=TWO_PROFILE_COUNT)[profile]
    assert len(rows) == TWO_PROFILE_HALF
    # Half the run (4500) never reached the change phase before the fix: the
    # per-profile index stopped one short of a transition derived from the total.
    assert change_share(scenario, profile, rows[:TWO_PROFILE_TRANSITION]) < 0.2
    assert change_share(scenario, profile, rows[TWO_PROFILE_TRANSITION:]) >= 0.6


def test_two_profiles_new_port_uses_8443_only_in_each_second_half() -> None:
    grouped = run_rows("ips-new-port", count=TWO_PROFILE_COUNT)
    for profile in MIXED_PROFILES:
        ports = value(grouped[profile], profile, "destination_port", "DstPort")
        assert str(loggen.IPS_NEW_PORT) not in set(ports[:TWO_PROFILE_TRANSITION])
        assert set(ports[TWO_PROFILE_TRANSITION:]) == {str(loggen.IPS_NEW_PORT)}


def test_two_profiles_signature_burst_concentrates_on_signature_zero() -> None:
    grouped = run_rows("ips-signature-burst", count=TWO_PROFILE_COUNT)
    signature = loggen.IPS_SIGNATURES[0]
    for profile in MIXED_PROFILES:
        target = (
            f"{signature.name}({signature.pan_id})" if profile == "pan_threat"
            else str(signature.sid)
        )
        signatures = value(grouped[profile], profile, "threat_id", "SID")
        assert signatures[:TWO_PROFILE_TRANSITION].count(target) == (
            TWO_PROFILE_TRANSITION // len(loggen.IPS_SIGNATURES)
        )
        assert set(signatures[TWO_PROFILE_TRANSITION:]) == {target}


def test_two_profiles_source_concentration_puts_seventy_percent_on_one_source() -> None:
    grouped = run_rows("ips-source-concentration", count=TWO_PROFILE_COUNT)
    for profile in MIXED_PROFILES:
        sources = value(grouped[profile], profile, "source_address", "SrcIP")
        anomaly = sources[TWO_PROFILE_TRANSITION:]
        top_source, top_count = Counter(anomaly).most_common(1)[0]
        assert top_source == loggen.IPS_EXTERNAL_IPS[0]
        assert top_count / len(anomaly) == pytest.approx(0.70)


@pytest.mark.parametrize("scenario", GATED_SCENARIOS)
@pytest.mark.parametrize("profile", MIXED_PROFILES)
def test_odd_total_two_profiles_both_reach_the_change_phase(
    scenario: str, profile: str
) -> None:
    # 9001 does not split evenly: one profile gets 4501 events, the other 4500.
    rows = run_rows(scenario, count=TWO_PROFILE_COUNT + 1)[profile]
    assert len(rows) in {TWO_PROFILE_HALF, TWO_PROFILE_HALF + 1}
    # floor(9001 / 2) / 2 = 2250 falls inside both profiles' own runs.
    assert change_share(scenario, profile, rows[:TWO_PROFILE_TRANSITION]) < 0.2
    assert change_share(scenario, profile, rows[TWO_PROFILE_TRANSITION:]) >= 0.6


@pytest.mark.parametrize("scenario", GATED_SCENARIOS)
@pytest.mark.parametrize("profile", MIXED_PROFILES)
def test_duration_path_two_profiles_enter_the_change_phase(
    scenario: str, profile: str
) -> None:
    rows = run_rows(scenario, duration=float(TWO_PROFILE_COUNT), eps=1.0)[profile]
    assert len(rows) == TWO_PROFILE_HALF
    assert change_share(scenario, profile, rows[:TWO_PROFILE_TRANSITION]) < 0.2
    assert change_share(scenario, profile, rows[TWO_PROFILE_TRANSITION:]) >= 0.6


def test_single_profile_transition_is_unchanged() -> None:
    # One profile keeps half of the whole run, exactly as before the fix.
    rows = parsed("pan_threat", "ips-new-port", count=TWO_PROFILE_COUNT)
    ports = value(rows, "pan_threat", "destination_port", "DstPort")
    assert str(loggen.IPS_NEW_PORT) not in set(ports[:4_500])
    assert set(ports[4_500:]) == {str(loggen.IPS_NEW_PORT)}
    # The count=4000 shape the existing tests rely on still begins at 2000.
    rows = parsed("ftd_ips", "ips-new-port", count=4_000)
    ports = value(rows, "ftd_ips", "destination_port", "DstPort")
    assert str(loggen.IPS_NEW_PORT) not in set(ports[:2_000])
    assert set(ports[2_000:]) == {str(loggen.IPS_NEW_PORT)}


def test_zero_planned_fallback_still_transitions_at_one_thousand() -> None:
    rows = run_rows(
        "ips-new-port", profiles=("pan_threat",), emit=1_001
    )["pan_threat"]
    ports = value(rows, "pan_threat", "destination_port", "DstPort")
    assert str(loggen.IPS_NEW_PORT) not in set(ports[:1_000])
    assert set(ports[1_000:]) == {str(loggen.IPS_NEW_PORT)}
