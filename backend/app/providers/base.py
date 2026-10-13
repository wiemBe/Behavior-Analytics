"""Provider abstraction.

Three interchangeable backends implement `QRadarProvider`:

  * QRadarRestProvider — deterministic background collection and scheduled AQL
    execution. Owns the full Ariel search lifecycle.
  * QRadarMCPProvider  — inventory retrieval only, over the GET-only IBM MCP
    service, for future AI-agent investigations. Declines AQL execution.
  * MockQRadarProvider — deterministic in-memory data for development and tests.

The application depends on this interface and on `ProviderCapability`, never on
a concrete provider. Code asks "can this backend run AQL?" rather than "is this
REST?", so capability differences are handled explicitly instead of by isinstance
checks scattered through services.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum

from app.models.enums import IpsDimension
from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    CustomPropertyDTO,
    DimensionAggregate,
    InstanceInfoDTO,
    IpsFieldProbeRequest,
    IpsKeyedResult,
    IpsSourceProbe,
    LogSourceDTO,
    LogSourceGroupDTO,
    LogSourceMetricBucketBatch,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
    RuleMatchBatch,
    WafBreakdownBatch,
    WafBreakdownBucketBatch,
    WafObservationBatch,
    WafObservationBucketBatch,
    WafRejectionObservation,
    WafSourceProbe,
    WindowedRuleMatches,
)

#: The only shape a site-authored QRadar property name may take before it is
#: interpolated into AQL. Letters, digits, underscore, space and hyphen; anything
#: else -- a quote, a comma, a parenthesis -- is rejected rather than escaped,
#: because an allowlist a reviewer can verify by eye beats a quoting scheme that
#: has to be trusted. Property names are never expressions.
PROPERTY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$")

#: Upper bound on a WAF property probe window. Validation is a spot check, not a
#: survey, and a small window keeps it cheap on a loaded appliance.
WAF_PROBE_MAX_WINDOW_SECONDS = 3600

#: Normalized Ariel identifiers IPS profiles may name. They are quoted only
#: after exact membership validation; arbitrary frontend text never reaches AQL.
IPS_NORMALIZED_FIELD_ALLOWLIST = (
    "sourceip",
    "destinationip",
    "destinationport",
    "sourceport",
    "protocolid",
    "qid",
    "severity",
    "category",
)
IPS_PROBE_MAX_WINDOW_SECONDS = WAF_PROBE_MAX_WINDOW_SECONDS

#: The logical fields each keyed IPS search reads: (required, optional). The plan
#: groups sources by exactly these references, and both providers refuse a search
#: that lacks a required one. TOTALS reads no field: it counts every event.
IPS_DIMENSION_FIELDS: dict[IpsDimension, tuple[tuple[str, ...], tuple[str, ...]]] = {
    IpsDimension.TOTALS: ((), ()),
    IpsDimension.DESTINATION_PORT: (("DESTINATION_PORT",), ("PROTOCOL",)),
    IpsDimension.DESTINATION_IP: (("DESTINATION_IP",), ()),
    IpsDimension.SIGNATURE: (("SIGNATURE",), ()),
    IpsDimension.SOURCE_IP: (("SOURCE_IP",), ()),
    IpsDimension.PORT_FANOUT: (("SOURCE_IP", "DESTINATION_PORT"), ()),
    IpsDimension.ACTION: (("ACTION",), ()),
}


class ProviderCapability(StrEnum):
    """What a given backend can actually do.

    MCP intentionally lacks AQL_EXECUTION: per the capability matrix, running
    Ariel searches is a POST surface we keep off the LLM-facing path and drive
    only through REST.
    """

    INVENTORY = "INVENTORY"          # log sources, rules, instance info
    OFFENSES = "OFFENSES"
    AQL_EXECUTION = "AQL_EXECUTION"  # create/poll/fetch Ariel searches
    CONFIG_SNAPSHOTS = "CONFIG_SNAPSHOTS"
    LOG_SOURCE_GROUPS = "LOG_SOURCE_GROUPS"
    CUSTOM_PROPERTY_CATALOG = "CUSTOM_PROPERTY_CATALOG"  # custom-property names, never bodies


class LogSourceTelemetry(StrEnum):
    """Which per-bucket log-source quantities a provider actually measures.

    `LogSourceMetricSample` has a field for every quantity any detector reads,
    and most of them default to 0 or None. A default is not a measurement: the
    REST provider's Ariel aggregate never computes an unknown-event count, so a
    stored `unknown_event_count=0` is an absence dressed as a zero. Declaring
    the measured set lets the anomaly engine run a detector only on inputs that
    were really observed, instead of judging a fabricated zero as healthy.
    """

    #: Event volume for the bucket: `event_count` and the `average_eps` derived
    #: from it. Every bucket row carries this by construction.
    EVENT_VOLUME = "EVENT_VOLUME"
    #: Timestamp of the newest event in the bucket.
    LAST_EVENT_TIME = "LAST_EVENT_TIME"
    #: Peak events-per-second within the bucket.
    PEAK_RATE = "PEAK_RATE"
    #: Events QRadar could not classify.
    UNKNOWN_EVENT_COUNT = "UNKNOWN_EVENT_COUNT"
    #: Username / source-IP field-extraction ratios.
    PARSE_RATIOS = "PARSE_RATIOS"
    #: Distinct source IPs in the bucket.
    DISTINCT_SOURCE_IPS = "DISTINCT_SOURCE_IPS"
    #: Delay between an event happening and QRadar receiving it. Not the gap
    #: between the newest event and the bucket end, which is bounded by the
    #: bucket width and says nothing about ingestion lag.
    INGESTION_DELAY = "INGESTION_DELAY"
    #: Errors raised while collecting the bucket that still produced a row.
    COLLECTION_ERRORS = "COLLECTION_ERRORS"
    #: Hash of a representative payload.
    PAYLOAD_SIGNATURE = "PAYLOAD_SIGNATURE"


class ProviderError(RuntimeError):
    """Base class for all provider failures.

    `status_code` is the upstream HTTP status when the failure was an HTTP
    response, for callers that must record *how* QRadar refused something. The
    message never carries upstream text.
    """

    def __init__(self, message: str = "", *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderUnavailableError(ProviderError):
    """Transient connectivity/timeout failure — safe to retry."""


class ProviderAuthError(ProviderError):
    """Authentication/authorization failure — not retryable."""


class ProviderQueryRejectedError(ProviderError):
    """QRadar refused the query as invalid (HTTP 422) before running it.

    Distinct from a query that ran and failed, because only a rejection says
    something about the query itself: an Ariel query naming a property that does
    not exist is rejected, while a timeout or a failed search proves nothing
    about whether the property exists.
    """


class CapabilityNotSupportedError(ProviderError):
    """The backend does not implement the requested capability."""


class QRadarProvider(ABC):
    """Read-only interface to a QRadar deployment.

    No method mutates QRadar state. There is intentionally no create/update/
    delete surface on this interface: the platform operates read-only, and the
    write tools the MCP server exposes are disabled by policy.
    """

    #: Concrete providers declare what they support.
    capabilities: frozenset[ProviderCapability] = frozenset()
    #: The quantities `get_log_source_metrics` really measures. Anything absent
    #: is left at its DTO default and must not be judged by a detector.
    log_source_telemetry: frozenset[LogSourceTelemetry] = frozenset()
    #: How `event_count` is aggregated, recorded as bucket provenance so a
    #: reader can tell events (`SUM(eventcount)`) from Ariel records
    #: (`COUNT(*)`). None when the provider does not aggregate Ariel at all.
    event_count_aggregation: str | None = None

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def require(self, capability: ProviderCapability) -> None:
        if not self.supports(capability):
            raise CapabilityNotSupportedError(
                f"{type(self).__name__} does not support {capability}"
            )

    # -- lifecycle ----------------------------------------------------------
    async def aclose(self) -> None:
        """Release any held resources (HTTP clients). Default no-op."""

    # -- health / inventory -------------------------------------------------
    @abstractmethod
    async def get_instance_info(self) -> InstanceInfoDTO:
        ...

    @abstractmethod
    async def list_log_sources(self) -> list[LogSourceDTO]:
        ...

    @abstractmethod
    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        ...

    @abstractmethod
    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        ...

    async def list_log_source_groups(self) -> list[LogSourceGroupDTO]:
        """Log-source group hierarchy, when exposed by the backend."""
        self.require(ProviderCapability.LOG_SOURCE_GROUPS)
        raise CapabilityNotSupportedError(
            f"{type(self).__name__} does not support log-source groups"
        )

    async def list_custom_properties(
        self, *, log_source_type_id: int | None = None
    ) -> list[CustomPropertyDTO]:
        """Custom-property names and the log-source types they apply to.

        Names, types and flags only: never a regex, payload or expression body.
        """
        self.require(ProviderCapability.CUSTOM_PROPERTY_CATALOG)
        raise CapabilityNotSupportedError(
            f"{type(self).__name__} does not support the custom-property catalog"
        )

    async def get_log_source_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[LogSourceMetricSample]:
        """Per-log-source metric samples for one collection interval.

        Requires INVENTORY. The REST provider derives these from AQL aggregates
        (Phase 3); the mock synthesizes them deterministically.
        """
        self.require(ProviderCapability.INVENTORY)
        raise NotImplementedError

    async def get_log_source_metrics_bucketed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        bucket_seconds: int,
        max_rows: int,
    ) -> LogSourceMetricBucketBatch:
        """Per-source metric samples keyed by bucket start for one bounded window."""
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_dimension_aggregates(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        dimensions: Sequence[str],
        top_n: int,
    ) -> list[DimensionAggregate]:
        """Bounded per-dimension event counts for one log source and window.

        The evidence primitive behind "what changed during the anomaly?". Each
        dimension becomes one aggregate Ariel query grouped by that field, with
        results capped at `top_n`.

        Requires AQL_EXECUTION, so this never runs through MCP: Ariel is a POST
        surface deliberately kept off the LLM-facing path.

        Implementations must return an entry for every requested dimension,
        marking the ones the source's DSM does not populate as
        ``available=False`` rather than omitting them. Silence about a field is
        indistinguishable from a measured zero, and only one of those is true.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_rule_matches(
        self, window_start: datetime, window_end: datetime, *, max_rows: int
    ) -> RuleMatchBatch:
        """How many events matched each rule or building block in one window.

        One bounded aggregate over `creEventList`, capped at `max_rows`. A full
        page sets ``truncated``: the batch is then a prefix, and a rule missing
        from it proves nothing. Events that matched no rule (a null id) are not
        rows.

        Requires AQL_EXECUTION, so this never runs through MCP. Read-only: it
        only searches events.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_rule_matches_windowed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        window_seconds: int,
        max_rows: int,
    ) -> WindowedRuleMatches:
        """`get_rule_matches` for many whole windows in one keyed search.

        The span must be a whole number of `window_seconds` windows, aligned to
        the UTC grid exactly as `floor_to_window` aligns them; anything else is
        a ValueError before any request. One batch comes back per window, keyed
        by its start. The row cap covers the span as a whole: more than
        ``max_rows`` raw rows set ``truncated``, and the batches are then a
        prefix the caller must not keep. A key outside the span, or one that is
        not an integer, fails the whole search with a ProviderError.

        Requires AQL_EXECUTION, so this never runs through MCP. Read-only: it
        only searches events.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

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
        """Bounded per-endpoint request volume for one WAF source and window.

        The property names are passed in rather than hard-coded because they are
        site-authored custom properties: QRadar display names do not follow the
        LEEF keys a device emits, and assuming otherwise produced an entire
        round of zero-population measurements. They are validated against a
        strict allowlist pattern before reaching any query text.

        Requires AQL_EXECUTION, so this never runs through MCP.

        Implementations must return raw, un-normalized values -- normalization
        is deterministic application logic (app.waf.normalize) and must not be
        pushed into AQL where it cannot be unit-tested.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

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
        """One bounded breakdown of one WAF source's requests for one window.

        Groups by the raw endpoint key plus one field -- a site-authored
        property for ACTION and RESPONSE_CODE, QRadar's normalized `sourceip` for
        SOURCE_IP (`breakdown_property` is then ignored) -- over events that
        carry a value for it, ordered by `SUM(eventcount)` and capped at
        `max_groups`. Values are raw: judging them is application logic.

        Requires AQL_EXECUTION, so this never runs through MCP.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

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
        """`get_waf_observations` for many whole buckets in one keyed search.

        The span must be a whole number of epoch-aligned `bucket_seconds`
        buckets; anything else is a ValueError before any request. One batch
        comes back per bucket, keyed by its start, each after the per-bucket
        top-`max_groups` cut and with the per-bucket ``truncated`` rule of the
        single-bucket search (``>= max_groups`` groups). The row budget
        `max_rows` covers the span as a whole: more rows set ``truncated`` on
        the result, and the buckets are then empty, to be split by the caller.
        A key outside the span, or one that is not an integer, fails the whole
        search with a ProviderError.

        Requires AQL_EXECUTION, so this never runs through MCP. Read-only.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

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
        """`get_waf_breakdown` for many whole buckets in one keyed search.

        The same contract as `get_waf_observations_bucketed`.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def probe_waf_properties(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        property_names: Sequence[str],
        max_values: int = 50,
    ) -> WafSourceProbe:
        """Bounded read-only evidence about site-authored WAF properties.

        For one log source over a small window (at most
        `WAF_PROBE_MAX_WINDOW_SECONDS`): the source's total event volume, and per
        property whether QRadar accepts it, how many events carry a value, how
        many distinct values there are, and the most frequent values. A query
        failure is reported per property rather than raised, so one bad name does
        not hide the evidence about the others.

        Returns raw values for the caller to judge and discard. Nothing here may
        be persisted as returned.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def probe_ips_fields(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        fields: Sequence[IpsFieldProbeRequest],
        max_action_values: int = 20,
    ) -> IpsSourceProbe:
        """Bounded read-only population evidence for an IPS field mapping.

        Implementations return aggregate counts only, except for the bounded
        ACTION value set the operator needs to author its value map. Ariel is
        required, so MCP fails closed through the capability check.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

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
        """One fleet-wide keyed IPS search for a dimension (design D.5).

        `fields` maps a logical field (`IPS_DIMENSION_FIELDS`) to its
        ``(kind, name)`` reference, and is validated again here before any name
        is quoted. The span must be whole epoch-aligned `bucket_seconds`
        buckets; the source count is capped at `ips_max_sources_per_search`.
        Rows carry the bucket start and volume (`SUM(eventcount)`), ordered by
        volume; more than `max_rows` rows set ``truncated`` and only the first
        `max_rows` are kept. A key outside the span, a non-integer key or a
        log source that was not asked for fails the whole search with a
        ProviderError.

        Requires AQL_EXECUTION, so this never runs through MCP. Read-only.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def observe_property_rejection(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        property_names: Sequence[str],
    ) -> list[WafRejectionObservation]:
        """How QRadar answers an Ariel query naming each property. Read-only.

        For the operator procedure that verifies what a nonexistent property
        looks like on a real appliance (docs/PHASE-B-WAF-BEHAVIOR.md section
        29.9). Records the HTTP status of the create, whether a search was
        created and how it ended -- never a result row, so no event value is
        fetched.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    @abstractmethod
    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        ...

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        """Building blocks, where the backend exposes them separately.

        QRadar returns building blocks inside `list_rules` as well; a backend
        with no distinct endpoint may return an empty list rather than fail.
        """
        self.require(ProviderCapability.INVENTORY)
        return []

    async def get_rule(self, qradar_id: int) -> AnalyticsRuleDTO | None:
        self.require(ProviderCapability.INVENTORY)
        raise NotImplementedError

    async def validate_connection(self) -> InstanceInfoDTO:
        """Prove credentials, TLS and reachability with one cheap call."""
        return await self.get_instance_info()

    # -- offenses -----------------------------------------------------------
    @abstractmethod
    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        """Offenses, optionally narrowed to those updated since a watermark.

        `updated_since` is what makes collection incremental; a backend that
        cannot filter server-side must still honour it by filtering locally, so
        callers can rely on the result never predating the watermark.
        """

    async def get_offense(self, qradar_id: int) -> OffenseDTO | None:
        self.require(ProviderCapability.OFFENSES)
        raise NotImplementedError

    async def list_offense_types(self) -> dict[int, str]:
        self.require(ProviderCapability.OFFENSES)
        return {}

    async def list_offense_closing_reasons(self) -> dict[int, str]:
        self.require(ProviderCapability.OFFENSES)
        return {}

    # -- AQL / Ariel (guarded by AQL_EXECUTION capability) ------------------
    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError
