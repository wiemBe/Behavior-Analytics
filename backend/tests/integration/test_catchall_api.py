"""Catch-all senders API: reads, ordering, review and dismiss with audit. DB-gated."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.alerts.fingerprint import catchall_sender_fingerprint
from app.alerts.service import AlertInput, AlertService
from app.models.alert import Alert
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.enums import AlertStatus, Severity
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run catch-all API integration tests",
    ),
]

VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
EDITOR = Principal(subject="editor", permissions=frozenset({"read:*", "log_source:write"}))
NOTHING = Principal(subject="nobody", permissions=frozenset())
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
MISSING = "00000000-0000-0000-0000-000000000000"
BASE = "/api/v1/fleet/catchall"


def sender(inst, source, name: str, *, status="REVIEWED", events=None, **kw) -> CatchallSender:  # type: ignore[no-untyped-def]
    return CatchallSender(
        instance_id=inst.id,
        catchall_log_source_id=source.id,
        identifier=name,
        first_seen_at=NOW - timedelta(days=1),
        last_seen_at=NOW,
        events_24h=events,
        active=True,
        review_status=status,
        **kw,
    )


def bucket(source, at: datetime, **kw) -> CatchallBucket:  # type: ignore[no-untyped-def]
    values = dict(
        bucket_seconds=300, origin="LIVE", events=10, records=2, distinct_senders=1,
        distinct_senders_lower_bound=False, measurement="EXACT",
        top_senders=[{"identifier": "a", "events": 10}], admitted=1,
        below_admission_threshold=0, rejected_over_quota=0, untracked_senders=0,
        untracked_events=0, query_provenance={"split_depth": 0}, collected_at=NOW,
    )
    values.update(kw)
    return CatchallBucket(catchall_log_source_id=source.id, bucket_start=at, **values)


@pytest.fixture
def seeded(db_schema):  # type: ignore[no-untyped-def]
    async def _seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        one = await make_log_source(s, inst, qradar_id=1, name="SIM Generic", type_id=67)
        two = await make_log_source(s, inst, qradar_id=2, name="Universal LEEF", type_id=39)
        rows = [
            sender(inst, one, "busy-reviewed", events=900),
            sender(inst, one, "quiet-reviewed", events=5),
            sender(inst, one, "new-quiet", status="NEW", events=7),
            sender(inst, one, "new-busy", status="NEW", events=300),
            sender(inst, one, "new-unknown", status="NEW", events=None),
            sender(inst, two, "dismissed-one", status="DISMISSED", events=40),
            sender(inst, two, "50%_literal", events=2),
            sender(inst, two, "50xyliteral", events=3),
        ]
        s.add_all(rows)
        for index in range(4):
            s.add(bucket(one, NOW + timedelta(minutes=5 * index)))
        s.add(bucket(two, NOW, events=1))
        await s.flush()
        return {
            "inst": inst.id,
            "one": one.id,
            "two": two.id,
            "senders": {r.identifier: r.id for r in rows},
        }

    return run(_seed)


class TestAuth:
    def test_a_principal_without_read_fleet_is_forbidden(self, seeded) -> None:
        client = Client(NOTHING)
        assert client.get(f"{BASE}/senders").status_code == 403
        assert client.get(
            f"{BASE}/buckets", params={"catchall_log_source_id": seeded["one"]}
        ).status_code == 403

    def test_a_reader_cannot_review_or_dismiss(self, seeded) -> None:
        client = Client(VIEWER)
        target = seeded["senders"]["new-busy"]
        assert client.post(f"{BASE}/senders/{target}/review").status_code == 403
        assert client.post(f"{BASE}/senders/{target}/dismiss").status_code == 403

        async def status(s):  # type: ignore[no-untyped-def]
            return (await s.get(CatchallSender, target)).review_status

        assert run(status) == "NEW"

    def test_a_reader_may_read(self, seeded) -> None:
        assert Client(VIEWER).get(f"{BASE}/senders").status_code == 200


class TestSenders:
    def test_new_first_then_events_24h_descending_with_unknown_last(self, seeded) -> None:
        body = Client(VIEWER).get(f"{BASE}/senders").json()
        names = [item["identifier"] for item in body["items"]]
        assert names == [
            "new-busy", "new-quiet", "new-unknown",
            "busy-reviewed", "dismissed-one", "quiet-reviewed", "50xyliteral", "50%_literal",
        ]
        assert body["total"] == 8 and body["limit"] == 50 and body["offset"] == 0
        first = body["items"][0]
        assert first["catchall_log_source_name"] == "SIM Generic"
        assert first["events_24h"] == 300 and first["review_status"] == "NEW"
        assert body["items"][2]["events_24h"] is None  # unknown, never zero

    def test_filters(self, seeded) -> None:
        client = Client(VIEWER)
        new = client.get(f"{BASE}/senders", params={"review_status": "NEW"}).json()
        assert {i["identifier"] for i in new["items"]} == {
            "new-busy", "new-quiet", "new-unknown"
        }
        assert new["total"] == 3
        two = client.get(
            f"{BASE}/senders", params={"catchall_log_source_id": seeded["two"]}
        ).json()
        assert {i["identifier"] for i in two["items"]} == {
            "dismissed-one", "50%_literal", "50xyliteral"
        }
        both = client.get(
            f"{BASE}/senders",
            params={"catchall_log_source_id": seeded["two"], "review_status": "DISMISSED"},
        ).json()
        assert [i["identifier"] for i in both["items"]] == ["dismissed-one"]

    def test_q_is_a_literal_substring_case_insensitive(self, seeded) -> None:
        client = Client(VIEWER)

        def names(q: str) -> list[str]:
            body = client.get(f"{BASE}/senders", params={"q": q}).json()
            return sorted(i["identifier"] for i in body["items"])

        assert names("%") == ["50%_literal"]
        assert names("_") == ["50%_literal"]
        assert names("0%_") == ["50%_literal"]
        assert names("50") == ["50%_literal", "50xyliteral"]
        assert names("NEW-B") == ["new-busy"]
        assert names("\\") == []
        assert names("nothing-like-this") == []

    def test_paging(self, seeded) -> None:
        client = Client(VIEWER)
        page = client.get(f"{BASE}/senders", params={"limit": 3, "offset": 2}).json()
        assert [i["identifier"] for i in page["items"]] == [
            "new-unknown", "busy-reviewed", "dismissed-one"
        ]
        assert page["total"] == 8 and page["limit"] == 3 and page["offset"] == 2
        past = client.get(f"{BASE}/senders", params={"offset": 100}).json()
        assert past["items"] == [] and past["total"] == 8

    @pytest.mark.parametrize(
        "params",
        [
            {"review_status": "MAYBE"},
            {"catchall_log_source_id": "not-a-uuid"},
            {"limit": 0},
            {"limit": 1001},
            {"offset": -1},
            {"q": ""},
            {"q": "x" * 256},
        ],
    )
    def test_bad_parameters_are_422(self, seeded, params) -> None:
        assert Client(VIEWER).get(f"{BASE}/senders", params=params).status_code == 422

    def test_the_page_size_is_clamped_to_the_setting(self, seeded) -> None:
        body = Client(VIEWER).get(f"{BASE}/senders", params={"limit": 1000}).json()
        assert body["limit"] == 200


class TestBuckets:
    def _get(self, client, source, since, until):  # type: ignore[no-untyped-def]
        return client.get(
            f"{BASE}/buckets",
            params={
                "catchall_log_source_id": source,
                "since": since.isoformat(),
                "until": until.isoformat(),
            },
        )

    def test_buckets_in_the_half_open_range_oldest_first(self, seeded) -> None:
        resp = self._get(Client(VIEWER), seeded["one"], NOW, NOW + timedelta(minutes=15))
        assert resp.status_code == 200
        body = resp.json()
        starts = [b["bucket_start"] for b in body["items"]]
        assert len(starts) == 3 and starts == sorted(starts)
        assert body["catchall_log_source_id"] == str(seeded["one"])
        item = body["items"][0]
        assert item["measurement"] == "EXACT" and item["distinct_senders_lower_bound"] is False
        assert item["top_senders"] == [{"identifier": "a", "events": 10}]
        assert item["query_provenance"] == {"split_depth": 0}
        # Another source's bucket is not in it.
        assert all(b["events"] == 10 for b in body["items"])

    def test_a_range_with_no_bucket_is_empty_not_zeroed(self, seeded) -> None:
        resp = self._get(
            Client(VIEWER), seeded["one"], NOW + timedelta(days=1), NOW + timedelta(days=2)
        )
        assert resp.status_code == 200 and resp.json()["items"] == []

    def test_the_range_is_bounded(self, seeded) -> None:
        client = Client(VIEWER)
        ok = self._get(client, seeded["one"], NOW, NOW + timedelta(days=7))
        assert ok.status_code == 200
        too_long = self._get(client, seeded["one"], NOW, NOW + timedelta(days=7, seconds=1))
        assert too_long.status_code == 422

    def test_an_empty_or_inverted_range_is_422(self, seeded) -> None:
        client = Client(VIEWER)
        assert self._get(client, seeded["one"], NOW, NOW).status_code == 422
        assert self._get(client, seeded["one"], NOW, NOW - timedelta(hours=1)).status_code == 422

    def test_a_naive_time_is_422(self, seeded) -> None:
        resp = Client(VIEWER).get(
            f"{BASE}/buckets",
            params={
                "catchall_log_source_id": seeded["one"],
                "since": "2026-10-13T10:00:00",
                "until": "2026-10-13T11:00:00",
            },
        )
        assert resp.status_code == 422

    def test_missing_parameters_and_bad_ids_are_422(self, seeded) -> None:
        client = Client(VIEWER)
        assert client.get(f"{BASE}/buckets").status_code == 422
        assert client.get(
            f"{BASE}/buckets", params={"catchall_log_source_id": seeded["one"]}
        ).status_code == 422
        assert client.get(
            f"{BASE}/buckets",
            params={"catchall_log_source_id": "x", "since": NOW.isoformat(),
                    "until": (NOW + timedelta(hours=1)).isoformat()},
        ).status_code == 422

    def test_an_unknown_source_is_404(self, seeded) -> None:
        resp = self._get(Client(VIEWER), MISSING, NOW, NOW + timedelta(hours=1))
        assert resp.status_code == 404


def audits(action: str) -> list[AuditLog]:
    async def _q(s):  # type: ignore[no-untyped-def]
        return list((await s.scalars(select(AuditLog).where(AuditLog.action == action))).all())

    return run(_q)


class TestReview:
    def test_review_marks_reviewed_and_audits_with_the_principal(self, seeded) -> None:
        target = seeded["senders"]["new-busy"]
        resp = Client(EDITOR).post(f"{BASE}/senders/{target}/review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["review_status"] == "REVIEWED" and body["reviewed_by"] == "editor"
        assert body["reviewed_at"] is not None and body["identifier"] == "new-busy"
        (entry,) = audits("catchall_sender.review")
        assert entry.actor == "editor" and entry.object_id == str(target)
        assert entry.object_type == "catchall_sender"
        assert entry.detail["from"] == "NEW" and entry.detail["to"] == "REVIEWED"

        async def stored(s):  # type: ignore[no-untyped-def]
            row = await s.get(CatchallSender, target)
            return row.review_status, row.reviewed_by

        assert run(stored) == ("REVIEWED", "editor")

    def test_dismiss_marks_dismissed_and_audits(self, seeded) -> None:
        target = seeded["senders"]["new-quiet"]
        resp = Client(EDITOR).post(f"{BASE}/senders/{target}/dismiss")
        assert resp.status_code == 200 and resp.json()["review_status"] == "DISMISSED"
        (entry,) = audits("catchall_sender.dismiss")
        assert entry.actor == "editor" and entry.detail["to"] == "DISMISSED"
        assert audits("catchall_sender.review") == []

    def test_a_repeat_is_idempotent_with_no_second_audit_row(self, seeded) -> None:
        target = seeded["senders"]["new-busy"]
        client = Client(EDITOR)
        first = client.post(f"{BASE}/senders/{target}/review").json()
        again = client.post(f"{BASE}/senders/{target}/review").json()
        assert again == first
        assert len(audits("catchall_sender.review")) == 1

    def test_a_dismissed_sender_can_be_reviewed_and_the_reverse(self, seeded) -> None:
        target = seeded["senders"]["dismissed-one"]
        client = Client(EDITOR)
        assert client.post(f"{BASE}/senders/{target}/review").json()["review_status"] == "REVIEWED"
        assert (
            client.post(f"{BASE}/senders/{target}/dismiss").json()["review_status"] == "DISMISSED"
        )
        assert len(audits("catchall_sender.review")) == 1
        assert len(audits("catchall_sender.dismiss")) == 1

    def test_reviewed_senders_leave_the_new_queue(self, seeded) -> None:
        client = Client(EDITOR)
        client.post(f"{BASE}/senders/{seeded['senders']['new-busy']}/review")
        client.post(f"{BASE}/senders/{seeded['senders']['new-quiet']}/dismiss")
        new = client.get(f"{BASE}/senders", params={"review_status": "NEW"}).json()
        assert [i["identifier"] for i in new["items"]] == ["new-unknown"]

    def test_unknown_sender_is_404_and_a_bad_id_is_422(self, seeded) -> None:
        client = Client(EDITOR)
        assert client.post(f"{BASE}/senders/{MISSING}/review").status_code == 404
        assert client.post(f"{BASE}/senders/{MISSING}/dismiss").status_code == 404
        assert client.post(f"{BASE}/senders/nope/review").status_code == 422
        assert audits("catchall_sender.review") == []

    def test_a_missing_permission_is_checked_before_the_lookup_result_leaks(
        self, seeded
    ) -> None:
        # 403, not 404: a reader learns nothing about which ids exist.
        assert Client(VIEWER).post(f"{BASE}/senders/{MISSING}/review").status_code == 403

    def test_review_resolves_the_new_sender_alert(self, seeded) -> None:
        target = seeded["senders"]["new-busy"]

        async def open_alert(s):  # type: ignore[no-untyped-def]
            fp = catchall_sender_fingerprint(seeded["inst"], seeded["one"], "new-busy")
            await AlertService(s, clock=lambda: NOW).apply_bulk(
                [AlertInput(fingerprint=fp, title="New sender", severity=Severity.LOW,
                            source_type="catchall_sender", source_id=target)],
                [],
            )

        run(open_alert)
        assert Client(EDITOR).post(f"{BASE}/senders/{target}/dismiss").status_code == 200

        async def alert(s):  # type: ignore[no-untyped-def]
            return (await s.scalar(select(Alert))).status

        assert run(alert) == AlertStatus.RESOLVED
        (entry,) = audits("catchall_sender.dismiss")
        assert entry.detail["alert_resolved"] is True

    def test_review_without_an_alert_still_works(self, seeded) -> None:
        target = seeded["senders"]["new-busy"]
        Client(EDITOR).post(f"{BASE}/senders/{target}/review")
        (entry,) = audits("catchall_sender.review")
        assert entry.detail["alert_resolved"] is False


class TestSources:
    def test_lists_each_catchall_source_with_its_latest_bucket_and_new_count(
        self, seeded
    ) -> None:
        async def other(s):  # type: ignore[no-untyped-def]
            inst = await s.get(QRadarInstance, seeded["inst"])
            await make_log_source(s, inst, qradar_id=3, name="Firewall", type_id=5)
            await make_log_source(s, inst, qradar_id=4, name="Never collected", type_id=367)

        run(other)
        body = Client(VIEWER).get(f"{BASE}/sources").json()
        assert body["catchall_type_ids"] == [39, 67, 212, 367, 4000]
        rows = {r["name"]: r for r in body["items"]}
        assert set(rows) == {"SIM Generic", "Universal LEEF", "Never collected"}
        one = rows["SIM Generic"]
        assert one["id"] == str(seeded["one"]) and one["new_sender_count"] == 3
        assert one["latest_bucket_start"].startswith("2026-10-13T10:15:00")
        assert rows["Universal LEEF"]["new_sender_count"] == 0
        assert rows["Universal LEEF"]["latest_bucket_start"].startswith("2026-10-13T10:00:00")
        # Never measured is null, not a time and not zero buckets.
        assert rows["Never collected"]["latest_bucket_start"] is None
        assert [r["name"] for r in body["items"]] == sorted(rows)

    def test_the_new_count_follows_review(self, seeded) -> None:
        client = Client(EDITOR)
        client.post(f"{BASE}/senders/{seeded['senders']['new-busy']}/review")
        rows = {r["name"]: r for r in client.get(f"{BASE}/sources").json()["items"]}
        assert rows["SIM Generic"]["new_sender_count"] == 2

    def test_an_inventory_without_catchall_sources_is_empty_but_names_the_types(
        self, db_schema
    ) -> None:
        async def _seed(s):  # type: ignore[no-untyped-def]
            await make_log_source(s, await make_instance(s), qradar_id=1, name="Firewall",
                                  type_id=5)

        run(_seed)
        body = Client(VIEWER).get(f"{BASE}/sources").json()
        assert body["items"] == [] and body["catchall_type_ids"]

    def test_needs_read_fleet(self, seeded) -> None:
        assert Client(NOTHING).get(f"{BASE}/sources").status_code == 403

    def test_statement_count_is_constant_at_two_sizes(self, db_schema) -> None:
        counts = {}
        total = 0
        for size in (2, 12):
            total += size

            async def _seed(s, size=size):  # type: ignore[no-untyped-def]
                inst = await make_instance(s)
                for n in range(size):
                    src = await make_log_source(
                        s, inst, qradar_id=100 + n, name=f"catch-{size}-{n}", type_id=67
                    )
                    s.add(sender(inst, src, f"host-{n}", status="NEW"))
                    s.add(bucket(src, NOW))

            run(_seed)
            client = Client(VIEWER)
            body = client.get(f"{BASE}/sources").json()
            assert len(body["items"]) == total
            counts[size] = client.statements
        assert counts[2] == counts[12] and counts[2] <= 2


def test_the_ids_in_the_fixture_are_unique() -> None:
    assert uuid.uuid4() != uuid.uuid4()
