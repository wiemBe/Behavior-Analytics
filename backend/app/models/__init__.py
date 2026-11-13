"""Aggregate model imports.

Importing this package registers every table on `Base.metadata`, which Alembic's
autogenerate and the test fixtures both rely on. Keep this list complete.
"""

from app.models.alert import Alert, AlertNotification
from app.models.alert_policy import AlertDigestRun, AlertPolicy
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.ariel_search import ArielSearchRun
from app.models.base import Base
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.config_change import ConfigurationChange, ConfigurationSnapshot
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.dashboard import Dashboard, UserDashboardPref
from app.models.email import EMAIL_ALERT_TYPES, EmailRecipient, EmailTemplate
from app.models.entity import EntityMention, EntityMentionBackfill
from app.models.explanation import (
    EXPLANATION_SCHEMA_VERSION,
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.fleet import (
    ClassificationRule,
    LogSourceClassification,
    LogSourceGroup,
    LogSourceType,
)
from app.models.geo import GeoCountryBucket
from app.models.handover import ShiftHandover
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope
from app.models.identity import AuditLog, AuthSession, LdapConfig, Role, User, user_role
from app.models.instance import QRadarInstance
from app.models.ips import (
    IpsAnomaly,
    IpsAnomalyTransition,
    IpsDetectorState,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.monitoring import (
    AnomalyEvaluationFault,
    AnomalyEvaluationWatermark,
    CollectionWatermark,
    QRadarAccessResult,
    QRadarAccessRun,
)
from app.models.offense import OffenseSnapshot
from app.models.outage import OutageAlertPolicy
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwAnomalyTransition,
    PwBucket,
    PwDetectorState,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.models.qradar_health import (
    QRadarHealthMetricBucket,
    QRadarHealthNotificationBucket,
    QRadarHealthRestSample,
    QRadarHealthRun,
)
from app.models.queue_marker import UserQueueMarker
from app.models.rule import (
    AnalyticsRule,
    DetectionCoverage,
    DetectionCoverageSnapshot,
    RuleCapacitySample,
    RuleDependency,
    RuleHealthSnapshot,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMetric,
    RuleStateTransition,
    TechniqueMapping,
)
from app.models.rule_anomaly import (
    RuleAnomaly,
    RuleAnomalyTransition,
    RuleDetectorState,
)
from app.models.search import (
    ScheduledSearch,
    SearchExecution,
    SearchQueryVersion,
    SearchResultMetric,
)
from app.models.search_cadence import SearchCadenceSetting
from app.models.training_exclusion import BaselineTrainingExclusion
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.models.waf import (
    WafAnomaly,
    WafAnomalyTransition,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
    WafSourceProfile,
)

# The set of tables Timescale should manage as hypertables, with their time
# column. Consumed by the migration so this stays the single source of truth.
HYPERTABLES: dict[str, str] = {
    "log_source_metric": "bucket_start",
    "search_result_metric": "bucket_start",
    "rule_metric": "bucket_start",
    "offense_snapshot": "captured_at",
    "rule_health_snapshot": "evaluated_at",
    "detection_coverage_snapshot": "captured_at",
    "waf_endpoint_metric": "bucket_start",
    "waf_source_bucket": "bucket_start",
    "rule_match_metric": "bucket_start",
    "catchall_bucket": "bucket_start",
    "geo_country_bucket": "bucket_start",
    "ips_source_bucket": "bucket_start",
    "ips_entity_metric": "bucket_start",
    "pw_bucket": "bucket_start",
    "pw_value_metric": "bucket_start",
    "qradar_health_run": "window_start",
    "qradar_health_metric_bucket": "bucket_start",
    "qradar_health_notice_bucket": "bucket_start",
    "qradar_health_rest_sample": "slot_start",
}

__all__ = [
    "EMAIL_ALERT_TYPES",
    "EXPLANATION_SCHEMA_VERSION",
    "HYPERTABLES",
    "Alert",
    "AlertDigestRun",
    "AlertNotification",
    "AlertPolicy",
    "AlertSuppression",
    "AlertSuppressionHit",
    "AnalyticsRule",
    "AnomalyEvaluationFault",
    "AnomalyEvaluationWatermark",
    "AnomalyExplanation",
    "AnomalyExplanationContributor",
    "AnomalyExplanationDimension",
    "AnomalyStateTransition",
    "AnomalyTriage",
    "AnomalyTriageEvent",
    "ArielSearchRun",
    "AuditLog",
    "AuthSession",
    "Base",
    "BaselineTrainingExclusion",
    "CatchallBucket",
    "CatchallSender",
    "ClassificationRule",
    "CollectionWatermark",
    "ConfigurationChange",
    "ConfigurationSnapshot",
    "CustomRule",
    "CustomRuleBreach",
    "CustomRuleWatermark",
    "Dashboard",
    "DetectionCoverage",
    "DetectionCoverageSnapshot",
    "EmailRecipient",
    "EmailTemplate",
    "EntityMention",
    "EntityMentionBackfill",
    "GeoCountryBucket",
    "HistoryBackfillDecision",
    "HistoryBackfillScope",
    "IpsAnomaly",
    "IpsAnomalyTransition",
    "IpsDetectorState",
    "IpsEntity",
    "IpsEntityBaseline",
    "IpsEntityMetric",
    "IpsSourceBaseline",
    "IpsSourceBucket",
    "IpsSourceProfile",
    "LdapConfig",
    "LogSource",
    "LogSourceAnomaly",
    "LogSourceBaseline",
    "LogSourceClassification",
    "LogSourceDetectorState",
    "LogSourceGroup",
    "LogSourceMetric",
    "LogSourceType",
    "OffenseSnapshot",
    "OutageAlertPolicy",
    "PropertyWatch",
    "PwAnomaly",
    "PwAnomalyTransition",
    "PwBucket",
    "PwDetectorState",
    "PwValue",
    "PwValueBaseline",
    "PwValueMetric",
    "QRadarAccessResult",
    "QRadarAccessRun",
    "QRadarHealthMetricBucket",
    "QRadarHealthNotificationBucket",
    "QRadarHealthRestSample",
    "QRadarHealthRun",
    "QRadarInstance",
    "Role",
    "RuleAnomaly",
    "RuleAnomalyTransition",
    "RuleCapacitySample",
    "RuleDependency",
    "RuleDetectorState",
    "RuleHealthSnapshot",
    "RuleMatchMetric",
    "RuleMatchWindow",
    "RuleMetric",
    "RuleStateTransition",
    "ScheduledSearch",
    "SearchCadenceSetting",
    "SearchExecution",
    "SearchQueryVersion",
    "SearchResultMetric",
    "ShiftHandover",
    "TechniqueMapping",
    "User",
    "UserDashboardPref",
    "UserQueueMarker",
    "WafAnomaly",
    "WafAnomalyTransition",
    "WafDetectorState",
    "WafEndpoint",
    "WafEndpointBaseline",
    "WafEndpointMetric",
    "WafSourceBucket",
    "WafSourceProfile",
    "user_role",
]
