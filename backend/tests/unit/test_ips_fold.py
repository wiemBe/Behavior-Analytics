"""The pure IPS folds: entity keys, the top-N cut, contributors, actions and coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.ips.fold import (
    CONTRIBUTOR_LIMIT,
    IpsFoldError,
    IpsTotalsFact,
    coverage,
    fold_actions,
    fold_entity_dimension,
    fold_outcome,
    fold_port_fanout,
    fold_source_ips,
    fold_totals,
    protocol_name,
)
from app.ips.keyed_search import IpsCellOutcome
from app.ips.profile import IpsFieldMapping, IpsFieldReference
from app.models.enums import IpsCellState as S
from app.models.enums import IpsDimension as D
from app.models.enums import IpsMeasurement as M
from app.models.enums import IpsReferenceKind
from app.providers.dto import IpsKeyedRow

T = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
TOTALS_100 = IpsTotalsFact(events=100, records=1, measurement=M.EXACT)


def row(events: int = 10, **over: Any) -> IpsKeyedRow:
    return IpsKeyedRow(
        qradar_log_source_id=412, bucket_start=T, events=events, records=1, **over
    )


def cell(
    dimension: D, rows: list[IpsKeyedRow] | None = None, state: S = S.COMPLETE
) -> IpsCellOutcome:
    measured = state in (S.COMPLETE, S.LOWER_BOUND)
    return IpsCellOutcome(dimension, 412, T, state, tuple(rows or ()) if measured else ())


def ports(*rows: IpsKeyedRow, state: S = S.COMPLETE, n: int = 200):
    return fold_entity_dimension(cell(D.DESTINATION_PORT, list(rows), state), max_entities=n)


class TestPorts:
    @pytest.mark.parametrize(
        ("protocol", "name"),
        [
            (6, "tcp"),
            (17, "udp"),
            (1, "icmp"),
            (58, "ipv6-icmp"),
            (132, "sctp"),
            (47, "47"),
            (254, "254"),
            (0, "unknown"),
            (255, "unknown"),
            (None, "unknown"),
            (-3, "unknown"),
            (300, "unknown"),
        ],
    )
    def test_protocol_names(self, protocol: int | None, name: str) -> None:
        assert protocol_name(protocol) == name

    def test_value_key_is_port_slash_protocol(self) -> None:
        fact = ports(
            row(30, destination_port=443, protocol_id=6),
            row(20, destination_port=53, protocol_id=17),
            row(5, destination_port=8080, protocol_id=None),
        )
        assert [e.value_key for e in fact.entities] == ["443/tcp", "53/udp", "8080/unknown"]

    def test_absent_and_unmapped_protocols_merge_into_unknown(self) -> None:
        fact = ports(
            row(4, destination_port=80, protocol_id=255),
            row(6, destination_port=80, protocol_id=0),
            row(1, destination_port=80, protocol_id=None),
        )
        assert [(e.value_key, e.events) for e in fact.entities] == [("80/unknown", 11)]

    def test_port_zero_is_never_an_entity_and_the_fold_asserts_it(self) -> None:
        with pytest.raises(IpsFoldError, match="port 0"):
            ports(row(destination_port=0, protocol_id=6))

    def test_a_missing_or_impossible_port_is_unattributed(self) -> None:
        fact = ports(
            row(3, destination_port=None),
            row(4, destination_port=70000),
            row(10, destination_port=22, protocol_id=6),
        )
        assert [e.value_key for e in fact.entities] == ["22/tcp"]
        assert fact.unattributed_events == 7
        assert fact.dimension_events == 17


class TestEntityCut:
    def test_top_n_by_events_with_ties_broken_by_value_key(self) -> None:
        fact = ports(
            row(5, destination_port=90, protocol_id=6),
            row(5, destination_port=100, protocol_id=6),
            row(5, destination_port=80, protocol_id=6),
            row(9, destination_port=7, protocol_id=6),
            n=3,
        )
        assert [e.value_key for e in fact.entities] == ["7/tcp", "100/tcp", "80/tcp"]

    def test_dimension_events_sums_every_row_not_just_the_top(self) -> None:
        rows = [row(10 + i, destination_port=1000 + i, protocol_id=6) for i in range(8)]
        fact = ports(*rows, n=3)
        assert len(fact.entities) == 3
        assert fact.entity_count == 8
        assert fact.dimension_events == sum(10 + i for i in range(8))
        assert sum(e.events for e in fact.entities) < fact.dimension_events

    def test_max_entities_must_be_positive(self) -> None:
        with pytest.raises(IpsFoldError):
            ports(row(destination_port=1), n=0)

    def test_a_complete_empty_cell_is_a_measured_zero(self) -> None:
        fact = ports()
        assert fact is not None
        assert (fact.dimension_events, fact.entities, fact.entity_count) == (0, (), 0)
        assert fact.measurement is M.EXACT

    @pytest.mark.parametrize("state", [S.FAILED, S.REJECTED, S.NOT_COLLECTED])
    def test_an_unmeasured_cell_folds_to_none_never_zero(self, state: S) -> None:
        assert ports(state=state) is None

    def test_lower_bound_is_carried(self) -> None:
        fact = ports(row(destination_port=22, protocol_id=6), state=S.LOWER_BOUND)
        assert fact.measurement is M.LOWER_BOUND


class TestDestinationsAndSignatures:
    def dest(self, *rows: IpsKeyedRow):
        return fold_entity_dimension(cell(D.DESTINATION_IP, list(rows)), max_entities=10)

    def test_destination_ips_are_canonical_text(self) -> None:
        fact = self.dest(
            row(5, destination_ip="10.10.20.15"),
            row(4, destination_ip=" 10.10.20.15 "),
            row(3, destination_ip="2001:DB8:0:0:0:0:0:1"),
        )
        assert [(e.value_key, e.events) for e in fact.entities] == [
            ("10.10.20.15", 9),
            ("2001:db8::1", 3),
        ]

    @pytest.mark.parametrize("bad", ["not-an-ip", "", "999.1.1.1", None, "10.0.0.0/8"])
    def test_an_unparseable_destination_is_unattributed_not_an_entity(
        self, bad: str | None
    ) -> None:
        fact = self.dest(row(7, destination_ip=bad), row(3, destination_ip="10.0.0.1"))
        assert [e.value_key for e in fact.entities] == ["10.0.0.1"]
        assert fact.unattributed_events == 7
        assert fact.dimension_events == 10

    def sig(self, *rows: IpsKeyedRow):
        return fold_entity_dimension(cell(D.SIGNATURE, list(rows)), max_entities=10)

    def test_a_normalized_signature_is_the_qid_with_a_display_name(self) -> None:
        fact = self.sig(
            row(9, qid=5000123, name="SQL injection"),
            row(2, qid=5000124, name="Unknown Cisco Event"),
        )
        first, second = fact.entities
        assert (first.value_key, first.display_name, first.generic_name) == (
            "qid:5000123",
            "SQL injection",
            False,
        )
        assert (second.value_key, second.generic_name) == ("qid:5000124", True)

    def test_the_name_is_display_only_so_two_names_one_qid_are_one_entity(self) -> None:
        fact = self.sig(row(4, qid=7, name="A"), row(3, qid=7, name="B"))
        assert [(e.value_key, e.events) for e in fact.entities] == [("qid:7", 7)]

    def test_a_non_positive_qid_is_unattributed(self) -> None:
        fact = self.sig(row(4, qid=0), row(3, qid=-1), row(2, qid=9))
        assert [e.value_key for e in fact.entities] == ["qid:9"]
        assert fact.unattributed_events == 7

    def test_a_custom_signature_is_capped_at_128_characters(self) -> None:
        fact = self.sig(row(5, signature="x" * 300), row(4, signature="  ET SCAN  "))
        keys = {e.value_key for e in fact.entities}
        assert keys == {"sig:" + "x" * 128, "sig:ET SCAN"}

    def test_a_blank_custom_signature_is_unattributed(self) -> None:
        fact = self.sig(row(5, signature="   "), row(4, signature=None))
        assert fact.entities == () and fact.unattributed_events == 9

    def test_a_non_entity_dimension_is_refused(self) -> None:
        with pytest.raises(IpsFoldError):
            fold_entity_dimension(cell(D.SOURCE_IP), max_entities=5)


class TestSourceIps:
    def fold(self, *rows: IpsKeyedRow, state: S = S.COMPLETE):
        return fold_source_ips(cell(D.SOURCE_IP, list(rows), state))

    def test_concentration_and_distinct_sources(self) -> None:
        rows = [row(100 - i * 10, source_ip=f"203.0.113.{i}") for i in range(1, 8)]
        fact = self.fold(*rows)
        assert fact.distinct_sources == 7
        assert fact.source_events == sum(r.events for r in rows)
        assert fact.top1_events == 90
        assert fact.top5_events == 90 + 80 + 70 + 60 + 50

    def test_contributors_are_capped_at_ten_and_ordered(self) -> None:
        rows = [row(1000 - i, source_ip=f"198.51.100.{i}") for i in range(1, 40)]
        fact = self.fold(*rows)
        assert len(fact.contributors) == CONTRIBUTOR_LIMIT == 10
        assert fact.contributors[0] == ("198.51.100.1", 999)
        counts = [c[1] for c in fact.contributors]
        assert counts == sorted(counts, reverse=True)
        assert fact.distinct_sources == 39

    def test_an_invalid_source_is_unattributed_and_not_a_source(self) -> None:
        fact = self.fold(row(5, source_ip="garbage"), row(4, source_ip="203.0.113.1"))
        assert fact.distinct_sources == 1
        assert fact.unattributed_events == 5
        assert fact.source_events == 9
        assert all("garbage" not in c[0] for c in fact.contributors)

    def test_an_empty_complete_cell_is_a_measured_zero(self) -> None:
        fact = self.fold()
        assert (fact.source_events, fact.distinct_sources, fact.top1_events) == (0, 0, 0)
        assert fact.contributors == ()

    def test_lower_bound_reaches_distinct_sources(self) -> None:
        fact = self.fold(row(5, source_ip="203.0.113.1"), state=S.LOWER_BOUND)
        assert fact.measurement is M.LOWER_BOUND and fact.distinct_sources == 1

    @pytest.mark.parametrize("state", [S.FAILED, S.REJECTED, S.NOT_COLLECTED])
    def test_unmeasured_is_none(self, state: S) -> None:
        assert self.fold(state=state) is None

    def test_ties_resolve_by_address(self) -> None:
        fact = self.fold(row(5, source_ip="203.0.113.9"), row(5, source_ip="203.0.113.10"))
        assert [c[0] for c in fact.contributors] == ["203.0.113.10", "203.0.113.9"]


class TestFanout:
    def fold(self, *rows: IpsKeyedRow, state: S = S.COMPLETE):
        return fold_port_fanout(cell(D.PORT_FANOUT, list(rows), state))

    def test_the_largest_distinct_port_count_and_contributors(self) -> None:
        fact = self.fold(
            row(source_ip="203.0.113.5", distinct_ports=240),
            row(source_ip="203.0.113.6", distinct_ports=3),
        )
        assert fact.max_distinct_ports == 240
        assert fact.contributors == (("203.0.113.5", 240), ("203.0.113.6", 3))

    def test_contributors_are_capped_at_ten(self) -> None:
        rows = [row(source_ip=f"198.51.100.{i}", distinct_ports=100 - i) for i in range(1, 30)]
        assert len(self.fold(*rows).contributors) == 10

    def test_a_row_without_a_valid_address_or_count_is_ignored(self) -> None:
        fact = self.fold(
            row(source_ip="bogus", distinct_ports=500),
            row(source_ip="203.0.113.1", distinct_ports=None),
            row(source_ip="203.0.113.2", distinct_ports=4),
        )
        assert fact.max_distinct_ports == 4

    def test_empty_complete_is_measured_zero_and_unmeasured_is_none(self) -> None:
        assert self.fold().max_distinct_ports == 0
        assert self.fold(state=S.FAILED) is None

    def test_lower_bound_is_carried(self) -> None:
        fact = self.fold(row(source_ip="203.0.113.1", distinct_ports=9), state=S.LOWER_BOUND)
        assert fact.measurement is M.LOWER_BOUND


def mapped(**over: Any) -> IpsFieldMapping:
    custom = IpsFieldReference(IpsReferenceKind.CUSTOM_PROPERTY, "IPS_Action")
    n = lambda name: IpsFieldReference(IpsReferenceKind.NORMALIZED, name)  # noqa: E731
    base: dict[str, Any] = {
        "source_ip": n("sourceip"),
        "destination_ip": n("destinationip"),
        "destination_port": n("destinationport"),
        "signature": n("qid"),
        "action": custom,
        "blocked_actions": ("BLOCK", "Drop"),
        "allowed_actions": ("ALLOW",),
    }
    return IpsFieldMapping(**{**base, **over})


class TestActions:
    def fold(self, *rows: IpsKeyedRow, mapping: IpsFieldMapping | None = None, state=S.COMPLETE):
        return fold_actions(cell(D.ACTION, list(rows), state), mapping or mapped())

    def test_values_go_through_the_value_map_by_strip_and_casefold(self) -> None:
        fact = self.fold(
            row(10, action="block"),
            row(5, action=" DROP "),
            row(7, action="Allow"),
        )
        assert (fact.blocked_events, fact.allowed_events, fact.unmapped_events) == (15, 7, 0)

    def test_an_unmapped_value_never_counts_as_allowed(self) -> None:
        fact = self.fold(row(3, action="ALERT"), row(2, action=None), row(1, action=""))
        assert fact.allowed_events == 0 and fact.blocked_events == 0
        assert fact.unmapped_events == 6

    def test_a_profile_without_a_value_map_treats_everything_as_unmapped(self) -> None:
        fact = self.fold(
            row(9, action="BLOCK"),
            mapping=mapped(blocked_actions=(), allowed_actions=()),
        )
        assert (fact.blocked_events, fact.allowed_events, fact.unmapped_events) == (0, 0, 9)

    def test_a_profile_without_an_action_field_is_a_bug(self) -> None:
        with pytest.raises(IpsFoldError):
            self.fold(
                row(1, action="x"),
                mapping=mapped(action=None, blocked_actions=(), allowed_actions=()),
            )

    def test_empty_is_a_measured_zero_and_unmeasured_is_none(self) -> None:
        fact = self.fold()
        assert (fact.blocked_events, fact.allowed_events, fact.unmapped_events) == (0, 0, 0)
        assert self.fold(state=S.REJECTED) is None

    def test_lower_bound_is_carried(self) -> None:
        assert self.fold(row(1, action="BLOCK"), state=S.LOWER_BOUND).measurement is M.LOWER_BOUND


class TestTotalsAndCoverage:
    def totals(self, *rows: IpsKeyedRow, state: S = S.COMPLETE):
        return fold_totals(cell(D.TOTALS, list(rows), state))

    def test_totals_sum_events_and_records(self) -> None:
        fact = self.totals(row(40, ), row(60))
        assert (fact.events, fact.records) == (100, 2)

    def test_coverage_is_dimension_events_over_totals(self) -> None:
        fact = ports(row(80, destination_port=22, protocol_id=6))
        result = coverage(fact, TOTALS_100)
        assert result.ratio == 0.8 and result.lower_bound is False

    def test_coverage_is_none_when_totals_are_missing_failed_or_zero(self) -> None:
        fact = ports(row(80, destination_port=22, protocol_id=6))
        assert coverage(fact, None) is None
        for state in (S.FAILED, S.REJECTED, S.NOT_COLLECTED):
            assert coverage(fact, self.totals(state=state)) is None
        assert coverage(fact, self.totals()) is None  # measured zero totals: no denominator

    def test_coverage_is_none_when_the_dimension_is_missing(self) -> None:
        assert coverage(None, TOTALS_100) is None
        assert coverage(ports(state=S.FAILED), TOTALS_100) is None

    def test_a_measured_zero_dimension_is_coverage_zero_not_none(self) -> None:
        result = coverage(ports(), TOTALS_100)
        assert result is not None and result.ratio == 0.0

    def test_lower_bound_totals_give_no_coverage(self) -> None:
        fact = ports(row(80, destination_port=22, protocol_id=6))
        assert coverage(fact, self.totals(row(100), state=S.LOWER_BOUND)) is None

    def test_a_lower_bound_dimension_flags_the_coverage(self) -> None:
        fact = ports(row(50, destination_port=22, protocol_id=6), state=S.LOWER_BOUND)
        result = coverage(fact, TOTALS_100)
        assert result.ratio == 0.5 and result.lower_bound is True

    def test_source_and_action_dimensions_have_coverage(self) -> None:
        totals = TOTALS_100
        src = fold_source_ips(cell(D.SOURCE_IP, [row(25, source_ip="203.0.113.1")]))
        assert coverage(src, totals).ratio == 0.25
        act = fold_actions(cell(D.ACTION, [row(10, action="BLOCK"), row(5, action="x")]), mapped())
        assert coverage(act, totals).ratio == 0.15


class TestDispatch:
    def test_fold_outcome_routes_by_dimension(self) -> None:
        assert fold_outcome(cell(D.TOTALS, [row(5)]), max_entities=5).events == 5
        assert fold_outcome(cell(D.SOURCE_IP), max_entities=5).distinct_sources == 0
        assert fold_outcome(cell(D.PORT_FANOUT), max_entities=5).max_distinct_ports == 0
        assert fold_outcome(cell(D.DESTINATION_IP), max_entities=5).entities == ()
        assert fold_outcome(cell(D.ACTION), mapping=mapped(), max_entities=5).unmapped_events == 0

    def test_folding_actions_needs_the_mapping(self) -> None:
        with pytest.raises(IpsFoldError):
            fold_outcome(cell(D.ACTION), max_entities=5)

    @pytest.mark.parametrize("dimension", list(D))
    def test_every_dimension_folds_none_when_unmeasured(self, dimension: D) -> None:
        out = fold_outcome(cell(dimension, state=S.NOT_COLLECTED), mapping=mapped(), max_entities=5)
        assert out is None

    def test_a_dimension_mismatch_is_refused(self) -> None:
        with pytest.raises(IpsFoldError):
            fold_totals(cell(D.SOURCE_IP))
