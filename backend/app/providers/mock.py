"""MockQRadarProvider — deterministic in-memory QRadar for dev and tests.

Deterministic by design: given the same seed the same log sources, EPS values,
offenses and AQL results come back every run, so tests assert on exact numbers
and demos look identical each time. It supports every capability, including the
full Ariel search lifecycle, so Phase 2 collection and anomaly work can proceed
with no real QRadar.

The mock also fabricates a few *unhealthy* sources on purpose — a silent source,
a volume-collapsed source, a parsing-degraded source — so the anomaly detector
and health scoring have something to detect in development.
"""

from __future__ import annotations

import hashlib
import ipaddress
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, TypedDict

from app.catchall.keyed_rows import parse_catchall_rows, validate_catchall_type_ids
from app.collectors.bucketed_metrics import validate_bucketed_metric_window
from app.geo.fold import GeoDirection, GeoKeyedResult, parse_geo_rows
from app.ips.keyed_rows import (
    parse_ips_keyed_rows,
    validate_ips_fields,
    validate_ips_sources,
)
from app.models.enums import IpsDimension, PwScopeKind
from app.providers.base import (
    IPS_NORMALIZED_FIELD_ALLOWLIST,
    PROPERTY_NAME_PATTERN,
    ConsoleEventTotals,
    LogSourceTelemetry,
    ProviderCapability,
    QRadarProvider,
)
from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    CatchallKeyedResult,
    CustomPropertyDTO,
    DimensionAggregate,
    DimensionValueCount,
    InstanceInfoDTO,
    IpsFieldProbe,
    IpsFieldProbeRequest,
    IpsKeyedResult,
    IpsProbeValue,
    IpsSourceProbe,
    LogSourceDTO,
    LogSourceGroupDTO,
    LogSourceMetricBucketBatch,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
    PwKeyedResult,
    PwSearchKind,
    RuleMatchBatch,
    RuleMatchRow,
    WafBreakdownBatch,
    WafBreakdownBucketBatch,
    WafBreakdownGroup,
    WafObservationBatch,
    WafObservationBucketBatch,
    WafPropertyProbe,
    WafPropertyValue,
    WafRejectionObservation,
    WafRequestGroup,
    WafSourceProbe,
    WindowedRuleMatches,
)
from app.rules.matches import window_starts
from app.waf.bucketed import (
    breakdown_sort_key,
    fold_breakdown_buckets,
    fold_observation_buckets,
    observation_sort_key,
)

_LOG_SOURCE_TYPES = [
    (12, "Cisco ASA", False, False),
    (13, "Microsoft Windows Security Event Log", False, False),
    (2, "Linux OS", False, False),
    (25, "McAfee Network Security Platform", False, False),
    (95, "Palo Alto PA Series", False, False),
    (145, "Okta", False, False),
    (212, "Universal LEEF", False, False),
    (351, "AWS CloudTrail", False, False),
    (368, "Health Metrics", True, False),
]

class _SourceTemplateFacts(TypedDict, total=False):
    auto_discovered: bool
    group_ids: list[int]
    identifier: str
    internal: bool


# name, type_id, health-shape. The health-shape drives what the mock emits so
# the detector has deterministic targets.
_SOURCE_TEMPLATES: list[tuple[str, int, str, _SourceTemplateFacts]] = [
    ("fw-edge-01 Cisco ASA", 12, "healthy", {}),
    ("fw-edge-02 Cisco ASA", 12, "healthy", {}),
    ("dc01 Windows Security", 13, "healthy", {}),
    ("dc02 Windows Security", 13, "silent", {}),  # -> NO_EVENTS
    ("web-cluster Linux", 2, "volume_drop", {}),  # -> VOLUME_DROP
    ("pan-fw-01 Palo Alto", 95, "healthy", {}),
    ("okta-prod Okta", 145, "parsing_degraded", {}),  # -> PARSING_DEGRADATION
    ("aws-org CloudTrail", 351, "spike", {}),  # -> VOLUME_SPIKE
    ("legacy-unix Linux", 2, "disabled", {}),
    (
        "dmz-waf-01",
        212,
        "healthy",
        {"auto_discovered": True, "group_ids": [20]},
    ),
    ("linux-auth-01", 25, "healthy", {"group_ids": [20]}),
    (
        "Health Metrics-2 :: mock",
        368,
        "healthy",
        {"identifier": "127.0.0.1", "internal": True},
    ),
]


#: (qradar_id, identifier, name, property_type, use_for_rule_engine, type ids).
#: The lab shape from T-041: five properties on the WAF type 212, `WAF_URL`
#: only on 232, `IPS_Action` on Snort type 2, one property nobody extracts, and
#: one name the AQL allowlist refuses.
_CUSTOM_PROPERTIES: tuple[tuple[int, str, str, str, bool, tuple[int, ...]], ...] = (
    (101, "DEFAULTCUSTOMEVENT10", "HTTP_Method", "string", False, (212,)),
    (102, "DEFAULTCUSTOMEVENT11", "URL", "string", True, (2, 64, 212)),
    (103, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1001", "WAF_Action", "string", False, (212,)),
    (104, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1002", "WAF_ResponseCode", "numeric", False, (212,)),
    (105, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1003", "WAF_USERAGENT", "string", False, (212,)),
    (106, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1004", "WAF_URL", "string", False, (232,)),
    (107, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1005", "IPS_Action", "string", False, (2,)),
    (108, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1006", "Unused Property", "string", False, ()),
    (109, "2f6c1d4e-0a51-4b0e-9c1a-5b7d3e8a1007", "Bad.Name(1)", "string", False, (212,)),
)


@dataclass(frozen=True)
class MockIpsSearch:
    """One keyed IPS search the mock was asked for, kept so tests can read the split."""

    dimension: IpsDimension
    qradar_log_source_ids: tuple[int, ...]
    window_start: datetime
    window_end: datetime


#: A fault hook: given a search, an exception to raise for it, or None.
IpsFault = Callable[[MockIpsSearch], Exception | None]
#: A truncation hook: True forces the search to read as truncated.
IpsTruncation = Callable[[MockIpsSearch], bool]


@dataclass(frozen=True)
class MockPwSearch:
    """One property-watch search the mock was asked for, kept so tests can read the split."""

    search: PwSearchKind
    scope_kind: PwScopeKind
    scope_ids: tuple[int, ...]
    window_start: datetime
    window_end: datetime


#: A fault hook: given a search, an exception to raise for it, or None.
PwFault = Callable[[MockPwSearch], Exception | None]
#: A truncation hook: True forces the search to read as truncated.
PwTruncation = Callable[[MockPwSearch], bool]
#: Replaces the synthetic events of a bucket: given the bucket key and the property names,
#: ``{"values": {name: value or None}, "events": n}`` dicts.
PwScript = Callable[[int, tuple[str, ...]], list[dict]]


@dataclass(frozen=True)
class MockCatchallSearch:
    """One catch-all senders search the mock was asked for, kept so tests can read the split."""

    device_type_ids: tuple[int, ...]
    window_start: datetime
    window_end: datetime


#: A fault hook: given a search, an exception to raise for it, or None.
CatchallFault = Callable[[MockCatchallSearch], Exception | None]
#: A truncation hook: True forces the search to read as truncated.
CatchallTruncation = Callable[[MockCatchallSearch], bool]
#: A script: the raw rows (`logsourceid`, `sender`, `events`, `records`) of a bucket key.
CatchallScript = Callable[[int], list[dict]]

@dataclass(frozen=True)
class MockGeoSearch:
    """One geo search the mock was asked for, kept so tests can read the split."""

    direction: GeoDirection
    window_start: datetime
    window_end: datetime


#: A fault hook: given a search, an exception to raise for it, or None.
GeoFault = Callable[[MockGeoSearch], Exception | None]
#: A truncation hook: True forces the search to read as truncated.
GeoTruncation = Callable[[MockGeoSearch], bool]
#: A script: the raw rows (`geo`, `events`) of a direction and bucket key.
GeoScript = Callable[[GeoDirection, int], list[dict]]

#: Lookup strings as QRadar returns them: a country, one with a different `confidence`,
#: and the sentinel for a private or unknown address.
_GEO_COUNTRIES = (
    ("US", "United States", 6252001),
    ("DE", "Germany", 2921044),
    ("JP", "Japan", 1861060),
    ("BR", "Brazil", 3469034),
    ("TR", "Turkey", 298795),
)


def geo_key(code: str, name: str = "Mock", geo_id: int = 1, confidence: int | None = None) -> str:
    """The lookup string for a country, in QRadar's shape."""
    shown = "null" if confidence is None else str(confidence)
    return (
        f'{{"country":{{"confidence":{shown},"name":"{name}",'
        f'"iso_code":"{code}","geo_id":{geo_id}}}}}'
    )


_IPS_PORTS = (0, 22, 53, 80, 443, 445, 3389, 5060, 8080, 8443)
_IPS_PROTOCOLS = (6, 6, 6, 17, 1, 255, 0)
_IPS_SOURCES = (
    "203.0.113.5",
    "203.0.113.9",
    "198.51.100.7",
    "192.0.2.44",
    "10.1.1.8",
    "127.0.0.1",
    "2001:db8::7",
)
_IPS_DESTINATIONS = (*(f"10.10.20.{n}" for n in range(1, 9)), "127.0.0.1")
_IPS_ACTIONS = ("BLOCK", "ALLOW", "block ", "Alert")


def _metric_source_ids(qradar_ids: Sequence[int]) -> set[int]:
    """The log-source ids of a top-up. An empty list is a caller's bug."""
    if not qradar_ids:
        raise ValueError("metric source filter is empty")
    try:
        return {int(item) for item in qradar_ids}
    except (TypeError, ValueError) as exc:
        raise ValueError("metric source ids must be integers") from exc


class MockQRadarProvider(QRadarProvider):
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
            ProviderCapability.AQL_EXECUTION,
            ProviderCapability.CONFIG_SNAPSHOTS,
            ProviderCapability.LOG_SOURCE_GROUPS,
            ProviderCapability.CUSTOM_PROPERTY_CATALOG,
        }
    )
    #: Synthetic, but genuinely varied per shape, so dev and tests can exercise
    #: the detectors that read them. COLLECTION_ERRORS is absent: the mock never
    #: simulates a collection error, so its constant 0 is not a measurement.
    log_source_telemetry = frozenset(LogSourceTelemetry) - {
        LogSourceTelemetry.COLLECTION_ERRORS
    }
    event_count_aggregation = "synthetic"

    def __init__(
        self,
        *,
        seed: int = 1337,
        now: datetime | None = None,
        ips_max_sources_per_search: int = 50,
        pw_max_sources_per_search: int = 50,
    ) -> None:
        self._seed = seed
        self._ips_max_sources_per_search = max(1, int(ips_max_sources_per_search))
        self._pw_max_sources_per_search = max(1, int(pw_max_sources_per_search))
        #: Every keyed IPS search asked for, in order.
        self.ips_searches: list[MockIpsSearch] = []
        #: Test hooks, so the real driver and fold run on a failure or a
        #: truncation (SEARCH-CADENCE §SC.5 rule 6). Truncation also happens
        #: naturally whenever a search has more rows than `max_rows`.
        self.ips_fault: IpsFault | None = None
        self.ips_force_truncated: IpsTruncation | None = None
        #: The same hooks for the property-watch searches, plus a script that replaces the
        #: synthetic events. Rows still pass the real parser and the real query builder.
        self.pw_searches: list[MockPwSearch] = []
        self.pw_fault: PwFault | None = None
        self.pw_force_truncated: PwTruncation | None = None
        self.pw_script: PwScript | None = None
        #: The same hooks for the catch-all senders search, plus a script that
        #: replaces the synthetic senders. Rows still pass the real parser.
        self.catchall_searches: list[MockCatchallSearch] = []
        self.catchall_fault: CatchallFault | None = None
        self.catchall_force_truncated: CatchallTruncation | None = None
        self.catchall_script: CatchallScript | None = None
        #: The same hooks for the geo search, plus a script that replaces the
        #: synthetic countries. Rows still pass the real parser.
        self.geo_searches: list[MockGeoSearch] = []
        self.geo_fault: GeoFault | None = None
        self.geo_force_truncated: GeoTruncation | None = None
        self.geo_script: GeoScript | None = None
        self._now = now or datetime.now(UTC)
        self._rng = random.Random(seed)  # noqa: S311 (not cryptographic)
        # Active Ariel searches: search_id -> (aql, created_at)
        self._searches: dict[str, tuple[str, datetime]] = {}
        self._search_counter = 0

    # -- health / inventory -------------------------------------------------
    async def get_instance_info(self) -> InstanceInfoDTO:
        return InstanceInfoDTO(
            version="7.5.0",
            build="20260601123456",
            reachable=True,
            raw_about={"release_name": "QRadar 7.5.0 UP9", "external_version": "7.5.0"},
        )

    async def list_log_sources(self) -> list[LogSourceDTO]:
        out: list[LogSourceDTO] = []
        type_names = {type_id: name for type_id, name, _internal, _custom in _LOG_SOURCE_TYPES}
        for idx, (name, type_id, shape, facts) in enumerate(_SOURCE_TEMPLATES, start=1):
            type_name = type_names.get(type_id)
            enabled = shape != "disabled"
            avg_eps, last_event = self._shape_liveness(shape)
            out.append(
                LogSourceDTO(
                    qradar_id=1000 + idx,
                    name=name,
                    description=f"Mock log source ({shape})",
                    type_id=type_id,
                    type_name=type_name,
                    protocol_type_id=0,
                    enabled=enabled,
                    status="SUCCESS" if enabled and shape != "silent" else "ERROR",
                    credibility=self._rng.randint(5, 10),
                    target_event_collector_id=1,
                    average_eps=avg_eps,
                    last_event_time=last_event,
                    qradar_created_at=self._now - timedelta(days=90 - idx),
                    qradar_modified_at=self._now - timedelta(days=idx),
                    auto_discovered=facts.get("auto_discovered", False),
                    group_ids=facts.get("group_ids", [10]),
                    identifier=facts.get("identifier", name.split()[0]),
                    internal=facts.get("internal", False),
                )
            )
        return out

    def _shape_liveness(self, shape: str) -> tuple[float, datetime | None]:
        """Map a health-shape to (average_eps, last_event_time)."""
        if shape == "silent":
            return 0.0, self._now - timedelta(hours=6)
        if shape == "disabled":
            return 0.0, None
        if shape == "volume_drop":
            return 3.0, self._now - timedelta(minutes=2)
        if shape == "spike":
            return 950.0, self._now - timedelta(seconds=20)
        base = {"healthy": 45.0, "parsing_degraded": 40.0}.get(shape, 30.0)
        jitter = self._rng.uniform(-5, 5)
        return round(base + jitter, 2), self._now - timedelta(seconds=self._rng.randint(5, 90))

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        for src in await self.list_log_sources():
            if src.qradar_id == qradar_id:
                return src
        return None

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        return [
            LogSourceTypeDTO(type_id=t, name=n, internal=internal, custom=custom)
            for t, n, internal, custom in _LOG_SOURCE_TYPES
        ]

    async def list_custom_properties(
        self, *, log_source_type_id: int | None = None
    ) -> list[CustomPropertyDTO]:
        self.require(ProviderCapability.CUSTOM_PROPERTY_CATALOG)
        return [
            CustomPropertyDTO(
                qradar_id=qid,
                identifier=ident,
                name=name,
                property_type=ptype,
                use_for_rule_engine=rule_engine,
                log_source_type_ids=types,
                name_allowed=PROPERTY_NAME_PATTERN.match(name) is not None,
            )
            for qid, ident, name, ptype, rule_engine, types in _CUSTOM_PROPERTIES
            if log_source_type_id is None or log_source_type_id in types
        ]

    async def list_log_source_groups(self) -> list[LogSourceGroupDTO]:
        return [
            LogSourceGroupDTO(
                qradar_id=10,
                name="Production",
                parent_qradar_id=0,
                assignable=False,
            ),
            LogSourceGroupDTO(
                qradar_id=20,
                name="DMZ",
                description="Internet-facing systems",
                parent_qradar_id=10,
                assignable=True,
            ),
            LogSourceGroupDTO(
                qradar_id=0,
                name="Other",
                parent_qradar_id=10,
                assignable=False,
            ),
        ]

    async def get_log_source_metrics(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        qradar_ids: Sequence[int] | None = None,
    ) -> list[LogSourceMetricSample]:
        """Deterministic per-interval samples.

        A healthy source follows a weekly-seasonal EPS curve (higher on weekday
        business hours) with small per-interval jitter seeded from the interval,
        so a baseline built over history is stable and reproducible. The injected
        failure shapes deviate from that curve in the specific way each anomaly
        type detects.
        """
        bucket_seconds = int((window_end - window_start).total_seconds()) or 300
        samples: list[LogSourceMetricSample] = []
        for idx, (_name, _type_id, shape, _facts) in enumerate(_SOURCE_TEMPLATES, start=1):
            qid = 1000 + idx
            base_eps = self._seasonal_eps(window_start, shape)
            sample = self._build_sample(qid, window_start, bucket_seconds, base_eps, shape)
            samples.append(sample)
        if qradar_ids is not None:
            chosen = _metric_source_ids(qradar_ids)
            samples = [sample for sample in samples if sample.qradar_id in chosen]
        return samples

    async def get_log_source_metrics_bucketed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        bucket_seconds: int,
        max_rows: int,
        qradar_ids: Sequence[int] | None = None,
    ) -> LogSourceMetricBucketBatch:
        """Build the keyed mock result from the existing per-bucket generator."""
        from app.collectors.bucketed_metrics import validate_bucketed_metric_window

        if max_rows < 1:
            raise ValueError("metric row limit must be positive")
        start, end = validate_bucketed_metric_window(
            window_start, window_end, bucket_seconds
        )
        bucket_ms = bucket_seconds * 1000
        aql = (
            'SELECT logsourceid AS "qradar_id", '  # noqa: S608 - validated integers only
            f'starttime / {bucket_ms} AS "k" FROM events '
            f"GROUP BY logsourceid, starttime / {bucket_ms} LIMIT {max_rows + 1}"
        )
        buckets: dict[datetime, list[LogSourceMetricSample]] = {}
        cursor = start
        rows_returned = 0
        truncated = False
        while cursor < end:
            # None stays an unfiltered call. A test that replaces this method
            # does not have to accept the keyword the live collector omits.
            bucket_end = cursor + timedelta(seconds=bucket_seconds)
            if qradar_ids is None:
                samples = await self.get_log_source_metrics(cursor, bucket_end)
            else:
                samples = await self.get_log_source_metrics(
                    cursor, bucket_end, qradar_ids=qradar_ids
                )
            remaining = max_rows - rows_returned
            buckets[cursor] = samples[: max(0, remaining)]
            rows_returned += len(buckets[cursor])
            if len(samples) > remaining:
                truncated = True
            cursor += timedelta(seconds=bucket_seconds)
        return LogSourceMetricBucketBatch(
            buckets=buckets,
            truncated=truncated,
            rows_returned=rows_returned,
            aql=aql,
        )

    #: Dimensions the mock "DSM" populates, and the value alphabet for each.
    #: `username` and `category` are deliberately absent so tests exercise the
    #: UNAVAILABLE path — a real firewall DSM populates neither.
    _MOCK_DIMENSION_VALUES: ClassVar[dict[str, list[str]]] = {
        "qid": ["1000001", "1000002", "1000003", "1000004"],
        "event_name": ["Accept", "Deny", "Session Closed", "Session Opened"],
        "source_ip": [
            "203.0.113.50", "198.51.100.11", "192.0.2.77", "198.51.100.42", "192.0.2.8",
        ],
        "destination_ip": ["10.0.0.5", "10.0.0.9", "10.0.1.20", "10.0.2.31"],
        "destination_port": ["445", "3389", "22", "443", "80"],
        "source_port": ["49152", "49153", "49154"],
        "action": ["DENY", "ACCEPT"],
        "protocol": ["6", "17"],
    }

    async def get_dimension_aggregates(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        dimensions: Sequence[str],
        top_n: int,
    ) -> list[DimensionAggregate]:
        """Deterministic bounded aggregates.

        Counts are seeded from (log source, dimension, value, window start), so
        two different windows produce genuinely different distributions while
        any given window is exactly reproducible. That is what lets an
        explanation test assert on contributor ranking rather than merely on
        shape.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        seconds = max(1.0, (window_end - window_start).total_seconds())
        out: list[DimensionAggregate] = []

        for dimension in dimensions:
            alphabet = self._MOCK_DIMENSION_VALUES.get(dimension)
            if alphabet is None:
                out.append(
                    DimensionAggregate(
                        dimension=dimension,
                        available=False,
                        error="field is not populated for this log source",
                    )
                )
                continue

            rng = random.Random(  # noqa: S311 - deterministic fixture, not crypto
                f"{qradar_log_source_id}:{dimension}:{int(window_start.timestamp())}"
            )
            counts: list[DimensionValueCount] = []
            for value in alphabet:
                # A dominant head plus a light tail, so a "top contributor" is a
                # meaningful concept rather than a uniform draw.
                weight = rng.random() ** 2
                count = int(weight * seconds * 4) + 1
                counts.append(DimensionValueCount(value=value, count=count))
            counts.sort(key=lambda c: c.count, reverse=True)
            capped = counts[:top_n]
            out.append(
                DimensionAggregate(
                    dimension=dimension,
                    available=True,
                    values=capped,
                    distinct_count=len(counts),
                    total_count=sum(c.count for c in counts),
                    truncated=len(counts) > top_n,
                    query=f"MOCK GROUP BY {dimension} LIMIT {top_n}",
                )
            )
        return out

    #: Ids the mock reports as matching: some mock rules, all three mock
    #: building blocks, and one id that is in neither inventory list.
    _MOCK_MATCHED_RULE_IDS: ClassVar[tuple[int, ...]] = (
        5001, 5002, 5004, 6000, 6001, 6002, 999_001,
    )

    async def get_rule_matches(
        self, window_start: datetime, window_end: datetime, *, max_rows: int
    ) -> RuleMatchBatch:
        """Deterministic rule matches, seeded from the window.

        Covers rules, building blocks and an unknown id, so the fold's join and
        its unknown-id count are both exercised without a QRadar.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        seconds = max(1.0, (window_end - window_start).total_seconds())
        rng = random.Random(  # noqa: S311 - deterministic fixture, not crypto
            f"rule-match:{int(window_start.timestamp())}"
        )
        rows: list[RuleMatchRow] = []
        for rule_id in self._MOCK_MATCHED_RULE_IDS:
            records = int(rng.random() ** 2 * seconds * 0.2) + 1
            rows.append(
                RuleMatchRow(
                    qradar_rule_id=rule_id, records=records, events=records * 2 + 1
                )
            )
        rows.sort(key=lambda r: (-r.records, r.qradar_rule_id))
        cap = max(1, max_rows)
        return RuleMatchBatch(
            window_start=window_start,
            window_end=window_end,
            rows=rows[:cap],
            truncated=len(rows) >= cap,
            row_cap=cap,
            query_version="rule_match/v1",
            query=f"MOCK creEventList LIMIT {cap}",
        )

    async def get_rule_matches_windowed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        window_seconds: int,
        max_rows: int,
    ) -> WindowedRuleMatches:
        """Per-window `get_rule_matches`, as one search.

        Each window's batch is exactly what `get_rule_matches` returns for it,
        so tests and the lab profile exercise the real collector path. The
        search is truncated when the span holds more than ``max_rows`` raw rows.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        step = timedelta(seconds=window_seconds)
        batches = {
            start: await self.get_rule_matches(start, start + step, max_rows=max_rows)
            for start in window_starts(window_start, window_end, window_seconds)
        }
        raw_total = len(self._MOCK_MATCHED_RULE_IDS) * len(batches)
        return WindowedRuleMatches(
            window_start=window_start,
            window_end=window_end,
            window_seconds=window_seconds,
            batches=batches,
            truncated=raw_total > max(1, max_rows),
            row_cap=max(1, max_rows),
            query=f"MOCK creEventList WINDOWED LIMIT {max(1, max_rows) + 1}",
        )

    async def get_console_event_totals(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        bucket_seconds: int,
    ) -> ConsoleEventTotals:
        """Deterministic console-wide events: a positive total per bucket.

        Seeded from the bucket start, so the floor scan finds its first event
        at the first bucket of every window and two calls agree exactly. Only
        buckets with events are returned, as the REST query returns them.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        step = timedelta(seconds=bucket_seconds)
        totals: dict[datetime, int] = {}
        cursor = start
        while cursor < end:
            rng = random.Random(  # noqa: S311 - deterministic fixture, not crypto
                f"console-totals:{int(cursor.timestamp())}"
            )
            totals[cursor] = int(rng.random() * 900) + 100
            cursor += step
        return ConsoleEventTotals(
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            totals=totals,
            query_version="mock_console_event_totals/v1",
            query="MOCK console event totals",
        )

    #: Endpoints the mock WAF source publishes. Mirrors the shape the lab
    #: generator produces so a fixture-driven test and a live lab run exercise
    #: the same normalization paths -- including the query string, which must be
    #: stripped, and the numeric id, which must be templated.
    _MOCK_WAF_ENDPOINTS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("/api/v1/report?id=1%27+OR+%271%27%3D%271", "GET"),
        ("/api/v1/status", "GET"),
        ("/portal/login", "POST"),
        ("/portal/login", "GET"),
        ("/static/app.css", "GET"),
        ("/users/12345/orders/98", "GET"),
        ("/", "GET"),
    )

    #: Requests the mock source emits that carry no parseable endpoint. Their
    #: presence is what makes endpoint coverage < 1.0, so a collector test can
    #: exercise the PARTIAL path without hand-building a batch.
    _MOCK_WAF_UNPARSED_SHARE = 0.05

    async def get_waf_observations(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None = None,
        max_groups: int = 500,
    ) -> WafObservationBatch:
        """Deterministic per-endpoint volume, seeded from the window.

        Values are raw and un-normalized on purpose: the mock stands in for
        QRadar, and QRadar returns what the device sent. A mock that returned
        pre-normalized paths would let a collector bug that skips normalization
        pass every test.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        seconds = max(1.0, (window_end - window_start).total_seconds())
        rng = random.Random(  # noqa: S311 - deterministic fixture, not crypto
            f"waf:{qradar_log_source_id}:{int(window_start.timestamp())}"
        )
        groups: list[WafRequestGroup] = []
        for raw_url, method in self._MOCK_WAF_ENDPOINTS:
            weight = rng.random() ** 2
            count = int(weight * seconds * 0.5) + 1
            groups.append(
                WafRequestGroup(
                    raw_url=raw_url,
                    raw_method=method,
                    raw_virtual_host=(
                        "portal.lab.test" if virtual_host_property else None
                    ),
                    request_count=count,
                    record_count=count,
                    distinct_client_count=1 + int(weight * 20),
                )
            )
        groups.sort(key=observation_sort_key)
        capped = groups[:max_groups]
        parsed = sum(g.request_count for g in capped)
        # Total exceeds the parsed sum, so coverage is genuinely below 1.
        total = int(parsed / (1.0 - self._MOCK_WAF_UNPARSED_SHARE))
        return WafObservationBatch(
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            groups=capped,
            total_events=total,
            # The rule the REST provider applies to a bucket: a full page.
            truncated=len(capped) >= max_groups,
            query=f"MOCK WAF GROUP BY {url_property}, {method_property}",
            total_query="MOCK WAF TOTAL",
        )

    #: Deterministic breakdown values: (value, share of an endpoint's requests).
    _MOCK_WAF_BREAKDOWN_VALUES: ClassVar[dict[str, tuple[tuple[str, float], ...]]] = {
        "ACTION": (("ALLOW", 0.9), ("DENY", 0.1)),
        "RESPONSE_CODE": (("200", 0.8), ("404", 0.15), ("500", 0.05)),
        "SOURCE_IP": (("192.0.2.30", 0.5), ("198.51.100.30", 0.3), ("203.0.113.50", 0.2)),
    }

    async def get_waf_breakdown(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None,
        breakdown: str,
        breakdown_property: str | None,
        max_groups: int = 2000,
    ) -> WafBreakdownBatch:
        """The mock endpoints' volume, divided by a fixed value distribution."""
        self.require(ProviderCapability.AQL_EXECUTION)
        volume = await self.get_waf_observations(
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            url_property=url_property,
            method_property=method_property,
            virtual_host_property=virtual_host_property,
            max_groups=5000,
        )
        groups = [
            WafBreakdownGroup(
                raw_url=g.raw_url,
                raw_method=g.raw_method,
                raw_virtual_host=g.raw_virtual_host,
                raw_value=value,
                request_count=int(g.request_count * share),
            )
            for g in volume.groups
            for value, share in self._MOCK_WAF_BREAKDOWN_VALUES[breakdown]
            if int(g.request_count * share) > 0
        ]
        groups.sort(key=breakdown_sort_key)
        return WafBreakdownBatch(
            breakdown=breakdown,
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            groups=groups[:max_groups],
            truncated=len(groups) >= max_groups,
            query=f"MOCK WAF BREAKDOWN {breakdown}",
        )

    async def get_waf_observations_bucketed(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None = None,
        max_groups: int = 500,
        max_rows: int,
    ) -> WafObservationBucketBatch:
        """The keyed result, built from the per-bucket generator and the real fold.

        Each bucket is what `get_waf_observations` returns for it; its groups
        become keyed rows and go through `fold_observation_buckets`, so a mock
        run exercises the same cut and truncation rule as the REST provider. A
        bucket whose own answer says it was truncated stays truncated.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        if max_rows < 1:
            raise ValueError("WAF row limit must be positive")
        starts = window_starts(window_start, window_end, bucket_seconds)
        per_bucket: dict[datetime, WafObservationBatch] = {}
        group_rows: list[dict] = []
        total_rows: list[dict] = []
        for start in starts:
            batch = await self.get_waf_observations(
                qradar_log_source_id=qradar_log_source_id,
                window_start=start,
                window_end=start + timedelta(seconds=bucket_seconds),
                url_property=url_property,
                method_property=method_property,
                virtual_host_property=virtual_host_property,
                max_groups=max_groups,
            )
            per_bucket[start] = batch
            key = int(start.timestamp()) // bucket_seconds
            group_rows.extend(
                {
                    "k": key,
                    "raw_url": g.raw_url,
                    "raw_method": g.raw_method,
                    "raw_virtual_host": g.raw_virtual_host,
                    "request_count": g.request_count,
                    "record_count": g.record_count,
                    "distinct_clients": g.distinct_client_count,
                }
                for g in batch.groups
            )
            if batch.groups or batch.total_events:
                total_rows.append({"k": key, "total_events": batch.total_events})
        aql = (
            f"MOCK WAF GROUP BY {url_property}, {method_property}, "
            f"starttime / {bucket_seconds * 1000}"
        )
        if len(group_rows) > max_rows or len(total_rows) > max_rows:
            return WafObservationBucketBatch(
                truncated=True,
                rows_returned=len(group_rows) + len(total_rows),
                query=aql,
                total_query="MOCK WAF TOTAL",
            )
        folded = fold_observation_buckets(
            group_rows,
            total_rows,
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            max_groups=max_groups,
            query=aql,
            total_query="MOCK WAF TOTAL",
        )
        return WafObservationBucketBatch(
            buckets={
                start: batch.model_copy(
                    update={"truncated": batch.truncated or per_bucket[start].truncated}
                )
                for start, batch in folded.items()
            },
            rows_returned=len(group_rows) + len(total_rows),
            query=aql,
            total_query="MOCK WAF TOTAL",
        )

    async def get_waf_breakdown_bucketed(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None,
        breakdown: str,
        breakdown_property: str | None,
        max_groups: int = 2000,
        max_rows: int,
    ) -> WafBreakdownBucketBatch:
        """The keyed breakdown, built as `get_waf_observations_bucketed` is."""
        self.require(ProviderCapability.AQL_EXECUTION)
        if max_rows < 1:
            raise ValueError("WAF row limit must be positive")
        starts = window_starts(window_start, window_end, bucket_seconds)
        per_bucket: dict[datetime, WafBreakdownBatch] = {}
        rows: list[dict] = []
        for start in starts:
            batch = await self.get_waf_breakdown(
                qradar_log_source_id=qradar_log_source_id,
                window_start=start,
                window_end=start + timedelta(seconds=bucket_seconds),
                url_property=url_property,
                method_property=method_property,
                virtual_host_property=virtual_host_property,
                breakdown=breakdown,
                breakdown_property=breakdown_property,
                max_groups=max_groups,
            )
            per_bucket[start] = batch
            key = int(start.timestamp()) // bucket_seconds
            rows.extend(
                {
                    "k": key,
                    "raw_url": g.raw_url,
                    "raw_method": g.raw_method,
                    "raw_virtual_host": g.raw_virtual_host,
                    "raw_value": g.raw_value,
                    "request_count": g.request_count,
                }
                for g in batch.groups
            )
        aql = f"MOCK WAF BREAKDOWN {breakdown} starttime / {bucket_seconds * 1000}"
        if len(rows) > max_rows:
            return WafBreakdownBucketBatch(
                breakdown=breakdown, truncated=True, rows_returned=len(rows), query=aql
            )
        folded = fold_breakdown_buckets(
            rows,
            breakdown=breakdown,
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            max_groups=max_groups,
            query=aql,
        )
        return WafBreakdownBucketBatch(
            breakdown=breakdown,
            buckets={
                start: batch.model_copy(
                    update={"truncated": batch.truncated or per_bucket[start].truncated}
                )
                for start, batch in folded.items()
            },
            rows_returned=len(rows),
            query=aql,
        )

    #: The properties the mock "site" authored, with the value distribution
    #: measured on the lab source (docs/PHASE-B-WAF-BEHAVIOR.md section 26.2).
    #: Any other name is rejected, exactly as QRadar rejects an unknown property.
    _MOCK_WAF_PROPERTY_VALUES: ClassVar[dict[str, tuple[tuple[str, int], ...]]] = {
        "URL": (
            ("/portal/login", 50),
            ("/api/v1/report?id=1%27+OR+%271%27%3D%271", 40),
            ("/static/app.css", 30),
        ),
        "HTTP_Method": (("GET", 83), ("POST", 37)),
        "WAF_Action": (("ALLOW", 78), ("DENY", 42)),
        "WAF_ResponseCode": (("200", 42), ("403", 42), ("304", 22), ("404", 14)),
        "WAF_USERAGENT": (("Mozilla/5.0 (lab)", 120),),
    }
    _MOCK_WAF_TOTAL_EVENTS = 120

    async def probe_waf_properties(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        property_names: Sequence[str],
        max_values: int = 50,
    ) -> WafSourceProbe:
        self.require(ProviderCapability.AQL_EXECUTION)
        probes: list[WafPropertyProbe] = []
        for name in dict.fromkeys(property_names):
            known = self._MOCK_WAF_PROPERTY_VALUES.get(name)
            if known is None or not PROPERTY_NAME_PATTERN.match(name):
                probes.append(
                    WafPropertyProbe(
                        property_name=name,
                        rejected=True,
                        error="QRadar rejected the request as invalid",
                        queries=[f"MOCK PROBE {name}"],
                    )
                )
                continue
            capped = known[:max_values]
            probes.append(
                WafPropertyProbe(
                    property_name=name,
                    populated_events=sum(c for _, c in known),
                    distinct_values=len(known),
                    values=[WafPropertyValue(value=v, events=c) for v, c in capped],
                    values_truncated=len(known) >= max_values,
                    queries=[f"MOCK PROBE {name}"],
                )
            )
        return WafSourceProbe(
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            total_events=self._MOCK_WAF_TOTAL_EVENTS,
            total_query="MOCK PROBE TOTAL",
            properties=probes,
        )

    def _ips_events(self, source_id: int, key: int) -> list[dict]:
        """The synthetic events of one source and bucket: deterministic, with the
        absent values (port 0, protocol 255, loopback) a real IPS carries."""
        rng = random.Random(f"{self._seed}:ips:{source_id}:{key}")  # noqa: S311
        count = 0 if rng.random() < 0.1 else rng.randint(1, 24)
        out: list[dict] = []
        for _ in range(count):
            events = rng.randint(1, 500)
            qid = 5_000_000 + rng.randint(0, 14)
            out.append(
                {
                    "sip": rng.choice(_IPS_SOURCES),
                    "dip": rng.choice(_IPS_DESTINATIONS),
                    "dport": rng.choice(_IPS_PORTS),
                    "proto": rng.choice(_IPS_PROTOCOLS),
                    "qid": qid,
                    "qname": "Unknown IPS Event" if qid % 5 == 0 else f"IPS signature {qid}",
                    "sig": f"SIG-{rng.randint(0, 9)}",
                    "act": rng.choice(_IPS_ACTIONS),
                    "events": events,
                    "records": rng.randint(1, events),
                }
            )
        return out

    @staticmethod
    def _ips_populated(logical: str, value: object) -> bool:
        """The mock's reading of `_ips_population_condition` on a raw value."""
        if value is None:
            return False
        if logical in {"SOURCE_IP", "DESTINATION_IP"}:
            address = ipaddress.ip_address(str(value))
            return not address.is_loopback
        if logical == "DESTINATION_PORT":
            return int(str(value)) > 0
        return True

    async def get_ips_keyed(
        self,
        dimension: IpsDimension,
        *,
        qradar_log_source_ids: Sequence[int],
        fields: Mapping[str, tuple[str, str]],
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        max_rows: int,
    ) -> IpsKeyedResult:
        """The keyed IPS result, aggregated here and parsed by the real row parser."""
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        ids = validate_ips_sources(
            qradar_log_source_ids, max_sources=self._ips_max_sources_per_search
        )
        checked = validate_ips_fields(dimension, fields)
        if max_rows < 1:
            raise ValueError("IPS row limit must be positive")
        search = MockIpsSearch(dimension, ids, start, end)
        self.ips_searches.append(search)
        if self.ips_fault is not None and (fault := self.ips_fault(search)) is not None:
            raise fault

        qid_normalized = checked.get("SIGNATURE") == ("NORMALIZED", "qid")
        first = int(start.timestamp()) // bucket_seconds
        last = int(end.timestamp()) // bucket_seconds
        columns = {
            IpsDimension.DESTINATION_PORT: (
                ("dport", "proto") if "PROTOCOL" in checked else ("dport",)
            ),
            IpsDimension.DESTINATION_IP: ("dip",),
            IpsDimension.SIGNATURE: ("qid", "qname") if qid_normalized else ("sig",),
            IpsDimension.SOURCE_IP: ("sip",),
            IpsDimension.PORT_FANOUT: ("sip",),
            IpsDimension.ACTION: ("act",),
            IpsDimension.TOTALS: (),
        }[dimension]
        logical = {
            "dport": "DESTINATION_PORT",
            "dip": "DESTINATION_IP",
            "sip": "SOURCE_IP",
            "qid": "SIGNATURE",
            "sig": "SIGNATURE",
            "act": "ACTION",
        }
        filtered = [c for c in columns if c in logical]
        if dimension is IpsDimension.PORT_FANOUT:
            filtered.append("dport")
        groups: dict[tuple, dict] = {}
        for source_id in ids:
            for key in range(first, last):
                for event in self._ips_events(source_id, key):
                    if not all(self._ips_populated(logical[c], event[c]) for c in filtered):
                        continue
                    group_key = (source_id, key, *(event[c] for c in columns))
                    group = groups.setdefault(
                        group_key,
                        {
                            "log_source_id": source_id,
                            "k": key,
                            **{c: event[c] for c in columns},
                            "events": 0,
                            "records": 0,
                            "_ports": set(),
                        },
                    )
                    group["events"] += event["events"]
                    group["records"] += event["records"]
                    group["_ports"].add(event["dport"])
        rows = list(groups.items())
        if dimension is IpsDimension.PORT_FANOUT:
            volume = {k: len(g["_ports"]) for k, g in rows}
        else:
            volume = {k: g["events"] for k, g in rows}
        rows.sort(key=lambda item: (-volume[item[0]], str(item[0])))
        raw: list[dict] = []
        for _key, group in rows:
            row = {k: v for k, v in group.items() if k != "_ports"}
            if dimension is IpsDimension.PORT_FANOUT:
                row["distinct_ports"] = len(group["_ports"])
            raw.append(row)
        parsed, truncated = parse_ips_keyed_rows(
            dimension,
            raw[: max_rows + 1],
            source_ids=ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            max_rows=max_rows,
            qid_normalized=qid_normalized,
        )
        if self.ips_force_truncated is not None and self.ips_force_truncated(search):
            truncated = True
        return IpsKeyedResult(
            dimension=dimension,
            qradar_log_source_ids=ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            rows=parsed,
            truncated=truncated,
            query=f"MOCK IPS {dimension.value}",
        )

    def _pw_events(self, scope_id: int, key: int, names: tuple[str, ...]) -> list[dict]:
        """The synthetic events of one scope id and bucket: deterministic.

        A property's values are ``<name>-v0`` .. ``<name>-v7``; some events carry none
        (NULL) or an empty string, the unparsed remainder a real property leaves.
        """
        rng = random.Random(f"{self._seed}:pw:{scope_id}:{key}")  # noqa: S311
        out: list[dict] = []
        for index in range(rng.randint(0, 20)):
            events = rng.randint(1, 300)
            values: dict[str, str | None] = {}
            for name in names:
                # One stream per property, so the events do not change with the names asked
                # for and the totals search sees the very events the grouped search groups.
                roll_rng = random.Random(f"{self._seed}:pw:{scope_id}:{key}:{index}:{name}")  # noqa: S311
                roll = roll_rng.random()
                values[name] = (
                    None
                    if roll < 0.08
                    else ""
                    if roll < 0.12
                    else f"{name}-v{roll_rng.randint(0, 7)}"
                )
            out.append({"values": values, "events": events})
        return out

    async def get_pw_keyed(
        self,
        search: PwSearchKind,
        *,
        scope_kind: PwScopeKind,
        scope_ids: Sequence[int],
        properties: Sequence[tuple[str, str]],
        pinned: Sequence[Sequence[str]] = (),
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        max_rows: int,
    ) -> PwKeyedResult:
        """The keyed property-watch result, aggregated here and parsed by the real parser."""
        # Imported here: `app.property_watch.reference` imports `app.providers.base`, and
        # the providers package imports this module, so a top-level import is circular.
        from app.property_watch.aql import build_query, pinned_components, validate_query_inputs
        from app.property_watch.keyed_rows import parse_pw_keyed_rows

        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        spec = validate_query_inputs(
            search,
            scope_kind=scope_kind,
            scope_ids=scope_ids,
            properties=properties,
            pinned=pinned,
            max_sources=self._pw_max_sources_per_search,
        )
        if max_rows < 1:
            raise ValueError("property-watch row limit must be positive")
        mock_search = MockPwSearch(
            spec.search, spec.scope_kind, spec.scope_ids, start, end
        )
        self.pw_searches.append(mock_search)
        if self.pw_fault is not None and (fault := self.pw_fault(mock_search)) is not None:
            raise fault

        totals = spec.search is PwSearchKind.TOTALS
        names = tuple(reference.name for reference in spec.properties)
        allowed = (
            [set(components) for components in pinned_components(spec)]
            if spec.search is PwSearchKind.PINNED
            else None
        )
        first = int(start.timestamp()) // bucket_seconds
        last = int(end.timestamp()) // bucket_seconds
        groups: dict[tuple, dict] = {}
        for key in range(first, last):
            if self.pw_script is not None:
                events = self.pw_script(key, names)
            else:
                events = [
                    e for sid in spec.scope_ids for e in self._pw_events(sid, key, names)
                ]
            for event in events:
                values = () if totals else tuple(event["values"][name] for name in names)
                if any(value is None for value in values):
                    continue  # `"x" IS NOT NULL`: only the unparsed remainder is NULL
                if allowed is not None and any(
                    value not in allowed[index] for index, value in enumerate(values)
                ):
                    continue
                group = groups.setdefault(
                    (key, *values),
                    {
                        "k": key,
                        "events": 0,
                        **{f"p{i}": value for i, value in enumerate(values)},
                    },
                )
                group["events"] += event["events"]
        ordered = sorted(
            groups.values(),
            key=lambda g: (-g["events"], g["k"], *(str(g.get(f"p{i}")) for i in range(2))),
        )
        parsed, truncated = parse_pw_keyed_rows(
            ordered[: max_rows + 1],
            property_count=len(names) if not totals else 0,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            max_rows=max_rows,
        )
        if self.pw_force_truncated is not None and self.pw_force_truncated(mock_search):
            truncated = True
        # The real builder, so the mock refuses what REST refuses and carries real provenance.
        query = build_query(
            spec,
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            bucket_ms=bucket_seconds * 1000,
            limit=max_rows + 1,
        )
        return PwKeyedResult(
            search=spec.search,
            scope_kind=spec.scope_kind,
            scope_ids=spec.scope_ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            rows=parsed,
            truncated=truncated,
            query=query,
        )

    def _catchall_events(self, source_id: int, key: int) -> list[dict]:
        """The synthetic senders of one catch-all source and bucket: deterministic."""
        rng = random.Random(f"{self._seed}:catchall:{source_id}:{key}")  # noqa: S311
        out: list[dict] = []
        for _ in range(rng.randint(0, 6)):
            events = rng.randint(1, 400)
            out.append(
                {
                    "logsourceid": source_id,
                    "sender": f"mock-host-{rng.randint(0, 11):02d}",
                    "events": events,
                    "records": rng.randint(1, events),
                }
            )
        return out

    async def get_catchall_senders_keyed(
        self,
        *,
        devicetype_ids: Sequence[int],
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        max_rows: int,
    ) -> CatchallKeyedResult:
        """The catch-all result, aggregated here and parsed by the real row parser."""
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        ids = validate_catchall_type_ids(devicetype_ids)
        if max_rows < 1:
            raise ValueError("catch-all row limit must be positive")
        search = MockCatchallSearch(ids, start, end)
        self.catchall_searches.append(search)
        if self.catchall_fault is not None and (fault := self.catchall_fault(search)) is not None:
            raise fault

        first = int(start.timestamp()) // bucket_seconds
        last = int(end.timestamp()) // bucket_seconds
        source_ids = [
            1000 + index
            for index, (_name, type_id, _shape, _facts) in enumerate(_SOURCE_TEMPLATES, start=1)
            if type_id in ids
        ]
        groups: dict[tuple[int, str | None, int], dict] = {}
        for key in range(first, last):
            if self.catchall_script is not None:
                events = self.catchall_script(key)
            else:
                events = [e for sid in source_ids for e in self._catchall_events(sid, key)]
            for event in events:
                group = groups.setdefault(
                    (event["logsourceid"], event["sender"], key),
                    {
                        "logsourceid": event["logsourceid"],
                        "sender": event["sender"],
                        "k": key,
                        "events": 0,
                        "records": 0,
                    },
                )
                group["events"] += event["events"]
                group["records"] += event["records"]
        ordered = sorted(
            groups.values(),
            key=lambda g: (-g["events"], g["logsourceid"], str(g["sender"]), g["k"]),
        )
        parsed, truncated = parse_catchall_rows(
            ordered[: max_rows + 1],
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            max_rows=max_rows,
        )
        if self.catchall_force_truncated is not None and self.catchall_force_truncated(search):
            truncated = True
        return CatchallKeyedResult(
            device_type_ids=ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            rows=parsed,
            truncated=truncated,
            query="MOCK CATCHALL",
        )

    def _geo_events(self, direction: GeoDirection, key: int) -> list[dict]:
        """The synthetic lookups of one direction and bucket: deterministic."""
        rng = random.Random(f"{self._seed}:geo:{direction.value}:{key}")  # noqa: S311
        out = [{"geo": "{}", "events": rng.randint(50, 400)}]
        for code, name, geo_id in _GEO_COUNTRIES:
            if rng.random() < 0.7:
                out.append(
                    {"geo": geo_key(code, name, geo_id), "events": rng.randint(1, 300)}
                )
        return out

    async def get_geo_keyed(
        self,
        start: datetime,
        end: datetime,
        *,
        direction: GeoDirection,
        bucket_seconds: int,
        max_rows: int,
    ) -> GeoKeyedResult:
        """The geo result, aggregated here and parsed by the real row parser."""
        self.require(ProviderCapability.AQL_EXECUTION)
        window_start, window_end = validate_bucketed_metric_window(start, end, bucket_seconds)
        checked = GeoDirection(direction)
        if max_rows < 1:
            raise ValueError("geo row limit must be positive")
        search = MockGeoSearch(checked, window_start, window_end)
        self.geo_searches.append(search)
        if self.geo_fault is not None and (fault := self.geo_fault(search)) is not None:
            raise fault

        first = int(window_start.timestamp()) // bucket_seconds
        last = int(window_end.timestamp()) // bucket_seconds
        groups: dict[tuple[str | None, int], dict] = {}
        for key in range(first, last):
            if self.geo_script is not None:
                events = self.geo_script(checked, key)
            else:
                events = self._geo_events(checked, key)
            for event in events:
                group = groups.setdefault(
                    (event["geo"], key), {"geo": event["geo"], "k": key, "events": 0}
                )
                group["events"] += event["events"]
        ordered = sorted(
            groups.values(), key=lambda g: (-g["events"], str(g["geo"]), g["k"])
        )
        parsed, truncated = parse_geo_rows(
            ordered[: max_rows + 1],
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            max_rows=max_rows,
        )
        if self.geo_force_truncated is not None and self.geo_force_truncated(search):
            truncated = True
        return GeoKeyedResult(
            direction=checked,
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            rows=parsed,
            truncated=truncated,
            query="MOCK GEO",
        )

    async def probe_ips_fields(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        fields: Sequence[IpsFieldProbeRequest],
        max_action_values: int = 20,
    ) -> IpsSourceProbe:
        self.require(ProviderCapability.AQL_EXECUTION)
        unique = list({field.logical_field: field for field in fields}.values())
        if len(unique) > 7:
            raise ValueError("IPS probe exceeds the logical-field bound")
        limit = max(1, min(int(max_action_values), 20))
        action_distribution = (("BLOCK", 70), ("ALLOW", 50))
        probes: list[IpsFieldProbe] = []
        for field in unique:
            normalized = (
                field.reference_kind == "NORMALIZED"
                and field.name in IPS_NORMALIZED_FIELD_ALLOWLIST
            )
            custom = field.reference_kind == "CUSTOM_PROPERTY" and field.name == "IPS_Action"
            if not (normalized or custom) or not PROPERTY_NAME_PATTERN.fullmatch(field.name):
                probes.append(
                    IpsFieldProbe(
                        logical_field=field.logical_field,
                        reference_kind=field.reference_kind,
                        name=field.name,
                        rejected=True,
                        error="QRadar rejected the request as invalid",
                    )
                )
                continue
            values = (
                [
                    IpsProbeValue(value=value, events=events)
                    for value, events in action_distribution[:limit]
                ]
                if field.logical_field == "ACTION"
                else []
            )
            probes.append(
                IpsFieldProbe(
                    logical_field=field.logical_field,
                    reference_kind=field.reference_kind,
                    name=field.name,
                    populated_events=120,
                    generic_name_events=(
                        12
                        if field.logical_field == "SIGNATURE"
                        and field.reference_kind == "NORMALIZED"
                        and field.name == "qid"
                        else None
                    ),
                    action_values=values,
                    values_truncated=len(action_distribution) > limit,
                )
            )
        truncated = any(probe.values_truncated for probe in probes)
        return IpsSourceProbe(
            qradar_log_source_id=qradar_log_source_id,
            window_start=window_start,
            window_end=window_end,
            total_events=120,
            fields=probes,
            truncated=truncated,
            ariel_searches=0,
        )

    async def observe_property_rejection(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        property_names: Sequence[str],
    ) -> list[WafRejectionObservation]:
        """Models the *assumed* appliance behaviour: an unknown name is a 422.

        That is an assumption under verification (docs section 29.9); the mock
        encodes it only so the CLI procedure can be exercised end to end.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        out: list[WafRejectionObservation] = []
        for name in dict.fromkeys(property_names):
            query = f"MOCK REJECTION PROBE {name}"
            if name in self._MOCK_WAF_PROPERTY_VALUES and PROPERTY_NAME_PATTERN.match(name):
                out.append(WafRejectionObservation(
                    property_name=name, outcome="SEARCH_COMPLETED", search_created=True,
                    search_status="COMPLETED", record_count=1, query=query,
                ))
            else:
                out.append(WafRejectionObservation(
                    property_name=name, outcome="REJECTED_AT_CREATE",
                    create_http_status=422, query=query,
                ))
        return out

    def _seasonal_eps(self, when: datetime, shape: str) -> float:
        # ISO weekday 1..7, hour 0..23.
        weekday = when.isoweekday()
        hour = when.hour
        business = weekday <= 5 and 8 <= hour < 18
        base = 50.0 if business else 18.0
        # Deterministic jitter from the interval so history is reproducible.
        seed = int(when.timestamp()) // 300
        jitter_rng = random.Random(f"{shape}:{seed}")  # noqa: S311
        return base * (0.9 + 0.2 * jitter_rng.random())

    def _build_sample(
        self, qid: int, start: datetime, bucket_seconds: int, base_eps: float, shape: str
    ) -> LogSourceMetricSample:
        event_count = int(base_eps * bucket_seconds)
        common: dict = dict(  # noqa: C408 - kwargs dict built incrementally below
            qradar_id=qid,
            bucket_start=start,
            bucket_seconds=bucket_seconds,
            last_event_at=start + timedelta(seconds=bucket_seconds - 5),
            event_delay_seconds=5.0,
            parsed_username_ratio=0.9,
            parsed_source_ip_ratio=0.95,
            unknown_event_count=int(event_count * 0.01),
            stored_event_count=event_count,
            distinct_qid_count=20,
            distinct_username_count=40,
            distinct_source_ip_count=60,
            collection_error_count=0,
            payload_signature=f"sig-{qid}-{int(start.timestamp()) // 300}",
        )

        if shape == "silent":
            common.update(event_count=0, average_eps=0.0, peak_eps=0.0,
                          last_event_at=start - timedelta(hours=6), event_delay_seconds=21600.0,
                          unknown_event_count=0, stored_event_count=0,
                          distinct_qid_count=0, distinct_username_count=0,
                          distinct_source_ip_count=0)
            return LogSourceMetricSample(**common)
        if shape == "disabled":
            common.update(event_count=0, average_eps=0.0, peak_eps=0.0, last_event_at=None,
                          event_delay_seconds=None, unknown_event_count=0, stored_event_count=0,
                          distinct_qid_count=0, distinct_username_count=0,
                          distinct_source_ip_count=0)
            return LogSourceMetricSample(**common)
        if shape == "volume_drop":
            base_eps *= 0.05  # collapse to 5% of normal
        if shape == "spike":
            base_eps *= 20.0
        if shape == "parsing_degraded":
            common.update(parsed_username_ratio=0.15, parsed_source_ip_ratio=0.2,
                          unknown_event_count=int(event_count * 0.6))

        event_count = int(base_eps * bucket_seconds)
        common.update(
            event_count=event_count,
            average_eps=round(base_eps, 2),
            peak_eps=round(base_eps * 1.5, 2),
            stored_event_count=event_count,
        )
        return LogSourceMetricSample(**common)

    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        """Rules shaped to exercise every rule-health classification.

        Each spec targets one branch of the health evaluator, so a developer
        running against the mock sees a populated rule-health page rather than a
        wall of HEALTHY: one noisy, one never-fired, one disabled, one whose
        required telemetry is the deliberately-silent Windows source, and one
        too new to judge.
        """
        # name, mitre, enabled, capacity, days-since-fired (None = never),
        # required log-source type, age in days
        specs: list[tuple[str, list[str], bool, float, int | None, int | None, int]] = [
            ("Excessive Failed Logins", ["T1110"], True, 12.0, 0, 13, 400),
            ("Impossible Travel", ["T1078"], True, 3.0, 1, 145, 300),
            ("Malware Beacon Detected", ["T1071"], True, 0.4, 2, 95, 250),
            ("Cleartext Protocol Usage", ["T1040"], True, 880.0, 0, 12, 200),
            ("Dormant Account Reactivated", ["T1078.002"], False, 0.0, 30, 13, 180),
            ("Data Exfil Over DNS", ["T1048.003"], True, 0.0, None, 95, 150),
            # Depends on the silent dc02 Windows source -> DEPENDENCY_DEGRADED.
            ("Admin Group Membership Change", ["T1098"], True, 1.0, 45, 13, 120),
            # Created two days ago -> INSUFFICIENT_DATA, must not read INACTIVE.
            ("New Detection Under Test", ["T1059"], True, 0.0, None, 2, 2),
        ]
        out: list[AnalyticsRuleDTO] = []
        for idx, (name, mitre, enabled, cap, fired_days, ls_type, age_days) in enumerate(
            specs, start=1
        ):
            last_fired = (
                self._now - timedelta(days=fired_days) if fired_days is not None else None
            )
            out.append(
                AnalyticsRuleDTO(
                    qradar_id=5000 + idx,
                    name=name,
                    description=f"Mock analytics rule for {', '.join(mitre)}",
                    rule_type="EVENT",
                    enabled=enabled,
                    origin="USER",
                    is_building_block=False,
                    owner="soc-team",
                    average_capacity=cap,
                    # Disabled rules read zero in QRadar. The benchmark is taken
                    # on a schedule, so the timestamp is a day old, not "now".
                    base_capacity=cap,
                    base_host_id=53,
                    capacity_timestamp=self._now - timedelta(days=1),
                    categories=["Authentication"] if "T1110" in mitre else [],
                    # QRadar exposes no MITRE mapping; the SOC owns it. The mock
                    # matches that contract and returns none here.
                    mitre_techniques=[],
                    created_at=self._now - timedelta(days=age_days),
                    modified_at=self._now - timedelta(days=idx * 3),
                    generates_offense=cap > 0,
                    response_actions=["CREATE_OFFENSE"] if cap > 0 else [],
                    building_block_ids=[6000 + (idx % 3)],
                    log_source_type_ids=[ls_type] if ls_type else [],
                    last_triggered_at=last_fired,
                    event_contribution_count=int(cap * 24),
                    offense_contribution_count=int(cap // 4),
                )
            )
        return out

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        specs = [
            (6000, "BB:CategoryDefinition: Authentication Failures", True),
            (6001, "BB:HostDefinition: Domain Controllers", True),
            (6002, "BB:NetworkDefinition: Untrusted Networks", False),  # disabled BB
        ]
        return [
            AnalyticsRuleDTO(
                qradar_id=bb_id,
                name=name,
                description="Mock building block",
                rule_type="BUILDINGBLOCK",
                enabled=enabled,
                origin="SYSTEM",
                is_building_block=True,
                owner="soc-team",
                created_at=self._now - timedelta(days=500),
            )
            for bb_id, name, enabled in specs
        ]

    # -- offenses -----------------------------------------------------------
    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        """A spread of ages, magnitudes and assignment states.

        Deliberately includes an unassigned 30-day-old critical offense so the
        SLA and aging views have something to show, and a closed one so the
        history and closing-reason paths are exercised.
        """
        # description, status, magnitude, assignee, age minutes
        specs: list[tuple[str, str, int, str | None, int]] = [
            ("Multiple Failed Logins then Success", "OPEN", 8, None, 240),
            ("Malware Beacon to Known C2", "OPEN", 9, "analyst.jones", 30),
            ("Suspicious Admin Group Change", "OPEN", 6, None, 1440),
            ("Data Transfer to Untrusted Host", "OPEN", 10, None, 43200),
            ("Privilege Escalation Attempt", "OPEN", 7, "analyst.lee", 720),
            ("Port Scan from Internal Host", "CLOSED", 4, "analyst.lee", 60),
        ]
        out: list[OffenseDTO] = []
        for idx, (desc, status, mag, assignee, age_min) in enumerate(specs, start=1):
            if open_only and status != "OPEN":
                continue
            start = self._now - timedelta(minutes=age_min)
            # Deterministic per-offense update time so repeat collections with
            # an unchanged offense produce an unchanged content hash.
            updated = self._now - timedelta(minutes=idx * 3)
            if updated_since is not None and updated <= updated_since:
                continue
            closed = status == "CLOSED"
            out.append(
                OffenseDTO(
                    qradar_id=9000 + idx,
                    description=desc,
                    status=status,
                    magnitude=mag,
                    severity=mag,
                    credibility=4 + (idx % 6),
                    relevance=3 + (idx % 6),
                    assigned_to=assignee,
                    offense_type=3,
                    offense_type_name="Source IP",
                    offense_source="10.20.30." + str(40 + idx),
                    source_network="internal.corporate",
                    event_count=50 * idx + 17,
                    flow_count=10 * idx,
                    device_count=1 + (idx % 6),
                    source_count=1 + (idx % 4),
                    destination_count=1 + (idx % 3),
                    start_time=start,
                    last_updated_time=updated,
                    close_time=updated if closed else None,
                    closing_reason_id=1 if closed else None,
                    closing_reason="False Positive, Tuned" if closed else None,
                    categories=["Authentication", "Suspicious Activity"],
                    source_addresses=["203.0.113." + str(idx)],
                    local_destination_addresses=["10.20.30." + str(40 + idx)],
                    usernames=[f"user{idx:02d}", "svc-backup"],
                    log_source_ids=[1001 + (idx % 9)],
                    rule_ids=[5000 + idx],
                )
            )
        return out

    # -- Ariel lifecycle ----------------------------------------------------
    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        self._search_counter += 1
        search_id = f"mock-{self._search_counter:06d}"
        self._searches[search_id] = (aql, self._now)
        return ArielSearchHandleDTO(search_id=search_id, status="EXECUTE", progress=0)

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        if search_id not in self._searches:
            return ArielSearchStatusDTO(
                search_id=search_id, status="ERROR", error_messages=["unknown search id"]
            )
        aql, _ = self._searches[search_id]
        rows = self._deterministic_rows(aql)
        # Mock completes immediately — the polling loop must still handle a
        # COMPLETED-on-first-poll response correctly.
        return ArielSearchStatusDTO(
            search_id=search_id, status="COMPLETED", progress=100, record_count=len(rows)
        )

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        aql, _ = self._searches.get(search_id, ("", self._now))
        rows = self._deterministic_rows(aql)
        truncated = len(rows) > max_rows
        cols = list(rows[0].keys()) if rows else []
        return ArielSearchResultsDTO(
            search_id=search_id,
            columns=cols,
            rows=rows[:max_rows],
            total_count=min(len(rows), max_rows),
            truncated=truncated,
        )

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.require(ProviderCapability.AQL_EXECUTION)
        self._searches.pop(search_id, None)

    def _deterministic_rows(self, aql: str) -> list[dict]:
        """Return stable pseudo-results keyed off the query text.

        The row shape mimics a typical aggregate AQL (a GROUP BY producing a
        label and a count), which is what our scheduled searches use.
        """
        digest = hashlib.sha256(aql.encode()).hexdigest()
        n = int(digest[:2], 16) % 8  # 0..7 rows, deterministic per query
        rng = random.Random(digest)  # noqa: S311
        return [
            {"grouping": f"host-{i}", "event_count": rng.randint(1, 500)}
            for i in range(n)
        ]
