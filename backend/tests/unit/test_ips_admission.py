"""IPS entity admission uses the shared deterministic admission policy."""

from __future__ import annotations

from app.collectors.ips_collector import _Candidate
from app.ips.fold import IpsEntityFact
from app.models.enums import IpsDimension
from app.waf.admission import AdmissionPolicy, decide_admission


def candidate(value_key: str, events: int) -> _Candidate:
    return _Candidate(
        IpsEntityFact(value_key=value_key, events=events),
        IpsDimension.SIGNATURE,
    )


def test_ips_admission_enforces_threshold_and_new_entity_quota() -> None:
    policy = AdmissionPolicy(max_new_per_bucket=2, max_active=20, min_requests=3)
    decision = decide_admission(
        [
            candidate("qid:3", 2),
            candidate("qid:2", 8),
            candidate("qid:1", 10),
            candidate("qid:4", 7),
            candidate("qid:5", 3),
        ],
        active_count=0,
        policy=policy,
    )

    assert [item.endpoint for item in decision.admitted] == ["qid:1", "qid:2"]
    assert [item.endpoint for item in decision.below_threshold] == ["qid:3"]
    assert [item.endpoint for item in decision.over_quota] == ["qid:4", "qid:5"]
    assert decision.untracked_requests == 12


def test_ips_admission_active_ceiling_can_refuse_every_unseen_entity() -> None:
    policy = AdmissionPolicy(max_new_per_bucket=20, max_active=2, min_requests=1)
    decision = decide_admission(
        [candidate("qid:1", 10), candidate("qid:2", 9)],
        active_count=2,
        policy=policy,
    )

    assert decision.quota == 0
    assert decision.admitted == []
    assert [item.endpoint for item in decision.over_quota] == ["qid:1", "qid:2"]


def test_ips_admission_order_is_independent_of_arrival_order() -> None:
    policy = AdmissionPolicy(max_new_per_bucket=2, max_active=20, min_requests=1)
    ordered = [candidate("qid:3", 10), candidate("qid:1", 10), candidate("qid:2", 10)]

    forward = decide_admission(ordered, active_count=0, policy=policy)
    reverse = decide_admission(list(reversed(ordered)), active_count=0, policy=policy)

    assert [item.endpoint for item in forward.admitted] == ["qid:1", "qid:2"]
    assert [item.endpoint for item in reverse.admitted] == ["qid:1", "qid:2"]
