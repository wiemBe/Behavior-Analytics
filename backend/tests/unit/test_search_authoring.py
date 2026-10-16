"""422 detail for authoring: the reasons list, the old string, reachability.

`authoring_detail_for_body` is what POST and PATCH put in `detail`. Reasons are
the validator's own sentences. The string is the text those routes already
returned.
"""

from __future__ import annotations

import pytest

from app.schemas.search import authoring_detail_for_body, unreachable_threshold_reason
from app.services.aql_validator import AQLValidationError, validate_aql

VALID = "SELECT sourceip FROM events LAST 1 HOURS"
BAD = "DELETE FROM events"


def _aql_reasons() -> list[str]:
    with pytest.raises(AQLValidationError) as exc:
        validate_aql(BAD)
    return list(exc.value.reasons)


def test_aql_reasons_are_a_list_and_the_string_is_kept() -> None:
    reasons = _aql_reasons()
    detail = authoring_detail_for_body({"aql_query": BAD})
    assert detail is not None
    assert detail["reasons"] == reasons
    assert "query must be a single SELECT statement" in detail["reasons"]
    assert "disallowed keyword(s): DELETE" in detail["reasons"]
    assert (
        "query must include a bounded time window (LAST N ..., or START/STOP)"
        in detail["reasons"]
    )
    assert len(detail["reasons"]) >= 2
    assert detail["message"] == "unsafe AQL: " + "; ".join(reasons)


def test_threshold_reason_is_listed_and_kept_as_the_string() -> None:
    sentence = unreachable_threshold_reason("GT", 101, 100)
    assert sentence is not None
    detail = authoring_detail_for_body(
        {
            "aql_query": VALID,
            "threshold_operator": "GT",
            "threshold_value": 101,
            "max_result_rows": 100,
        }
    )
    assert detail is not None
    assert detail["reasons"] == [sentence]
    assert detail["message"] == sentence
    assert "can never be reached" in detail["message"]
    assert "max_result_rows (100)" in detail["message"]


def test_threshold_reason_is_added_beside_aql_reasons() -> None:
    reasons = _aql_reasons()
    sentence = unreachable_threshold_reason("GT", 101, 100)
    assert sentence is not None
    detail = authoring_detail_for_body(
        {"aql_query": BAD, "threshold_value": 101, "max_result_rows": 100}
    )
    assert detail is not None
    assert detail["reasons"] == [*reasons, sentence]
    assert detail["message"] == "unsafe AQL: " + "; ".join(reasons) + "; " + sentence
    # The threshold sentence is intact, not folded into the AQL prefix.
    assert detail["reasons"][-1] == sentence
    assert not detail["reasons"][-1].startswith("unsafe AQL:")


def test_reachable_authoring_has_no_refusal() -> None:
    assert authoring_detail_for_body(
        {"aql_query": VALID, "threshold_value": 10, "max_result_rows": 100}
    ) is None


def test_omitted_row_cap_uses_the_create_default() -> None:
    detail = authoring_detail_for_body(
        {"aql_query": VALID, "threshold_operator": "GT", "threshold_value": 10_001}
    )
    assert detail is not None
    assert "max_result_rows (10000)" in detail["message"]
    assert detail["reasons"] == [detail["message"]]


def test_update_uses_stored_threshold_when_the_patch_omits_it() -> None:
    # Lowering the cap under a stored GT 100 makes the effective search
    # unreachable. The patch itself carries no threshold.
    sentence = unreachable_threshold_reason("GT", 100, 99)
    detail = authoring_detail_for_body(
        {"max_result_rows": 99},
        current_operator="GT",
        current_threshold=100,
        current_max_rows=100,
        check_aql=False,
    )
    assert detail is not None
    assert sentence is not None
    assert detail["reasons"] == [sentence]
    assert detail["message"] == sentence


def test_description_patch_of_a_reachable_search_is_not_a_refusal() -> None:
    assert (
        authoring_detail_for_body(
            {"description": "note"},
            current_operator="GT",
            current_threshold=100,
            current_max_rows=100,
            check_aql=False,
        )
        is None
    )


def test_non_numeric_threshold_is_left_to_schema_validation() -> None:
    assert authoring_detail_for_body(
        {"aql_query": VALID, "threshold_value": "high", "max_result_rows": 100}
    ) is None
