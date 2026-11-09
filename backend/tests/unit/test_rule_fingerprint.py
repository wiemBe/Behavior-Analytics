"""The rule silence and storm alert fingerprint (SOC-6 design §5.5)."""

from __future__ import annotations

import uuid

from app.alerts.fingerprint import custom_rule_breach_fingerprint, rule_anomaly_fingerprint

INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("33333333-3333-3333-3333-333333333333")
RULE = uuid.UUID("22222222-2222-2222-2222-222222222222")
SILENCE = "RULE_SILENCE"
STORM = "RULE_STORM"


def test_the_fingerprint_is_stable_and_prefixed() -> None:
    first = rule_anomaly_fingerprint(INSTANCE, RULE, SILENCE)
    assert first == rule_anomaly_fingerprint(str(INSTANCE), str(RULE), SILENCE)
    assert first.startswith("rule_anomaly:")
    assert len(first.split(":", 1)[1]) == 32


def test_instance_and_detector_change_the_fingerprint_and_the_name_does_not() -> None:
    silence = rule_anomaly_fingerprint(INSTANCE, RULE, SILENCE)
    assert silence != rule_anomaly_fingerprint(OTHER, RULE, SILENCE)
    assert silence != rule_anomaly_fingerprint(INSTANCE, RULE, STORM)
    assert silence != rule_anomaly_fingerprint(INSTANCE, uuid.uuid4(), SILENCE)
    # The function takes no name: a rename cannot mint a new alert.
    assert silence == rule_anomaly_fingerprint(INSTANCE, RULE, SILENCE)


def test_it_does_not_collide_with_a_custom_rule_breach() -> None:
    mine = rule_anomaly_fingerprint(INSTANCE, RULE, SILENCE)
    breach = custom_rule_breach_fingerprint(RULE)
    assert mine != breach
    assert not breach.startswith("rule_anomaly:")
