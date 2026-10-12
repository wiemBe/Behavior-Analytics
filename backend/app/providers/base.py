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
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    DimensionAggregate,
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceGroupDTO,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
    WafBreakdownBatch,
    WafObservationBatch,
    WafRejectionObservation,
    WafSourceProbe,
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

    async def get_log_source_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[LogSourceMetricSample]:
        """Per-log-source metric samples for one collection interval.

        Requires INVENTORY. The REST provider derives these from AQL aggregates
        (Phase 3); the mock synthesizes them deterministically.
        """
        self.require(ProviderCapability.INVENTORY)
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
