"""The IPS search plan: grouping by references, ACTION only where mapped, chunking."""

from __future__ import annotations

import pytest

from app.ips.collection_plan import IpsPlannedSearch, plan_searches
from app.ips.profile import IpsFieldMapping, IpsFieldReference
from app.models.enums import IpsDimension as D
from app.models.enums import IpsReferenceKind


def normalized(name: str) -> IpsFieldReference:
    return IpsFieldReference(IpsReferenceKind.NORMALIZED, name)


def custom(name: str) -> IpsFieldReference:
    return IpsFieldReference(IpsReferenceKind.CUSTOM_PROPERTY, name)


def mapping(**over: IpsFieldReference | None) -> IpsFieldMapping:
    base: dict = {
        "source_ip": normalized("sourceip"),
        "destination_ip": normalized("destinationip"),
        "destination_port": normalized("destinationport"),
        "protocol": normalized("protocolid"),
        "signature": normalized("qid"),
    }
    return IpsFieldMapping(**{**base, **over})


def by_dimension(plan: list[IpsPlannedSearch], dimension: D) -> list[IpsPlannedSearch]:
    return [p for p in plan if p.dimension is dimension]


def test_all_normalized_sources_form_one_group_per_dimension() -> None:
    sources = [(5, mapping()), (3, mapping()), (4, mapping())]
    plan = plan_searches(sources, max_sources_per_search=50)
    assert [p.dimension for p in plan] == [d for d in D if d is not D.ACTION]
    assert all(p.group.source_ids == (3, 4, 5) for p in plan)


def test_totals_is_one_group_of_every_source_whatever_the_mapping() -> None:
    plan = plan_searches(
        [(1, mapping()), (2, mapping(signature=custom("Sig"))), (3, mapping(protocol=None))],
        max_sources_per_search=50,
    )
    totals = by_dimension(plan, D.TOTALS)
    assert len(totals) == 1
    assert totals[0].group.source_ids == (1, 2, 3)
    assert totals[0].group.fields == ()


def test_a_custom_property_makes_its_own_group_only_for_the_dimension_it_changes() -> None:
    plan = plan_searches(
        [(1, mapping()), (2, mapping(signature=custom("IPS Sig"))), (3, mapping())],
        max_sources_per_search=50,
    )
    signatures = by_dimension(plan, D.SIGNATURE)
    # CUSTOM_PROPERTY sorts before NORMALIZED.
    assert [p.group.source_ids for p in signatures] == [(2,), (1, 3)]
    for dimension in (D.DESTINATION_PORT, D.DESTINATION_IP, D.SOURCE_IP, D.PORT_FANOUT):
        (only,) = by_dimension(plan, dimension)
        assert only.group.source_ids == (1, 2, 3)
    group = next(p.group for p in signatures if p.group.source_ids == (2,))
    assert group.provider_fields() == {"SIGNATURE": ("CUSTOM_PROPERTY", "IPS Sig")}


def test_action_covers_only_sources_with_an_action_mapped() -> None:
    plan = plan_searches(
        [
            (1, mapping()),
            (2, mapping(action=custom("IPS_Action"))),
            (3, mapping(action=custom("IPS_Action"))),
            (4, mapping(action=custom("Other"))),
        ],
        max_sources_per_search=50,
    )
    actions = by_dimension(plan, D.ACTION)
    covered = sorted(i for p in actions for i in p.group.source_ids)
    assert covered == [2, 3, 4]
    assert {p.group.fields for p in actions} == {
        (("ACTION", "CUSTOM_PROPERTY", "IPS_Action"),),
        (("ACTION", "CUSTOM_PROPERTY", "Other"),),
    }


def test_no_action_search_when_no_source_maps_it() -> None:
    plan = plan_searches([(1, mapping())], max_sources_per_search=50)
    assert by_dimension(plan, D.ACTION) == []


def test_ports_use_the_protocol_only_when_mapped() -> None:
    plan = plan_searches(
        [(1, mapping()), (2, mapping(protocol=None))], max_sources_per_search=50
    )
    ports = by_dimension(plan, D.DESTINATION_PORT)
    assert len(ports) == 2
    with_protocol = next(p for p in ports if p.group.source_ids == (1,))
    without = next(p for p in ports if p.group.source_ids == (2,))
    assert "PROTOCOL" in with_protocol.group.provider_fields()
    assert "PROTOCOL" not in without.group.provider_fields()
    # Other dimensions do not split on the protocol.
    assert len(by_dimension(plan, D.DESTINATION_IP)) == 1


def test_fanout_reads_source_ip_and_port_only() -> None:
    plan = plan_searches([(1, mapping())], max_sources_per_search=50)
    (fanout,) = by_dimension(plan, D.PORT_FANOUT)
    assert set(fanout.group.provider_fields()) == {"SOURCE_IP", "DESTINATION_PORT"}


def test_a_large_group_is_chunked_in_sorted_runs() -> None:
    ids = [9, 2, 7, 4, 1, 8, 3, 6, 5]
    plan = plan_searches([(i, mapping()) for i in ids], max_sources_per_search=4)
    totals = by_dimension(plan, D.TOTALS)
    assert [p.group.source_ids for p in totals] == [(1, 2, 3, 4), (5, 6, 7, 8), (9,)]
    assert all(len(p.group.source_ids) <= 4 for p in plan)


def test_order_is_dimension_then_references_then_source_id_and_ignores_input_order() -> None:
    sources = [
        (7, mapping(signature=custom("B"))),
        (3, mapping(signature=custom("A"))),
        (5, mapping()),
        (1, mapping(signature=custom("A"))),
    ]
    first = plan_searches(sources, max_sources_per_search=50)
    assert first == plan_searches(list(reversed(sources)), max_sources_per_search=50)
    assert [p.dimension for p in first] == sorted([p.dimension for p in first], key=list(D).index)
    sigs = [p.group for p in by_dimension(first, D.SIGNATURE)]
    assert [g.fields for g in sigs] == sorted(g.fields for g in sigs)
    assert [g.source_ids for g in sigs] == [(1, 3), (7,), (5,)]


def test_a_duplicate_source_is_a_bug() -> None:
    with pytest.raises(ValueError, match="twice"):
        plan_searches([(1, mapping()), (1, mapping())], max_sources_per_search=50)


def test_the_chunk_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        plan_searches([(1, mapping())], max_sources_per_search=0)


def test_no_sources_plan_nothing() -> None:
    assert plan_searches([], max_sources_per_search=50) == []
