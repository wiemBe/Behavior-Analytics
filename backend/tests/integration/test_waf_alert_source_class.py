"""WAF alerts carry the Phase C source class, so class-scoped routes match them.

`app.alerts.routing.RoutingRule` matches `alert.context["source_class"]`, and
Phase A (and inventory) alerts set it. A WAF incident must reach the same
routes: the class is added to the alert context when the source has a
classification row, omitted when it has none, and never enters the fingerprint.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import waf_anomaly_fingerprint
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    ClassificationReviewStatus,
    NotificationChannel,
    SourceClass,
    WafAnomalyType,
)
from app.models.fleet import LogSourceClassification
from tests.integration.test_waf_alerting import (
    STEP,
    T0,
    alert_settings,
    evaluate,
    notification_count,
    seed_endpoint,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def classify(session, log_source_id, source_class: SourceClass) -> None:  # type: ignore[no-untyped-def]
    """The Phase C outcome for one source, as the fleet sync would write it."""
    session.add(
        LogSourceClassification(
            log_source_id=log_source_id,
            source_class=source_class.value,
            basis="NAME_REGEX",
            conflicts=[],
            classified_at=T0,
            review_status=ClassificationReviewStatus.REVIEWED.value,
        )
    )
    await session.flush()


def class_scoped_policy(*, source_classes: frozenset[str], target: str) -> RoutingPolicy:
    return RoutingPolicy(
        rules=[
            RoutingRule(
                channel=NotificationChannel.SLACK,
                target=target,
                alert_types=frozenset({"waf_anomaly"}),
                source_classes=source_classes,
            )
        ]
    )


async def test_classified_source_reaches_a_waf_scoped_route(db_session) -> None:
    settings = alert_settings()
    _instance, source, endpoint = await seed_endpoint(db_session)
    await classify(db_session, source.id, SourceClass.WAF)
    policy = RoutingPolicy(
        rules=[
            RoutingRule(
                channel=NotificationChannel.SLACK,
                target="waf-class",
                alert_types=frozenset({"waf_anomaly"}),
                source_classes=frozenset({"WAF"}),
            ),
            RoutingRule(
                channel=NotificationChannel.EMAIL,
                target="firewall-class",
                alert_types=frozenset({"waf_anomaly"}),
                source_classes=frozenset({"FIREWALL"}),
            ),
        ]
    )
    dispatcher = NotificationDispatcher(db_session, {}, policy, settings=settings)

    await evaluate(db_session, settings, source, endpoint, T0, 500, enqueuer=dispatcher)
    alert = await db_session.scalar(select(Alert))
    assert alert is not None
    assert alert.context["source_class"] == "WAF"
    # The WAF-scoped rule matches; the FIREWALL-scoped one does not.
    assert [route.target for route in policy.resolve(alert)] == ["waf-class"]
    assert (await db_session.scalars(select(AlertNotification.target))).all() == ["waf-class"]

    # A refresh of the still-open condition keeps carrying the class.
    await evaluate(
        db_session, settings, source, endpoint, T0 + STEP, 600, enqueuer=dispatcher
    )
    assert alert.occurrence_count == 2
    assert alert.context["source_class"] == "WAF"


async def test_unclassified_source_omits_the_key(db_session) -> None:
    settings = alert_settings()
    _instance, source, endpoint = await seed_endpoint(db_session)
    policy = class_scoped_policy(source_classes=frozenset({"WAF"}), target="waf-class")
    dispatcher = NotificationDispatcher(db_session, {}, policy, settings=settings)

    await evaluate(db_session, settings, source, endpoint, T0, 500, enqueuer=dispatcher)
    alert = await db_session.scalar(select(Alert))
    assert alert is not None
    # Absent, not None and not "": a class-scoped rule must not match it.
    assert "source_class" not in alert.context
    assert policy.resolve(alert) == []
    assert await notification_count(db_session) == 0


async def test_route_scoped_to_another_class_does_not_match(db_session) -> None:
    settings = alert_settings()
    _instance, source, endpoint = await seed_endpoint(db_session)
    await classify(db_session, source.id, SourceClass.WAF)
    policy = class_scoped_policy(source_classes=frozenset({"FIREWALL"}), target="fw")
    dispatcher = NotificationDispatcher(db_session, {}, policy, settings=settings)

    await evaluate(db_session, settings, source, endpoint, T0, 500, enqueuer=dispatcher)
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and alert.context["source_class"] == "WAF"
    assert policy.resolve(alert) == []
    assert await notification_count(db_session) == 0


async def test_the_fingerprint_does_not_change_with_a_classification_row(
    db_session,
) -> None:
    settings = alert_settings()
    _instance, source, endpoint = await seed_endpoint(db_session)
    await evaluate(db_session, settings, source, endpoint, T0, 500)
    opened = await db_session.scalar(select(Alert))
    assert opened is not None
    assert "source_class" not in opened.context
    expected = waf_anomaly_fingerprint(
        instance_id=endpoint.instance_id,
        log_source_id=endpoint.log_source_id,
        virtual_host=endpoint.virtual_host,
        normalized_endpoint=endpoint.normalized_endpoint,
        http_method=endpoint.http_method,
        normalizer_version=endpoint.normalizer_version,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
    )
    assert opened.fingerprint == expected

    # Classify mid-incident, then recover and recur: identity is the class-free
    # fingerprint, so the new episode is a new row with the same fingerprint.
    await classify(db_session, source.id, SourceClass.WAF)
    await evaluate(db_session, settings, source, endpoint, T0 + STEP, 100)
    await evaluate(db_session, settings, source, endpoint, T0 + STEP * 2, 700)
    alerts = list((await db_session.scalars(select(Alert))).all())
    assert len(alerts) == 2
    assert alerts[0].id != alerts[1].id
    assert alerts[1].fingerprint == expected == alerts[0].fingerprint
    assert alerts[1].context["source_class"] == "WAF"
