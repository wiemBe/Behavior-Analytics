"""Domain enumerations shared across models and schemas."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Criticality(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SourceClass(StrEnum):
    WAF = "WAF"
    IPS = "IPS"
    FIREWALL = "FIREWALL"
    PROXY = "PROXY"
    VPN = "VPN"
    LINUX = "LINUX"
    WINDOWS = "WINDOWS"
    NETWORK = "NETWORK"
    DNS = "DNS"
    DATABASE = "DATABASE"
    EMAIL = "EMAIL"
    ENDPOINT = "ENDPOINT"
    CLOUD = "CLOUD"
    IDENTITY = "IDENTITY"
    APPLICATION = "APPLICATION"
    QRADAR_INTERNAL = "QRADAR_INTERNAL"
    OTHER = "OTHER"
    UNCLASSIFIED = "UNCLASSIFIED"


class ClassificationRuleKind(StrEnum):
    GROUP = "GROUP"
    TYPE = "TYPE"
    TYPE_NAME_REGEX = "TYPE_NAME_REGEX"
    NAME_REGEX = "NAME_REGEX"
    IDENTIFIER_REGEX = "IDENTIFIER_REGEX"


class ClassificationReviewStatus(StrEnum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"


class CatchallReviewStatus(StrEnum):
    """Review state of a catch-all sender. D-9: NEW goes to a review queue, not a page."""

    NEW = "NEW"
    REVIEWED = "REVIEWED"
    DISMISSED = "DISMISSED"


class CatchallMeasurement(StrEnum):
    """What a stored catch-all bucket claims about the true value.

    A bucket that was not measured is not stored at all: there is no FAILED or
    NOT_COLLECTED row, so absence is never read as a zero.
    """

    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"


class OutagePolicyScope(StrEnum):
    """What an ``outage_alert_policy`` row applies to.

    ``CLASS`` names a :class:`SourceClass`; ``SOURCE`` names one ``log_source``.
    The scope is part of the row's identity, so a class and a source can never
    collide even if their values were equal.
    """

    CLASS = "CLASS"
    SOURCE = "SOURCE"


class OutagePolicyMode(StrEnum):
    """Whether a source's ``NO_EVENTS`` opens an alert.

    ``RECORD_ONLY`` records the anomaly and its lifecycle exactly as before and
    only suppresses the alert and its notification. ``ALERT`` keeps Phase A's
    behaviour, and is the default (D-9), so an upgrade alerts as it did.
    """

    ALERT = "ALERT"
    RECORD_ONLY = "RECORD_ONLY"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class SearchFamily(StrEnum):
    PHASE_A_VOLUME = "PHASE_A_VOLUME"
    WAF_COLLECTION = "WAF_COLLECTION"
    ANOMALY_EXPLANATION = "ANOMALY_EXPLANATION"
    RULE_MATCH = "RULE_MATCH"
    IPS_COLLECTION = "IPS_COLLECTION"
    PROPERTY_WATCH = "PROPERTY_WATCH"
    CATCHALL_SENDERS = "CATCHALL_SENDERS"
    GEO_COLLECTION = "GEO_COLLECTION"


class SearchCadence(IntEnum):
    FIVE_MINUTES = 300
    FIFTEEN_MINUTES = 900
    THIRTY_MINUTES = 1800
    ONE_HOUR = 3600
    TWO_HOURS = 7200
    FOUR_HOURS = 14400
    EIGHT_HOURS = 28800


#: D-28, amended 2026-10-18: WAF and IPS cost the most searches per source, so they default to
#: 15 minutes; every other family defaults to 5.
_FIFTEEN_MINUTE_FAMILIES = frozenset({SearchFamily.WAF_COLLECTION, SearchFamily.IPS_COLLECTION})


def default_search_cadence(family: SearchFamily) -> SearchCadence:
    """The cadence a family has when nobody has set one."""
    if family in _FIFTEEN_MINUTE_FAMILIES:
        return SearchCadence.FIFTEEN_MINUTES
    return SearchCadence.FIVE_MINUTES


#: A first run (no watermark) looks back this many buckets, whatever the cadence.
FIRST_RUN_LOOKBACK_BUCKETS = 12


class AnomalyType(StrEnum):
    """Log-source anomaly taxonomy.

    Ordering is meaningful: NO_EVENTS suppresses the downstream volume and
    parsing anomalies, because a source sending nothing trivially satisfies
    every other failure condition and would otherwise emit five alerts.
    """

    NO_EVENTS = "NO_EVENTS"
    VOLUME_DROP = "VOLUME_DROP"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    PARSING_DEGRADATION = "PARSING_DEGRADATION"
    UNKNOWN_EVENT_SPIKE = "UNKNOWN_EVENT_SPIKE"
    TIMESTAMP_DELAY = "TIMESTAMP_DELAY"
    CARDINALITY_DROP = "CARDINALITY_DROP"
    COLLECTION_ERROR = "COLLECTION_ERROR"
    REPEATED_PAYLOAD = "REPEATED_PAYLOAD"


class WafAnomalyType(StrEnum):
    """Phase B WAF detector taxonomy.

    Deliberately separate from `AnomalyType` rather than added to it: the Phase A
    enum is persisted in `log_source_anomaly.anomaly_type` and in alert
    fingerprints, and widening it would make a Phase A row's type column able to
    hold a value no Phase A detector can produce.

    Each member has a spec in `app.anomaly.capabilities.WAF_DETECTORS`, which
    states what it measures and whether anything can run it. A member existing
    says nothing about whether it can fire: `WAF_CLIENT_ENDPOINT_FANOUT_SPIKE`
    is named so its blocker is published, and has no detector function.
    """

    #: Endpoint request volume above its seasonal baseline.
    WAF_REQUEST_RATE_SPIKE = "WAF_REQUEST_RATE_SPIKE"
    #: The share of an endpoint's requests from its largest source IP rose.
    WAF_CLIENT_CONCENTRATION_SPIKE = "WAF_CLIENT_CONCENTRATION_SPIKE"
    #: The proportion of an endpoint's requests the WAF blocked rose, judged
    #: through the profile's validated action map -- never from HTTP 403.
    WAF_BLOCK_RATIO_SPIKE = "WAF_BLOCK_RATIO_SPIKE"
    #: The proportion of an endpoint's responses that were HTTP 5xx rose.
    WAF_ERROR_RATIO_SPIKE = "WAF_ERROR_RATIO_SPIKE"
    #: The proportion of an endpoint's responses that were HTTP 4xx rose. Kept
    #: separate from 5xx: client and server errors are different operational
    #: conditions, and one incident per class lets each recover on its own.
    WAF_CLIENT_ERROR_RATIO_SPIKE = "WAF_CLIENT_ERROR_RATIO_SPIKE"
    #: Material traffic to a normalized endpoint absent from a verified history.
    WAF_NEW_ENDPOINT_ACCESS = "WAF_NEW_ENDPOINT_ACCESS"
    #: Unsupported: see its capability spec for the blocker.
    WAF_CLIENT_ENDPOINT_FANOUT_SPIKE = "WAF_CLIENT_ENDPOINT_FANOUT_SPIKE"


class IpsAnomalyType(StrEnum):
    """Phase D IPS detector taxonomy (design D.6).

    Separate from `AnomalyType` and `WafAnomalyType` for the same reason the WAF
    one is: those enums are persisted in rows and alert fingerprints, and an IPS
    value in either would be a value none of their detectors can produce. Each
    member has a spec in `app.anomaly.capabilities.IPS_DETECTORS`.
    """

    #: A destination-port entity's events above its seasonal band.
    IPS_PORT_RATE_SPIKE = "IPS_PORT_RATE_SPIKE"
    #: A destination-IP entity's events above its seasonal band.
    IPS_DESTINATION_RATE_SPIKE = "IPS_DESTINATION_RATE_SPIKE"
    #: A signature entity's events above its seasonal band.
    IPS_SIGNATURE_BURST = "IPS_SIGNATURE_BURST"
    #: A destination port below the admission threshold throughout a verified
    #: history window now carries traffic above a floor.
    IPS_NEW_DESTINATION_PORT = "IPS_NEW_DESTINATION_PORT"
    #: The top-1 source IP's share of source-IP events rose.
    IPS_SOURCE_CONCENTRATION_SPIKE = "IPS_SOURCE_CONCENTRATION_SPIKE"
    #: One source IP reached more distinct destination ports than the baseline allows.
    IPS_PORT_FANOUT_SPIKE = "IPS_PORT_FANOUT_SPIKE"
    #: The blocked share of action events rose, judged through the profile's action map.
    IPS_BLOCK_RATIO_SHIFT = "IPS_BLOCK_RATIO_SHIFT"


class PwAnomalyType(StrEnum):
    """Phase G property-watch detector taxonomy (design §G.8.0).

    Separate from `AnomalyType`, `WafAnomalyType` and `IpsAnomalyType` for the same
    reason they are separate from each other: those enums are persisted in rows and
    alert fingerprints, and a `PW_` value in any of them would be a value none of
    their detectors can produce. Persisted in `pw_anomaly.detector_type` and in the
    watch's `detectors` list.
    """

    #: A watched value's events above its seasonal band.
    PW_VALUE_SPIKE = "PW_VALUE_SPIKE"
    #: A watched value's events exactly below its band while it normally carries traffic.
    PW_VALUE_DROP = "PW_VALUE_DROP"
    #: A watched value, normally present, has a measured zero for several buckets.
    PW_VALUE_DISAPPEARED = "PW_VALUE_DISAPPEARED"
    #: A value absent from a verified history appears with material traffic.
    PW_NEW_VALUE = "PW_NEW_VALUE"
    #: A tuple of two known components absent from a verified history appears.
    PW_NEW_COMBINATION = "PW_NEW_COMBINATION"


class PwScopeKind(StrEnum):
    """What a property watch is scoped to: exactly one of a source, a type or a group."""

    LOG_SOURCE = "LOG_SOURCE"
    LOG_SOURCE_TYPE = "LOG_SOURCE_TYPE"
    LOG_SOURCE_GROUP = "LOG_SOURCE_GROUP"


class PwReferenceKind(StrEnum):
    """How a watched property is named. The IPS enum is IPS-only by its own use."""

    NORMALIZED = "NORMALIZED"
    CUSTOM_PROPERTY = "CUSTOM_PROPERTY"


class PwBucketMeasurement(StrEnum):
    """What a `pw_bucket` row claims about its per-value counts.

    `EXACT`: the grouped search was whole, so a tracked value absent from the bucket
    had none of the parsed events. `LOWER_BOUND`: the search was cut at one bucket;
    every stored count is real but the tail is missing, so an absent tracked value is
    unknown. `NOT_COLLECTED`: nothing was measured; no value reading exists.
    """

    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"
    NOT_COLLECTED = "NOT_COLLECTED"


class PwPinnedMeasurement(StrEnum):
    """What the small pinned search established for a bucket (design §G.6, §G.7.1).

    The `PwBucketMeasurement` states, plus `NOT_REQUESTED` for a watch that pins no
    value: nothing was asked, so nothing is measured, and it is never an `EXACT` zero.
    """

    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"
    NOT_COLLECTED = "NOT_COLLECTED"
    NOT_REQUESTED = "NOT_REQUESTED"


class PwWatchStatus(StrEnum):
    """`property_watch.last_status` (design §G.3). A never-run watch is NULL, not OK."""

    NOT_ENABLED = "NOT_ENABLED"
    DISABLED = "DISABLED"
    SCOPE_EMPTY = "SCOPE_EMPTY"
    SCOPE_STALE = "SCOPE_STALE"
    SCOPE_TOO_LARGE = "SCOPE_TOO_LARGE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    STALE = "STALE"
    OPEN = "OPEN"
    RECOVERING = "RECOVERING"
    OK = "OK"


class CustomRuleSubjectKind(StrEnum):
    """What a custom threshold rule's subject is (Phase F, §F.3.1).

    A rule names exactly one subject. The kind decides which subject column on
    ``custom_rule`` carries it, which metric catalog entries are legal, and which
    bulk loader feeds evaluation -- all enforced by the CHECK the migration
    declares and by the catalog guard, never by evaluator reasoning.
    """

    LOG_SOURCE = "LOG_SOURCE"
    FLEET_CLASS = "FLEET_CLASS"
    WAF_ENDPOINT = "WAF_ENDPOINT"
    IPS_ENTITY = "IPS_ENTITY"
    IPS_SOURCE = "IPS_SOURCE"
    #: A pinned value of a property watch (Phase G, design §G.10.2).
    PW_VALUE = "PW_VALUE"


class CustomRuleMetric(StrEnum):
    """The per-bucket metrics a custom rule may threshold (Phase F, §F.1.1).

    Exactly the ten aggregates the platform already collects every bucket;
    nothing else is legal. Each member has one entry in
    ``app.custom_rules.catalog.CUSTOM_RULE_METRICS`` stating its subject kind,
    unit, columns, measurement rule and bulk loader. Deliberately separate
    values per kind (``event_count`` vs ``events`` vs ``total_events``) so the
    metric alone identifies the table it reads.
    """

    #: LOG_SOURCE — events in the bucket (`log_source_metric.event_count`).
    EVENT_COUNT = "event_count"
    #: LOG_SOURCE — events per second (`log_source_metric.average_eps`).
    AVERAGE_EPS = "average_eps"
    #: FLEET_CLASS — class events per second, summed over monitored sources.
    EPS = "eps"
    #: WAF_ENDPOINT — requests in the bucket (`waf_endpoint_metric.request_count`).
    REQUEST_COUNT = "request_count"
    #: WAF_ENDPOINT — share of requests the WAF blocked.
    BLOCK_RATIO = "block_ratio"
    #: WAF_ENDPOINT — share of responses that were HTTP 5xx.
    SERVER_ERROR_RATIO = "server_error_ratio"
    #: WAF_ENDPOINT — share of responses that were HTTP 4xx.
    CLIENT_ERROR_RATIO = "client_error_ratio"
    #: IPS_ENTITY — events in the bucket for one admitted entity.
    EVENTS = "events"
    #: IPS_SOURCE — events in the bucket (`ips_source_bucket.total_events`).
    TOTAL_EVENTS = "total_events"
    #: IPS_SOURCE — blocked share of action events.
    BLOCKED_SHARE = "blocked_share"
    #: PW_VALUE — events in the bucket for one pinned property-watch value
    #: (`pw_value_metric.events`, Phase G design §G.10.2).
    PW_VALUE_EVENTS = "pw_value_events"


class WafLogicalField(StrEnum):
    """A logical WAF field a source profile maps onto a site-authored property.

    The logical name is what business logic reads. The QRadar property that
    carries it is per-source configuration, because display names are authored
    per site and differ between WAF products and DSMs.
    """

    URL = "URL"
    METHOD = "METHOD"
    VIRTUAL_HOST = "VIRTUAL_HOST"
    ACTION = "ACTION"
    RESPONSE_CODE = "RESPONSE_CODE"
    RULE_ID = "RULE_ID"
    USER_AGENT = "USER_AGENT"


class WafFieldStatus(StrEnum):
    """What validation established about one logical field on one source.

    Kept deliberately fine-grained, because each state calls for a different
    operator action and collapsing any two of them misleads: a property QRadar
    rejects does not exist; one that exists but carries nothing is a parser
    problem; one that carries nonsense is a mapping problem; and a window with no
    events says nothing about the field at all.
    """

    AVAILABLE = "AVAILABLE"
    #: The profile maps no property to this field. Unavailable by configuration.
    NOT_MAPPED = "NOT_MAPPED"
    #: QRadar rejected a query naming the property: it does not exist as named.
    PROPERTY_REJECTED = "PROPERTY_REJECTED"
    #: The source had events in the window, and none carried a value.
    UNPOPULATED = "UNPOPULATED"
    #: Populated on too small a share of the source's events.
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    #: Populated, but too few values pass the field's plausibility check.
    IMPLAUSIBLE = "IMPLAUSIBLE"
    #: The source produced no events in the window, so nothing can be concluded.
    NO_EVENTS = "NO_EVENTS"
    #: The query failed for a reason that is not proof of absence.
    QUERY_FAILED = "QUERY_FAILED"


class WafProfileValidationStatus(StrEnum):
    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    INVALID = "INVALID"
    #: Validation could not decide -- no events in the window, or a query failed.
    INCONCLUSIVE = "INCONCLUSIVE"


class IpsLogicalField(StrEnum):
    """A logical IPS field mapped to a normalized or custom QRadar field."""

    SOURCE_IP = "SOURCE_IP"
    DESTINATION_IP = "DESTINATION_IP"
    DESTINATION_PORT = "DESTINATION_PORT"
    PROTOCOL = "PROTOCOL"
    SIGNATURE = "SIGNATURE"
    ACTION = "ACTION"
    SEVERITY = "SEVERITY"


class IpsReferenceKind(StrEnum):
    NORMALIZED = "NORMALIZED"
    CUSTOM_PROPERTY = "CUSTOM_PROPERTY"


class IpsPreset(StrEnum):
    CISCO_FTD = "CISCO_FTD"
    PALO_ALTO_THREAT = "PALO_ALTO_THREAT"


class IpsFieldStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_MAPPED = "NOT_MAPPED"
    FIELD_REJECTED = "FIELD_REJECTED"
    UNPOPULATED = "UNPOPULATED"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    NO_EVENTS = "NO_EVENTS"
    QUERY_FAILED = "QUERY_FAILED"


class IpsProfileValidationStatus(StrEnum):
    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class IpsDimension(StrEnum):
    """One fleet-wide keyed search of IPS collection (design D.5).

    IPS-only. It is not persisted by the collection layer in this form until
    T-050b names a column for it, and it never shares a type with Phase A or WAF.
    """

    TOTALS = "TOTALS"
    DESTINATION_PORT = "DESTINATION_PORT"
    DESTINATION_IP = "DESTINATION_IP"
    SIGNATURE = "SIGNATURE"
    SOURCE_IP = "SOURCE_IP"
    PORT_FANOUT = "PORT_FANOUT"
    ACTION = "ACTION"


class IpsCellState(StrEnum):
    """How one (source, bucket, dimension) cell was measured.

    Only COMPLETE and LOWER_BOUND carry a measurement. The other three say why
    there is none, and none of them may ever be read as zero.
    """

    #: The search that covered the cell was whole; no row means a measured zero.
    COMPLETE = "COMPLETE"
    #: One source, one bucket, truncated: the top rows are exact, the tail is missing.
    LOWER_BOUND = "LOWER_BOUND"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    #: The tick's search budget ran out before the cell was searched.
    NOT_COLLECTED = "NOT_COLLECTED"


class IpsMeasurement(StrEnum):
    """What a folded IPS fact claims about the true value."""

    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"


class AnomalyState(StrEnum):
    """Explicit anomaly lifecycle.

    INSUFFICIENT_DATA is a first-class state, not a synonym for NORMAL: "the
    source is behaving" and "we cannot yet tell whether the source is behaving"
    drive different operator actions, and collapsing them lets an unbaselined
    source masquerade as healthy.

    CANDIDATE exists so a single abnormal bucket is recorded without being
    promoted to an incident; RECOVERING exists so a single normal bucket does
    not immediately close one. Both directions need hysteresis or the state
    flaps on every bucket.
    """

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NORMAL = "NORMAL"
    CANDIDATE = "CANDIDATE"
    OPEN = "OPEN"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


#: States in which an incident is live and must not be duplicated by a second
#: OPEN for the same (log source, detector type).
ACTIVE_ANOMALY_STATES = frozenset(
    {AnomalyState.CANDIDATE, AnomalyState.OPEN, AnomalyState.RECOVERING}
)


class BucketCompleteness(StrEnum):
    """Whether a collector observed a whole time bucket.

    Only COMPLETE buckets may enter a baseline or produce a verdict. PARTIAL
    and MISSING buckets are displayable but never authoritative — treating a
    half-collected interval as a real observation is how a collection outage
    turns into a fleet-wide false VOLUME_DROP.
    """

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


class WafMeasurement(StrEnum):
    """What a stored WAF request count claims about the true count.

    `EXACT`: the bucket observed every event of the endpoint -- no grouped result
    was truncated and endpoint coverage met its floor -- so the count is the
    count, and in a sparse bucket an absent tracked endpoint served zero.

    `LOWER_BOUND`: some of the bucket's events could not be attributed to an
    endpoint (the grouped query hit its cap, or too many events carried no
    parseable endpoint). Every attributed group's SUM(eventcount) is still a real
    count of real events, so the stored value can only *understate* the truth.
    It may prove an increase; it can never prove normality or a decrease, and it
    must never be displayed as an exact count.
    """

    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"


class WafBreakdown(StrEnum):
    """An optional per-endpoint breakdown collected with its own bounded search.

    Each is grouped by the raw endpoint key plus one more field, so it measures
    something the request-volume query cannot: how an endpoint's requests
    divide by WAF action, by response class, or by source IP.
    """

    ACTION = "ACTION"
    RESPONSE_CODE = "RESPONSE_CODE"
    SOURCE_IP = "SOURCE_IP"


class WafTelemetryStatus(StrEnum):
    """What one breakdown established about one bucket.

    Deliberately not a count: NOT_COLLECTED and FAILED mean nothing was
    measured, and every per-endpoint value of that breakdown is NULL. A detector
    reading them must say "unavailable", never "zero".
    """

    #: The breakdown's grouped result was whole: every value is exact, and a
    #: tracked endpoint absent from it had none of the measured events.
    EXACT = "EXACT"
    #: The grouped result hit its cap: every value is the visible part only.
    LOWER_BOUND = "LOWER_BOUND"
    #: Not collected: disabled, or the profile field is not validated.
    NOT_COLLECTED = "NOT_COLLECTED"
    #: The search was attempted and failed.
    FAILED = "FAILED"


class EvidenceStatus(StrEnum):
    """Completeness of an anomaly's bounded explanation package.

    UNAVAILABLE and FAILED are distinct: "this source's DSM emits none of the
    requested fields" is a permanent property of the source, while "the Ariel
    query did not complete" is a transient condition worth retrying.
    """

    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class DimensionAvailability(StrEnum):
    """Per-dimension outcome within an explanation package.

    A dimension absent from a source's DSM output is UNAVAILABLE and must never
    be rendered as a count of zero — that would invent evidence of absence.
    """

    AVAILABLE = "AVAILABLE"
    TRUNCATED = "TRUNCATED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class RobustScoreStatus(StrEnum):
    """Whether the robust z-score was usable for a verdict.

    DEGENERATE means MAD was zero (more than half the baseline samples were
    identical — common for a steady low-volume source). The score is then not
    meaningful, but the source must not therefore be declared normal: the
    detector falls back to the deterministic expected-bound, ratio and
    absolute-delta tests and records that it did so.
    """

    OK = "OK"
    DEGENERATE = "DEGENERATE"
    UNAVAILABLE = "UNAVAILABLE"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class NotificationChannel(StrEnum):
    TEAMS = "TEAMS"
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    WEBHOOK = "WEBHOOK"
    SYSLOG = "SYSLOG"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"          # failed this attempt; may retry
    DEAD_LETTER = "DEAD_LETTER"  # exhausted retries; parked for inspection
    SUPPRESSED = "SUPPRESSED"  # deduplicated / routed away


class AlertTransition(StrEnum):
    """The lifecycle transitions that may trigger a notification."""

    OPENED = "OPENED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class ExecutionErrorType(StrEnum):
    """Distinct failure modes for a search execution. Each drives different
    retry and alerting behaviour, so they must not be collapsed to a generic
    'error'."""

    VALIDATION = "VALIDATION"              # AQL rejected before dispatch — never retried
    AUTH = "AUTH"                          # QRadar authentication/authorization failure
    RATE_LIMITED = "RATE_LIMITED"          # QRadar throttled us — retry with backoff
    TIMEOUT = "TIMEOUT"                    # exceeded execution timeout
    SEARCH_FAILED = "SEARCH_FAILED"        # QRadar reported the search itself failed
    NETWORK = "NETWORK"                    # connectivity/transport failure — retryable
    PARSING = "PARSING"                    # result normalization failure
    CANCELLED = "CANCELLED"                # cancelled (expiry/operator)


class DetectorSignal(StrEnum):
    """Per-interval verdict feeding anomaly hysteresis."""

    HEALTHY = "HEALTHY"
    ANOMALOUS = "ANOMALOUS"
    UNKNOWN = "UNKNOWN"  # insufficient data / in maintenance


class VisualizationType(StrEnum):
    TABLE = "TABLE"
    LINE = "LINE"
    BAR = "BAR"
    PIE = "PIE"
    SINGLE_VALUE = "SINGLE_VALUE"
    HEATMAP = "HEATMAP"


class ChangeType(StrEnum):
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class CoverageStatus(StrEnum):
    """Coverage of a MITRE technique by live, working detection.

    MISSING and NOT_EVALUATED are deliberately distinct: "we have no rule for
    this" and "we have not looked" are different findings, and collapsing them
    would let an unevaluated technique masquerade as a known gap. NOT_COVERED is
    retained as the Phase 1 spelling of MISSING so historical rows keep meaning.
    """

    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    DEGRADED = "DEGRADED"
    MISSING = "MISSING"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_COVERED = "NOT_COVERED"  # legacy alias for MISSING; not emitted in Phase 3


class RuleHealthStatus(StrEnum):
    """Why a rule is or is not currently producing detection value.

    Each value answers a different operator question and drives a different
    action, so they must never be collapsed into a boolean:

      HEALTHY             — firing within expectation.
      NEVER_OBSERVED      — enabled, past its grace period, has never fired.
      INACTIVE            — has fired historically but not within the window.
      NOISY               — firing far above the configured expectation.
      DISABLED            — intentionally off in QRadar; not a fault.
      DEPENDENCY_DEGRADED — its required telemetry is unhealthy, so silence is
                            not evidence about the rule itself.
      INSUFFICIENT_DATA   — too new, or too little history, to judge.
      UNKNOWN             — evaluation could not be completed.
    """

    HEALTHY = "HEALTHY"
    NEVER_OBSERVED = "NEVER_OBSERVED"
    INACTIVE = "INACTIVE"
    NOISY = "NOISY"
    DISABLED = "DISABLED"
    DEPENDENCY_DEGRADED = "DEPENDENCY_DEGRADED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNKNOWN = "UNKNOWN"


class MappingSource(StrEnum):
    """Provenance of a rule/technique/dependency mapping.

    EXPLICIT mappings are SOC-owned and authoritative. INFERRED mappings are
    derived by the platform from rule text or configuration and are always
    reported as inferences carrying a confidence — never presented as fact.
    """

    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"


class RuleDependencyKind(StrEnum):
    BUILDING_BLOCK = "BUILDING_BLOCK"
    LOG_SOURCE_TYPE = "LOG_SOURCE_TYPE"
    LOG_SOURCE = "LOG_SOURCE"


class InstanceStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN = "UNKNOWN"


class AuthSource(StrEnum):
    """Where an account's password is checked.

    LOCAL is a bcrypt hash stored here. LDAP binds as the user against the
    directory; this database never stores that password. Roles are not read
    from the directory.
    """

    LOCAL = "LOCAL"
    LDAP = "LDAP"
