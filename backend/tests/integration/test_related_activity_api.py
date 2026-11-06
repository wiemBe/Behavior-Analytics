"""SOC-3a: related activity over HTTP, against a real Postgres schema.

Every read is seeded through a committed session and exercised through the route, as the
other API tests do. The four anchor families, the offense newest-snapshot rule, the
cross-family anomaly list, the rule union and the fail-closed section states are each
pinned here; the pure window/ordering rules live in `tests/unit/test_related_activity.py`.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.custom_rule import CustomRule, CustomRuleBreach
from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsAnomalyType,
    IpsDimension,
    PwAnomalyType,
    PwScopeKind,
    Severity,
    TriageStatus,
    WafAnomalyType,
)
from app.models.ips import IpsAnomaly, IpsEntity
from app.models.log_source import LogSourceAnomaly
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule import (
    AnalyticsRule,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.models.triage import AnomalyTriage
from app.models.waf import WafAnomaly, WafEndpoint
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the related activity API tests",
    ),
]

NOW = datetime.now(UTC).replace(microsecond=0)
T0 = NOW - timedelta(hours=1)
ANOMALY_END = T0 + timedelta(minutes=10)
#: The default `pad=2h` window, computed independently of the service under test.
WINDOW_START = T0 - timedelta(hours=2)
WINDOW_END = ANOMALY_END + timedelta(hours=2)

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ANOMALY_READER = Principal(subject="ar", permissions=frozenset({"read:anomalies"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())


# ------------------------------------------------------------------------- seeding
def phase_a(source_id: uuid.UUID, **kw) -> LogSourceAnomaly:  # type: ignore[no-untyped-def]
    values = dict(
        log_source_id=source_id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        state=AnomalyState.OPEN,
        detected_at=T0,
        anomaly_start=T0,
        anomaly_end=ANOMALY_END,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return LogSourceAnomaly(**values)


def waf(endpoint_id: uuid.UUID, **kw) -> WafAnomaly:  # type: ignore[no-untyped-def]
    values = dict(
        waf_endpoint_id=endpoint_id,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.MEDIUM,
        state=AnomalyState.OPEN,
        detected_at=T0,
        anomaly_start=T0,
        anomaly_end=ANOMALY_END,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return WafAnomaly(**values)


def ips(**kw) -> IpsAnomaly:  # type: ignore[no-untyped-def]
    values = dict(
        subject_kind="IPS_SOURCE",
        detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        fingerprint=f"ips:{uuid.uuid4().hex}",
        severity=Severity.MEDIUM,
        state=AnomalyState.OPEN,
        detected_at=T0,
        anomaly_start=T0,
        anomaly_end=ANOMALY_END,
        profile_version=1,
        normalizer_version=1,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return IpsAnomaly(**values)


def pw(watch_id: uuid.UUID, **kw) -> PwAnomaly:  # type: ignore[no-untyped-def]
    values = dict(
        watch_id=watch_id,
        value_key="/api/login",
        detector_type=PwAnomalyType.PW_VALUE_SPIKE,
        fingerprint=f"pw:{uuid.uuid4().hex}",
        severity=Severity.LOW,
        state=AnomalyState.OPEN,
        detected_at=T0,
        anomaly_start=T0,
        anomaly_end=ANOMALY_END,
        watch_version=1,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return PwAnomaly(**values)


async def _watermark(
    session, instance_id: uuid.UUID, *, last_run_at: datetime | None, failures: int = 0
) -> None:  # type: ignore[no-untyped-def]
    session.add(
        CollectionWatermark(
            instance_id=instance_id,
            collector="offense_snapshot",
            watermark_at=last_run_at,
            last_run_at=last_run_at,
            intervals_collected=1,
            consecutive_failures=failures,
        )
    )
    await session.flush()


async def _world(session) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    source = await make_log_source(
        session, inst, qradar_id=9001, name=f"rel-{uuid.uuid4().hex[:6]}"
    )
    other = await make_log_source(
        session, inst, qradar_id=9002, name=f"other-{uuid.uuid4().hex[:6]}"
    )
    await _watermark(session, inst.id, last_run_at=NOW)

    anchor = phase_a(source.id)
    endpoint = WafEndpoint(
        instance_id=inst.id,
        log_source_id=source.id,
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=1,
        first_seen_at=T0,
        last_seen_at=T0,
        tracked_since=T0,
        is_active=True,
    )
    session.add_all([anchor, endpoint])
    await session.flush()

    waf_anomaly = waf(endpoint.id)
    entity = IpsEntity(
        instance_id=inst.id,
        log_source_id=source.id,
        dimension=IpsDimension.DESTINATION_PORT,
        value_key="443",
        first_seen_at=T0,
        last_seen_at=T0,
        active=True,
    )
    session.add_all([waf_anomaly, entity])
    await session.flush()

    # An entity-subject IPS incident stores no source of its own: its entity does.
    ips_anomaly = ips(
        subject_kind="IPS_ENTITY", ips_entity_id=entity.id, log_source_id=None
    )
    watch = PropertyWatch(
        instance_id=inst.id,
        name=f"watch-{uuid.uuid4().hex[:8]}",
        scope_kind=PwScopeKind.LOG_SOURCE,
        log_source_id=source.id,
        properties=[{"kind": "NORMALIZED", "name": "URL"}],
        detectors=[PwAnomalyType.PW_VALUE_SPIKE.value],
    )
    session.add_all([ips_anomaly, watch])
    await session.flush()
    pw_anomaly = pw(watch.id)
    session.add(pw_anomaly)
    await session.flush()
    return {
        "instance": inst.id,
        "source": source.id,
        "other": other.id,
        "phase_a": anchor.id,
        "waf": waf_anomaly.id,
        "ips": ips_anomaly.id,
        "pw": pw_anomaly.id,
        "endpoint": endpoint.id,
        "entity": entity.id,
        "watch": watch.id,
    }


@pytest.fixture
def world(db_schema):  # type: ignore[no-untyped-def]
    return run(_world)


def _snapshot(
    instance_id: uuid.UUID,
    offense_id: int,
    *,
    log_source_ids: list[int] | None = None,
    magnitude: int = 5,
    start: datetime = T0,
    end: datetime = T0 + timedelta(minutes=5),
    closed: bool = True,
    rule_ids: list[int] | None = None,
    captured_at: datetime = NOW,
) -> OffenseSnapshot:
    return OffenseSnapshot(
        instance_id=instance_id,
        qradar_offense_id=offense_id,
        captured_at=captured_at,
        description=f"offense {offense_id}",
        status="CLOSED" if closed else "OPEN",
        magnitude=magnitude,
        severity=7,
        offense_type_name="Malware",
        start_time=start,
        last_updated_time=end,
        close_time=end if closed else None,
        log_source_ids=[9001] if log_source_ids is None else log_source_ids,
        rule_ids=rule_ids or [],
    )


def _rule(
    instance_id: uuid.UUID, qradar_id: int, *, name: str | None = None, rule_type: str = "EVENT"
) -> AnalyticsRule:
    return AnalyticsRule(
        instance_id=instance_id,
        qradar_id=qradar_id,
        name=name or f"rule-{qradar_id}",
        rule_type=rule_type,
        enabled=True,
    )


def _window_row(
    instance_id: uuid.UUID,
    *,
    start: datetime,
    seconds: int,
    status: RuleMatchWindowStatus,
) -> RuleMatchWindow:
    return RuleMatchWindow(
        instance_id=instance_id,
        window_start=start,
        window_seconds=seconds,
        origin="LIVE",
        status=status,
        rows_returned=1,
        row_cap=1000,
        unknown_ids=0,
        query_version="v1",
        collected_at=NOW,
        attempts=1,
    )


def url(family: str, anomaly_id: uuid.UUID) -> str:
    return f"/api/v1/anomalies/related/{family}/{anomaly_id}"


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --------------------------------------------------------------- authorization
class TestLookup:
    def test_reading_needs_the_anomaly_read_permission(self, world) -> None:
        path = url("log_source", world["phase_a"])
        assert Client(NOBODY).get(path).status_code == 403
        assert Client(ANOMALY_READER).get(path).status_code == 200
        assert Client(READER).get(path).status_code == 200

    def test_an_unknown_id_is_404_and_an_unknown_family_is_422(self, world) -> None:
        assert Client(READER).get(url("log_source", uuid.uuid4())).status_code == 404
        assert (
            Client(READER)
            .get(f"/api/v1/anomalies/related/not_a_family/{world['phase_a']}")
            .status_code
            == 422
        )

    def test_an_unknown_pad_is_422(self, world) -> None:
        response = Client(READER).get(
            url("log_source", world["phase_a"]), params={"pad": "15m"}
        )
        assert response.status_code == 422


# --------------------------------------------------------------------- anchors
class TestAnchors:
    @pytest.mark.parametrize(
        ("key", "family"),
        [
            ("phase_a", "log_source"),
            ("waf", "waf"),
            ("ips", "ips"),
            ("pw", "property_watch"),
        ],
    )
    def test_each_family_resolves_its_source_and_window(self, world, key, family) -> None:
        response = Client(READER).get(url(family, world[key]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["anchor"]["family"] == family
        assert body["anchor"]["anomaly_id"] == str(world[key])
        assert body["anchor"]["log_source_id"] == str(world["source"])
        assert body["anchor"]["qradar_log_source_id"] == 9001
        assert body["anchor"]["pad"] == "2h"
        assert _dt(body["anchor"]["window_start"]) == WINDOW_START
        assert _dt(body["anchor"]["window_end"]) == WINDOW_END

    @pytest.mark.parametrize(
        ("pad", "hours"),
        [("30m", 0.5), ("1h", 1.0), ("2h", 2.0), ("6h", 6.0)],
    )
    def test_each_pad_widens_the_window(self, world, pad: str, hours: float) -> None:
        body = Client(READER).get(
            url("log_source", world["phase_a"]), params={"pad": pad}
        ).json()
        assert _dt(body["anchor"]["window_start"]) == T0 - timedelta(hours=hours)
        assert _dt(body["anchor"]["window_end"]) == ANOMALY_END + timedelta(hours=hours)

    def test_an_entity_subject_ips_anomaly_anchors_on_its_entitys_source(
        self, world
    ) -> None:
        body = Client(READER).get(url("ips", world["ips"])).json()
        assert body["anchor"]["log_source_id"] == str(world["source"])
        assert body["anchor"]["qradar_log_source_id"] == 9001

    def test_a_watch_without_a_single_source_is_not_applicable(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            watch = PropertyWatch(
                instance_id=inst.id,
                name=f"typed-{uuid.uuid4().hex[:8]}",
                scope_kind=PwScopeKind.LOG_SOURCE_TYPE,
                scope_type_id=11,
                properties=[{"kind": "NORMALIZED", "name": "URL"}],
                detectors=[PwAnomalyType.PW_VALUE_SPIKE.value],
            )
            session.add(watch)
            await session.flush()
            anomaly = pw(watch.id)
            session.add(anomaly)
            await session.flush()
            return anomaly.id

        anomaly_id = run(seed)
        body = Client(READER).get(url("property_watch", anomaly_id)).json()
        assert body["state"] == "NOT_APPLICABLE"
        assert body["reason_code"] == "related_activity.reason.no_log_source"
        assert body["anchor"]["log_source_id"] is None
        assert body["anchor"]["qradar_log_source_id"] is None
        for section in ("offenses", "anomalies", "rules"):
            assert body[section]["state"] == "NOT_APPLICABLE"
            assert body[section]["total"] == 0
            assert body[section]["items"] == []


# -------------------------------------------------------------------- offenses
class TestOffenses:
    def test_only_the_newest_snapshot_counts(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add_all(
                [
                    _snapshot(
                        world["instance"],
                        501,
                        magnitude=10,
                        captured_at=NOW - timedelta(minutes=10),
                    ),
                    _snapshot(
                        world["instance"],
                        501,
                        magnitude=3,
                        captured_at=NOW - timedelta(minutes=1),
                    ),
                ]
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        items = body["offenses"]["items"]
        assert len(items) == 1
        assert items[0]["qradar_offense_id"] == 501
        assert items[0]["magnitude"] == 3
        assert _dt(items[0]["captured_at"]) == NOW - timedelta(minutes=1)

    def test_overlap_is_inclusive_on_both_edges_and_other_sources_are_excluded(
        self, world
    ) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add_all(
                [
                    # Ends one second after the window starts: it overlaps.
                    _snapshot(
                        world["instance"],
                        601,
                        start=WINDOW_START - timedelta(hours=1),
                        end=WINDOW_START + timedelta(seconds=1),
                    ),
                    # Ends one second before the window starts: it does not.
                    _snapshot(
                        world["instance"],
                        602,
                        start=WINDOW_START - timedelta(hours=1),
                        end=WINDOW_START - timedelta(seconds=1),
                    ),
                    # Same span, another source.
                    _snapshot(
                        world["instance"],
                        603,
                        start=WINDOW_START,
                        end=WINDOW_START + timedelta(minutes=1),
                        log_source_ids=[9002],
                    ),
                    # Open: the span ends at last_updated_time.
                    _snapshot(
                        world["instance"],
                        604,
                        start=WINDOW_END - timedelta(minutes=1),
                        end=WINDOW_END + timedelta(seconds=1),
                        closed=False,
                    ),
                ]
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        ids = [item["qradar_offense_id"] for item in body["offenses"]["items"]]
        assert ids == [604, 601]

    def test_ordering_is_magnitude_then_start_then_id(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add_all(
                [
                    _snapshot(world["instance"], 701, magnitude=4, start=T0),
                    _snapshot(world["instance"], 702, magnitude=4, start=T0 + timedelta(minutes=1)),
                    _snapshot(world["instance"], 703, magnitude=9, start=T0),
                    _snapshot(world["instance"], 704, magnitude=4, start=T0),
                ]
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        assert [item["qradar_offense_id"] for item in body["offenses"]["items"]] == [
            703,
            702,
            701,
            704,
        ]


# ------------------------------------------------------------------- anomalies
class TestAnomalySection:
    def test_every_family_is_included_and_the_anchor_is_not(self, world) -> None:
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        families = {item["family"] for item in body["anomalies"]["items"]}
        assert families == {"waf", "ips", "property_watch"}
        ids = {item["id"] for item in body["anomalies"]["items"]}
        assert str(world["phase_a"]) not in ids
        assert str(world["waf"]) in ids and str(world["ips"]) in ids
        assert str(world["pw"]) in ids

    def test_another_source_is_excluded(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(phase_a(world["other"]))

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        assert all(item["family"] != "log_source" for item in body["anomalies"]["items"])

    def test_triage_status_is_joined_and_defaults_to_new(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(
                AnomalyTriage(
                    family=AnomalyFamily.WAF,
                    anomaly_id=world["waf"],
                    status=TriageStatus.IN_PROGRESS,
                    updated_by="analyst@example.com",
                )
            )

        run(seed)
        items = {
            item["id"]: item
            for item in Client(READER)
            .get(url("log_source", world["phase_a"]))
            .json()["anomalies"]["items"]
        }
        assert items[str(world["waf"])]["triage_status"] == "IN_PROGRESS"
        assert items[str(world["ips"])]["triage_status"] == "NEW"

    def test_a_custom_rule_breach_tied_to_the_source_is_listed(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            tied = CustomRule(
                name=f"tied-{uuid.uuid4().hex[:8]}",
                subject_kind="LOG_SOURCE",
                log_source_id=world["source"],
                metric="average_eps",
                operator="GT",
                threshold_value=1.0,
                severity=Severity.HIGH,
                enabled=True,
            )
            loose = CustomRule(
                name=f"class-{uuid.uuid4().hex[:8]}",
                subject_kind="FLEET_CLASS",
                source_class="FIREWALL",
                metric="average_eps",
                operator="GT",
                threshold_value=1.0,
                severity=Severity.LOW,
                enabled=True,
            )
            session.add_all([tied, loose])
            await session.flush()
            session.add_all(
                [
                    CustomRuleBreach(
                        rule_id=tied.id,
                        state=AnomalyState.OPEN,
                        first_bucket_start=T0,
                        last_bucket_start=ANOMALY_END,
                        opened_at=T0,
                        subject_label="the source",
                    ),
                    CustomRuleBreach(
                        rule_id=loose.id,
                        state=AnomalyState.OPEN,
                        first_bucket_start=T0,
                        last_bucket_start=ANOMALY_END,
                        opened_at=T0,
                        subject_label="the class",
                    ),
                ]
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        breaches = [
            item for item in body["anomalies"]["items"] if item["family"] == "custom_rule"
        ]
        assert len(breaches) == 1
        assert breaches[0]["type"] == "CUSTOM_RULE_BREACH"
        assert breaches[0]["triage_status"] == "NEW"

    def test_more_than_twenty_is_truncated_with_the_full_total(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add_all(
                phase_a(world["source"], anomaly_start=T0 + timedelta(minutes=i))
                for i in range(25)
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        section = body["anomalies"]
        assert section["state"] == "TRUNCATED"
        assert section["reason_code"] == "related_activity.reason.items_truncated"
        assert section["total"] == 28
        assert len(section["items"]) == 20


# ----------------------------------------------------------------------- rules
class TestRules:
    def test_the_union_covers_all_offenses_not_only_the_twenty_shown(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add_all(
                _snapshot(
                    world["instance"],
                    800 + i,
                    magnitude=i,
                    rule_ids=[101, 102] if i == 0 else [101],
                )
                for i in range(25)
            )
            session.add(_rule(world["instance"], 101, name="frequent"))
            session.add(_rule(world["instance"], 102, name="once"))

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        assert len(body["offenses"]["items"]) == 20
        rules = {rule["qradar_id"]: rule for rule in body["rules"]["items"]}
        assert rules[101]["related_offenses"] == 25
        assert rules[101]["name"] == "frequent"
        assert rules[102]["related_offenses"] == 1

    def test_a_rule_missing_from_the_catalog_is_listed_with_a_null_name(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(_snapshot(world["instance"], 901, rule_ids=[999]))
            session.add(_rule(world["instance"], 101, name="known"))

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        rules = {rule["qradar_id"]: rule for rule in body["rules"]["items"]}
        assert rules[999]["id"] is None
        assert rules[999]["name"] is None
        assert rules[999]["type"] is None
        assert rules[999]["related_offenses"] == 1
        assert rules[999]["matched_events"] is None
        assert rules[999]["measurement"] == "NOT_COLLECTED"

    def test_measured_windows_give_exact_and_lower_bound_counts(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(_snapshot(world["instance"], 1001, rule_ids=[201, 202]))
            exact = _rule(world["instance"], 201, name="exact")
            common = _rule(world["instance"], 202, name="common", rule_type="COMMON")
            session.add_all([exact, common])
            await session.flush()
            session.add(
                _window_row(
                    world["instance"],
                    start=WINDOW_START,
                    seconds=int((WINDOW_END - WINDOW_START).total_seconds()),
                    status=RuleMatchWindowStatus.COMPLETE,
                )
            )
            session.add_all(
                [
                    RuleMatchMetric(
                        rule_id=exact.id,
                        bucket_start=WINDOW_START,
                        bucket_seconds=300,
                        match_records=10,
                        matched_events=50,
                    ),
                    RuleMatchMetric(
                        rule_id=common.id,
                        bucket_start=WINDOW_START,
                        bucket_seconds=300,
                        match_records=5,
                        matched_events=20,
                    ),
                ]
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        rules = {rule["qradar_id"]: rule for rule in body["rules"]["items"]}
        assert rules[201]["matched_events"] == 50
        assert rules[201]["measurement"] == "EXACT"
        assert rules[202]["matched_events"] == 20
        assert rules[202]["measurement"] == "LOWER_BOUND"

    def test_a_rule_with_no_measured_window_is_null_not_zero(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(_snapshot(world["instance"], 1101, rule_ids=[301]))
            session.add(_rule(world["instance"], 301, name="unmeasured"))

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        rule = body["rules"]["items"][0]
        assert rule["matched_events"] is None
        assert rule["measurement"] == "NOT_COLLECTED"

    def test_an_event_rule_absent_from_a_complete_window_is_a_measured_zero(
        self, world
    ) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(_snapshot(world["instance"], 1201, rule_ids=[401]))
            session.add(_rule(world["instance"], 401, name="quiet"))
            session.add(
                _window_row(
                    world["instance"],
                    start=WINDOW_START,
                    seconds=int((WINDOW_END - WINDOW_START).total_seconds()),
                    status=RuleMatchWindowStatus.COMPLETE,
                )
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        rule = body["rules"]["items"][0]
        assert rule["matched_events"] == 0
        assert rule["measurement"] == "EXACT"

    def test_rules_order_by_offense_count_then_measurement_then_qradar_id(
        self, world
    ) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add_all(
                [
                    _snapshot(world["instance"], 1301, rule_ids=[503]),
                    _snapshot(world["instance"], 1302, rule_ids=[501]),
                    _snapshot(world["instance"], 1303, rule_ids=[502]),
                    _snapshot(world["instance"], 1304, rule_ids=[502]),
                ]
            )

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        assert [rule["qradar_id"] for rule in body["rules"]["items"]] == [502, 501, 503]


# --------------------------------------------------------------- section states
class TestSectionStates:
    def test_offense_collection_that_never_ran_is_not_collected(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            source = await make_log_source(session, inst, qradar_id=9001)
            anchor = phase_a(source.id)
            session.add(anchor)
            await session.flush()
            return anchor.id

        anomaly_id = run(seed)
        body = Client(READER).get(url("log_source", anomaly_id)).json()
        assert body["offenses"]["state"] == "NOT_COLLECTED"
        assert body["offenses"]["reason_code"] == (
            "related_activity.reason.offense_not_collected"
        )
        assert body["offenses"]["items"] == []
        assert body["rules"]["state"] == "NOT_COLLECTED"
        assert body["rules"]["reason_code"] == (
            "related_activity.reason.rules_not_collected"
        )
        # The local anomaly tables are still readable.
        assert body["anomalies"]["state"] == "OK"

    def test_a_stale_watermark_makes_offenses_and_rules_stale(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            source = await make_log_source(session, inst, qradar_id=9001)
            await _watermark(
                session, inst.id, last_run_at=NOW - timedelta(hours=1), failures=0
            )
            anchor = phase_a(source.id)
            session.add(anchor)
            await session.flush()
            return anchor.id

        anomaly_id = run(seed)
        body = Client(READER).get(url("log_source", anomaly_id)).json()
        assert body["offenses"]["state"] == "STALE"
        assert body["offenses"]["reason_code"] == "related_activity.reason.offense_stale"
        assert body["rules"]["state"] == "STALE"
        assert body["rules"]["reason_code"] == "related_activity.reason.rules_stale"

    def test_a_recent_run_is_ok_and_an_empty_window_is_an_empty_ok_list(
        self, world
    ) -> None:
        # The fixture's watermark was stamped when this module was imported. A whole
        # integration run can be longer than the staleness threshold, so restamp it
        # at test time: freshness is what this test is about.
        async def refresh(session):  # type: ignore[no-untyped-def]
            watermark = await session.scalar(
                select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == world["instance"],
                    CollectionWatermark.collector == "offense_snapshot",
                )
            )
            assert watermark is not None
            watermark.last_run_at = datetime.now(UTC)
            watermark.watermark_at = watermark.last_run_at

        run(refresh)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        assert body["offenses"]["state"] == "OK"
        assert body["offenses"]["reason_code"] is None
        assert body["offenses"]["total"] == 0
        assert body["rules"]["state"] == "OK"


# --------------------------------------------------------------- response shape
class TestResponseShape:
    def test_every_key_set_is_pinned(self, world) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            session.add(_snapshot(world["instance"], 1401, rule_ids=[601]))
            session.add(_rule(world["instance"], 601, name="shape"))

        run(seed)
        body = Client(READER).get(url("log_source", world["phase_a"])).json()
        assert set(body) == {"state", "reason_code", "anchor", "offenses", "anomalies", "rules"}
        assert set(body["anchor"]) == {
            "family",
            "anomaly_id",
            "log_source_id",
            "qradar_log_source_id",
            "log_source_name",
            "window_start",
            "window_end",
            "pad",
        }
        for section in ("offenses", "anomalies", "rules"):
            assert set(body[section]) == {"state", "reason_code", "total", "items"}
        assert set(body["offenses"]["items"][0]) == {
            "qradar_offense_id",
            "description",
            "status",
            "magnitude",
            "severity",
            "start_time",
            "last_updated_time",
            "close_time",
            "offense_type_name",
            "rule_count",
            "captured_at",
        }
        assert set(body["anomalies"]["items"][0]) == {
            "family",
            "id",
            "type",
            "lifecycle_state",
            "severity",
            "start",
            "end",
            "triage_status",
        }
        assert set(body["rules"]["items"][0]) == {
            "id",
            "qradar_id",
            "name",
            "type",
            "related_offenses",
            "matched_events",
            "measurement",
        }


# ------------------------------------------------------------- statement budget
def test_statement_count_is_the_same_at_one_and_fifty_rows(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        inst = await make_instance(session)
        source = await make_log_source(session, inst, qradar_id=9001)
        await _watermark(session, inst.id, last_run_at=NOW)
        anchor = phase_a(source.id)
        session.add(anchor)
        session.add(_snapshot(inst.id, 1501, rule_ids=[701]))
        session.add(_rule(inst.id, 701))
        session.add(phase_a(source.id, anomaly_start=T0 + timedelta(minutes=1)))
        await session.flush()
        return {"source": source.id, "instance": inst.id, "anchor": anchor.id}

    ids = run(seed)
    client = Client(READER)
    first = client.get(url("log_source", ids["anchor"]))
    assert first.status_code == 200
    assert first.json()["offenses"]["total"] == 1
    assert first.json()["anomalies"]["total"] == 1
    assert first.json()["rules"]["total"] == 1
    small = client.statements

    async def grow(session):  # type: ignore[no-untyped-def]
        session.add_all(
            _snapshot(ids["instance"], 1600 + i, rule_ids=[800 + i]) for i in range(49)
        )
        session.add_all(
            _rule(ids["instance"], 800 + i) for i in range(49)
        )
        session.add_all(
            phase_a(ids["source"], anomaly_start=T0 + timedelta(minutes=2 + i))
            for i in range(49)
        )

    run(grow)
    second = client.get(url("log_source", ids["anchor"]))
    assert second.status_code == 200
    assert second.json()["offenses"]["total"] == 50
    assert second.json()["anomalies"]["total"] == 50
    assert second.json()["rules"]["total"] == 50
    assert client.statements == small
