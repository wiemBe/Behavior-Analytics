"""Aggregate model imports.

Importing this package registers every table on `Base.metadata`, which Alembic's
autogenerate and the test fixtures both rely on. Keep this list complete.
"""

from app.models.alert import Alert, AlertNotification
from app.models.base import Base
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.config_change import ConfigurationChange, ConfigurationSnapshot
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.dashboard import Dashboard, UserDashboardPref
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
from app.models.identity import AuditLog, Role, User, user_role
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
from app.models.monitoring import AnomalyEvaluationWatermark, CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.outage import OutageAlertPolicy
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
from app.models.search import (
    ScheduledSearch,
    SearchExecution,
    SearchQueryVersion,
    SearchResultMetric,
)
from app.models.search_cadence import SearchCadenceSetting
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
}

__all__ = [
    "EXPLANATION_SCHEMA_VERSION",
    "HYPERTABLES",
    "Alert",
    "AlertNotification",
    "AnalyticsRule",
    "AnomalyEvaluationWatermark",
    "AnomalyExplanation",
    "AnomalyExplanationContributor",
    "AnomalyExplanationDimension",
    "AnomalyStateTransition",
    "AuditLog",
    "Base",
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
    "GeoCountryBucket",
    "IpsAnomaly",
    "IpsAnomalyTransition",
    "IpsDetectorState",
    "IpsEntity",
    "IpsEntityBaseline",
    "IpsEntityMetric",
    "IpsSourceBaseline",
    "IpsSourceBucket",
    "IpsSourceProfile",
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
    "QRadarInstance",
    "Role",
    "RuleCapacitySample",
    "RuleDependency",
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
    "TechniqueMapping",
    "User",
    "UserDashboardPref",
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
