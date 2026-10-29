"""MSG-6: the WAF anomaly sentences carry message codes and keep their English.

`EXPECTED` is the English the detectors wrote before they had codes, captured from that code
(the base commit) for every scenario of `waf_message_scenarios.py`; a reworded template, a
changed number format or a lost code fails here. The remaining sections pin what surrounds the
sentences: the scope map, the history checks, the caveats, the noun phrases and the projection
the API serves.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import DetectorSignal, WafAnomalyType, WafMeasurement
from app.models.waf import WafAnomaly
from app.schemas.anomaly import waf_detection_detail
from app.schemas.messages import MessageValue
from app.waf.behavior import (
    BLOCK_RATIO,
    CLIENT_ERROR_RATIO,
    SERVER_ERROR_RATIO,
    contributors,
    detect_new_endpoint_access,
)
from app.waf.detector import (
    _SCOPE_CODES,
    detect_request_rate_spike,
    scope_reason_ref,
    semantics_message,
    split_reason,
)
from app.waf.engine import WafAnomalyEngine
from app.waf.inputs import HistoryFacts, WafHistoryFacts
from app.waf.measurement import WafMeasurementRequirement, WafVerdictScope, verdict_scope
from app.waf.observations import BucketFacts
from app.waf.semantics import semantics_incompatibility
from tests.unit import test_waf_behavior as tb
from tests.unit import test_waf_detector as td
from tests.unit import test_waf_novelty as tn
from tests.unit.waf_message_scenarios import (
    SCENARIOS,
    SCOPE_INCREASE,
    SCOPE_NEEDS_EXACT,
    SCOPE_UNSTATED,
)

EXPECTED: dict[str, tuple[str, str]] = {
    "rate.partial": (
        "UNKNOWN",
        "bucket is partial, not a whole observation",
    ),
    "rate.missing": (
        "UNKNOWN",
        "bucket is missing, not a whole observation",
    ),
    "rate.scope_unstated": (
        "UNKNOWN",
        "the bucket does not state whether its counts are exact or lower bounds",
    ),
    "rate.scope_needs_exact": (
        "UNKNOWN",
        "the detector needs exact counts and this count is a lower bound",
    ),
    "rate.scope_plain_text": (
        "UNKNOWN",
        "a caller's own wording",
    ),
    "rate.coverage_low": (
        "UNKNOWN",
        "only 62\xa0% of this source's events carried a parseable endpoint in this bucket",
    ),
    "rate.incompatible_bucket": (
        "UNKNOWN",
        (
            "the bucket carries no metric-semantics identity; it was collected before identities "
            "were recorded and cannot be judged"
        ),
    ),
    "rate.incompatible_baseline": (
        "UNKNOWN",
        "the baseline cell carries no metric-semantics identity; rebuild required",
    ),
    "rate.incompatible_mismatch": (
        "UNKNOWN",
        (
            "the baseline was built under 'request_count/v1' but the bucket was collected under "
            "'request_count/v2'; rebuild required"
        ),
    ),
    "rate.no_baseline": (
        "UNKNOWN",
        "no request-rate baseline for this weekday/hour cell yet",
    ),
    "rate.baseline_samples": (
        "UNKNOWN",
        "baseline has 3 sample(s), below the minimum required for a defensible verdict",
    ),
    "rate.below_min_volume": (
        "HEALTHY",
        "12 requests/bucket is below the 20 minimum for a defensible verdict",
    ),
    "rate.ratio_not_reached": (
        "HEALTHY",
        "observed/expected ratio 1.50 has not reached 2.00",
    ),
    "rate.delta_below_min": (
        "HEALTHY",
        "increase of 25 requests/bucket is below the 50 minimum absolute delta",
    ),
    "rate.z_not_reached": (
        "HEALTHY",
        "robust z=2.53 has not reached the +3.5 threshold",
    ),
    "rate.mad_within_band": (
        "HEALTHY",
        (
            "robust score unavailable (MAD=0) and observed value is inside the expected band "
            "[80.00, 300.00]"
        ),
    ),
    "rate.fired": (
        "ANOMALOUS",
        (
            "500 requests is above the baseline median 100.0 (5.00x expected, +400 "
            "requests/bucket); robust z=26.98 (threshold +3.5)"
        ),
    ),
    "rate.fired_mad_outside_band": (
        "ANOMALOUS",
        (
            "500 requests is above the baseline median 100.0 (5.00x expected, +400 "
            "requests/bucket); robust score unavailable (MAD=0); observed value is outside the "
            "expected band [80.00, 130.00]"
        ),
    ),
    "rate.fired_expected_zero": (
        "ANOMALOUS",
        (
            "300 requests is above the baseline median 0.0 (expected no traffic, +300 "
            "requests/bucket); robust score unavailable (MAD=0); observed value is outside the "
            "expected band [0.00, 10.00]"
        ),
    ),
    "rate.lower_fired": (
        "ANOMALOUS",
        (
            "at least 500 requests (a lower bound: the count is a lower bound: an increase can be "
            "proven, normal behaviour cannot) is above the baseline median 100.0 (5.00x expected, "
            "+400 requests/bucket); robust z=26.98 (threshold +3.5)"
        ),
    ),
    "rate.lower_fired_plain_scope": (
        "ANOMALOUS",
        (
            "at least 500 requests (a lower bound: a caller's own wording) is above the baseline "
            "median 100.0 (5.00x expected, +400 requests/bucket); robust z=26.98 (threshold +3.5)"
        ),
    ),
    "rate.lower_fired_no_scope": (
        "ANOMALOUS",
        (
            "at least 500 requests (a lower bound: some events could not be attributed) is above "
            "the baseline median 100.0 (5.00x expected, +400 requests/bucket); robust z=26.98 "
            "(threshold +3.5)"
        ),
    ),
    "rate.lower_fired_mad_outside_band": (
        "ANOMALOUS",
        (
            "at least 500 requests (a lower bound: the count is a lower bound: an increase can be "
            "proven, normal behaviour cannot) is above the baseline median 100.0 (5.00x expected, "
            "+400 requests/bucket); robust score unavailable (MAD=0); observed value is outside "
            "the expected band [80.00, 130.00]"
        ),
    ),
    "rate.lower_below_min_volume": (
        "UNKNOWN",
        (
            "at least 12 requests were attributed to this endpoint, but the count is a lower "
            "bound and 12 requests/bucket is below the 20 minimum for a defensible verdict; a "
            "normal verdict cannot be concluded from a lower bound"
        ),
    ),
    "rate.lower_ratio_not_reached": (
        "UNKNOWN",
        (
            "at least 150 requests were attributed to this endpoint, but the count is a lower "
            "bound and observed/expected ratio 1.50 has not reached 2.00; a normal verdict cannot "
            "be concluded from a lower bound"
        ),
    ),
    "rate.lower_delta_below_min": (
        "UNKNOWN",
        (
            "at least 45 requests were attributed to this endpoint, but the count is a lower "
            "bound and increase of 25 requests/bucket is below the 50 minimum absolute delta; a "
            "normal verdict cannot be concluded from a lower bound"
        ),
    ),
    "rate.lower_z_not_reached": (
        "UNKNOWN",
        (
            "at least 250 requests were attributed to this endpoint, but the count is a lower "
            "bound and robust z=2.53 has not reached the +3.5 threshold; a normal verdict cannot "
            "be concluded from a lower bound"
        ),
    ),
    "rate.lower_mad_within_band": (
        "UNKNOWN",
        (
            "at least 250 requests were attributed to this endpoint, but the count is a lower "
            "bound and robust score unavailable (MAD=0) and observed value is inside the expected "
            "band [80.00, 300.00]; a normal verdict cannot be concluded from a lower bound"
        ),
    ),
    "ratio.block.not_collected": (
        "UNKNOWN",
        (
            "the ACTION breakdown was not collected for this bucket: the value is unavailable, "
            "not zero"
        ),
    ),
    "ratio.block.failed": (
        "UNKNOWN",
        "the ACTION breakdown failed for this bucket: the value is unavailable, not zero",
    ),
    "ratio.block.scope_none_coded": (
        "UNKNOWN",
        "the detector needs exact counts and this count is a lower bound",
    ),
    "ratio.block.scope_none_plain": (
        "UNKNOWN",
        "semantics unknown",
    ),
    "ratio.block.scope_none_no_reason": (
        "UNKNOWN",
        "the inputs do not support a verdict",
    ),
    "ratio.block.source_coverage_low": (
        "UNKNOWN",
        (
            "only 62\xa0% of this source's events parsed to an endpoint, below the 90\xa0% "
            "coverage floor: the unparsed requests may differ in kind from the parsed ones, so "
            "the visible proportion bounds nothing"
        ),
    ),
    "ratio.block.field_coverage_low": (
        "UNKNOWN",
        (
            "only 85\xa0% of the endpoint's requests carried the action field, below the 90\xa0% "
            "coverage floor"
        ),
    ),
    "ratio.block.incompatible": (
        "UNKNOWN",
        (
            "the baseline was built under 'request_count/v1' but the bucket was collected under "
            "'request_count/v2'; rebuild required"
        ),
    ),
    "ratio.block.no_baseline": (
        "UNKNOWN",
        "no block_ratio baseline for this weekday/hour cell yet",
    ),
    "ratio.block.baseline_samples": (
        "UNKNOWN",
        (
            "the block_ratio baseline has 2 material bucket(s), below the minimum for a "
            "defensible verdict"
        ),
    ),
    "ratio.block.no_denominator": (
        "UNKNOWN",
        "the block_ratio baseline has no pooled denominator",
    ),
    "ratio.block.numerator_below_min": (
        "HEALTHY",
        (
            "9 blocked requests is below the 10 minimum; no material rise in the proportion is "
            "possible"
        ),
    ),
    "ratio.block.denominator_below_min": (
        "UNKNOWN",
        "49 requests carrying a WAF action is below the 50 minimum denominator for a ratio verdict",
    ),
    "ratio.block.guard_rise": (
        "HEALTHY",
        "6.0\xa0% is +3.0\xa0pp from the expected 3.0\xa0%, below the 10\xa0pp minimum rise",
    ),
    "ratio.block.lower_guard_rise": (
        "UNKNOWN",
        (
            "6.0\xa0% is +3.0\xa0pp from the expected 3.0\xa0%, below the 10\xa0pp minimum rise; "
            "the numerator is a lower bound (the count is a lower bound: an increase can be "
            "proven, normal behaviour cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.block.guard_multiple": (
        "HEALTHY",
        "50.0\xa0% is 1.67x the expected 30.0\xa0%, below 2.00x",
    ),
    "ratio.block.lower_guard_multiple": (
        "UNKNOWN",
        (
            "50.0\xa0% is 1.67x the expected 30.0\xa0%, below 2.00x; the numerator is a lower "
            "bound (the count is a lower bound: an increase can be proven, normal behaviour "
            "cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.block.guard_robust_z": (
        "HEALTHY",
        "robust z=1.01 has not reached +3.5",
    ),
    "ratio.block.lower_guard_robust_z": (
        "UNKNOWN",
        (
            "robust z=1.01 has not reached +3.5; the numerator is a lower bound (the count is a "
            "lower bound: an increase can be proven, normal behaviour cannot), so a normal "
            "verdict cannot be concluded from it"
        ),
    ),
    "ratio.block.guard_mad_zero_within_p95": (
        "HEALTHY",
        "robust score unavailable (MAD=0) and 20.0\xa0% is within the historical p95 20.0\xa0%",
    ),
    "ratio.block.lower_guard_mad_zero_within_p95": (
        "UNKNOWN",
        (
            "robust score unavailable (MAD=0) and 20.0\xa0% is within the historical p95 "
            "20.0\xa0%; the numerator is a lower bound (the count is a lower bound: an increase "
            "can be proven, normal behaviour cannot), so a normal verdict cannot be concluded "
            "from it"
        ),
    ),
    "ratio.block.lower_guard_no_scope": (
        "UNKNOWN",
        (
            "9 blocked requests is below the 10 minimum; no material rise in the proportion is "
            "possible; the numerator is a lower bound (some values were unclassified), so a "
            "normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.block.lower_guard_plain_scope": (
        "UNKNOWN",
        (
            "9 blocked requests is below the 10 minimum; no material rise in the proportion is "
            "possible; the numerator is a lower bound (odd), so a normal verdict cannot be "
            "concluded from it"
        ),
    ),
    "ratio.block.fired": (
        "ANOMALOUS",
        (
            "40 of 100 requests carrying a WAF action to GET /login were blocked requests "
            "(40.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.block.fired_mad_above_p95": (
        "ANOMALOUS",
        (
            "25 of 100 requests carrying a WAF action to GET /login were blocked requests "
            "(25.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+15.0\xa0pp, 2.5x); robust score unavailable (MAD=0); 25.0\xa0% is above the "
            "historical p95 20.0\xa0%"
        ),
    ),
    "ratio.block.fired_no_multiple": (
        "ANOMALOUS",
        (
            "15 of 100 requests carrying a WAF action to GET /login were blocked requests "
            "(15.0\xa0%), against an expected 0.0\xa0% from 1000 historical requests "
            "(+15.0\xa0pp); robust score unavailable (MAD=0); 15.0\xa0% is above the historical "
            "p95 0.0\xa0%"
        ),
    ),
    "ratio.block.lower_fired": (
        "ANOMALOUS",
        (
            "at least 40 of 100 requests carrying a WAF action to GET /login were blocked "
            "requests (at least 40.0\xa0%), against an expected 10.0\xa0% from 1000 historical "
            "requests (+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.block.lower_fired_no_multiple": (
        "ANOMALOUS",
        (
            "at least 15 of 100 requests carrying a WAF action to GET /login were blocked "
            "requests (at least 15.0\xa0%), against an expected 0.0\xa0% from 1000 historical "
            "requests (+15.0\xa0pp); robust score unavailable (MAD=0); 15.0\xa0% is above the "
            "historical p95 0.0\xa0%"
        ),
    ),
    "ratio.block.fired_no_entity": (
        "ANOMALOUS",
        (
            "40 of 100 requests carrying a WAF action to this endpoint were blocked requests "
            "(40.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.server.not_collected": (
        "UNKNOWN",
        (
            "the RESPONSE_CODE breakdown was not collected for this bucket: the value is "
            "unavailable, not zero"
        ),
    ),
    "ratio.server.failed": (
        "UNKNOWN",
        "the RESPONSE_CODE breakdown failed for this bucket: the value is unavailable, not zero",
    ),
    "ratio.server.scope_none_coded": (
        "UNKNOWN",
        "the detector needs exact counts and this count is a lower bound",
    ),
    "ratio.server.scope_none_plain": (
        "UNKNOWN",
        "semantics unknown",
    ),
    "ratio.server.scope_none_no_reason": (
        "UNKNOWN",
        "the inputs do not support a verdict",
    ),
    "ratio.server.source_coverage_low": (
        "UNKNOWN",
        (
            "only 62\xa0% of this source's events parsed to an endpoint, below the 90\xa0% "
            "coverage floor: the unparsed requests may differ in kind from the parsed ones, so "
            "the visible proportion bounds nothing"
        ),
    ),
    "ratio.server.field_coverage_low": (
        "UNKNOWN",
        (
            "only 85\xa0% of the endpoint's requests carried the response_code field, below the "
            "90\xa0% coverage floor"
        ),
    ),
    "ratio.server.incompatible": (
        "UNKNOWN",
        (
            "the baseline was built under 'request_count/v1' but the bucket was collected under "
            "'request_count/v2'; rebuild required"
        ),
    ),
    "ratio.server.no_baseline": (
        "UNKNOWN",
        "no server_error_ratio baseline for this weekday/hour cell yet",
    ),
    "ratio.server.baseline_samples": (
        "UNKNOWN",
        (
            "the server_error_ratio baseline has 2 material bucket(s), below the minimum for a "
            "defensible verdict"
        ),
    ),
    "ratio.server.no_denominator": (
        "UNKNOWN",
        "the server_error_ratio baseline has no pooled denominator",
    ),
    "ratio.server.numerator_below_min": (
        "HEALTHY",
        (
            "9 HTTP 5xx responses is below the 10 minimum; no material rise in the proportion is "
            "possible"
        ),
    ),
    "ratio.server.denominator_below_min": (
        "UNKNOWN",
        (
            "49 requests carrying a response code is below the 50 minimum denominator for a ratio "
            "verdict"
        ),
    ),
    "ratio.server.guard_rise": (
        "HEALTHY",
        "6.0\xa0% is +3.0\xa0pp from the expected 3.0\xa0%, below the 10\xa0pp minimum rise",
    ),
    "ratio.server.lower_guard_rise": (
        "UNKNOWN",
        (
            "6.0\xa0% is +3.0\xa0pp from the expected 3.0\xa0%, below the 10\xa0pp minimum rise; "
            "the numerator is a lower bound (the count is a lower bound: an increase can be "
            "proven, normal behaviour cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.server.guard_multiple": (
        "HEALTHY",
        "50.0\xa0% is 1.67x the expected 30.0\xa0%, below 2.00x",
    ),
    "ratio.server.lower_guard_multiple": (
        "UNKNOWN",
        (
            "50.0\xa0% is 1.67x the expected 30.0\xa0%, below 2.00x; the numerator is a lower "
            "bound (the count is a lower bound: an increase can be proven, normal behaviour "
            "cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.server.guard_robust_z": (
        "HEALTHY",
        "robust z=1.01 has not reached +3.5",
    ),
    "ratio.server.lower_guard_robust_z": (
        "UNKNOWN",
        (
            "robust z=1.01 has not reached +3.5; the numerator is a lower bound (the count is a "
            "lower bound: an increase can be proven, normal behaviour cannot), so a normal "
            "verdict cannot be concluded from it"
        ),
    ),
    "ratio.server.guard_mad_zero_within_p95": (
        "HEALTHY",
        "robust score unavailable (MAD=0) and 20.0\xa0% is within the historical p95 20.0\xa0%",
    ),
    "ratio.server.lower_guard_mad_zero_within_p95": (
        "UNKNOWN",
        (
            "robust score unavailable (MAD=0) and 20.0\xa0% is within the historical p95 "
            "20.0\xa0%; the numerator is a lower bound (the count is a lower bound: an increase "
            "can be proven, normal behaviour cannot), so a normal verdict cannot be concluded "
            "from it"
        ),
    ),
    "ratio.server.lower_guard_no_scope": (
        "UNKNOWN",
        (
            "9 HTTP 5xx responses is below the 10 minimum; no material rise in the proportion is "
            "possible; the numerator is a lower bound (some values were unclassified), so a "
            "normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.server.lower_guard_plain_scope": (
        "UNKNOWN",
        (
            "9 HTTP 5xx responses is below the 10 minimum; no material rise in the proportion is "
            "possible; the numerator is a lower bound (odd), so a normal verdict cannot be "
            "concluded from it"
        ),
    ),
    "ratio.server.fired": (
        "ANOMALOUS",
        (
            "40 of 100 requests carrying a response code to GET /login were HTTP 5xx responses "
            "(40.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.server.fired_mad_above_p95": (
        "ANOMALOUS",
        (
            "25 of 100 requests carrying a response code to GET /login were HTTP 5xx responses "
            "(25.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+15.0\xa0pp, 2.5x); robust score unavailable (MAD=0); 25.0\xa0% is above the "
            "historical p95 20.0\xa0%"
        ),
    ),
    "ratio.server.fired_no_multiple": (
        "ANOMALOUS",
        (
            "15 of 100 requests carrying a response code to GET /login were HTTP 5xx responses "
            "(15.0\xa0%), against an expected 0.0\xa0% from 1000 historical requests "
            "(+15.0\xa0pp); robust score unavailable (MAD=0); 15.0\xa0% is above the historical "
            "p95 0.0\xa0%"
        ),
    ),
    "ratio.server.lower_fired": (
        "ANOMALOUS",
        (
            "at least 40 of 100 requests carrying a response code to GET /login were HTTP 5xx "
            "responses (at least 40.0\xa0%), against an expected 10.0\xa0% from 1000 historical "
            "requests (+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.server.lower_fired_no_multiple": (
        "ANOMALOUS",
        (
            "at least 15 of 100 requests carrying a response code to GET /login were HTTP 5xx "
            "responses (at least 15.0\xa0%), against an expected 0.0\xa0% from 1000 historical "
            "requests (+15.0\xa0pp); robust score unavailable (MAD=0); 15.0\xa0% is above the "
            "historical p95 0.0\xa0%"
        ),
    ),
    "ratio.server.fired_no_entity": (
        "ANOMALOUS",
        (
            "40 of 100 requests carrying a response code to this endpoint were HTTP 5xx responses "
            "(40.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.client.not_collected": (
        "UNKNOWN",
        (
            "the RESPONSE_CODE breakdown was not collected for this bucket: the value is "
            "unavailable, not zero"
        ),
    ),
    "ratio.client.failed": (
        "UNKNOWN",
        "the RESPONSE_CODE breakdown failed for this bucket: the value is unavailable, not zero",
    ),
    "ratio.client.scope_none_coded": (
        "UNKNOWN",
        "the detector needs exact counts and this count is a lower bound",
    ),
    "ratio.client.scope_none_plain": (
        "UNKNOWN",
        "semantics unknown",
    ),
    "ratio.client.scope_none_no_reason": (
        "UNKNOWN",
        "the inputs do not support a verdict",
    ),
    "ratio.client.source_coverage_low": (
        "UNKNOWN",
        (
            "only 62\xa0% of this source's events parsed to an endpoint, below the 90\xa0% "
            "coverage floor: the unparsed requests may differ in kind from the parsed ones, so "
            "the visible proportion bounds nothing"
        ),
    ),
    "ratio.client.field_coverage_low": (
        "UNKNOWN",
        (
            "only 85\xa0% of the endpoint's requests carried the response_code field, below the "
            "90\xa0% coverage floor"
        ),
    ),
    "ratio.client.incompatible": (
        "UNKNOWN",
        (
            "the baseline was built under 'request_count/v1' but the bucket was collected under "
            "'request_count/v2'; rebuild required"
        ),
    ),
    "ratio.client.no_baseline": (
        "UNKNOWN",
        "no client_error_ratio baseline for this weekday/hour cell yet",
    ),
    "ratio.client.baseline_samples": (
        "UNKNOWN",
        (
            "the client_error_ratio baseline has 2 material bucket(s), below the minimum for a "
            "defensible verdict"
        ),
    ),
    "ratio.client.no_denominator": (
        "UNKNOWN",
        "the client_error_ratio baseline has no pooled denominator",
    ),
    "ratio.client.numerator_below_min": (
        "HEALTHY",
        (
            "9 HTTP 4xx responses is below the 10 minimum; no material rise in the proportion is "
            "possible"
        ),
    ),
    "ratio.client.denominator_below_min": (
        "UNKNOWN",
        (
            "49 requests carrying a response code is below the 50 minimum denominator for a ratio "
            "verdict"
        ),
    ),
    "ratio.client.guard_rise": (
        "HEALTHY",
        "6.0\xa0% is +3.0\xa0pp from the expected 3.0\xa0%, below the 10\xa0pp minimum rise",
    ),
    "ratio.client.lower_guard_rise": (
        "UNKNOWN",
        (
            "6.0\xa0% is +3.0\xa0pp from the expected 3.0\xa0%, below the 10\xa0pp minimum rise; "
            "the numerator is a lower bound (the count is a lower bound: an increase can be "
            "proven, normal behaviour cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.client.guard_multiple": (
        "HEALTHY",
        "50.0\xa0% is 1.67x the expected 30.0\xa0%, below 2.00x",
    ),
    "ratio.client.lower_guard_multiple": (
        "UNKNOWN",
        (
            "50.0\xa0% is 1.67x the expected 30.0\xa0%, below 2.00x; the numerator is a lower "
            "bound (the count is a lower bound: an increase can be proven, normal behaviour "
            "cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.client.guard_robust_z": (
        "HEALTHY",
        "robust z=1.01 has not reached +3.5",
    ),
    "ratio.client.lower_guard_robust_z": (
        "UNKNOWN",
        (
            "robust z=1.01 has not reached +3.5; the numerator is a lower bound (the count is a "
            "lower bound: an increase can be proven, normal behaviour cannot), so a normal "
            "verdict cannot be concluded from it"
        ),
    ),
    "ratio.client.guard_mad_zero_within_p95": (
        "HEALTHY",
        "robust score unavailable (MAD=0) and 20.0\xa0% is within the historical p95 20.0\xa0%",
    ),
    "ratio.client.lower_guard_mad_zero_within_p95": (
        "UNKNOWN",
        (
            "robust score unavailable (MAD=0) and 20.0\xa0% is within the historical p95 "
            "20.0\xa0%; the numerator is a lower bound (the count is a lower bound: an increase "
            "can be proven, normal behaviour cannot), so a normal verdict cannot be concluded "
            "from it"
        ),
    ),
    "ratio.client.lower_guard_no_scope": (
        "UNKNOWN",
        (
            "9 HTTP 4xx responses is below the 10 minimum; no material rise in the proportion is "
            "possible; the numerator is a lower bound (some values were unclassified), so a "
            "normal verdict cannot be concluded from it"
        ),
    ),
    "ratio.client.lower_guard_plain_scope": (
        "UNKNOWN",
        (
            "9 HTTP 4xx responses is below the 10 minimum; no material rise in the proportion is "
            "possible; the numerator is a lower bound (odd), so a normal verdict cannot be "
            "concluded from it"
        ),
    ),
    "ratio.client.fired": (
        "ANOMALOUS",
        (
            "40 of 100 requests carrying a response code to GET /login were HTTP 4xx responses "
            "(40.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.client.fired_mad_above_p95": (
        "ANOMALOUS",
        (
            "25 of 100 requests carrying a response code to GET /login were HTTP 4xx responses "
            "(25.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+15.0\xa0pp, 2.5x); robust score unavailable (MAD=0); 25.0\xa0% is above the "
            "historical p95 20.0\xa0%"
        ),
    ),
    "ratio.client.fired_no_multiple": (
        "ANOMALOUS",
        (
            "15 of 100 requests carrying a response code to GET /login were HTTP 4xx responses "
            "(15.0\xa0%), against an expected 0.0\xa0% from 1000 historical requests "
            "(+15.0\xa0pp); robust score unavailable (MAD=0); 15.0\xa0% is above the historical "
            "p95 0.0\xa0%"
        ),
    ),
    "ratio.client.lower_fired": (
        "ANOMALOUS",
        (
            "at least 40 of 100 requests carrying a response code to GET /login were HTTP 4xx "
            "responses (at least 40.0\xa0%), against an expected 10.0\xa0% from 1000 historical "
            "requests (+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "ratio.client.lower_fired_no_multiple": (
        "ANOMALOUS",
        (
            "at least 15 of 100 requests carrying a response code to GET /login were HTTP 4xx "
            "responses (at least 15.0\xa0%), against an expected 0.0\xa0% from 1000 historical "
            "requests (+15.0\xa0pp); robust score unavailable (MAD=0); 15.0\xa0% is above the "
            "historical p95 0.0\xa0%"
        ),
    ),
    "ratio.client.fired_no_entity": (
        "ANOMALOUS",
        (
            "40 of 100 requests carrying a response code to this endpoint were HTTP 4xx responses "
            "(40.0\xa0%), against an expected 10.0\xa0% from 1000 historical requests "
            "(+30.0\xa0pp, 4.0x); robust z=10.12 (threshold +3.5)"
        ),
    ),
    "conc.not_collected": (
        "UNKNOWN",
        (
            "the SOURCE_IP breakdown was not collected for this bucket: the value is unavailable, "
            "not zero"
        ),
    ),
    "conc.failed": (
        "UNKNOWN",
        "the SOURCE_IP breakdown failed for this bucket: the value is unavailable, not zero",
    ),
    "conc.scope_inc_coded": (
        "UNKNOWN",
        "the count is a lower bound: an increase can be proven, normal behaviour cannot",
    ),
    "conc.scope_plain": (
        "UNKNOWN",
        "needs exact counts",
    ),
    "conc.scope_no_reason": (
        "UNKNOWN",
        "composition needs exact measurements",
    ),
    "conc.source_coverage_low": (
        "UNKNOWN",
        (
            "only 62\xa0% of this source's events parsed to an endpoint, below the 90\xa0% "
            "coverage floor: the unparsed requests may differ in kind from the parsed ones, so "
            "the visible proportion bounds nothing"
        ),
    ),
    "conc.attributed_low": (
        "UNKNOWN",
        (
            "only 85\xa0% of the endpoint's requests were attributed to a source IP, below the "
            "90\xa0% coverage floor"
        ),
    ),
    "conc.no_baseline": (
        "UNKNOWN",
        "no top1_client_share baseline for this weekday/hour cell yet",
    ),
    "conc.baseline_samples": (
        "UNKNOWN",
        (
            "the top1_client_share baseline has 1 material bucket(s), below the minimum for a "
            "defensible verdict"
        ),
    ),
    "conc.no_denominator": (
        "UNKNOWN",
        "the top1_client_share baseline has no pooled denominator",
    ),
    "conc.top_below_min": (
        "HEALTHY",
        "the largest source IP sent 19 requests, below the 20 minimum for a material concentration",
    ),
    "conc.endpoint_below_min": (
        "UNKNOWN",
        "49 requests is below the 50 minimum for a composition verdict",
    ),
    "conc.rise_below_min": (
        "HEALTHY",
        (
            "the largest source IP's share 25.0\xa0% is +15.0\xa0pp from the expected 10.0\xa0%, "
            "below the 20\xa0pp minimum rise"
        ),
    ),
    "conc.z_not_reached": (
        "HEALTHY",
        "robust z=1.69 has not reached +3.5",
    ),
    "conc.mad_within_p95": (
        "HEALTHY",
        "robust score unavailable (MAD=0) and 35.0\xa0% is within the historical p95 40.0\xa0%",
    ),
    "conc.fired_no_volume_baseline": (
        "ANOMALOUS",
        (
            "the largest source IP sent 60 of 100 requests to GET /login (60.0\xa0%), against an "
            "expected share of 10.0\xa0% (+50.0\xa0pp); the five largest sent 80.0\xa0%; endpoint "
            "volume 100 requests (no reliable volume baseline); robust z=33.72 (threshold +3.5)"
        ),
    ),
    "conc.fired_volume_baseline": (
        "ANOMALOUS",
        (
            "the largest source IP sent 60 of 100 requests to GET /login (60.0\xa0%), against an "
            "expected share of 10.0\xa0% (+50.0\xa0pp); the five largest sent 80.0\xa0%; endpoint "
            "volume 100 requests against an expected 120.5; robust z=33.72 (threshold +3.5)"
        ),
    ),
    "conc.fired_mad_above_p95": (
        "ANOMALOUS",
        (
            "the largest source IP sent 60 of 100 requests to GET /login (60.0\xa0%), against an "
            "expected share of 10.0\xa0% (+50.0\xa0pp); the five largest sent 70.0\xa0%; endpoint "
            "volume 100 requests (no reliable volume baseline); robust score unavailable (MAD=0); "
            "60.0\xa0% is above the historical p95 15.0\xa0%"
        ),
    ),
    "conc.fired_no_entity": (
        "ANOMALOUS",
        (
            "the largest source IP sent 60 of 100 requests to this endpoint (60.0\xa0%), against "
            "an expected share of 10.0\xa0% (+50.0\xa0pp); the five largest sent 70.0\xa0%; "
            "endpoint volume 100 requests (no reliable volume baseline); robust z=33.72 "
            "(threshold +3.5)"
        ),
    ),
    "new.no_facts": (
        "UNKNOWN",
        "the endpoint's first appearance was not established",
    ),
    "new.outside_window": (
        "HEALTHY",
        (
            "established endpoint: first admitted 2026-08-03T19:15:00+00:00, outside the "
            "12-bucket novelty window"
        ),
    ),
    "new.hand_off": (
        "HEALTHY",
        (
            "established endpoint: first admitted 2026-08-03T19:15:00+00:00 before the history "
            "hand-off 2026-08-04T19:25:00+00:00"
        ),
    ),
    "new.history_reason": (
        "UNKNOWN",
        "novelty cannot be established: bucket 12 was never collected",
    ),
    "new.history_scope_reason": (
        "UNKNOWN",
        "novelty cannot be established: scope reason",
    ),
    "new.history_scope_reason_coded": (
        "UNKNOWN",
        (
            "novelty cannot be established: the detector needs exact counts and this count is a "
            "lower bound"
        ),
    ),
    "new.history_default": (
        "UNKNOWN",
        "novelty cannot be established: the history is incomplete",
    ),
    "new.flood_plain": (
        "UNKNOWN",
        "discovery truncated",
    ),
    "new.below_requests": (
        "HEALTHY",
        (
            "19 request(s) in 5 bucket(s) since first appearance has not reached 20 requests in 2 "
            "buckets"
        ),
    ),
    "new.below_buckets": (
        "HEALTHY",
        (
            "500 request(s) in 1 bucket(s) since first appearance has not reached 20 requests in "
            "2 buckets"
        ),
    ),
    "new.lower_below": (
        "UNKNOWN",
        (
            "3 request(s) in 1 bucket(s) since first appearance has not reached 20 requests in 2 "
            "buckets; the counts are lower bounds"
        ),
    ),
    "new.fired": (
        "ANOMALOUS",
        (
            "GET /login received 30 requests across 3 buckets since it first appeared at "
            "2026-08-03T19:15:00+00:00; in the verified history 2026-07-27T19:25:00+00:00 to "
            "2026-08-03T19:15:00+00:00 it was never requested 3 or more times in one bucket"
        ),
    ),
    "new.lower_fired": (
        "ANOMALOUS",
        (
            "GET /login received at least 30 requests across 3 buckets since it first appeared at "
            "2026-08-03T19:15:00+00:00; in the verified history 2026-07-27T19:25:00+00:00 to "
            "2026-08-03T19:15:00+00:00 it was never requested 3 or more times in one bucket"
        ),
    ),
    "new.fired_no_entity": (
        "ANOMALOUS",
        (
            "this endpoint received 30 requests across 3 buckets since it first appeared at "
            "2026-08-03T19:15:00+00:00; in the verified history 2026-07-27T19:25:00+00:00 to "
            "2026-08-03T19:15:00+00:00 it was never requested 3 or more times in one bucket"
        ),
    ),
    "new.fired_scope_inc": (
        "ANOMALOUS",
        (
            "GET /login received at least 30 requests across 3 buckets since it first appeared at "
            "2026-08-03T19:15:00+00:00; in the verified history 2026-07-27T19:25:00+00:00 to "
            "2026-08-03T19:15:00+00:00 it was never requested 3 or more times in one bucket"
        ),
    ),
}


EXPECTED_CODE: dict[str, str | None] = {
    "rate.partial": "waf.rate.bucket_not_whole",
    "rate.missing": "waf.rate.bucket_not_whole",
    "rate.scope_unstated": "waf.scope.unstated",
    "rate.scope_needs_exact": "waf.scope.needs_exact",
    "rate.scope_plain_text": None,
    "rate.coverage_low": "waf.rate.coverage_low",
    "rate.incompatible_bucket": None,
    "rate.incompatible_baseline": None,
    "rate.incompatible_mismatch": None,
    "rate.no_baseline": "waf.rate.no_baseline",
    "rate.baseline_samples": "waf.rate.baseline_samples",
    "rate.below_min_volume": "waf.rate.below_min_volume",
    "rate.ratio_not_reached": "waf.rate.ratio_not_reached",
    "rate.delta_below_min": "waf.rate.delta_below_min",
    "rate.z_not_reached": "waf.rate.z_not_reached",
    "rate.mad_within_band": "waf.rate.mad_within_band",
    "rate.fired": "waf.rate.fired",
    "rate.fired_mad_outside_band": "waf.rate.fired",
    "rate.fired_expected_zero": "waf.rate.fired",
    "rate.lower_fired": "waf.rate.fired_lower",
    "rate.lower_fired_plain_scope": "waf.rate.fired_lower",
    "rate.lower_fired_no_scope": "waf.rate.fired_lower",
    "rate.lower_fired_mad_outside_band": "waf.rate.fired_lower",
    "rate.lower_below_min_volume": "waf.rate.not_proven_lower_bound",
    "rate.lower_ratio_not_reached": "waf.rate.not_proven_lower_bound",
    "rate.lower_delta_below_min": "waf.rate.not_proven_lower_bound",
    "rate.lower_z_not_reached": "waf.rate.not_proven_lower_bound",
    "rate.lower_mad_within_band": "waf.rate.not_proven_lower_bound",
    "ratio.block.not_collected": "waf.guard.breakdown_not_collected",
    "ratio.block.failed": "waf.guard.breakdown_failed",
    "ratio.block.scope_none_coded": "waf.scope.needs_exact",
    "ratio.block.scope_none_plain": None,
    "ratio.block.scope_none_no_reason": "waf.scope.inputs_unsupported",
    "ratio.block.source_coverage_low": "waf.guard.source_coverage_low",
    "ratio.block.field_coverage_low": "waf.ratio.field_coverage_low",
    "ratio.block.incompatible": None,
    "ratio.block.no_baseline": "waf.guard.no_baseline",
    "ratio.block.baseline_samples": "waf.guard.baseline_samples",
    "ratio.block.no_denominator": "waf.guard.baseline_no_denominator",
    "ratio.block.numerator_below_min": "waf.ratio.numerator_below_min",
    "ratio.block.denominator_below_min": "waf.ratio.denominator_below_min",
    "ratio.block.guard_rise": "waf.ratio.rise_below_min",
    "ratio.block.lower_guard_rise": "waf.guard.not_proven_lower_bound",
    "ratio.block.guard_multiple": "waf.ratio.multiple_below_min",
    "ratio.block.lower_guard_multiple": "waf.guard.not_proven_lower_bound",
    "ratio.block.guard_robust_z": "waf.basis.z_not_reached",
    "ratio.block.lower_guard_robust_z": "waf.guard.not_proven_lower_bound",
    "ratio.block.guard_mad_zero_within_p95": "waf.basis.mad_within_p95",
    "ratio.block.lower_guard_mad_zero_within_p95": "waf.guard.not_proven_lower_bound",
    "ratio.block.lower_guard_no_scope": "waf.guard.not_proven_lower_bound",
    "ratio.block.lower_guard_plain_scope": "waf.guard.not_proven_lower_bound",
    "ratio.block.fired": "waf.ratio.fired",
    "ratio.block.fired_mad_above_p95": "waf.ratio.fired",
    "ratio.block.fired_no_multiple": "waf.ratio.fired_no_multiple",
    "ratio.block.lower_fired": "waf.ratio.fired_lower",
    "ratio.block.lower_fired_no_multiple": "waf.ratio.fired_lower_no_multiple",
    "ratio.block.fired_no_entity": "waf.ratio.fired",
    "ratio.server.not_collected": "waf.guard.breakdown_not_collected",
    "ratio.server.failed": "waf.guard.breakdown_failed",
    "ratio.server.scope_none_coded": "waf.scope.needs_exact",
    "ratio.server.scope_none_plain": None,
    "ratio.server.scope_none_no_reason": "waf.scope.inputs_unsupported",
    "ratio.server.source_coverage_low": "waf.guard.source_coverage_low",
    "ratio.server.field_coverage_low": "waf.ratio.field_coverage_low",
    "ratio.server.incompatible": None,
    "ratio.server.no_baseline": "waf.guard.no_baseline",
    "ratio.server.baseline_samples": "waf.guard.baseline_samples",
    "ratio.server.no_denominator": "waf.guard.baseline_no_denominator",
    "ratio.server.numerator_below_min": "waf.ratio.numerator_below_min",
    "ratio.server.denominator_below_min": "waf.ratio.denominator_below_min",
    "ratio.server.guard_rise": "waf.ratio.rise_below_min",
    "ratio.server.lower_guard_rise": "waf.guard.not_proven_lower_bound",
    "ratio.server.guard_multiple": "waf.ratio.multiple_below_min",
    "ratio.server.lower_guard_multiple": "waf.guard.not_proven_lower_bound",
    "ratio.server.guard_robust_z": "waf.basis.z_not_reached",
    "ratio.server.lower_guard_robust_z": "waf.guard.not_proven_lower_bound",
    "ratio.server.guard_mad_zero_within_p95": "waf.basis.mad_within_p95",
    "ratio.server.lower_guard_mad_zero_within_p95": "waf.guard.not_proven_lower_bound",
    "ratio.server.lower_guard_no_scope": "waf.guard.not_proven_lower_bound",
    "ratio.server.lower_guard_plain_scope": "waf.guard.not_proven_lower_bound",
    "ratio.server.fired": "waf.ratio.fired",
    "ratio.server.fired_mad_above_p95": "waf.ratio.fired",
    "ratio.server.fired_no_multiple": "waf.ratio.fired_no_multiple",
    "ratio.server.lower_fired": "waf.ratio.fired_lower",
    "ratio.server.lower_fired_no_multiple": "waf.ratio.fired_lower_no_multiple",
    "ratio.server.fired_no_entity": "waf.ratio.fired",
    "ratio.client.not_collected": "waf.guard.breakdown_not_collected",
    "ratio.client.failed": "waf.guard.breakdown_failed",
    "ratio.client.scope_none_coded": "waf.scope.needs_exact",
    "ratio.client.scope_none_plain": None,
    "ratio.client.scope_none_no_reason": "waf.scope.inputs_unsupported",
    "ratio.client.source_coverage_low": "waf.guard.source_coverage_low",
    "ratio.client.field_coverage_low": "waf.ratio.field_coverage_low",
    "ratio.client.incompatible": None,
    "ratio.client.no_baseline": "waf.guard.no_baseline",
    "ratio.client.baseline_samples": "waf.guard.baseline_samples",
    "ratio.client.no_denominator": "waf.guard.baseline_no_denominator",
    "ratio.client.numerator_below_min": "waf.ratio.numerator_below_min",
    "ratio.client.denominator_below_min": "waf.ratio.denominator_below_min",
    "ratio.client.guard_rise": "waf.ratio.rise_below_min",
    "ratio.client.lower_guard_rise": "waf.guard.not_proven_lower_bound",
    "ratio.client.guard_multiple": "waf.ratio.multiple_below_min",
    "ratio.client.lower_guard_multiple": "waf.guard.not_proven_lower_bound",
    "ratio.client.guard_robust_z": "waf.basis.z_not_reached",
    "ratio.client.lower_guard_robust_z": "waf.guard.not_proven_lower_bound",
    "ratio.client.guard_mad_zero_within_p95": "waf.basis.mad_within_p95",
    "ratio.client.lower_guard_mad_zero_within_p95": "waf.guard.not_proven_lower_bound",
    "ratio.client.lower_guard_no_scope": "waf.guard.not_proven_lower_bound",
    "ratio.client.lower_guard_plain_scope": "waf.guard.not_proven_lower_bound",
    "ratio.client.fired": "waf.ratio.fired",
    "ratio.client.fired_mad_above_p95": "waf.ratio.fired",
    "ratio.client.fired_no_multiple": "waf.ratio.fired_no_multiple",
    "ratio.client.lower_fired": "waf.ratio.fired_lower",
    "ratio.client.lower_fired_no_multiple": "waf.ratio.fired_lower_no_multiple",
    "ratio.client.fired_no_entity": "waf.ratio.fired",
    "conc.not_collected": "waf.guard.breakdown_not_collected",
    "conc.failed": "waf.guard.breakdown_failed",
    "conc.scope_inc_coded": "waf.scope.lower_bound_increase",
    "conc.scope_plain": None,
    "conc.scope_no_reason": "waf.scope.composition_exact",
    "conc.source_coverage_low": "waf.guard.source_coverage_low",
    "conc.attributed_low": "waf.concentration.attributed_low",
    "conc.no_baseline": "waf.guard.no_baseline",
    "conc.baseline_samples": "waf.guard.baseline_samples",
    "conc.no_denominator": "waf.guard.baseline_no_denominator",
    "conc.top_below_min": "waf.concentration.top_below_min",
    "conc.endpoint_below_min": "waf.concentration.endpoint_below_min",
    "conc.rise_below_min": "waf.concentration.rise_below_min",
    "conc.z_not_reached": "waf.basis.z_not_reached",
    "conc.mad_within_p95": "waf.basis.mad_within_p95",
    "conc.fired_no_volume_baseline": "waf.concentration.fired",
    "conc.fired_volume_baseline": "waf.concentration.fired",
    "conc.fired_mad_above_p95": "waf.concentration.fired",
    "conc.fired_no_entity": "waf.concentration.fired",
    "new.no_facts": "waf.novelty.not_established",
    "new.outside_window": "waf.novelty.established_outside_window",
    "new.hand_off": "waf.novelty.established_hand_off",
    "new.history_reason": "waf.novelty.cannot_establish",
    "new.history_scope_reason": "waf.novelty.cannot_establish",
    "new.history_scope_reason_coded": "waf.novelty.cannot_establish",
    "new.history_default": "waf.novelty.cannot_establish",
    "new.flood_plain": None,
    "new.below_requests": "waf.novelty.below_materiality",
    "new.below_buckets": "waf.novelty.below_materiality",
    "new.lower_below": "waf.novelty.below_materiality_lower",
    "new.fired": "waf.novelty.fired",
    "new.lower_fired": "waf.novelty.fired_lower",
    "new.fired_no_entity": "waf.novelty.fired",
    "new.fired_scope_inc": "waf.novelty.fired_lower",
}


#: The scenarios whose sentence is a caller's own wording (or a plain-text cause): no code.
UNCODED = {name for name, code in EXPECTED_CODE.items() if code is None}


def _codes_of(message: MessageRef) -> set[str]:
    seen = {message.code}
    for value in message.params.values():
        if isinstance(value, MessageRef):
            seen |= _codes_of(value)
    return seen


def _all_registry_codes() -> set[str]:
    return {spec.code for spec in REGISTRY if spec.code.startswith("waf.")}


# ------------------------------------------------------------------ the scenario table
def test_the_tables_cover_every_scenario() -> None:
    assert set(EXPECTED) == set(SCENARIOS) == set(EXPECTED_CODE)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_sentence_keeps_its_english_and_carries_its_code(name: str) -> None:
    evidence = SCENARIOS[name]().evidence
    signal, reason = EXPECTED[name]
    assert (evidence.signal.value, evidence.reason) == (signal, reason)
    assert evidence.insufficient_data is (signal == "UNKNOWN")
    wire = evidence.to_dict()
    if EXPECTED_CODE[name] is None:
        assert evidence.reason_message is None
        assert "reason_code" not in wire and "reason_params" not in wire
        return
    message = evidence.reason_message
    assert message is not None and message.code == EXPECTED_CODE[name]
    assert render(message) == evidence.reason
    assert wire["reason_code"] == message.code
    assert wire["reason_params"] == message.to_wire()["params"]
    # What the row stores is JSON: no reference object and no datetime survives in it.
    assert json.loads(json.dumps(wire))["reason_code"] == message.code


def test_every_scenario_that_is_coded_uses_the_waf_family() -> None:
    for name, code in EXPECTED_CODE.items():
        if code is not None:
            assert code.startswith("waf."), name


def test_every_waf_code_is_reached() -> None:
    """A code nothing emits is dead copy: each scenario, history check or caveat reaches one."""
    reached: set[str] = set()
    for name in SCENARIOS:
        message = SCENARIOS[name]().evidence.reason_message
        if message is not None:
            reached |= _codes_of(message)
    reached |= {facts.message.code for _, facts, _ in _history_cases() if facts.message}
    reached |= set(CAVEAT_ENGLISH)
    reached |= {"waf.suppression.maintenance_window", *_SCOPE_CODES}
    reached |= {
        "waf.semantics.bucket_unidentified",
        "waf.semantics.baseline_unidentified",
        "waf.semantics.mismatch",
        "waf.novelty.flood_discovery",
        "waf.novelty.flood_admission",
        "waf.history_check.not_loaded",
        "waf.history_check.appearance_never_collected",
        "waf.history_check.appearance_no_profile",
        "waf.history_check.appearance_normalizer",
        "waf.noun.response_code_requests",
        "waf.noun.http_5xx",
        "waf.noun.http_4xx",
    }
    assert _all_registry_codes() - reached == set()


# ------------------------------------------------------------- the shape of the nesting
def test_the_lower_bound_wrap_nests_the_wrapped_sentence() -> None:
    evidence = SCENARIOS["rate.lower_z_not_reached"]().evidence
    wrap = evidence.reason_message
    assert wrap is not None and wrap.code == "waf.rate.not_proven_lower_bound"
    inner = wrap.params["reason"]
    assert isinstance(inner, MessageRef) and inner.code == "waf.rate.z_not_reached"
    wire = evidence.to_dict()
    assert wire["reason_params"]["reason"]["code"] == "waf.rate.z_not_reached"
    assert wire["reason_params"]["observed"] == 250.0


def test_the_ratio_wrap_carries_its_scope_as_a_nested_code() -> None:
    evidence = SCENARIOS["ratio.block.lower_guard_rise"]().evidence
    params = evidence.to_dict()["reason_params"]
    assert params["reason"]["code"] == "waf.ratio.rise_below_min"
    assert params["scope"] == {"code": "waf.scope.lower_bound_increase", "params": {}}


def test_a_plain_text_scope_travels_as_text_inside_the_wrap() -> None:
    evidence = SCENARIOS["ratio.block.lower_guard_plain_scope"]().evidence
    assert evidence.to_dict()["reason_params"]["scope"] == "odd"


def test_the_noun_phrases_are_references_in_the_firing_sentence() -> None:
    params = SCENARIOS["ratio.server.fired"]().evidence.to_dict()["reason_params"]
    assert params["numerator"] == {"code": "waf.noun.http_5xx", "params": {}}
    assert params["denominator"] == {"code": "waf.noun.response_code_requests", "params": {}}
    assert params["subject"] == {
        "code": "waf.subject.named",
        "params": {"method": "GET", "endpoint": "/login"},
    }
    assert params["share"] == {"kind": "percent", "ratio": pytest.approx(0.4), "precision": 1}
    assert params["rise"]["kind"] == "points"


def test_the_firing_sentence_picks_one_variant_per_condition() -> None:
    codes = {
        name: SCENARIOS[name]().evidence.reason_message.code  # type: ignore[union-attr]
        for name in (
            "ratio.block.fired",
            "ratio.block.fired_no_multiple",
            "ratio.block.lower_fired",
            "ratio.block.lower_fired_no_multiple",
        )
    }
    assert codes == {
        "ratio.block.fired": "waf.ratio.fired",
        "ratio.block.fired_no_multiple": "waf.ratio.fired_no_multiple",
        "ratio.block.lower_fired": "waf.ratio.fired_lower",
        "ratio.block.lower_fired_no_multiple": "waf.ratio.fired_lower_no_multiple",
    }


def test_the_concentration_volume_clause_has_two_variants() -> None:
    with_baseline = SCENARIOS["conc.fired_volume_baseline"]().evidence.to_dict()
    without = SCENARIOS["conc.fired_no_volume_baseline"]().evidence.to_dict()
    assert with_baseline["reason_params"]["volume"] == {
        "code": "waf.concentration.volume_with_baseline",
        "params": {"requests": 100, "expected": 120.5},
    }
    assert without["reason_params"]["volume"] == {
        "code": "waf.concentration.volume_no_baseline",
        "params": {"requests": 100},
    }


def test_the_new_endpoint_sentence_carries_three_timestamps_as_iso_text() -> None:
    params = SCENARIOS["new.fired"]().evidence.to_dict()["reason_params"]
    assert params["first_seen"] == "2026-08-03T19:15:00+00:00"
    assert params["history_start"] == "2026-07-27T19:25:00+00:00"
    assert params["history_end"] == "2026-08-03T19:15:00+00:00"


def test_the_healthy_sentence_of_an_established_endpoint_is_coded() -> None:
    evidence = SCENARIOS["new.hand_off"]().evidence
    assert evidence.signal is DetectorSignal.HEALTHY
    assert evidence.to_dict()["reason_code"] == "waf.novelty.established_hand_off"


def test_a_healthy_guard_exit_is_coded_too() -> None:
    evidence = SCENARIOS["ratio.block.guard_rise"]().evidence
    assert evidence.signal is DetectorSignal.HEALTHY
    assert evidence.to_dict()["reason_code"] == "waf.ratio.rise_below_min"


# --------------------------------------------------------------------- the scope map
def test_every_scope_decision_sentence_has_a_code() -> None:
    """`scope_reason_ref` maps the English of `verdict_scope`; a reworded decision fails here."""
    reasons = set()
    for requirement in WafMeasurementRequirement:
        for measurement in (None, WafMeasurement.EXACT, WafMeasurement.LOWER_BOUND):
            decision = verdict_scope(requirement, measurement)
            if decision.reason is None:
                assert decision.scope is WafVerdictScope.FULL
                continue
            reasons.add(decision.reason)
            ref = scope_reason_ref(decision.reason)
            assert isinstance(ref, MessageRef), decision.reason
            assert render(ref) == decision.reason
    assert reasons == {SCOPE_UNSTATED, SCOPE_INCREASE, SCOPE_NEEDS_EXACT}


def test_an_unknown_scope_reason_stays_text_and_none_stays_none() -> None:
    assert scope_reason_ref("something else") == "something else"
    assert scope_reason_ref(None) is None
    assert scope_reason_ref("") == ""


# ---------------------------------------------------------- the metric-semantics reasons
@pytest.mark.parametrize(
    ("bucket", "baseline", "code"),
    [
        (None, None, "waf.semantics.bucket_unidentified"),
        (None, "request_count/v1", "waf.semantics.bucket_unidentified"),
        ("request_count/v1", None, "waf.semantics.baseline_unidentified"),
        ("request_count/v2", "request_count/v1", "waf.semantics.mismatch"),
        ("request_count/v1", "request_count/v1", None),
    ],
)
def test_the_semantics_reasons_render_as_the_engine_text(
    bucket: str | None, baseline: str | None, code: str | None
) -> None:
    message = semantics_message(bucket, baseline)
    text = semantics_incompatibility(bucket, baseline)
    if code is None:
        assert message is None and text is None
        return
    assert message is not None and message.code == code
    assert render(message) == text


def test_a_detector_carries_the_semantics_code_when_the_engine_supplies_it() -> None:
    text = semantics_incompatibility("request_count/v2", "request_count/v1")
    message = semantics_message("request_count/v2", "request_count/v1")
    rate = detect_request_rate_spike(
        td.ctx(baseline_incompatibility=text, baseline_incompatibility_message=message)
    )
    ratio = tb.run(
        "block",
        tb.ratio_ctx(
            "block", 40, baseline_incompatibility=text, baseline_incompatibility_message=message
        ),
    )
    for verdict in (rate, ratio):
        assert verdict.evidence.reason == text
        assert verdict.evidence.to_dict()["reason_code"] == "waf.semantics.mismatch"
        assert verdict.evidence.to_dict()["reason_params"] == {
            "baseline": "request_count/v1",
            "bucket": "request_count/v2",
        }


def test_a_message_that_disagrees_with_its_text_is_dropped() -> None:
    """Fail closed: a code is only ever sent beside the sentence it renders."""
    message = semantics_message("request_count/v2", "request_count/v1")
    verdict = detect_request_rate_spike(
        td.ctx(baseline_incompatibility="some other text", baseline_incompatibility_message=message)
    )
    assert verdict.evidence.reason == "some other text"
    assert verdict.evidence.reason_message is None


# ---------------------------------------------------------------- history verification
def _check(name: str, facts: HistoryFacts, english: str) -> WafHistoryFacts:
    assert isinstance(facts, WafHistoryFacts), name
    assert facts.complete is False
    assert facts.reason == english
    assert facts.message is not None and render(facts.message) == english
    assert facts.message.code == f"waf.history_check.{name}"
    return facts


def _history_cases() -> list[tuple[str, WafHistoryFacts, str]]:
    """Each failure of `verify_endpoint_history`: its name, the facts and the old English."""
    cases: list[tuple[str, WafHistoryFacts, str]] = []

    def check(name: str, facts: HistoryFacts, english: str) -> None:
        cases.append((name, _check(name, facts, english), english))

    count = tn.COUNT
    stamp = tn.START + timedelta(seconds=tn.WIDTH * 5)
    iso = stamp.isoformat()
    check(
        "not_divisible",
        tn.verify(tn.history(), bucket_seconds=7),
        "the history window is not divisible by the collection bucket width",
    )
    check(
        "never_collected",
        tn.verify(tn.history()[3:]),
        f"3 of {count} history buckets were never collected",
    )
    check(
        "record_count",
        tn.verify([*tn.history(), tn.bucket(0)]),
        f"the history contains {count + 1} bucket records for {count} expected buckets",
    )
    rows = tn.history()
    rows[5] = replace(rows[5], bucket_seconds=60)
    check(
        "bucket_width",
        tn.verify(rows),
        f"history bucket {iso} used a different collection bucket width",
    )
    rows = tn.history()
    rows[5] = replace(rows[5], groups_truncated=True)
    check(
        "discovery_truncated",
        tn.verify(rows),
        f"history bucket {iso} had truncated endpoint discovery",
    )
    rows = tn.history()
    rows[5] = replace(rows[5], admission_truncated=True)
    check(
        "admission_truncated",
        tn.verify(rows),
        f"history bucket {iso} had truncated endpoint admission",
    )
    rows = tn.history()
    rows[5] = replace(rows[5], profile_version=2)
    check(
        "profile_version",
        tn.verify(rows),
        f"history bucket {iso} used profile version 2, not first-appearance version 3",
    )
    rows = tn.history()
    rows[5] = replace(rows[5], metric_semantics="x;normalizer/v99;y")
    check(
        "normalizer_version",
        tn.verify(rows),
        f"history bucket {iso} is incompatible with endpoint normalizer version "
        f"{tn.ENDPOINT_NORMALIZER_VERSION}",
    )
    return cases


def test_history_verification_codes_every_failure() -> None:
    cases = _history_cases()
    assert [name for name, _, _ in cases] == [
        "not_divisible",
        "never_collected",
        "record_count",
        "bucket_width",
        "discovery_truncated",
        "admission_truncated",
        "profile_version",
        "normalizer_version",
    ]


def test_a_history_bucket_with_no_profile_version_is_coded_not_crashed() -> None:
    rows = tn.history()
    rows[5] = replace(rows[5], profile_version=None)
    facts = tn.verify(rows)
    assert isinstance(facts, WafHistoryFacts) and facts.message is not None
    assert facts.reason is not None
    assert "used profile version None, not first-appearance" in facts.reason
    assert facts.message.params["found"] == "None"


def test_a_complete_history_has_no_reason_and_no_message() -> None:
    facts = tn.verify(tn.history())
    assert facts.complete is True and facts.reason is None
    assert getattr(facts, "message", None) is None


def _engine_history(appearance: BucketFacts | None, **kw: Any):  # type: ignore[no-untyped-def]
    rows = tn.history()
    buckets = rows if appearance is None else [*rows, appearance]
    starts = [b.bucket_start for b in buckets]
    return WafAnomalyEngine._verify_history(
        tn.END,
        kw.pop("normalizer_version", tn.ENDPOINT_NORMALIZER_VERSION),
        timedelta(seconds=tn.WIDTH * tn.COUNT),
        buckets,
        starts,
        {b.bucket_start: b for b in buckets},
    )


def _appearance(**kw: Any) -> BucketFacts:
    return replace(tn.bucket(tn.COUNT), **kw)


def test_the_engine_codes_the_first_appearance_reasons_and_floods() -> None:
    facts, _, flood = _engine_history(None)
    _check(
        "appearance_never_collected", facts, "the first-appearance bucket was never collected"
    )
    assert flood is None
    facts, _, _ = _engine_history(_appearance(profile_version=None))
    _check(
        "appearance_no_profile", facts, "the first-appearance bucket carries no profile version"
    )
    facts, _, _ = _engine_history(_appearance(metric_semantics="x;normalizer/v99;y"))
    _check(
        "appearance_normalizer",
        facts,
        "the first-appearance bucket is incompatible with endpoint normalizer version "
        f"{tn.ENDPOINT_NORMALIZER_VERSION}",
    )
    facts, _, flood = _engine_history(_appearance(groups_truncated=True))
    assert facts.complete is True
    assert flood == MessageRef("waf.novelty.flood_discovery")
    assert render(flood) == "endpoint discovery was truncated in the first-appearance bucket"
    _, _, flood = _engine_history(_appearance(admission_truncated=True))
    assert flood == MessageRef("waf.novelty.flood_admission")
    assert render(flood) == "endpoint admission was truncated in the first-appearance bucket"
    _, _, flood = _engine_history(_appearance())
    assert flood is None


def test_a_history_message_travels_inside_the_cannot_establish_sentence() -> None:
    history = WafHistoryFacts(
        complete=False,
        reason="the first-appearance bucket was never collected",
        message=MessageRef("waf.history_check.appearance_never_collected"),
    )
    evidence = detect_new_endpoint_access(
        tb.new_ctx(tb.novelty(history=history), scope=tb.NONE)
    ).evidence
    assert evidence.reason == (
        "novelty cannot be established: the first-appearance bucket was never collected"
    )
    assert evidence.to_dict()["reason_params"]["cause"] == {
        "code": "waf.history_check.appearance_never_collected",
        "params": {},
    }


def test_a_history_message_that_disagrees_with_its_reason_is_sent_as_text() -> None:
    history = WafHistoryFacts(
        complete=False,
        reason="a different sentence",
        message=MessageRef("waf.history_check.appearance_never_collected"),
    )
    evidence = detect_new_endpoint_access(
        tb.new_ctx(tb.novelty(history=history), scope=tb.NONE)
    ).evidence
    assert evidence.to_dict()["reason_params"]["cause"] == "a different sentence"


def test_a_flood_reference_is_the_verdict_sentence_itself() -> None:
    flood = MessageRef("waf.novelty.flood_admission")
    evidence = detect_new_endpoint_access(
        tb.new_ctx(tb.novelty(appearance_flood=flood))
    ).evidence
    assert evidence.reason == render(flood)
    assert evidence.to_dict()["reason_code"] == "waf.novelty.flood_admission"


# ------------------------------------------------------------------------- the caveats
CAVEAT_ENGLISH = {
    "waf.caveat.block_ratio": (
        "Blocking is judged through the profile's action map. A higher block ratio "
        "describes WAF decisions; it does not establish that an attack succeeded."
    ),
    "waf.caveat.server_error_ratio": (
        "HTTP 5xx responses describe server-side failures. They do not establish that "
        "the traffic was malicious."
    ),
    "waf.caveat.client_error_ratio": (
        "HTTP 4xx responses are response codes, not WAF decisions: a 403 is not by "
        "itself a block, and WAF blocking is reported separately."
    ),
    "waf.caveat.client_concentration": (
        "A dominant source IP describes traffic composition, not intent."
    ),
    "waf.caveat.new_endpoint": (
        "A newly requested endpoint may be a deployment as easily as a probe."
    ),
}
CAVEAT_SCENARIOS = {
    "waf.caveat.block_ratio": "ratio.block.fired",
    "waf.caveat.server_error_ratio": "ratio.server.fired",
    "waf.caveat.client_error_ratio": "ratio.client.fired",
    "waf.caveat.client_concentration": "conc.fired_no_volume_baseline",
    "waf.caveat.new_endpoint": "new.fired",
}


@pytest.mark.parametrize("code", sorted(CAVEAT_ENGLISH))
def test_a_firing_verdict_carries_its_caveat_text_and_code(code: str) -> None:
    extra = SCENARIOS[CAVEAT_SCENARIOS[code]]().evidence.extra
    assert extra["caveat"] == CAVEAT_ENGLISH[code]
    assert extra["caveat_code"] == code
    assert extra["caveat_params"] == {}
    assert render(MessageRef(code)) == extra["caveat"]


def test_the_ratio_definitions_hold_their_phrases_as_text_and_reference() -> None:
    expected = {
        "block": ("blocked requests", "requests carrying a WAF action"),
        "server": ("HTTP 5xx responses", "requests carrying a response code"),
        "client": ("HTTP 4xx responses", "requests carrying a response code"),
    }
    definitions = {"block": BLOCK_RATIO, "server": SERVER_ERROR_RATIO, "client": CLIENT_ERROR_RATIO}
    for kind, definition in definitions.items():
        numerator, denominator = expected[kind]
        assert definition.numerator_noun == numerator
        assert definition.denominator_noun == denominator
        assert definition.numerator_ref is not None and definition.denominator_ref is not None
        assert render(definition.numerator_ref) == numerator
        assert render(definition.denominator_ref) == denominator
        assert definition.caveat_ref is not None
        assert render(definition.caveat_ref) == definition.caveat


def test_a_definition_without_references_falls_back_to_its_text() -> None:
    bare = replace(BLOCK_RATIO, numerator_ref=None, denominator_ref=None, caveat_ref=None)
    assert bare.numerator_phrase == "blocked requests"
    assert bare.denominator_phrase == "requests carrying a WAF action"
    assert split_reason(bare.numerator_phrase) == ("blocked requests", None)


# -------------------------------------------------------- the contributor block stays uncoded
def test_the_contributor_block_is_not_coded_and_not_forwarded() -> None:
    """Its sentences never reach the API: the projection reads named keys only."""
    obs = tb.observation(
        counts=tb.BreakdownCounts(client_events=100, top_clients=()),
        breakdowns=tb.BreakdownStatus(client=tb.EXACT),
    )
    block = contributors(obs, min_top_requests=20)
    assert block["note"] == "no source IP reached 20 requests, so none was recorded"
    assert not any(key.endswith(("_code", "_params")) for key in block)
    evidence = SCENARIOS["ratio.block.fired"]().evidence.to_dict()
    assert "contributors" in evidence["extra"]
    projected = waf_detection_detail(evidence)
    assert projected is not None
    assert "contributors" not in projected.model_dump()


# ------------------------------------------------------------------- the API projection
def test_the_detection_block_serves_reason_and_caveat_codes() -> None:
    details = SCENARIOS["ratio.block.lower_fired"]().evidence.to_dict()
    projected = waf_detection_detail(details)
    assert projected is not None
    assert projected.reason == details["reason"]
    assert projected.reason_code == "waf.ratio.fired_lower"
    assert projected.reason_params is not None
    basis = projected.reason_params["basis"]
    assert isinstance(basis, MessageValue) and basis.code == "waf.basis.z"
    assert projected.caveat == CAVEAT_ENGLISH["waf.caveat.block_ratio"]
    assert projected.caveat_code == "waf.caveat.block_ratio"
    assert projected.caveat_params == {}


def test_a_row_stored_before_codes_serves_its_text_and_no_code() -> None:
    details = SCENARIOS["ratio.block.fired"]().evidence.to_dict()
    for key in ("reason_code", "reason_params"):
        details.pop(key)
    for key in ("caveat_code", "caveat_params"):
        details["extra"].pop(key)
    projected = waf_detection_detail(details)
    assert projected is not None
    assert projected.reason == details["reason"] and projected.reason_code is None
    assert projected.reason_params is None
    assert projected.caveat == details["extra"]["caveat"] and projected.caveat_code is None
    assert projected.caveat_params is None


@pytest.mark.parametrize(
    "broken",
    [
        {"reason_code": "waf.rate.fired", "reason_params": "not a mapping"},
        {"reason_code": 7, "reason_params": {}},
        {"reason_code": "waf.rate.fired", "reason_params": {"x": object()}},
    ],
)
def test_a_malformed_stored_reason_is_dropped_not_served(broken: dict[str, Any]) -> None:
    details = {**SCENARIOS["rate.fired"]().evidence.to_dict(), **broken}
    projected = waf_detection_detail(details)
    assert projected is not None
    assert projected.reason_code is None and projected.reason_params is None
    assert projected.reason == details["reason"]


def test_the_request_rate_reason_is_served_with_its_nested_basis() -> None:
    projected = waf_detection_detail(SCENARIOS["rate.lower_fired"]().evidence.to_dict())
    assert projected is not None and projected.reason_code == "waf.rate.fired_lower"
    params = projected.reason_params
    assert params is not None
    scope = params["scope"]
    assert isinstance(scope, MessageValue)
    assert (scope.code, scope.params) == ("waf.scope.lower_bound_increase", {})
    assert params["observed"] == 500.0


# ------------------------------------------------------------- engine: established history
def test_the_engine_hands_an_established_endpoint_a_coded_history_fact() -> None:
    """No history is loaded for an established endpoint, and the fact says so in a code."""
    import asyncio
    import uuid

    from app.models.waf import WafEndpoint
    from app.waf.behavior import WafBehaviorThresholds
    from app.waf.engine import _Subject

    first_seen = tb.START - timedelta(days=3)
    endpoint = WafEndpoint(
        id=uuid.uuid4(),
        log_source_id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        normalized_endpoint="/old",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=first_seen,
    )
    subject = _Subject(endpoint=endpoint, observation=tb.observation())
    engine = WafAnomalyEngine(None, settings=None)  # type: ignore[arg-type]
    facts = asyncio.run(engine._load_novelty([subject], WafBehaviorThresholds()))[endpoint.id]
    history = facts.history
    assert isinstance(history, WafHistoryFacts) and history.complete is False
    assert history.reason == "history was not loaded for an established endpoint"
    assert history.message == MessageRef("waf.history_check.not_loaded")
    assert facts.appearance_flood is None


# ------------------------------------------------------------------ engine: suppression
class _Session:
    """What `_apply_transition` asks of a session: it records what was added."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)


def _suppress(reason: str | None, pair: dict[str, Any] | None) -> WafAnomaly:
    import uuid

    from app.anomaly.lifecycle import LifecycleDecision
    from app.models.enums import AnomalyState
    from app.models.waf import WafAnomaly, WafDetectorState, WafEndpoint
    from app.waf.engine import WafEvaluationReport

    endpoint = WafEndpoint(id=uuid.uuid4(), normalized_endpoint="/x", http_method="GET")
    anomaly = WafAnomaly(
        id=uuid.uuid4(),
        waf_endpoint_id=endpoint.id,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        state=AnomalyState.OPEN,
        suppressed=False,
        suppression_reason=reason,
        suppression_reason_i18n=pair,
    )
    state = WafDetectorState(
        waf_endpoint_id=endpoint.id,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        state=AnomalyState.OPEN,
        active_anomaly_id=anomaly.id,
    )
    evidence = SCENARIOS["rate.fired"]().evidence
    engine = WafAnomalyEngine(_Session(), settings=None)  # type: ignore[arg-type]
    engine._apply_transition(
        endpoint,
        tb.observation(),
        WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        evidence,
        state,
        LifecycleDecision(state=AnomalyState.SUPPRESSED, reason="suppressed"),
        {anomaly.id: anomaly},
        WafEvaluationReport(waf_endpoint_id=endpoint.id, bucket_start=None),
        [],
    )
    return anomaly


def test_a_maintenance_suppression_stores_its_reason_with_its_code() -> None:
    anomaly = _suppress(None, None)
    assert anomaly.suppressed is True
    assert anomaly.suppression_reason == "maintenance window"
    assert anomaly.suppression_reason_i18n == {
        "code": "waf.suppression.maintenance_window",
        "params": {},
    }
    assert anomaly.suppression_reason_code == "waf.suppression.maintenance_window"
    assert anomaly.suppression_reason_params == {}


def test_an_existing_suppression_reason_and_its_code_are_left_alone() -> None:
    anomaly = _suppress("silenced by an operator", None)
    assert anomaly.suppression_reason == "silenced by an operator"
    assert anomaly.suppression_reason_i18n is None
    pair = {"code": "waf.suppression.maintenance_window", "params": {}}
    kept = _suppress("maintenance window", pair)
    assert kept.suppression_reason == "maintenance window"
    assert kept.suppression_reason_i18n == pair


def test_the_maintenance_sentence_is_the_old_english() -> None:
    assert render(MessageRef("waf.suppression.maintenance_window")) == "maintenance window"
