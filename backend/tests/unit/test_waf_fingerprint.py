"""Natural-key fingerprints for WAF endpoint alerts."""

from __future__ import annotations

import uuid

from app.alerts.fingerprint import waf_anomaly_fingerprint


def _fingerprint(**overrides) -> str:  # type: ignore[no-untyped-def]
    values = {
        "instance_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "log_source_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "virtual_host": "portal.example",
        "normalized_endpoint": "/login/{id}",
        "http_method": "POST",
        "normalizer_version": 1,
        "detector_type": "WAF_REQUEST_RATE_SPIKE",
    }
    values.update(overrides)
    return waf_anomaly_fingerprint(**values)


def test_waf_fingerprint_is_stable_for_the_same_natural_key() -> None:
    assert _fingerprint() == _fingerprint()
    assert _fingerprint().startswith("waf_anomaly:")


def test_waf_fingerprint_differs_for_every_natural_key_part_and_detector() -> None:
    original = _fingerprint()
    variants = {
        _fingerprint(instance_id=uuid.UUID("33333333-3333-3333-3333-333333333333")),
        _fingerprint(log_source_id=uuid.UUID("44444444-4444-4444-4444-444444444444")),
        _fingerprint(virtual_host=None),
        _fingerprint(normalized_endpoint="/admin/{id}"),
        _fingerprint(http_method="GET"),
        _fingerprint(normalizer_version=2),
        _fingerprint(detector_type="WAF_BLOCK_RATIO_SPIKE"),
    }
    assert original not in variants
    assert len(variants) == 7
