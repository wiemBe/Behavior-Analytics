"""Integration-suite default for the API's effective-settings cache (OPS-FLAGS S3b).

The cache keeps one resolved read of the feature switches for five seconds, so the
first request of a test pays one extra statement and the second does not. Several
older tests pin that two requests cost the same number of statements. With a zero
lifetime every request reads the switches once, which is the constant they measure.
`tests/integration/test_effective_settings_api.py` sets the real lifetime again.
"""

from __future__ import annotations

import pytest

from app.services.feature_switches import effective_cache


@pytest.fixture(autouse=True)
def _effective_settings_are_read_per_request():  # type: ignore[no-untyped-def]
    saved = effective_cache.ttl
    effective_cache.ttl = 0.0
    effective_cache.invalidate()
    yield
    effective_cache.ttl = saved
    effective_cache.invalidate()
