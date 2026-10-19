"""The Phase G enums: separate from every persisted taxonomy (AGENTS.md rule 6)."""

from __future__ import annotations

from app.models import HYPERTABLES, Base
from app.models.enums import (
    AnomalyType,
    IpsAnomalyType,
    PwAnomalyType,
    PwBucketMeasurement,
    PwBucketOrigin,
    PwHistoryState,
    PwPinnedMeasurement,
    PwReferenceKind,
    PwScopeKind,
    PwWatchStatus,
    SearchFamily,
    WafAnomalyType,
)
from app.models.property_watch import PropertyWatch, PwBucket


def _check(model: type[PropertyWatch] | type[PwBucket], name: str) -> str:
    return str(
        next(item.sqltext for item in model.__table__.constraints if item.name.endswith(name))
    )


def test_the_pw_taxonomy_is_its_own() -> None:
    assert [member.value for member in PwAnomalyType] == [
        "PW_VALUE_SPIKE",
        "PW_VALUE_DROP",
        "PW_VALUE_DISAPPEARED",
        "PW_NEW_VALUE",
        "PW_NEW_COMBINATION",
    ]
    for other in (AnomalyType, WafAnomalyType, IpsAnomalyType):
        assert not [m for m in other if m.value.startswith("PW_")]
        assert not {m.value for m in PwAnomalyType} & {m.value for m in other}


def test_the_phase_a_taxonomy_is_unchanged() -> None:
    assert [member.value for member in AnomalyType] == [
        "NO_EVENTS", "VOLUME_DROP", "VOLUME_SPIKE", "PARSING_DEGRADATION",
        "UNKNOWN_EVENT_SPIKE", "TIMESTAMP_DELAY", "CARDINALITY_DROP", "COLLECTION_ERROR",
        "REPEATED_PAYLOAD",
    ]


def test_the_search_family_only_grew() -> None:
    assert [member.value for member in SearchFamily] == [
        "PHASE_A_VOLUME", "WAF_COLLECTION", "ANOMALY_EXPLANATION", "RULE_MATCH",
        "IPS_COLLECTION", "PROPERTY_WATCH", "CATCHALL_SENDERS", "GEO_COLLECTION",
    ]


def test_the_vocabularies() -> None:
    assert [m.value for m in PwScopeKind] == ["LOG_SOURCE", "LOG_SOURCE_TYPE", "LOG_SOURCE_GROUP"]
    assert [m.value for m in PwReferenceKind] == ["NORMALIZED", "CUSTOM_PROPERTY"]
    assert [m.value for m in PwBucketMeasurement] == ["EXACT", "LOWER_BOUND", "NOT_COLLECTED"]
    assert "NOT_REQUESTED" in {m.value for m in PwPinnedMeasurement}
    assert [m.value for m in PwHistoryState] == [
        "NOT_REQUESTED", "READING", "COMPLETE", "PARTIAL", "UNAVAILABLE",
    ]
    assert [m.value for m in PwBucketOrigin] == ["LIVE", "HISTORY"]
    assert {"NOT_ENABLED", "DISABLED", "SCOPE_EMPTY", "SCOPE_STALE", "SCOPE_TOO_LARGE",
            "NOT_APPLICABLE", "READING_HISTORY", "STALE", "OPEN", "RECOVERING", "OK"} == {
        m.value for m in PwWatchStatus
    }


def test_history_enum_check_literals_stay_in_parity() -> None:
    history_states = "(" + ", ".join(f"'{m.value}'" for m in PwHistoryState) + ")"
    bucket_origins = "(" + ", ".join(f"'{m.value}'" for m in PwBucketOrigin) + ")"
    watch_statuses = "(" + ", ".join(f"'{m.value}'" for m in PwWatchStatus) + ")"
    assert _check(PropertyWatch, "history_state_known") == f"history_state IN {history_states}"
    assert _check(PwBucket, "origin_known") == f"origin IN {bucket_origins}"
    assert _check(PropertyWatch, "last_status_known") == (
        f"last_status IS NULL OR last_status IN {watch_statuses}"
    )


def test_the_tables_are_registered_and_two_are_hypertables() -> None:
    tables = set(Base.metadata.tables)
    assert {"property_watch", "pw_bucket", "pw_value", "pw_value_metric", "pw_value_baseline",
            "pw_detector_state", "pw_anomaly", "pw_anomaly_transition"} <= tables
    assert HYPERTABLES["pw_bucket"] == "bucket_start"
    assert HYPERTABLES["pw_value_metric"] == "bucket_start"
