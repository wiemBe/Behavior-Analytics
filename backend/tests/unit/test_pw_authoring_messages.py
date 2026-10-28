"""MSG-4b: the 422 reasons of a property-watch draft carry message codes, English unchanged.

Each case is one `reasons.append` site of `validate_draft` (and the reference and pin sentences
nested inside two of them): the English is what the service wrote before it had codes.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import property_watches as routes
from app.history.planner import Gap
from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import PwScopeKind
from app.property_watch.reference import PwReferenceError, validate_pinned_value
from app.services import property_watches as svc

NORMALIZED = {"kind": "NORMALIZED", "name": "username"}
CUSTOM = {"kind": "CUSTOM_PROPERTY", "name": "action"}


def draft(**change: Any) -> svc.WatchDraft:
    values: dict[str, Any] = {
        "scope_kind": PwScopeKind.LOG_SOURCE_TYPE,
        "log_source_id": None,
        "scope_type_id": 1,
        "scope_group_id": None,
        "properties": [NORMALIZED],
        "pinned_values": [],
        "detectors": ["PW_VALUE_SPIKE"],
    }
    values.update(change)
    return svc.WatchDraft(**values)


def reasons(change: dict[str, Any], *, max_pinned: int = 10) -> list[tuple[str, str]]:
    refs = svc.validate_draft_refs(draft(**change), max_pinned=max_pinned)
    return [(ref.code, render(ref)) for ref in refs]


CASES: list[tuple[str, dict[str, Any], list[tuple[str, str]]]] = [
    (
        "scope id missing",
        {"scope_kind": PwScopeKind.LOG_SOURCE, "scope_type_id": None},
        [("pw.draft.scope_required", "log_source_id is required for a LOG_SOURCE scope")],
    ),
    (
        "other scope ids set",
        {"scope_group_id": 4},
        [
            (
                "pw.draft.scope_must_be_empty",
                "scope_group_id must be empty for a LOG_SOURCE_TYPE scope",
            )
        ],
    ),
    (
        "negative id",
        {"scope_type_id": -1},
        [("pw.draft.scope_negative", "scope_type_id must not be negative")],
    ),
    (
        "no properties",
        {"properties": []},
        [("pw.draft.property_count", "a watch names one or two properties, not 0")],
    ),
    (
        "three properties",
        {"properties": [NORMALIZED, CUSTOM, {"kind": "CUSTOM_PROPERTY", "name": "other"}]},
        [("pw.draft.property_count", "a watch names one or two properties, not 3")],
    ),
    (
        "unknown reference kind",
        {"properties": [{"kind": "NOPE", "name": "x"}]},
        [("pw.draft.property_invalid", "property 1: unknown property reference kind")],
    ),
    (
        "kind not text",
        {"properties": [{"kind": 4, "name": "x"}]},
        [("pw.draft.property_invalid", "property 1: unknown property reference kind")],
    ),
    (
        "reference not an object",
        {"properties": [NORMALIZED, "x"]},
        [
            (
                "pw.draft.property_invalid",
                "property 2: a property reference is an object with a kind and a name",
            )
        ],
    ),
    (
        "name not text",
        {"properties": [{"kind": "NORMALIZED", "name": 5}]},
        [("pw.draft.property_invalid", "property 1: property reference name must be text")],
    ),
    (
        "normalized field not allowed",
        {"properties": [{"kind": "NORMALIZED", "name": "sourceip"}]},
        [
            (
                "pw.draft.property_invalid",
                "property 1: normalized field is not in the property-watch allowlist",
            )
        ],
    ),
    (
        "custom name pattern",
        {"properties": [NORMALIZED, {"kind": "CUSTOM_PROPERTY", "name": "bad;name"}]},
        [
            (
                "pw.draft.property_invalid",
                "property 2: invalid QRadar property name: letters, digits, underscore, "
                "space and hyphen only, at most 64 characters",
            )
        ],
    ),
    (
        "same property twice",
        {"properties": [NORMALIZED, NORMALIZED]},
        [("pw.draft.property_twice", "a watch cannot name the same property twice")],
    ),
    (
        "too many pins",
        {"pinned_values": [["a"], ["b"]]},
        [("pw.draft.pin_count", "a watch pins at most 1 values, not 2")],
    ),
    (
        "pin of the wrong width",
        {"pinned_values": [["a", "b"]]},
        [("pw.draft.pin_parts", "pinned value 1: needs 1 string(s), one per property")],
    ),
    (
        "pin empty",
        {"pinned_values": [[""]]},
        [("pw.draft.pin_invalid", "pinned value 1, part 1: a pinned value cannot be empty")],
    ),
    (
        "pin not text",
        {"pinned_values": [[5]]},
        [("pw.draft.pin_invalid", "pinned value 1, part 1: a pinned value is text")],
    ),
    (
        "pin too long",
        {"pinned_values": [["a" * 256]]},
        [
            (
                "pw.draft.pin_invalid",
                "pinned value 1, part 1: a pinned value is at most 255 characters",
            )
        ],
    ),
    (
        "pin with a quote",
        {"properties": [NORMALIZED, CUSTOM], "pinned_values": [["alice", "it's"]]},
        [
            (
                "pw.draft.pin_invalid",
                "pinned value 1, part 2: a pinned value cannot hold a quote, a backslash, a "
                "control character or leading or trailing whitespace",
            )
        ],
    ),
    (
        "pin twice",
        {"pinned_values": [["a"], ["a"]]},
        [("pw.draft.pin_twice", "pinned value 2: pinned twice")],
    ),
    (
        "no detector",
        {"detectors": []},
        [("pw.draft.detector_required", "a watch evaluates at least one detector")],
    ),
    (
        "combination needs two properties",
        {"detectors": ["PW_NEW_COMBINATION"]},
        [
            (
                "pw.draft.combination_needs_two",
                "PW_NEW_COMBINATION needs a watch with two properties",
            )
        ],
    ),
]


@pytest.mark.parametrize(("label", "change", "expected"), CASES, ids=[c[0] for c in CASES])
def test_each_draft_reason_keeps_its_english_and_names_its_code(
    label: str, change: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    max_pinned = 1 if label == "too many pins" else 10
    assert reasons(change, max_pinned=max_pinned) == expected
    again = svc.validate_draft(draft(**change), max_pinned=max_pinned)
    assert again == [english for _, english in expected]


def test_every_reason_of_a_bad_draft_comes_back_at_once_in_order() -> None:
    change = {
        "scope_kind": PwScopeKind.LOG_SOURCE,
        "scope_type_id": 5,
        "properties": [{"kind": "NORMALIZED", "name": "sourceip"}, CUSTOM],
        "pinned_values": [["", "x"], ["", "x"]],
        "detectors": [],
    }
    codes = [code for code, _ in reasons(change)]
    assert codes == [
        "pw.draft.scope_required",
        "pw.draft.scope_must_be_empty",
        "pw.draft.property_invalid",
        "pw.draft.pin_invalid",
        "pw.draft.pin_invalid",
        "pw.draft.pin_twice",
        "pw.draft.detector_required",
    ]


def test_the_nested_reference_reason_is_a_reference_not_text() -> None:
    ref = svc.validate_draft_refs(
        draft(properties=[{"kind": "NOPE", "name": "x"}]), max_pinned=10
    )[0]
    assert ref.params["reason"] == MessageRef("pw.reference.kind_unknown")
    wire = ref.to_wire()
    assert wire == {
        "code": "pw.draft.property_invalid",
        "params": {"index": 1, "reason": {"code": "pw.reference.kind_unknown", "params": {}}},
    }
    json.dumps(wire)


def test_a_reference_error_the_editor_cannot_show_stays_plain_text() -> None:
    error = PwReferenceError("properties is a list of one or two references")
    assert error.ref is None and str(error) == "properties is a list of one or two references"
    with pytest.raises(PwReferenceError) as caught:
        validate_pinned_value("")
    assert caught.value.ref == MessageRef("pw.reference.pin_empty")
    assert str(caught.value) == "a pinned value cannot be empty"


def test_the_pin_reasons_of_the_pins_route_keep_their_english() -> None:
    assert svc.pin_reasons([["a"], ["a"]], 1, 10) == ["pinned value 2: pinned twice"]
    refs = svc.pin_reason_refs([["a"], ["a"]], 1, 10)
    assert [ref.code for ref in refs] == ["pw.draft.pin_twice"]


def test_a_valid_draft_has_no_reasons_and_no_references() -> None:
    assert svc.validate_draft_refs(draft(), max_pinned=10) == []


# ------------------------------------------------------------- the other 422 reasons
def settings(low: int = 7, high: int = 30) -> SimpleNamespace:
    return SimpleNamespace(pw_new_value_history_days=low, pw_bootstrap_days=high)


@pytest.mark.parametrize(
    ("days", "english"),
    [
        (3, "history_days must be 0 (no history) or between 7 and 30, not 3"),
        (31, "history_days must be 0 (no history) or between 7 and 30, not 31"),
        (-1, "history_days must be 0 (no history) or between 7 and 30, not -1"),
    ],
)
def test_history_days_out_of_range(days: int, english: str) -> None:
    refs = svc.history_days_refs(days, settings())  # type: ignore[arg-type]
    assert [render(ref) for ref in refs] == [english]
    assert refs[0].code == "pw.draft.history_days_range"
    assert svc.history_days_reasons(days, settings()) == [english]  # type: ignore[arg-type]


@pytest.mark.parametrize("days", [0, 7, 15, 30])
def test_history_days_in_range_has_no_reason(days: int) -> None:
    assert svc.history_days_refs(days, settings()) == []  # type: ignore[arg-type]


def test_the_422_body_carries_the_text_and_a_code_for_each_reason() -> None:
    refs = [
        routes._refusal("history_days_null"),
        routes._refusal("field_null", name="severity"),
        routes._refusal("series_max", max=12, count=13),
        routes._refusal("scope_id_uuid"),
    ]
    with pytest.raises(HTTPException) as caught:
        routes._reject(refs)
    assert caught.value.status_code == 422
    body = caught.value.detail
    assert body["reasons"] == [
        "history_days must not be null; use 0 for no history",
        "severity must not be null",
        "at most 12 values per request, not 13",
        "scope_id of a LOG_SOURCE scope is the source's id",
    ]
    assert body["message"] == "; ".join(body["reasons"])
    assert body["reason_codes"] == [ref.to_wire() for ref in refs]
    assert [item["code"] for item in body["reason_codes"]] == [
        "pw.draft.history_days_null",
        "pw.draft.field_null",
        "pw.draft.series_max",
        "pw.draft.scope_id_uuid",
    ]


def test_no_reason_is_no_refusal() -> None:
    routes._reject([])


@pytest.mark.parametrize(
    ("ref", "english"),
    [
        (
            routes._refusal("log_source_missing", id="5b0c8b0e-0000-4000-8000-000000000001"),
            "log source 5b0c8b0e-0000-4000-8000-000000000001 does not exist",
        ),
        (
            routes._refusal("type_missing", id=11),
            "log source type 11 is not in the synced inventory",
        ),
        (
            routes._refusal("group_missing", id=12),
            "log source group 12 is not in the synced inventory",
        ),
        (
            routes._refusal("instance_differs"),
            "instance_id differs from the log source's instance",
        ),
        (routes._refusal("no_instance"), "no QRadar instance is registered"),
        (
            routes._refusal("scope_id_number", kind="LOG_SOURCE_TYPE"),
            "scope_id of a LOG_SOURCE_TYPE scope is a number",
        ),
        (
            routes._refusal("series_untracked", value="abc"),
            "value abc is not a tracked value of this watch's current version",
        ),
        (
            routes._refusal("pinned_rows", rows=9000, target=5000),
            "the pinned values can return up to 9000 rows per bucket, above the 5000 rows "
            "one history search may carry; unpin values or request no history",
        ),
    ],
)
def test_the_route_reasons_keep_their_english(ref: MessageRef, english: str) -> None:
    assert render(ref) == english


@pytest.mark.parametrize(
    ("ref", "english"),
    [
        (
            MessageRef("pw.history_change.already_requested", {"state": "READING"}),
            "history is READING on this watch and cannot be requested again; "
            "create a new watch to read history",
        ),
        (
            MessageRef(
                "pw.history_change.already_collected",
                {"marks": ["collected_through", "evaluated_through"]},
            ),
            "this watch has already collected (collected_through and evaluated_through set); "
            "create a new watch to read history",
        ),
        (
            MessageRef("pw.history_change.already_collected", {"marks": ["evaluated_through"]}),
            "this watch has already collected (evaluated_through set); "
            "create a new watch to read history",
        ),
        (
            MessageRef("pw.history_change.holds_buckets", {"stored": 288}),
            "this watch holds 288 stored bucket(s) of its current version; "
            "create a new watch to read history",
        ),
        (
            MessageRef(
                "pw.preview.scope_unresolved",
                {"status": "SCOPE_STALE", "reason": MessageRef("pw.scope.group_empty")},
            ),
            "SCOPE_STALE: the group and its descendants hold no synced log source",
        ),
        (
            MessageRef("pw.preview.group_too_large", {"members": 700, "max": 500}),
            "the group resolves to 700 sources and a preview covers one search chunk of at "
            "most 500; preview a smaller group, a type or a source",
        ),
        (
            MessageRef("pw.preview.unsearchable", {"error": "unknown property reference kind"}),
            "the stored watch cannot be searched: unknown property reference kind",
        ),
        (
            MessageRef("pw.preview.search_failed"),
            "a search failed, so its buckets were not measured",
        ),
        (
            MessageRef("pw.preview.cut_at_row_limit"),
            "the grouped search was cut at the row limit and a preview does not split it; "
            "narrow the scope or preview a type or a source",
        ),
        (
            MessageRef("pw.preview.lower_bound"),
            "a bucket was cut at the row limit: its counts are lower bounds",
        ),
    ],
)
def test_the_history_change_and_preview_sentences_keep_their_english(
    ref: MessageRef, english: str
) -> None:
    assert render(ref) == english


def test_a_refused_preview_keeps_its_text_and_its_reference() -> None:
    ref = MessageRef("pw.preview.search_failed")
    refused = svc.PreviewRefused(ref)
    assert str(refused) == "a search failed, so its buckets were not measured"
    assert refused.ref is ref


def test_wire_parts_of_a_message_and_of_no_message() -> None:
    assert svc.wire_parts(None) == (None, None)
    code, params = svc.wire_parts(
        MessageRef("pw.scope.too_large", {"sources": 5, "searches": 3, "limit": 1})
    )
    assert code == "pw.scope.too_large"
    assert params == {"sources": 5, "searches": 3, "limit": 1}


def test_a_stored_gap_is_served_with_a_code_only_when_it_reads_back() -> None:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 1, 1, tzinfo=UTC)
    coded = svc._gap_out(
        Gap(start, end, "search failed 3 times at the minimum width",
            "history.gap.search_failed", {"failures": 3})
    )
    assert (coded.reason_code, coded.reason_params) == (
        "history.gap.search_failed",
        {"failures": 3},
    )
    old = svc._gap_out(Gap(start, end, "stored before codes"))
    assert (old.reason_code, old.reason_params) == (None, None)
    assert old.reason == "stored before codes"
    malformed = svc._gap_out(Gap(start, end, "x", "history.gap.search_failed", {"n": object()}))
    assert (malformed.reason_code, malformed.reason_params) == (None, None)


def test_every_registry_code_of_the_family_has_a_pinned_sample_sentence() -> None:
    family = ("pw.", "history.gap.")
    for spec in REGISTRY:
        if spec.code.startswith(family):
            assert render(MessageRef(spec.code, spec.sample)).strip()
    assert uuid.UUID("5b0c8b0e-0000-4000-8000-000000000001")
