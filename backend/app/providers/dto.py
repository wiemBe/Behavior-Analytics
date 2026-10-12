"""Provider data-transfer objects.

Every provider returns these normalized shapes, never raw QRadar payloads. This
is the contract that lets REST, MCP and Mock be genuinely interchangeable: a
service consuming a `LogSourceDTO` cannot tell, and must not care, which backend
produced it.

These are transport DTOs, deliberately distinct from both the SQLAlchemy models
(persistence) and the API schemas (presentation). Keeping the three layers
separate is what stops QRadar's field naming from leaking end to end.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProviderDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class InstanceInfoDTO(ProviderDTO):
    version: str | None = None
    build: str | None = None
    reachable: bool = True
    raw_about: dict = Field(default_factory=dict)


class LogSourceDTO(ProviderDTO):
    qradar_id: int
    name: str
    description: str | None = None
    type_id: int | None = None
    type_name: str | None = None
    protocol_type_id: int | None = None
    enabled: bool = True
    status: str | None = None
    credibility: int | None = None
    target_event_collector_id: int | None = None
    average_eps: float | None = None
    last_event_time: datetime | None = None
    qradar_created_at: datetime | None = None
    qradar_modified_at: datetime | None = None
    auto_discovered: bool | None = None
    group_ids: list[int] = Field(default_factory=list)
    identifier: str | None = None
    internal: bool | None = None


class LogSourceTypeDTO(ProviderDTO):
    type_id: int
    name: str
    internal: bool = False
    custom: bool = False


class LogSourceGroupDTO(ProviderDTO):
    qradar_id: int
    name: str
    description: str | None = None
    parent_qradar_id: int | None = None
    assignable: bool = False


class DimensionValueCount(ProviderDTO):
    """One value of one field, and how many events carried it in a window."""

    value: str
    count: int
    #: Friendlier form where the backend supplies one (QID -> event name). Never
    #: synthesized: a missing label stays None rather than becoming the raw
    #: value dressed up as a name.
    label: str | None = None


class DimensionAggregate(ProviderDTO):
    """Bounded aggregate over one explanation dimension for one window.

    ``available=False`` means the field is not populated in this source's DSM
    output. That is materially different from "the field exists and no events
    carried a value", so the two must never collapse into an empty list.
    """

    dimension: str
    available: bool = True
    values: list[DimensionValueCount] = Field(default_factory=list)
    #: Distinct values in the window. May exceed len(values) when the result was
    #: capped, which is why it is measured separately rather than inferred.
    distinct_count: int = 0
    #: Total events in the window, across all values of this dimension.
    total_count: int = 0
    #: The configured value cap was hit, so `values` is a prefix of the truth.
    truncated: bool = False
    #: The AQL that produced this aggregate. Non-secret; recorded as provenance.
    query: str | None = None
    #: Sanitized failure reason when the aggregate could not be produced.
    error: str | None = None


class LogSourceMetricSample(ProviderDTO):
    """One log source's observed metrics over a single collection interval.

    Field names mirror LogSourceMetric so the collector maps them directly. The
    REST provider derives these from AQL aggregates over the interval; the mock
    synthesizes them deterministically.
    """

    qradar_id: int
    bucket_start: datetime
    bucket_seconds: int = 300
    event_count: int = 0
    average_eps: float = 0.0
    peak_eps: float = 0.0
    last_event_at: datetime | None = None
    event_delay_seconds: float | None = None
    unknown_event_count: int = 0
    stored_event_count: int = 0
    parsed_username_ratio: float | None = None
    parsed_source_ip_ratio: float | None = None
    distinct_qid_count: int = 0
    distinct_username_count: int = 0
    distinct_source_ip_count: int = 0
    collection_error_count: int = 0
    # Hash of a representative payload, for REPEATED_PAYLOAD detection.
    payload_signature: str | None = None


class LogSourceMetricBucketBatch(ProviderDTO):
    """One bounded Ariel result split into its original metric buckets."""

    buckets: dict[datetime, list[LogSourceMetricSample]] = Field(default_factory=dict)
    truncated: bool = False
    rows_returned: int = 0
    aql: str


class WafRequestGroup(ProviderDTO):
    """One (raw URL, method) group of WAF requests over a bounded window.

    Deliberately *raw*: normalization is an application concern (see
    app.waf.normalize), because the rules are not expressible in AQL and must
    stay deterministic and unit-testable. `raw_url` therefore may still carry a
    query string and must be normalized before any value derived from it is
    persisted. It exists only for the lifetime of one collection call.

    `request_count` is SUM(eventcount), never COUNT(*): QRadar coalesces
    identical events into one Ariel record carrying a multiplier.
    """

    raw_url: str | None = None
    raw_method: str | None = None
    raw_virtual_host: str | None = None
    request_count: int = 0
    #: Physical Ariel rows behind `request_count`. Kept for provenance so an
    #: operator can see how hard this group coalesced; never used as volume.
    record_count: int = 0
    distinct_client_count: int | None = None


class WafObservationBatch(ProviderDTO):
    """Everything one WAF log source produced over one bucket.

    `total_events` is the source's whole volume for the window, including events
    whose URL or method did not parse. `groups` covers only the parseable ones,
    so `sum(g.request_count) / total_events` is the endpoint coverage ratio --
    the number that decides whether the bucket is COMPLETE or PARTIAL. Deriving
    coverage from a second query rather than assuming 1.0 is the whole point:
    a bucket where a third of the traffic had no path is not a whole observation
    of any endpoint in it.
    """

    qradar_log_source_id: int
    window_start: datetime
    window_end: datetime
    groups: list[WafRequestGroup] = Field(default_factory=list)
    total_events: int = 0
    #: The group cap was hit, so `groups` is a prefix of the truth and the
    #: bucket must be marked PARTIAL rather than presented as whole.
    truncated: bool = False
    query: str | None = None
    #: The AQL behind `total_events`. Recorded separately because coverage --
    #: and therefore COMPLETE vs PARTIAL -- is derived from it, and provenance
    #: that names only the grouped query cannot justify the completeness verdict.
    total_query: str | None = None
    #: Ariel searches issued to produce this batch. 0 for a provider that does
    #: not search Ariel (mock). Reported for query-budget observability.
    ariel_searches: int = 0


class RuleMatchRow(ProviderDTO):
    """One rule-or-building-block id and how many events matched it in a window."""

    qradar_rule_id: int
    #: Ariel records, `COUNT(*)`. Provenance only: QRadar coalesces identical
    #: events into one record, so this is not a volume.
    records: int
    #: Events, `SUM(eventcount)`. The quantity a rule's match volume is read from.
    events: int


class RuleMatchBatch(ProviderDTO):
    """Everything one `creEventList` search returned for one window.

    `creEventList` is expanded by Ariel, so an event that matched several rules
    is counted once per rule. Rules and building blocks share one id space, so
    the batch holds ids only; joining them to names and types is the caller's
    job, because `RULENAME()` is unreliable.
    """

    window_start: datetime
    window_end: datetime
    rows: list[RuleMatchRow] = Field(default_factory=list)
    #: The row cap was reached, so `rows` is a prefix of the truth: a rule absent
    #: from a truncated batch may still have matched.
    truncated: bool = False
    #: The effective row cap the search ran with.
    row_cap: int = 0
    #: Rows dropped because the id was missing or not an integer. A null id is
    #: not counted here: it is the events that matched no rule at all.
    malformed_rows: int = 0
    #: Version of the stored AQL, so a stored window can be re-justified.
    query_version: str = ""
    query: str | None = None
    ariel_searches: int = 0


class WafBreakdownGroup(ProviderDTO):
    """One (raw URL, method[, host], value) group of one breakdown. Transient.

    `raw_value` is the action value, the response-code value or the source IP,
    depending on the breakdown, and is raw: the collector judges it (against the
    action map, as a three-digit status, as an address) and never persists an
    action or response-code value verbatim.
    """

    raw_url: str | None = None
    raw_method: str | None = None
    raw_virtual_host: str | None = None
    raw_value: str | None = None
    request_count: int = 0


class WafBreakdownBatch(ProviderDTO):
    """One breakdown search over one bucket.

    Only events carrying a value for the breakdown field are counted (`IS NOT
    NULL` in the query), so for each endpoint the groups' sum is the
    denominator and the matching groups' sum the numerator -- both from this one
    search, never divided across two separately timed ones.
    """

    breakdown: str
    qradar_log_source_id: int
    window_start: datetime
    window_end: datetime
    groups: list[WafBreakdownGroup] = Field(default_factory=list)
    #: The group cap was hit: every value derived from `groups` is a lower bound.
    truncated: bool = False
    query: str | None = None
    ariel_searches: int = 0


class WafPropertyValue(ProviderDTO):
    """One raw property value and the events that carried it. Transient."""

    value: str
    events: int


class WafPropertyProbe(ProviderDTO):
    """What a bounded probe established about one site-authored property.

    `rejected` and `error` are different findings. A rejected query means QRadar
    refused the property name; an error means the question went unanswered.
    """

    property_name: str
    rejected: bool = False
    error: str | None = None
    #: Events in the window carrying a non-NULL value. None when not measured.
    populated_events: int | None = None
    distinct_values: int | None = None
    #: Most frequent values, capped. Raw and transient: judged, then discarded.
    values: list[WafPropertyValue] = Field(default_factory=list)
    values_truncated: bool = False
    queries: list[str] = Field(default_factory=list)


class WafSourceProbe(ProviderDTO):
    qradar_log_source_id: int
    window_start: datetime
    window_end: datetime
    #: None when the total could not be measured; 0 is a measured empty window.
    total_events: int | None = None
    total_error: str | None = None
    total_query: str | None = None
    properties: list[WafPropertyProbe] = Field(default_factory=list)


class WafRejectionObservation(ProviderDTO):
    """What QRadar did with one Ariel query naming one property.

    `outcome` is one of: `REJECTED_AT_CREATE` (the create returned 4xx),
    `CREATE_FAILED` (transport failure or 5xx -- nothing learned),
    `SEARCH_FAILED` (created, then ended in an error state), `SEARCH_COMPLETED`
    (created and completed: QRadar accepted the name), `TIMED_OUT`.
    """

    property_name: str
    outcome: str
    create_http_status: int | None = None
    search_created: bool = False
    search_status: str | None = None
    #: Rows the completed search matched, as QRadar reported it. A count only.
    record_count: int | None = None
    query: str


class OffenseDTO(ProviderDTO):
    qradar_id: int
    description: str | None = None
    status: str | None = None
    magnitude: int | None = None
    severity: int | None = None
    credibility: int | None = None
    relevance: int | None = None
    assigned_to: str | None = None
    offense_type: int | None = None
    offense_type_name: str | None = None
    offense_source: str | None = None
    source_network: str | None = None
    event_count: int | None = None
    flow_count: int | None = None
    device_count: int | None = None
    # QRadar reports these as counts independently of the (capped) address
    # lists, so a wide offense still reports its true breadth.
    source_count: int | None = None
    destination_count: int | None = None
    start_time: datetime | None = None
    last_updated_time: datetime | None = None
    close_time: datetime | None = None
    closing_reason_id: int | None = None
    closing_reason: str | None = None
    categories: list[str] = Field(default_factory=list)
    source_addresses: list[str] = Field(default_factory=list)
    local_destination_addresses: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    log_source_ids: list[int] = Field(default_factory=list)
    rule_ids: list[int] = Field(default_factory=list)


class AnalyticsRuleDTO(ProviderDTO):
    qradar_id: int
    name: str
    description: str | None = None
    rule_type: str | None = None
    enabled: bool = True
    origin: str | None = None
    is_building_block: bool = False
    owner: str | None = None
    average_capacity: float | None = None
    categories: list[str] = Field(default_factory=list)
    mitre_techniques: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    modified_at: datetime | None = None

    # Whether the rule's response configuration creates/dispatches an offense.
    # A rule that fires but generates no offense is a real and distinct state
    # from one that never fires, and rule health must not conflate them.
    generates_offense: bool | None = None
    response_actions: list[str] = Field(default_factory=list)
    # Building blocks this rule tests. QRadar exposes rule dependencies only
    # partially; anything the provider could not establish authoritatively is
    # reported separately as an inference by the collector, never merged here.
    building_block_ids: list[int] = Field(default_factory=list)
    log_source_type_ids: list[int] = Field(default_factory=list)

    last_triggered_at: datetime | None = None
    event_contribution_count: int | None = None
    offense_contribution_count: int | None = None


class ArielSearchHandleDTO(ProviderDTO):
    """Handle returned when an Ariel search is created."""

    search_id: str
    status: str
    progress: int = 0


class ArielSearchStatusDTO(ProviderDTO):
    search_id: str
    status: str  # WAIT | EXECUTE | SORTING | COMPLETED | CANCELED | ERROR
    progress: int = 0
    record_count: int | None = None
    error_messages: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in {"COMPLETED", "CANCELED", "ERROR"}

    @property
    def is_success(self) -> bool:
        return self.status == "COMPLETED"


class ArielSearchResultsDTO(ProviderDTO):
    """One page of Ariel rows.

    `total_count` is the number of rows returned, not the size of the full
    result. When `truncated` is true the search had more rows than were fetched
    and `total_count` is only a lower bound; every reader must treat it as one.
    """

    search_id: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


class WindowedRuleMatches(ProviderDTO):
    """What one keyed `creEventList` search returned for many whole windows.

    `batches` holds one entry per window of the span, keyed by the window's start,
    and a window with no rows has an empty batch: the search covered it. When
    `truncated` is set the whole result is a prefix and the batches must not be
    kept, because `LIMIT` caps the result as a whole and cannot be applied per
    window (T-032 Q3).
    """

    window_start: datetime
    window_end: datetime
    window_seconds: int
    batches: dict[datetime, RuleMatchBatch] = Field(default_factory=dict)
    #: More than `row_cap` raw rows came back for the span as a whole.
    truncated: bool = False
    row_cap: int = 0
    query: str = ""
    #: Ariel searches issued: 1 for the real provider, 0 for the mock.
    ariel_searches: int = 0
