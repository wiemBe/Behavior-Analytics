"""The shared percent and percentage-point spelling for analyst-facing sentences.

The detectors, validators and planners write sentences that sit on screen next
to figures the frontend draws through ``formatPercent``/``formatShare`` and
``formatPoints``, so both must read the same: the number, U+00A0, then the unit.
These tests pin the helpers themselves and — by scanning the sentence-writing
modules — that no new ``:.N%`` spec or raw ``} pp`` sneaks a bare spelling back
into a sentence.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.core.text import percent, points

# The modules that write analyst-facing sentences with a percent or a
# percentage-point figure. A ``:.N%}`` spec or a raw ``} pp`` here bypasses the
# shared spelling; any new sentence-writer module belongs on this list.
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

#: A literal space or tab before "pp" after a format field. U+00A0 is the
#: correct spelling and must not match.
_RAW_POINTS = re.compile(r"[ \t]+pp\b")

_LOG_LEVELS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical"}
)


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


def test_points_signs_a_change_and_joins_pp_with_u00a0() -> None:
    assert points(0.297) == "+29.7\u00a0pp"
    assert points(-0.051) == "-5.1\u00a0pp"
    assert "\u00a0pp" in points(0.297)
    assert " pp" not in points(0.297)


def test_points_zero_carries_no_sign_positive_or_negative() -> None:
    """A zero is not a rise; the UI's ``formatPoints`` signs only a value above zero."""
    assert points(0.0) == "0.0\u00a0pp"
    assert points(-0.0) == "0.0\u00a0pp"


def test_points_precision_and_the_unsigned_threshold_shape() -> None:
    assert points(0.297, precision=0) == "+30\u00a0pp"
    assert points(0.297, precision=2) == "+29.70\u00a0pp"
    assert points(0.4, precision=0, signed=False) == "40\u00a0pp"
    assert points(-0.051, precision=0, signed=False) == "-5\u00a0pp"


def test_points_missing_measurement_is_the_caller_s_word_never_a_zero() -> None:
    assert points(None) == "n/a"
    assert points(None, missing="unknown") == "unknown"
    assert points(float("nan")) == "n/a"
    assert points(float("inf"), missing="no values") == "no values"
    assert points(float("-inf"), precision=0, signed=False) == "n/a"


def _raw_points_fields(tree: ast.AST) -> list[str]:
    """Literal ``" pp"`` segments that directly follow a format field, outside logs.

    Comments never reach the tree. A log message is not an analyst-facing
    sentence, so f-strings passed to a logger are skipped as well. The offending
    literal segments are returned so a failure names the text.
    """
    logged = {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LOG_LEVELS
        and isinstance(node.func.value, ast.Name)
        and "log" in node.func.value.id.lower()
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]
        for part in ast.walk(argument)
    }
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or id(node) in logged:
            continue
        for previous, part in zip(node.values, node.values[1:], strict=False):
            if (
                isinstance(previous, ast.FormattedValue)
                and isinstance(part, ast.Constant)
                and isinstance(part.value, str)
                and _RAW_POINTS.match(part.value)
            ):
                hits.append(part.value)
    return hits


def test_no_sentence_writer_joins_points_with_a_raw_pp() -> None:
    """A raw ``} pp`` after a format field bypasses ``points()``.

    That would print a plain space before "pp" (the UI uses U+00A0) and skip the
    sign rule. Scoped to the sentence-writer modules; comments are not in the
    tree, and these modules write no log messages today.
    """
    backend = Path(__file__).resolve().parents[2]
    for relative in SENTENCE_WRITERS:
        source = (backend / relative).read_text(encoding="utf-8")
        hits = _raw_points_fields(ast.parse(source))
        assert hits == [], f"{relative} formats percentage points with a raw pp: {hits}"


def test_the_points_guard_catches_the_signed_and_unsigned_shapes() -> None:
    signed = ast.parse('f"{rise * 100:+.1f} pp from the expected"')
    unsigned = ast.parse('f"{thr.ratio_min_increase * 100:.0f} pp minimum rise"')
    assert _raw_points_fields(signed) == [" pp from the expected"]
    assert _raw_points_fields(unsigned) == [" pp minimum rise"]


def test_the_points_guard_ignores_comments_log_lines_and_the_shared_spelling() -> None:
    """No false positive: comments are not parsed; a log is not a sentence."""
    tree = ast.parse(
        '# f"{rise * 100:+.1f} pp in a comment"\n'
        'logger.info(f"{rise * 100:+.1f} pp in a log line")\n'
        'reason = f"{rise * 100:+.1f}\\u00a0pp in a sentence"\n'
    )
    assert _raw_points_fields(tree) == []
