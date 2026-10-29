"""MSG-6b: the IPS anomaly sentences carry message codes and keep their English.

`EXPECTED` is the English the IPS detectors wrote before they had codes, captured from that
code (the base commit) for every scenario of `ips_message_scenarios.py`; a reworded template, a
changed number format or a lost code fails here. The remaining sections pin what surrounds the
sentences: the scope map, the history checks, the caveats and the projection the API serves.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.anomaly.capabilities import IPS_DETECTORS
from app.i18n.messages import REGISTRY, MessageRef, render
from app.ips.behavior import appearance_flood, appearance_flood_ref
from app.ips.detector import ROLE_CODES, IpsVerdict, ips_scope_ref, state_ref
from app.ips.engine import _ANOMALY_COLUMNS
from app.models.enums import DetectorSignal
from app.schemas.anomaly import ips_detection_detail
from app.waf.inputs import HistoryFacts, WafHistoryFacts
from tests.unit import test_ips_detectors as ti
from tests.unit.ips_message_scenarios import LAST_HISTORY, RAW, SCENARIOS

EXPECTED: dict[str, tuple[str, str]] = {
    "spike.destination_port.fired": (
        "ANOMALOUS",
        (
            "100 events for destination port 443/tcp is above the baseline median 10.0"
            " (10.00x expected, +90 events/bucket); robust z=30.35 (threshold +3.5)"
        ),
    ),
    "spike.destination_port.lower_fired": (
        "ANOMALOUS",
        (
            "at least 100 events (a lower bound: volume: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot) for destination port"
            " 443/tcp is above the baseline median 10.0 (10.00x expected, +90"
            " events/bucket); robust z=30.35 (threshold +3.5)"
        ),
    ),
    "spike.destination_ip.fired": (
        "ANOMALOUS",
        (
            "100 events for destination IP 10.10.20.15 is above the baseline median 10.0"
            " (10.00x expected, +90 events/bucket); robust z=30.35 (threshold +3.5)"
        ),
    ),
    "spike.destination_ip.lower_fired": (
        "ANOMALOUS",
        (
            "at least 100 events (a lower bound: volume: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot) for destination IP"
            " 10.10.20.15 is above the baseline median 10.0 (10.00x expected, +90"
            " events/bucket); robust z=30.35 (threshold +3.5)"
        ),
    ),
    "spike.signature.fired": (
        "ANOMALOUS",
        (
            "100 events for signature Example signature is above the baseline median 10.0"
            " (10.00x expected, +90 events/bucket); robust z=30.35 (threshold +3.5)"
        ),
    ),
    "spike.signature.lower_fired": (
        "ANOMALOUS",
        (
            "at least 100 events (a lower bound: volume: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot) for signature Example"
            " signature is above the baseline median 10.0 (10.00x expected, +90"
            " events/bucket); robust z=30.35 (threshold +3.5)"
        ),
    ),
    "spike.fired_big": (
        "ANOMALOUS",
        (
            "12,345 events for destination port 443/tcp is above the baseline median"
            " 1000.0 (12.35x expected, +11,345 events/bucket); robust z=38.26 (threshold"
            " +3.5)"
        ),
    ),
    "spike.fired_mad_above_p95": (
        "ANOMALOUS",
        (
            "100 events for destination port 443/tcp is above the baseline median 10.0"
            " (10.00x expected, +90 events/bucket); robust score unavailable (MAD=0); 100"
            " is above the historical p95 15"
        ),
    ),
    "spike.fired_expected_zero": (
        "ANOMALOUS",
        (
            "100 events for destination port 443/tcp is above the baseline median 0.0"
            " (expected none, +100 events/bucket); robust score unavailable (MAD=0); 100"
            " is above the historical p95 10"
        ),
    ),
    "spike.lower_fired_big": (
        "ANOMALOUS",
        (
            "at least 12,345 events (a lower bound: volume: the count is a lower bound:"
            " an increase can be proven, normal behaviour cannot) for destination port"
            " 443/tcp is above the baseline median 1000.0 (12.35x expected, +11,345"
            " events/bucket); robust z=38.26 (threshold +3.5)"
        ),
    ),
    "spike.lower_fired_mad_above_p95": (
        "ANOMALOUS",
        (
            "at least 100 events (a lower bound: volume: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot) for destination port"
            " 443/tcp is above the baseline median 10.0 (10.00x expected, +90"
            " events/bucket); robust score unavailable (MAD=0); 100 is above the"
            " historical p95 15"
        ),
    ),
    "spike.lower_fired_expected_zero": (
        "ANOMALOUS",
        (
            "at least 100 events (a lower bound: volume: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot) for destination port"
            " 443/tcp is above the baseline median 0.0 (expected none, +100"
            " events/bucket); robust score unavailable (MAD=0); 100 is above the"
            " historical p95 10"
        ),
    ),
    "spike.below_min": (
        "HEALTHY",
        "15 events/bucket is below the 20 minimum for a defensible verdict",
    ),
    "spike.ratio_not_reached": (
        "HEALTHY",
        "observed/expected ratio 1.25 has not reached 2.00",
    ),
    "spike.delta_below_min": (
        "HEALTHY",
        "increase of 15 events/bucket is below the 50 minimum absolute delta",
    ),
    "spike.z_not_reached": (
        "HEALTHY",
        "robust z=0.81 has not reached +3.5",
    ),
    "spike.mad_within_p95": (
        "HEALTHY",
        "robust score unavailable (MAD=0) and 70 is within the historical p95 1,000",
    ),
    "spike.below_min_big": (
        "HEALTHY",
        "15 events/bucket is below the 20 minimum for a defensible verdict",
    ),
    "spike.lower_below_min": (
        "UNKNOWN",
        (
            "15 events/bucket is below the 20 minimum for a defensible verdict; the count"
            " is a lower bound (volume: the count is a lower bound: an increase can be"
            " proven, normal behaviour cannot), so a normal verdict cannot be concluded"
            " from it"
        ),
    ),
    "spike.lower_ratio_not_reached": (
        "UNKNOWN",
        (
            "observed/expected ratio 1.25 has not reached 2.00; the count is a lower"
            " bound (volume: the count is a lower bound: an increase can be proven,"
            " normal behaviour cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "spike.lower_delta_below_min": (
        "UNKNOWN",
        (
            "increase of 15 events/bucket is below the 50 minimum absolute delta; the"
            " count is a lower bound (volume: the count is a lower bound: an increase can"
            " be proven, normal behaviour cannot), so a normal verdict cannot be"
            " concluded from it"
        ),
    ),
    "spike.lower_z_not_reached": (
        "UNKNOWN",
        (
            "robust z=0.81 has not reached +3.5; the count is a lower bound (volume: the"
            " count is a lower bound: an increase can be proven, normal behaviour"
            " cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "spike.lower_mad_within_p95": (
        "UNKNOWN",
        (
            "robust score unavailable (MAD=0) and 70 is within the historical p95 1,000;"
            " the count is a lower bound (volume: the count is a lower bound: an increase"
            " can be proven, normal behaviour cannot), so a normal verdict cannot be"
            " concluded from it"
        ),
    ),
    "spike.maintenance": (
        "UNKNOWN",
        "the source was in maintenance during this bucket",
    ),
    "spike.no_entity": (
        "UNKNOWN",
        "no destination port entity was supplied for this verdict",
    ),
    "spike.wrong_dimension": (
        "UNKNOWN",
        "a DESTINATION_IP entity is not judged by IPS_PORT_RATE_SPIKE",
    ),
    "spike.absent_port.0/tcp": (
        "UNKNOWN",
        (
            "port 0 and protocol 255 are absent values, not a service: no claim about"
            " them"
        ),
    ),
    "spike.absent_port.443/255": (
        "UNKNOWN",
        (
            "port 0 and protocol 255 are absent values, not a service: no claim about"
            " them"
        ),
    ),
    "spike.generic_signature": (
        "UNKNOWN",
        (
            "the signature's name is generic: a claim about a specific signature is not"
            " safe"
        ),
    ),
    "spike.count_unknown_text": (
        "UNKNOWN",
        "count unknown",
    ),
    "spike.count_unknown_default": (
        "UNKNOWN",
        "the entity's count is unknown for this bucket",
    ),
    "spike.destination_port.dimension_not_collected": (
        "UNKNOWN",
        (
            "the destination port search was not collected for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.destination_port.dimension_failed": (
        "UNKNOWN",
        (
            "the destination port search was failed for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.destination_port.dimension_rejected": (
        "UNKNOWN",
        (
            "the destination port search was rejected for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.destination_port.coverage_low": (
        "UNKNOWN",
        (
            "destination port coverage was 89\xa0%, below the 90\xa0% floor: the events without"
            " a value may differ in kind from the ones with one, so the visible numbers"
            " bound nothing"
        ),
    ),
    "spike.destination_ip.dimension_not_collected": (
        "UNKNOWN",
        (
            "the destination IP search was not collected for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.destination_ip.dimension_failed": (
        "UNKNOWN",
        (
            "the destination IP search was failed for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.destination_ip.dimension_rejected": (
        "UNKNOWN",
        (
            "the destination IP search was rejected for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.destination_ip.coverage_low": (
        "UNKNOWN",
        (
            "destination IP coverage was 89\xa0%, below the 90\xa0% floor: the events without a"
            " value may differ in kind from the ones with one, so the visible numbers"
            " bound nothing"
        ),
    ),
    "spike.signature.dimension_not_collected": (
        "UNKNOWN",
        (
            "the signature search was not collected for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "spike.signature.dimension_failed": (
        "UNKNOWN",
        (
            "the signature search was failed for this bucket: the value is unavailable,"
            " not zero"
        ),
    ),
    "spike.signature.dimension_rejected": (
        "UNKNOWN",
        (
            "the signature search was rejected for this bucket: the value is unavailable,"
            " not zero"
        ),
    ),
    "spike.signature.coverage_low": (
        "UNKNOWN",
        (
            "signature coverage was 89\xa0%, below the 90\xa0% floor: the events without a"
            " value may differ in kind from the ones with one, so the visible numbers"
            " bound nothing"
        ),
    ),
    "spike.coverage_unmeasurable": (
        "UNKNOWN",
        (
            "destination port coverage was not measurable, below the 90\xa0% floor: the"
            " events without a value may differ in kind from the ones with one, so the"
            " visible numbers bound nothing"
        ),
    ),
    "spike.scope_unstated": (
        "UNKNOWN",
        (
            "volume: the bucket does not state whether its counts are exact or lower"
            " bounds"
        ),
    ),
    "spike.no_baseline": (
        "UNKNOWN",
        "no entity_events baseline for this weekday/hour cell yet",
    ),
    "spike.baseline_unreliable": (
        "UNKNOWN",
        (
            "the entity_events baseline has 12 sample(s), below the minimum of 8 for a"
            " defensible verdict"
        ),
    ),
    "spike.baseline_samples": (
        "UNKNOWN",
        (
            "the entity_events baseline has 7 sample(s), below the minimum of 8 for a"
            " defensible verdict"
        ),
    ),
    "spike.baseline_profile": (
        "UNKNOWN",
        (
            "the entity_events baseline was built under profile version 4 and normalizer"
            " version 2, not the bucket's 3 and 2"
        ),
    ),
    "spike.baseline_normalizer": (
        "UNKNOWN",
        (
            "the entity_events baseline was built under profile version 3 and normalizer"
            " version 9, not the bucket's 3 and 2"
        ),
    ),
    "spike.baseline_versions_none": (
        "UNKNOWN",
        (
            "the entity_events baseline was built under profile version None and"
            " normalizer version 2, not the bucket's 3 and 2"
        ),
    ),
    "fanout.fired": (
        "ANOMALOUS",
        (
            "60 distinct ports for the busiest source IP's distinct ports is above the"
            " baseline median 10.0 (6.00x expected, +50 distinct ports/bucket); robust"
            " z=16.86 (threshold +3.5)"
        ),
    ),
    "fanout.fired_big": (
        "ANOMALOUS",
        (
            "4,321 distinct ports for the busiest source IP's distinct ports is above the"
            " baseline median 1500.0 (2.88x expected, +2,821 distinct ports/bucket);"
            " robust z=18.81 (threshold +3.5)"
        ),
    ),
    "fanout.lower_fired": (
        "ANOMALOUS",
        (
            "at least 60 distinct ports (a lower bound: largest fan-out: the count is a"
            " lower bound: an increase can be proven, normal behaviour cannot) for the"
            " busiest source IP's distinct ports is above the baseline median 10.0 (6.00x"
            " expected, +50 distinct ports/bucket); robust z=16.86 (threshold +3.5)"
        ),
    ),
    "fanout.below_min": (
        "HEALTHY",
        "15 distinct ports/bucket is below the 20 minimum for a defensible verdict",
    ),
    "fanout.lower_below_min": (
        "UNKNOWN",
        (
            "15 distinct ports/bucket is below the 20 minimum for a defensible verdict;"
            " the count is a lower bound (largest fan-out: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot), so a normal verdict"
            " cannot be concluded from it"
        ),
    ),
    "fanout.unstated": (
        "UNKNOWN",
        "the bucket does not state its largest distinct-port count",
    ),
    "fanout.port_coverage_low": (
        "UNKNOWN",
        (
            "destination-port coverage was 50\xa0%, below the 90\xa0% floor: the events without"
            " a value may differ in kind from the ones with one, so the visible numbers"
            " bound nothing"
        ),
    ),
    "fanout.dimension_failed": (
        "UNKNOWN",
        (
            "the port fan-out search was failed for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "fanout.port_dimension_not_collected": (
        "UNKNOWN",
        (
            "the destination-port search was not collected for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "conc.fired": (
        "ANOMALOUS",
        (
            "the largest source IP sent 70 of 100 events (70.0\xa0%), against an expected"
            " share of 10.0\xa0% (+60.0\xa0pp); the five largest sent 95.0\xa0%; robust z=20.23"
            " (threshold +3.5)"
        ),
    ),
    "conc.fired_no_top5": (
        "ANOMALOUS",
        (
            "the largest source IP sent 70 of 100 events (70.0\xa0%), against an expected"
            " share of 10.0\xa0% (+60.0\xa0pp); the five largest sent n/a; robust z=20.23"
            " (threshold +3.5)"
        ),
    ),
    "conc.top_below_min": (
        "HEALTHY",
        (
            "the largest source IP sent 15 events, below the 20 minimum for a material"
            " concentration"
        ),
    ),
    "conc.total_below_min": (
        "UNKNOWN",
        "40 events is below the 50 minimum for a composition verdict",
    ),
    "conc.rise_below_min": (
        "HEALTHY",
        (
            "the largest source IP's share 20.0\xa0% is +10.0\xa0pp from the expected 10.0\xa0%,"
            " below the 20\xa0pp minimum rise"
        ),
    ),
    "conc.unstated": (
        "UNKNOWN",
        "the bucket does not state its source-IP totals",
    ),
    "conc.maintenance": (
        "UNKNOWN",
        "the source was in maintenance during this bucket",
    ),
    "conc.scope_needs_exact": (
        "UNKNOWN",
        (
            "largest contributor: the detector needs exact counts and this count is a"
            " lower bound"
        ),
    ),
    "conc.source_coverage_low": (
        "UNKNOWN",
        (
            "source-IP coverage was 50\xa0%, below the 90\xa0% floor: the events without a"
            " value may differ in kind from the ones with one, so the visible numbers"
            " bound nothing"
        ),
    ),
    "conc.z_not_reached": (
        "HEALTHY",
        "robust z=1.18 has not reached +3.5",
    ),
    "conc.mad_within_p95": (
        "HEALTHY",
        (
            "robust score unavailable (MAD=0) and 45.0\xa0% is within the historical p95"
            " 90.0\xa0%"
        ),
    ),
    "conc.mad_above_p95": (
        "ANOMALOUS",
        (
            "the largest source IP sent 70 of 100 events (70.0\xa0%), against an expected"
            " share of 10.0\xa0% (+60.0\xa0pp); the five largest sent 95.0\xa0%; robust score"
            " unavailable (MAD=0); 70.0\xa0% is above the historical p95 15.0\xa0%"
        ),
    ),
    "conc.no_denominator": (
        "UNKNOWN",
        "the top1_source_share baseline has no pooled denominator",
    ),
    "block.fired": (
        "ANOMALOUS",
        (
            "60 of 100 events carrying an action were blocked (60.0\xa0%), against an"
            " expected 10.0\xa0% from 100 historical events (+50.0\xa0pp, 6.0x); robust z=16.86"
            " (threshold +3.5)"
        ),
    ),
    "block.fired_no_multiple": (
        "ANOMALOUS",
        (
            "60 of 100 events carrying an action were blocked (60.0\xa0%), against an"
            " expected 0.0\xa0% from 100 historical events (+60.0\xa0pp); robust score"
            " unavailable (MAD=0); 60.0\xa0% is above the historical p95 0.0\xa0%"
        ),
    ),
    "block.lower_fired": (
        "ANOMALOUS",
        (
            "at least 60 of 100 events carrying an action were blocked (at least 60.0\xa0%),"
            " against an expected 10.0\xa0% from 100 historical events (+50.0\xa0pp, 6.0x);"
            " robust z=16.86 (threshold +3.5)"
        ),
    ),
    "block.lower_fired_no_multiple": (
        "ANOMALOUS",
        (
            "at least 60 of 100 events carrying an action were blocked (at least 60.0\xa0%),"
            " against an expected 0.0\xa0% from 100 historical events (+60.0\xa0pp); robust"
            " score unavailable (MAD=0); 60.0\xa0% is above the historical p95 0.0\xa0%"
        ),
    ),
    "block.numerator_below_min": (
        "HEALTHY",
        (
            "5 blocked events is below the 10 minimum; no material rise in the proportion"
            " is possible"
        ),
    ),
    "block.lower_numerator_below_min": (
        "UNKNOWN",
        (
            "5 blocked events is below the 10 minimum; no material rise in the proportion"
            " is possible; the count is a lower bound (numerator: the count is a lower"
            " bound: an increase can be proven, normal behaviour cannot), so a normal"
            " verdict cannot be concluded from it"
        ),
    ),
    "block.denominator_below_min": (
        "UNKNOWN",
        (
            "40 events carrying an action is below the 50 minimum denominator for a ratio"
            " verdict"
        ),
    ),
    "block.rise_below_min": (
        "HEALTHY",
        "15.0\xa0% is +5.0\xa0pp from the expected 10.0\xa0%, below the 10\xa0pp minimum rise",
    ),
    "block.lower_rise_below_min": (
        "UNKNOWN",
        (
            "15.0\xa0% is +5.0\xa0pp from the expected 10.0\xa0%, below the 10\xa0pp minimum rise;"
            " the count is a lower bound (numerator: the count is a lower bound: an"
            " increase can be proven, normal behaviour cannot), so a normal verdict"
            " cannot be concluded from it"
        ),
    ),
    "block.multiple_below_min": (
        "HEALTHY",
        "62.0\xa0% is 1.24x the expected 50.0\xa0%, below 2.00x",
    ),
    "block.lower_multiple_below_min": (
        "UNKNOWN",
        (
            "62.0\xa0% is 1.24x the expected 50.0\xa0%, below 2.00x; the count is a lower bound"
            " (numerator: the count is a lower bound: an increase can be proven, normal"
            " behaviour cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "block.z_not_reached": (
        "HEALTHY",
        "robust z=0.45 has not reached +3.5",
    ),
    "block.mad_within_p95": (
        "HEALTHY",
        (
            "robust score unavailable (MAD=0) and 30.0\xa0% is within the historical p95"
            " 90.0\xa0%"
        ),
    ),
    "block.mad_above_p95": (
        "ANOMALOUS",
        (
            "60 of 100 events carrying an action were blocked (60.0\xa0%), against an"
            " expected 10.0\xa0% from 100 historical events (+50.0\xa0pp, 6.0x); robust score"
            " unavailable (MAD=0); 60.0\xa0% is above the historical p95 15.0\xa0%"
        ),
    ),
    "block.lower_z_not_reached": (
        "UNKNOWN",
        (
            "robust z=0.45 has not reached +3.5; the count is a lower bound (numerator:"
            " the count is a lower bound: an increase can be proven, normal behaviour"
            " cannot), so a normal verdict cannot be concluded from it"
        ),
    ),
    "block.lower_mad_within_p95": (
        "UNKNOWN",
        (
            "robust score unavailable (MAD=0) and 30.0\xa0% is within the historical p95"
            " 90.0\xa0%; the count is a lower bound (numerator: the count is a lower bound:"
            " an increase can be proven, normal behaviour cannot), so a normal verdict"
            " cannot be concluded from it"
        ),
    ),
    "block.lower_mad_above_p95": (
        "ANOMALOUS",
        (
            "at least 60 of 100 events carrying an action were blocked (at least 60.0\xa0%),"
            " against an expected 10.0\xa0% from 100 historical events (+50.0\xa0pp, 6.0x);"
            " robust score unavailable (MAD=0); 60.0\xa0% is above the historical p95 15.0\xa0%"
        ),
    ),
    "block.no_action_mapped": (
        "UNKNOWN",
        "no action property mapped",
    ),
    "block.counts_unstated": (
        "UNKNOWN",
        "the bucket does not state its blocked and action event counts",
    ),
    "block.action_failed": (
        "UNKNOWN",
        (
            "the action search was failed for this bucket: the value is unavailable, not"
            " zero"
        ),
    ),
    "block.action_coverage_low": (
        "UNKNOWN",
        (
            "action coverage was 50\xa0%, below the 90\xa0% floor: the events without a value"
            " may differ in kind from the ones with one, so the visible numbers bound"
            " nothing"
        ),
    ),
    "block.denominator_scope": (
        "UNKNOWN",
        "denominator: the detector needs exact counts and this count is a lower bound",
    ),
    "port.fired": (
        "ANOMALOUS",
        (
            "destination port 443/tcp carried 30 events across 2 buckets since it first"
            " appeared at 2026-10-13T12:00:00+00:00; in the verified history"
            " 2026-10-06T12:00:00+00:00 to 2026-10-13T12:00:00+00:00 it stayed below 3"
            " events in every bucket"
        ),
    ),
    "port.fired_big": (
        "ANOMALOUS",
        (
            "destination port 443/tcp carried 12345 events across 9 buckets since it"
            " first appeared at 2026-10-13T12:00:00+00:00; in the verified history"
            " 2026-10-06T12:00:00+00:00 to 2026-10-13T12:00:00+00:00 it stayed below 3"
            " events in every bucket"
        ),
    ),
    "port.fired_lower": (
        "ANOMALOUS",
        (
            "destination port 443/tcp carried at least 30 events across 2 buckets since"
            " it first appeared at 2026-10-13T12:00:00+00:00; in the verified history"
            " 2026-10-06T12:00:00+00:00 to 2026-10-13T12:00:00+00:00 it stayed below 3"
            " events in every bucket"
        ),
    ),
    "port.below_materiality": (
        "HEALTHY",
        (
            "5 event(s) in 1 bucket(s) since first appearance has not reached 20 events"
            " in 2 buckets"
        ),
    ),
    "port.below_materiality_lower": (
        "UNKNOWN",
        (
            "5 event(s) in 1 bucket(s) since first appearance has not reached 20 events"
            " in 2 buckets; the counts are lower bounds"
        ),
    ),
    "port.not_established": (
        "UNKNOWN",
        "the port's first appearance was not established",
    ),
    "port.established_outside_window": (
        "HEALTHY",
        (
            "established port: first admitted 2026-10-13T12:00:00+00:00, outside the"
            " 12-bucket novelty window"
        ),
    ),
    "port.readmitted": (
        "HEALTHY",
        "re-admitted port: it was retired and seen again, so it is not new",
    ),
    "port.maintenance": (
        "UNKNOWN",
        "the source was in maintenance during this bucket",
    ),
    "port.no_entity": (
        "UNKNOWN",
        "no destination-port entity was supplied for this verdict",
    ),
    "port.wrong_dimension": (
        "UNKNOWN",
        "no destination-port entity was supplied for this verdict",
    ),
    "port.absent_port": (
        "UNKNOWN",
        (
            "port 0 and protocol 255 are absent values, not a service: no claim about"
            " them"
        ),
    ),
    "port.dimension_failed": (
        "UNKNOWN",
        (
            "the destination-port search was failed for this bucket: the value is"
            " unavailable, not zero"
        ),
    ),
    "port.coverage_low": (
        "UNKNOWN",
        (
            "destination-port coverage was 50\xa0%, below the 90\xa0% floor: the events without"
            " a value may differ in kind from the ones with one, so the visible numbers"
            " bound nothing"
        ),
    ),
    "port.history_incomplete_default": (
        "UNKNOWN",
        "novelty cannot be established: the history is incomplete",
    ),
    "port.history_incomplete_text": (
        "UNKNOWN",
        "novelty cannot be established: a caller's own wording",
    ),
    "port.history_not_loaded": (
        "UNKNOWN",
        (
            "novelty cannot be established: history was not loaded for an established"
            " port"
        ),
    ),
    "port.history_never_collected": (
        "UNKNOWN",
        (
            "novelty cannot be established: the first-appearance bucket was never"
            " collected"
        ),
    ),
    "port.flood_discovery": (
        "UNKNOWN",
        (
            "the first-appearance bucket's destination-port discovery was incomplete: the"
            " port cannot be told apart from a flood of new ports"
        ),
    ),
    "port.flood_admission": (
        "UNKNOWN",
        (
            "the first-appearance bucket's destination-port admission was truncated: the"
            " port appeared in a flood of new ports and is not individually new"
        ),
    ),
    "port.flood_text": (
        "UNKNOWN",
        "a caller's own wording",
    ),
    "flood.none": (
        "FLOOD",
        "none",
    ),
    "flood.not_measured": (
        "FLOOD",
        (
            "the first-appearance bucket's destination-port discovery was incomplete: the"
            " port cannot be told apart from a flood of new ports"
        ),
    ),
    "flood.not_exact": (
        "FLOOD",
        (
            "the first-appearance bucket's destination-port discovery was incomplete: the"
            " port cannot be told apart from a flood of new ports"
        ),
    ),
    "flood.admission": (
        "FLOOD",
        (
            "the first-appearance bucket's destination-port admission was truncated: the"
            " port appeared in a flood of new ports and is not individually new"
        ),
    ),
    "flood.admission_unrecorded": (
        "FLOOD",
        (
            "the first-appearance bucket's destination-port admission was truncated: the"
            " port appeared in a flood of new ports and is not individually new"
        ),
    ),
    "history.complete": (
        "HISTORY",
        "True: None",
    ),
    "history.not_divisible": (
        "HISTORY",
        "False: the history window is not divisible by the collection bucket width",
    ),
    "history.zero_width": (
        "HISTORY",
        "False: the history window is not divisible by the collection bucket width",
    ),
    "history.never_collected": (
        "HISTORY",
        "False: 1 of 3 history buckets were never collected",
    ),
    "history.record_count": (
        "HISTORY",
        "False: the history contains 4 bucket records for 3 expected buckets",
    ),
    "history.bucket_width": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 used a different collection"
            " bucket width"
        ),
    ),
    "history.port_search_not_collected": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had a destination-port"
            " search that was not collected, not exact"
        ),
    ),
    "history.port_search_failed": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had a destination-port"
            " search that was failed, not exact"
        ),
    ),
    "history.port_search_rejected": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had a destination-port"
            " search that was rejected, not exact"
        ),
    ),
    "history.port_search_lower_bound": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had a destination-port"
            " search that was lower_bound, not exact"
        ),
    ),
    "history.coverage_low": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had destination-port"
            " coverage below the 90\xa0% floor"
        ),
    ),
    "history.admission_truncated": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had truncated or unrecorded"
            " destination-port admission"
        ),
    ),
    "history.admission_unrecorded": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 had truncated or unrecorded"
            " destination-port admission"
        ),
    ),
    "history.profile_version": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 used profile version 2, not"
            " first-appearance version 3"
        ),
    ),
    "history.normalizer_version": (
        "HISTORY",
        (
            "False: history bucket 2026-10-13T10:00:00+00:00 is incompatible with"
            " normalizer version 2"
        ),
    ),
    "history.maintenance": (
        "HISTORY",
        "False: history bucket 2026-10-13T10:00:00+00:00 was in maintenance",
    ),
}

#: Scenarios whose sentence is a caller's own wording or the input layer's, which has no code.
UNCODED = {
    "spike.count_unknown_text",
    "port.history_incomplete_text",
    "port.flood_text",
    # These two build the flood from the text API (`appearance_flood`); the reference API the
    # engine uses is pinned in `test_the_appearance_flood_reference_renders_its_sentence`.
    "port.flood_discovery",
    "port.flood_admission",
}
#: Sentences only the engine writes (database-backed), pinned by the integration tests.
ENGINE_ONLY = {
    "ips.novelty.established_hand_off",
    "ips.history_check.not_loaded",
    "ips.history_check.before_hand_off",
}


def _walk(message: MessageRef):  # type: ignore[no-untyped-def]
    yield message
    for value in message.params.values():
        if isinstance(value, MessageRef):
            yield from _walk(value)


def _verdict_scenarios() -> list[str]:
    return [name for name in SCENARIOS if not name.startswith(("history.", "flood."))]


def test_every_scenario_has_an_expected_sentence() -> None:
    assert set(SCENARIOS) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_sentence_keeps_the_english_the_detector_wrote_before_codes(name: str) -> None:
    assert SCENARIOS[name]() == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(set(_verdict_scenarios()) - UNCODED))
def test_each_verdict_sentence_carries_the_reference_it_was_rendered_from(name: str) -> None:
    verdict = RAW[name]()
    assert isinstance(verdict, IpsVerdict)
    message = verdict.evidence.reason_message
    assert message is not None, f"{name} has no code"
    assert render(message) == verdict.evidence.reason
    wire = message.to_wire()
    assert wire["code"] == message.code
    if verdict.evidence.is_anomalous:
        stored = verdict.evidence.to_dict()
        assert stored["reason_code"] == message.code
        assert stored["reason_params"] == wire["params"]


@pytest.mark.parametrize("name", sorted(UNCODED & set(_verdict_scenarios())))
def test_a_callers_own_wording_stays_plain_text(name: str) -> None:
    verdict = RAW[name]()
    assert isinstance(verdict, IpsVerdict)
    # A nested text param is allowed; the sentence itself either has no code or wraps the text.
    text = verdict.evidence.reason
    assert text == EXPECTED[name][1]
    if verdict.evidence.reason_message is not None:
        assert "cannot be established" in text


def test_a_lower_bound_names_its_scope_by_a_code_and_a_role() -> None:
    verdict = RAW["spike.destination_port.lower_fired"]()
    assert isinstance(verdict, IpsVerdict)
    message = verdict.evidence.reason_message
    assert message is not None and message.code == "ips.spike.fired_lower"
    scope = message.params["scope"]
    assert isinstance(scope, MessageRef) and scope.code == "ips.scope.role"
    assert scope.params["role"] == MessageRef("ips.role.volume")
    assert scope.params["reason"] == MessageRef("waf.scope.lower_bound_increase")
    wrap = RAW["spike.lower_below_min"]()
    assert isinstance(wrap, IpsVerdict) and wrap.evidence.reason_message is not None
    assert wrap.evidence.reason_message.code == "ips.guard.not_proven_lower_bound"
    assert wrap.signal is DetectorSignal.UNKNOWN


def test_every_ips_sentence_is_used_by_a_scenario_or_the_engine() -> None:
    used: set[str] = set()
    for name in _verdict_scenarios():
        verdict = RAW[name]()
        assert isinstance(verdict, IpsVerdict)
        if verdict.evidence.reason_message is not None:
            used.update(m.code for m in _walk(verdict.evidence.reason_message))
        if verdict.evidence.extra.get("caveat_code"):
            used.add(verdict.evidence.extra["caveat_code"])
    for name in SCENARIOS:
        if name.startswith("history."):
            SCENARIOS[name]()
            facts = LAST_HISTORY[-1]
            if isinstance(facts, WafHistoryFacts) and facts.message is not None:
                used.update(m.code for m in _walk(facts.message))
    discovery = ti._bucket(destination_port=ti._dimension(coverage=0.5))
    admission = ti._bucket(destination_port=ti._dimension(admission_truncated=True))
    for flood in (
        appearance_flood_ref(discovery, min_coverage=0.9),
        appearance_flood_ref(admission, min_coverage=0.9),
    ):
        assert flood is not None
        used.add(flood.code)
    # The roles a scope narrows on are reached through `ips_scope_ref` (pinned below).
    used.update(ROLE_CODES.values())
    ips = {spec.code for spec in REGISTRY if spec.code.startswith("ips.")}
    unused = ips - used - ENGINE_ONLY
    assert not unused, f"codes no scenario reaches: {sorted(unused)}"


# ------------------------------------------------------------------- the scope map
def test_every_declared_input_role_has_a_code() -> None:
    roles = {req.role for spec in IPS_DETECTORS.values() for req in spec.inputs}
    assert roles == set(ROLE_CODES)


@pytest.mark.parametrize("role", sorted(ROLE_CODES))
@pytest.mark.parametrize(
    "scope",
    [
        "the bucket does not state whether its counts are exact or lower bounds",
        "the count is a lower bound: an increase can be proven, normal behaviour cannot",
        "the detector needs exact counts and this count is a lower bound",
    ],
)
def test_a_role_led_scope_reason_maps_back_to_its_reference(role: str, scope: str) -> None:
    text = f"{role}: {scope}"
    ref = ips_scope_ref(text)
    assert isinstance(ref, MessageRef) and ref.code == "ips.scope.role"
    assert render(ref) == text
    assert ref.params["role"] == MessageRef(ROLE_CODES[role])


def test_a_scope_reason_this_build_does_not_know_stays_text() -> None:
    assert ips_scope_ref(None) is None
    assert ips_scope_ref("a caller's own wording") == "a caller's own wording"
    # An unknown role leads a sentence that is not in the map: plain text, never a guess.
    unknown = "an unknown role: the detector needs exact counts and this count is a lower bound"
    assert ips_scope_ref(unknown) == unknown
    # A scope sentence without a role is the WAF scope code itself.
    bare = ips_scope_ref("the detector needs exact counts and this count is a lower bound")
    assert bare == MessageRef("waf.scope.needs_exact")


def test_a_dimension_state_maps_to_its_reference_and_unknown_stays_text() -> None:
    assert state_ref("failed") == MessageRef("ips.state.failed")
    assert state_ref("lower_bound") == MessageRef("ips.state.lower_bound")
    assert state_ref("something else") == "something else"


# --------------------------------------------------------------------- the history
@pytest.mark.parametrize("name", [n for n in sorted(EXPECTED) if n.startswith("history.")])
def test_each_incomplete_history_carries_its_reference(name: str) -> None:
    SCENARIOS[name]()
    facts = LAST_HISTORY[-1]
    if name == "history.complete":
        assert facts.complete is True and facts.reason is None
        assert not isinstance(facts, WafHistoryFacts)
        return
    assert isinstance(facts, WafHistoryFacts) and facts.message is not None
    assert facts.complete is False
    assert render(facts.message) == facts.reason
    assert facts.message.to_wire()["code"] == facts.message.code


def test_the_appearance_flood_reference_renders_its_sentence() -> None:
    cases = (
        (ti._bucket(), None),
        (ti._bucket(destination_port=ti._dimension(coverage=0.5)), "ips.novelty.flood_discovery"),
        (
            ti._bucket(destination_port=ti._dimension(admission_truncated=True)),
            "ips.novelty.flood_admission",
        ),
    )
    for bucket, code in cases:
        ref = appearance_flood_ref(bucket, min_coverage=0.9)
        text = appearance_flood(bucket, min_coverage=0.9)
        if code is None:
            assert ref is None and text is None
        else:
            assert ref is not None and ref.code == code and render(ref) == text


# -------------------------------------------------------------------- the caveats
CAVEATS = {
    "spike.destination_port.fired": "ips.caveat.port_rate",
    "spike.destination_ip.fired": "ips.caveat.destination_rate",
    "spike.signature.fired": "ips.caveat.signature_burst",
    "fanout.fired": "ips.caveat.port_fanout",
    "conc.fired": "ips.caveat.source_concentration",
    "block.fired": "ips.caveat.block_ratio",
    "port.fired": "ips.caveat.new_port",
}


@pytest.mark.parametrize(("name", "code"), sorted(CAVEATS.items()))
def test_each_detector_stores_its_caveat_with_a_code(name: str, code: str) -> None:
    verdict = RAW[name]()
    assert isinstance(verdict, IpsVerdict)
    extra = verdict.evidence.extra
    assert extra["caveat_code"] == code
    assert extra["caveat_params"] == {}
    assert extra["caveat"] == render(MessageRef(code))


# ---------------------------------------------------------------- the API projection
def _details(name: str) -> dict[str, Any]:
    verdict = RAW[name]()
    assert isinstance(verdict, IpsVerdict)
    return verdict.evidence.to_dict()


@pytest.mark.parametrize("name", sorted(CAVEATS))
def test_the_detection_block_serves_the_reason_and_the_caveat_codes(name: str) -> None:
    details = _details(name)
    detection = ips_detection_detail(details)
    assert detection is not None
    assert detection.reason == details["reason"]
    served = detection.model_dump(mode="json")
    assert served["reason_code"] == details["reason_code"]
    assert served["reason_params"] == details["reason_params"]
    assert detection.caveat == details["extra"]["caveat"]
    assert detection.caveat_code == CAVEATS[name]
    assert detection.caveat_params == {}


def test_a_row_stored_before_codes_serves_the_text_and_no_code() -> None:
    details = _details("spike.destination_port.fired")
    for key in ("reason_code", "reason_params"):
        del details[key]
    for key in ("caveat_code", "caveat_params"):
        del details["extra"][key]
    detection = ips_detection_detail(details)
    assert detection is not None
    assert detection.reason and detection.caveat
    assert (detection.reason_code, detection.reason_params) == (None, None)
    assert (detection.caveat_code, detection.caveat_params) == (None, None)


def test_a_malformed_stored_code_reads_as_no_code() -> None:
    details = _details("spike.destination_port.fired")
    details["reason_params"] = "not an object"
    details["extra"]["caveat_code"] = 7
    detection = ips_detection_detail(details)
    assert detection is not None
    assert (detection.reason_code, detection.reason_params) == (None, None)
    assert (detection.caveat_code, detection.caveat_params) == (None, None)


def test_the_contributor_block_and_the_claim_are_not_coded_and_not_forwarded() -> None:
    # The contributor block keeps its English notes; no view receives it.
    fanout = _details("fanout.fired")
    block = fanout["extra"]["contributors"]
    assert block["scope"] == (
        "source IP as QRadar normalized it; the list is bounded and not exhaustive"
    )
    assert "reason_code" not in block and "code" not in block
    port = _details("port.fired")
    claim = port["extra"]["expected"]["claim"]
    assert claim == "below 3 events in every bucket of the history window"
    for details in (fanout, port):
        dumped = ips_detection_detail(details).model_dump_json()  # type: ignore[union-attr]
        assert '"contributors"' not in dumped and "192.0.2.4" not in dumped
        assert "source IP as QRadar normalized it" not in dumped
        assert claim not in dumped


def test_the_engine_flags_the_new_columns_for_its_uniform_updates() -> None:
    assert {"explanation_i18n", "suppression_reason_i18n"} <= set(_ANOMALY_COLUMNS)


def test_history_facts_without_a_reference_are_unchanged() -> None:
    plain = HistoryFacts(complete=False, reason="a caller's own wording")
    assert not isinstance(plain, WafHistoryFacts)


# ------------------------------------------------------------------ engine: suppression
class _Session:
    """What `_apply_transition` asks of a unit-test session: record added transitions."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)


def test_a_maintenance_suppression_stores_its_reason_with_its_code() -> None:
    import uuid

    from app.anomaly.lifecycle import LifecycleDecision
    from app.ips.engine import IpsAnomalyEngine, IpsEvaluationReport, _Subject
    from app.models.enums import AnomalyState, IpsAnomalyType, IpsDimension
    from app.models.ips import IpsAnomaly, IpsDetectorState, IpsEntity
    from app.models.log_source import LogSource

    source = LogSource(id=uuid.uuid4())
    entity = IpsEntity(
        id=uuid.uuid4(),
        log_source_id=source.id,
        instance_id=uuid.uuid4(),
        dimension=IpsDimension.DESTINATION_PORT,
        value_key="443/tcp",
        display_name="443/tcp",
    )
    observation = ti._entity_context(IpsDimension.DESTINATION_PORT).observation
    subject = _Subject("IPS_ENTITY", entity.id, entity, observation)
    anomaly = IpsAnomaly(
        id=uuid.uuid4(),
        subject_kind="IPS_ENTITY",
        ips_entity_id=entity.id,
        detector_type=IpsAnomalyType.IPS_PORT_RATE_SPIKE,
        state=AnomalyState.OPEN,
        suppressed=False,
    )
    state = IpsDetectorState(
        subject_kind="IPS_ENTITY",
        ips_entity_id=entity.id,
        detector_type=IpsAnomalyType.IPS_PORT_RATE_SPIKE,
        state=AnomalyState.OPEN,
        active_anomaly_id=anomaly.id,
    )
    evidence = RAW["spike.destination_port.fired"]().evidence  # type: ignore[union-attr]
    engine = IpsAnomalyEngine(_Session(), settings=None)  # type: ignore[arg-type]
    engine._apply_transition(
        source,
        subject,
        IpsAnomalyType.IPS_PORT_RATE_SPIKE,
        evidence,
        state,
        LifecycleDecision(
            state=AnomalyState.SUPPRESSED,
            message=MessageRef("anomaly.lifecycle.suppressed"),
        ),
        {anomaly.id: anomaly},
        IpsEvaluationReport("IPS_ENTITY", entity.id, observation.bucket.bucket_start),
        [],
    )

    assert anomaly.suppressed is True
    assert anomaly.suppression_reason == "maintenance window"
    assert anomaly.suppression_reason_i18n == {
        "code": "waf.suppression.maintenance_window",
        "params": {},
    }
    assert anomaly.suppression_reason_code == "waf.suppression.maintenance_window"
    assert anomaly.suppression_reason_params == {}
