"""The shared percent spelling for analyst-facing sentences.

The detectors, validators and planners write sentences that sit on screen next
to percentages the frontend draws through ``formatPercent``/``formatShare``, so
both must read the same: the number, U+00A0, then "%". These tests pin the
helper itself and — by scanning the sentence-writing modules — that no new
``:.N%`` format spec sneaks a bare "%" back into a sentence.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.text import percent

# The modules that write analyst-facing sentences with a percent figure. A
# ``:.N%}`` spec here formats a ratio into a sentence without the shared
# spelling; any new sentence-writer module belongs on this list.
SENTENCE_WRITERS = (
    "app/anomaly/detectors.py",
    "app/history/planner.py",
    "app/ips/behavior.py",
    "app/ips/detector.py",
    "app/ips/inputs.py",
    "app/ips/validation.py",
    "app/property_watch/detectors.py",
    "app/waf/behavior.py",
    "app/waf/detector.py",
    "app/waf/validation.py",
)

_PERCENT_SPEC = re.compile(r":\.\d+%\}")


def test_precision_is_the_caller_s_and_the_sign_is_u00a0() -> None:
    assert percent(0.318, precision=1) == "31.8\u00a0%"
    assert percent(0.318) == "32\u00a0%"
    assert percent(0.318, precision=2) == "31.80\u00a0%"
    assert percent(0.9) == "90\u00a0%"
    assert percent(1.0, precision=1) == "100.0\u00a0%"


def test_a_missing_measurement_is_the_caller_s_word_never_a_zero() -> None:
    assert percent(None) == "n/a"
    assert percent(None, missing="unknown") == "unknown"
    assert percent(None, missing="no values") == "no values"


def test_a_non_finite_leak_is_not_drawn_as_a_number() -> None:
    assert percent(float("nan")) == "n/a"
    assert percent(float("inf"), missing="unknown") == "unknown"


def test_no_sentence_writer_formats_a_ratio_with_a_percent_spec() -> None:
    """A ``:.N%`` spec in a sentence writer bypasses the shared spelling.

    Scoped to the sentence-writer modules on purpose: a percent spec inside a
    log message elsewhere would be a false positive, and those are not
    sentences.
    """
    backend = Path(__file__).resolve().parents[2]
    for relative in SENTENCE_WRITERS:
        source = (backend / relative).read_text(encoding="utf-8")
        hit = _PERCENT_SPEC.search(source)
        assert hit is None, f"{relative} formats a ratio with a raw percent spec: {hit}"


def test_guard_catches_a_new_percent_spec() -> None:
    """The scan itself fires on a sentence writer carrying a raw spec."""
    assert _PERCENT_SPEC.search('f"{ratio:.0%} of the buckets"') is not None
