"""Saved dashboards API (UR.5.5): RBAC matrix, bounds, concurrency, limits, default, audit."""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models.dashboard import Dashboard
from app.models.identity import AuditLog
from app.security.rbac import Principal
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run dashboard API integration tests",
    ),
]

BASE = "/api/v1/dashboards"
MISSING = "00000000-0000-0000-0000-000000000000"
NOTHING = Principal(subject="nobody", permissions=frozenset())
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ALICE = Principal(subject="alice", permissions=frozenset({"read:*", "dashboard:write"}))
BOB = Principal(subject="bob", permissions=frozenset({"read:*", "dashboard:write"}))
# Holds admin:dashboards without admin:*: may share a dashboard, may not edit another's.
ALICE_SHARING = Principal(
    subject="alice", permissions=frozenset({"read:*", "dashboard:write", "admin:dashboards"})
)
BOB_SHARING = Principal(
    subject="bob", permissions=frozenset({"read:*", "dashboard:write", "admin:dashboards"})
)
ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))


def widget(index: int = 0, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"w_{index:04d}", "type": "kpi.total_eps", "x": 0, "y": index, "w": 3, "h": 3,
        "range": None, "config": {},
    }
    base.update(over)
    return base


def layout(count: int = 1, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"v": 1, "range": "24h", "widgets": [widget(i) for i in range(count)]}
    base.update(over)
    return base


def create(client: Client, name: str = "Main", **over: Any) -> dict[str, Any]:
    response = client.post(BASE, json={"name": name, "layout": layout(), **over})
    assert response.status_code == 201, response.text
    return response.json()


def put(client: Client, dashboard: dict[str, Any], **over: Any):  # type: ignore[no-untyped-def]
    body = {
        "name": dashboard["name"],
        "layout": dashboard["layout"],
        "expected_revision": dashboard["revision"],
        **over,
    }
    return client.request("PUT", f"{BASE}/{dashboard['id']}", json=body)


def default_to(client: Client, value: str | None):  # type: ignore[no-untyped-def]
    return client.request("PUT", f"{BASE}/default", json={"dashboard_id": value})


def visibility(client: Client, dashboard: dict[str, Any], value: str):  # type: ignore[no-untyped-def]
    return client.patch(f"{BASE}/{dashboard['id']}", json={"visibility": value})


def share(owner: Client, dashboard: dict[str, Any]) -> None:
    """`owner` must hold admin:dashboards."""
    response = owner.patch(f"{BASE}/{dashboard['id']}", json={"visibility": "SHARED"})
    assert response.status_code == 200, response.text


def audit_rows() -> list[AuditLog]:
    async def _read(s):  # type: ignore[no-untyped-def]
        rows = await s.scalars(
            select(AuditLog)
            .where(AuditLog.action.like("dashboard.%"))
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        return list(rows)

    return run(_read)


def audit_actions() -> list[str]:
    return [row.action for row in audit_rows()]


@pytest.fixture(autouse=True)
def _schema(db_schema):  # type: ignore[no-untyped-def]
    return db_schema


class TestRbacMatrix:
    def test_a_principal_with_no_permission_is_refused_everywhere(self) -> None:
        client = Client(NOTHING)
        assert client.get(BASE).status_code == 403
        assert client.get(f"{BASE}/{MISSING}").status_code == 403
        assert client.post(BASE, json={"name": "x", "layout": layout()}).status_code == 403
        assert default_to(client, None).status_code == 403

    def test_a_read_only_principal_reads_and_cannot_write(self) -> None:
        owner = create(Client(ALICE))
        reader = Client(READER)
        assert reader.get(BASE).status_code == 200
        # A PRIVATE dashboard is another owner's: 404, not 403. Share it first.
        sharing = Client(ALICE_SHARING)
        share(sharing, owner)
        assert reader.get(f"{BASE}/{owner['id']}").status_code == 200
        target = f"{BASE}/{owner['id']}"
        assert reader.post(BASE, json={"name": "x", "layout": layout()}).status_code == 403
        assert put(reader, owner).status_code == 403
        assert reader.patch(target, json={"name": "y"}).status_code == 403
        assert reader.delete(target).status_code == 403
        assert reader.post(f"{target}/duplicate").status_code == 403
        assert reader.request(
            "PUT", f"{BASE}/default", json={"dashboard_id": owner["id"]}
        ).status_code == 403
        assert audit_actions() == ["dashboard.create", "dashboard.share"]

    def test_an_owner_who_lost_dashboard_write_cannot_change_or_delete_their_own(self) -> None:
        made = create(Client(ALICE))
        demoted = Client(Principal(subject="alice", permissions=frozenset({"read:*"})))
        target = f"{BASE}/{made['id']}"
        assert demoted.get(target).status_code == 200
        assert put(demoted, made).status_code == 403
        assert demoted.patch(target, json={"name": "y"}).status_code == 403
        assert demoted.delete(target).status_code == 403
        assert demoted.post(f"{target}/duplicate").status_code == 403
        assert default_to(demoted, made["id"]).status_code == 403
        assert Client(ALICE).get(target).status_code == 200
        assert audit_actions() == ["dashboard.create"]

    def test_an_owner_writes_every_route(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        assert alice.get(f"{BASE}/{made['id']}").status_code == 200
        assert put(alice, made, name="Renamed").status_code == 200
        assert alice.patch(f"{BASE}/{made['id']}", json={"description": "d"}).status_code == 200
        copy = alice.post(f"{BASE}/{made['id']}/duplicate")
        assert copy.status_code == 201
        assert alice.request(
            "PUT", f"{BASE}/default", json={"dashboard_id": made["id"]}
        ).status_code == 200
        assert alice.delete(f"{BASE}/{copy.json()['id']}").status_code == 204

    def test_another_owners_private_dashboard_is_404_on_every_route(self) -> None:
        made = create(Client(ALICE))
        bob = Client(BOB)
        target = f"{BASE}/{made['id']}"
        assert bob.get(target).status_code == 404
        assert put(bob, made).status_code == 404
        assert bob.patch(target, json={"name": "y"}).status_code == 404
        assert bob.delete(target).status_code == 404
        assert bob.post(f"{target}/duplicate").status_code == 404
        assert default_to(bob, made["id"]).status_code == 404
        assert bob.get(BASE).json()["items"] == []
        # Not even an administrator reads someone's PRIVATE dashboard.
        assert Client(ADMIN).get(target).status_code == 404
        assert audit_actions() == ["dashboard.create"]

    def test_a_non_owner_reads_and_duplicates_a_shared_dashboard_but_cannot_change_it(self) -> None:
        made = create(Client(ALICE))
        share(Client(ALICE_SHARING), made)
        bob = Client(BOB)
        target = f"{BASE}/{made['id']}"
        assert bob.get(target).json()["owner_subject"] == "alice"
        assert [i["id"] for i in bob.get(BASE).json()["items"]] == [made["id"]]
        shared = bob.get(target).json()
        assert put(bob, shared).status_code == 403
        assert bob.patch(target, json={"name": "y"}).status_code == 403
        assert bob.delete(target).status_code == 403
        copy = bob.post(f"{target}/duplicate")
        assert copy.status_code == 201
        assert copy.json()["owner_subject"] == "bob" and copy.json()["visibility"] == "PRIVATE"
        # Nothing changed, and the refusals left no success row.
        assert audit_actions() == ["dashboard.create", "dashboard.share", "dashboard.duplicate"]
        assert Client(ALICE).get(target).json()["revision"] == 2

    def test_an_administrator_edits_and_deletes_a_shared_dashboard_of_another_owner(self) -> None:
        made = create(Client(ALICE))
        share(Client(ALICE_SHARING), made)
        admin = Client(ADMIN)
        current = admin.get(f"{BASE}/{made['id']}").json()
        assert put(admin, current, name="By admin").status_code == 200
        assert visibility(admin, made, "PRIVATE").status_code == 200
        # It is now PRIVATE and alice's: even the administrator no longer sees it.
        assert admin.delete(f"{BASE}/{made['id']}").status_code == 404
        assert Client(ALICE).delete(f"{BASE}/{made['id']}").status_code == 204

    def test_admin_dashboards_alone_does_not_edit_another_owners_dashboard(self) -> None:
        made = create(Client(ALICE))
        share(Client(ALICE_SHARING), made)
        sharer = Client(BOB_SHARING)
        assert visibility(sharer, made, "PRIVATE").status_code == 403

    def test_any_change_of_visibility_needs_admin_dashboards(self) -> None:
        made = create(Client(ALICE))
        target = f"{BASE}/{made['id']}"
        assert Client(ALICE).patch(target, json={"visibility": "SHARED"}).status_code == 403
        share(Client(ALICE_SHARING), made)
        # Taking it back is a change of visibility too.
        assert Client(ALICE).patch(target, json={"visibility": "PRIVATE"}).status_code == 403
        assert visibility(Client(ALICE_SHARING), made, "PRIVATE").status_code == 200
        # Restating the current visibility is not a change.
        assert Client(ALICE).patch(target, json={"visibility": "PRIVATE"}).status_code == 200

    def test_a_missing_dashboard_is_404(self) -> None:
        alice = Client(ALICE)
        assert alice.get(f"{BASE}/{MISSING}").status_code == 404
        assert alice.delete(f"{BASE}/{MISSING}").status_code == 404
        assert alice.post(f"{BASE}/{MISSING}/duplicate").status_code == 404
        assert alice.patch(f"{BASE}/{MISSING}", json={"name": "x"}).status_code == 404
        body = {"name": "x", "layout": layout(), "expected_revision": 1}
        assert alice.request("PUT", f"{BASE}/{MISSING}", json=body).status_code == 404


class TestListAndRead:
    def test_list_fields_order_paging_and_no_layout_body(self) -> None:
        alice = Client(ALICE)
        first = create(alice, "First", template_key="soc_overview", template_version=2)
        create(alice, "Second")
        put(alice, {**first, "layout": layout(3)})
        body = alice.get(BASE).json()
        assert [i["name"] for i in body["items"]] == ["First", "Second"]  # updated_at descending
        item = body["items"][0]
        assert set(item) == {
            "id", "name", "owner_subject", "visibility", "template_key", "updated_at",
            "widget_count", "is_default",
        }
        assert item["widget_count"] == 3 and item["template_key"] == "soc_overview"
        assert body["total"] == 2 and body["limit"] == 50 and body["offset"] == 0
        page = alice.get(BASE, params={"limit": 1, "offset": 1}).json()
        assert [i["name"] for i in page["items"]] == ["Second"] and page["total"] == 2
        assert alice.get(BASE, params={"limit": 201}).status_code == 422
        assert alice.get(BASE, params={"limit": 0}).status_code == 422

    def test_get_returns_the_layout_and_revision(self) -> None:
        alice = Client(ALICE)
        made = create(alice, description="notes")
        got = alice.get(f"{BASE}/{made['id']}").json()
        assert got["layout"] == layout() and got["revision"] == 1 and got["schema_version"] == 1
        assert got["description"] == "notes" and got["updated_by"] == "alice"

    def test_list_is_one_constant_cost_query_set(self) -> None:
        alice = Client(ALICE)
        for index in range(5):
            create(alice, f"d{index}")
        alice.get(BASE)
        few = alice.statements
        for index in range(5, 12):
            create(alice, f"d{index}")
        alice.get(BASE)
        assert alice.statements == few


class TestLayoutBounds:
    def post_layout(self, value: dict[str, Any]):  # type: ignore[no-untyped-def]
        return Client(ALICE).post(BASE, json={"name": f"n{uuid.uuid4().hex[:8]}", "layout": value})

    def test_24_widgets_are_stored_and_25_are_refused(self) -> None:
        assert self.post_layout(layout(24)).status_code == 201
        assert self.post_layout(layout(25)).status_code == 422

    @pytest.mark.parametrize(
        ("over", "ok"),
        [
            ({"x": 11, "w": 1}, True), ({"x": 12, "w": 1}, False), ({"x": -1}, False),
            ({"x": 0, "w": 12}, True), ({"w": 13}, False), ({"w": 0}, False),
            ({"x": 6, "w": 6}, True), ({"x": 6, "w": 7}, False),
            ({"y": -1}, False), ({"h": 24}, True), ({"h": 25}, False), ({"h": 0}, False),
            ({"id": "w_abcd"}, True), ({"id": "w_abc"}, False),
            ({"id": "w_abcdefghijkl"}, True), ({"id": "w_abcdefghijklm"}, False),
            ({"type": "a" * 64}, True), ({"type": "a" * 65}, False), ({"type": "A"}, False),
            ({"range": "7d"}, True), ({"range": "30d"}, False),
        ],
    )
    def test_each_widget_bound(self, over: dict[str, Any], ok: bool) -> None:
        status = self.post_layout(layout(0, widgets=[widget(0, **over)])).status_code
        assert status == (201 if ok else 422)

    def test_duplicate_widget_ids_are_refused(self) -> None:
        both = layout(0, widgets=[widget(0, id="w_same"), widget(1, id="w_same")])
        assert self.post_layout(both).status_code == 422

    def test_config_at_4_kb_and_one_byte_past(self) -> None:
        def sized(size: int) -> dict[str, Any]:
            return {"k": "a" * (size - len('{"k":""}'))}

        fits = layout(0, widgets=[widget(0, config=sized(4096))])
        past = layout(0, widgets=[widget(0, config=sized(4097))])
        assert self.post_layout(fits).status_code == 201
        assert self.post_layout(past).status_code == 422

    def test_the_whole_layout_is_at_most_64_kb(self) -> None:
        big = {"k": "a" * (4096 - len('{"k":""}'))}
        fits = layout(0, widgets=[widget(i, config=big) for i in range(15)])
        too_big = layout(0, widgets=[widget(i, config=big) for i in range(16)])
        assert self.post_layout(fits).status_code == 201
        assert self.post_layout(too_big).status_code == 422

    def test_unknown_keys_are_refused_at_both_levels(self) -> None:
        assert self.post_layout({**layout(), "extra": 1}).status_code == 422
        assert self.post_layout(layout(0, widgets=[widget(0, extra=1)])).status_code == 422
        response = Client(ALICE).post(BASE, json={"name": "x", "layout": layout(), "owner": "z"})
        assert response.status_code == 422

    def test_a_dashboard_range_outside_the_three_is_refused(self) -> None:
        assert self.post_layout(layout(range="30d")).status_code == 422

    def test_an_unknown_layout_version_is_refused(self) -> None:
        assert self.post_layout(layout(v=2)).status_code == 422
        assert self.post_layout(layout(v=0)).status_code == 422
        made = create(Client(ALICE))
        assert put(Client(ALICE), made, layout=layout(v=2)).status_code == 422

    def test_an_unknown_widget_type_is_stored_and_returned_unchanged(self) -> None:
        alice = Client(ALICE)
        exotic = widget(0, type="future.hologram", config={"nested": {"a": [1, 2, {"b": None}]}})
        made = alice.post(BASE, json={"name": "x", "layout": layout(0, widgets=[exotic])}).json()
        assert made["layout"]["widgets"] == [exotic]
        assert alice.get(f"{BASE}/{made['id']}").json()["layout"]["widgets"] == [exotic]

    def test_a_name_over_80_characters_is_refused_and_80_is_stored(self) -> None:
        alice = Client(ALICE)
        assert alice.post(BASE, json={"name": "n" * 81, "layout": layout()}).status_code == 422
        assert alice.post(BASE, json={"name": "n" * 80, "layout": layout()}).status_code == 201
        assert alice.post(BASE, json={"name": "  ", "layout": layout()}).status_code == 422


class TestConcurrency:
    def test_the_second_of_two_writers_gets_409_with_the_current_revision(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        first = put(alice, made, layout=layout(2))
        assert first.status_code == 200 and first.json()["revision"] == 2
        second = put(Client(ALICE), made, layout=layout(3))  # still holds revision 1
        assert second.status_code == 409
        assert second.json()["detail"]["current_revision"] == 2
        stored = alice.get(f"{BASE}/{made['id']}").json()
        assert len(stored["layout"]["widgets"]) == 2 and stored["revision"] == 2
        assert audit_actions() == ["dashboard.create", "dashboard.update"]

    def test_a_stale_revision_is_refused_even_when_it_is_in_the_future(self) -> None:
        made = create(Client(ALICE))
        assert put(Client(ALICE), {**made, "revision": 5}).status_code == 409

    def test_put_and_patch_each_increment_the_revision(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        updated = put(alice, made, description="d").json()
        assert updated["revision"] == 2
        patched = alice.patch(f"{BASE}/{made['id']}", json={"name": "Other"}).json()
        assert patched["revision"] == 3 and patched["updated_by"] == "alice"

    def test_the_replace_is_one_conditional_update_statement(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        put(alice, made)
        # one visibility read, one UPDATE .. RETURNING, one default lookup, one audit insert
        assert alice.statements <= 6

    def test_put_replaces_name_description_and_layout_only(self) -> None:
        alice = Client(ALICE)
        made = create(alice, template_key="soc_overview", template_version=1)
        done = put(alice, made, name="New", description=None, layout=layout(2)).json()
        assert done["name"] == "New" and len(done["layout"]["widgets"]) == 2
        assert done["template_key"] == "soc_overview" and done["created_at"] == made["created_at"]

    def test_put_requires_expected_revision(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        body = {"name": "x", "layout": layout()}
        assert alice.request("PUT", f"{BASE}/{made['id']}", json=body).status_code == 422


class TestLimitsAndNames:
    def test_the_21st_dashboard_is_409_naming_the_limit(self) -> None:
        alice = Client(ALICE)
        for index in range(20):
            create(alice, f"d{index}")
        refused = alice.post(BASE, json={"name": "one-too-many", "layout": layout()})
        assert refused.status_code == 409 and "20" in refused.json()["detail"]
        first = alice.get(BASE).json()["items"][0]
        assert alice.post(f"{BASE}/{first['id']}/duplicate").status_code == 409
        # The limit is per owner.
        assert Client(BOB).post(BASE, json={"name": "mine", "layout": layout()}).status_code == 201
        assert len(audit_actions()) == 21

    def test_a_duplicate_name_for_the_same_owner_is_409_and_not_across_owners(self) -> None:
        alice = Client(ALICE)
        made = create(alice, "Main")
        again = alice.post(BASE, json={"name": "Main", "layout": layout()})
        assert again.status_code == 409 and "Main" in again.json()["detail"]
        assert Client(BOB).post(BASE, json={"name": "Main", "layout": layout()}).status_code == 201
        other = create(alice, "Other")
        assert put(alice, other, name="Main").status_code == 409
        assert alice.patch(f"{BASE}/{other['id']}", json={"name": "Main"}).status_code == 409
        assert alice.get(f"{BASE}/{made['id']}").json()["revision"] == 1

    def test_duplicate_names_the_copy_and_numbers_further_copies(self) -> None:
        alice = Client(ALICE)
        made = create(alice, "Main")
        names = [alice.post(f"{BASE}/{made['id']}/duplicate").json()["name"] for _ in range(3)]
        assert names == ["Copy of Main", "Copy of Main (2)", "Copy of Main (3)"]

    def test_duplicate_truncates_so_the_whole_name_fits_80_characters(self) -> None:
        alice = Client(ALICE)
        made = create(alice, "n" * 80)
        names = [alice.post(f"{BASE}/{made['id']}/duplicate").json()["name"] for _ in range(2)]
        assert all(len(name) == 80 for name in names) and names[0] != names[1]
        assert names[1].endswith(" (2)")

    def test_duplicate_copies_the_layout_and_starts_a_new_history(self) -> None:
        alice = Client(ALICE)
        made = create(alice, "Main", description="d", template_key="soc_overview")
        copy = alice.post(f"{BASE}/{made['id']}/duplicate").json()
        assert copy["layout"] == made["layout"] and copy["revision"] == 1
        assert copy["id"] != made["id"] and copy["description"] == "d"
        assert copy["template_key"] == "soc_overview" and copy["is_default"] is False


class TestDefault:
    def put_default(self, client: Client, value: str | None):  # type: ignore[no-untyped-def]
        return client.request("PUT", f"{BASE}/default", json={"dashboard_id": value})

    def default_ids(self, client: Client) -> list[str]:
        return [i["id"] for i in client.get(BASE).json()["items"] if i["is_default"]]

    def test_default_is_not_captured_by_the_id_route(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        response = self.put_default(alice, made["id"])
        assert response.status_code == 200 and response.json() == {"dashboard_id": made["id"]}
        # The id route has no PUT body of this shape: it would be a 422 on the path or body.
        assert alice.request("PUT", f"{BASE}/default", json={}).status_code == 422
        assert alice.get(f"{BASE}/default").status_code == 422

    def test_set_clear_and_replace(self) -> None:
        alice = Client(ALICE)
        one, two = create(alice, "One"), create(alice, "Two")
        assert self.default_ids(alice) == []
        self.put_default(alice, one["id"])
        assert self.default_ids(alice) == [one["id"]]
        assert alice.get(f"{BASE}/{one['id']}").json()["is_default"] is True
        self.put_default(alice, two["id"])
        assert self.default_ids(alice) == [two["id"]]
        assert self.put_default(alice, None).json() == {"dashboard_id": None}
        assert self.default_ids(alice) == []

    def test_a_default_must_be_readable_by_the_caller(self) -> None:
        mine = create(Client(ALICE))
        assert self.put_default(Client(BOB), mine["id"]).status_code == 404
        assert self.put_default(Client(BOB), MISSING).status_code == 404
        share(Client(ALICE_SHARING), mine)
        assert self.put_default(Client(BOB), mine["id"]).status_code == 200

    def test_deleting_the_dashboard_clears_the_default(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        self.put_default(alice, made["id"])
        assert alice.delete(f"{BASE}/{made['id']}").status_code == 204
        assert self.default_ids(alice) == []
        assert alice.get(f"{BASE}/{made['id']}").status_code == 404

        async def _pref(s):  # type: ignore[no-untyped-def]
            from app.models.dashboard import UserDashboardPref

            return (await s.get(UserDashboardPref, "alice")).default_dashboard_id

        assert run(_pref) is None

    def test_a_default_that_turned_private_reads_as_no_default(self) -> None:
        made = create(Client(ALICE))
        share(Client(ALICE_SHARING), made)
        bob = Client(BOB)
        self.put_default(bob, made["id"])
        assert self.default_ids(bob) == [made["id"]]
        Client(ALICE_SHARING).patch(f"{BASE}/{made['id']}", json={"visibility": "PRIVATE"})
        assert self.default_ids(bob) == []

    def test_one_users_default_does_not_show_for_another(self) -> None:
        made = create(Client(ALICE))
        share(Client(ALICE_SHARING), made)
        self.put_default(Client(ALICE), made["id"])
        assert self.default_ids(Client(BOB)) == []

    def test_deleting_clears_every_users_default_to_it(self) -> None:
        made = create(Client(ALICE))
        share(Client(ALICE_SHARING), made)
        bob = Client(BOB)
        self.put_default(bob, made["id"])
        Client(ALICE).delete(f"{BASE}/{made['id']}")
        assert self.default_ids(bob) == []


class TestAudit:
    def test_each_write_records_its_action_and_detail_without_the_layout(self) -> None:
        alice = Client(ALICE)
        made = create(alice, "Main")
        renamed = put(alice, made, name="Main2", layout=layout(2)).json()
        alice.patch(f"{BASE}/{made['id']}", json={"name": "Main3"})
        alice.patch(f"{BASE}/{made['id']}", json={"description": "d"})
        share(Client(ALICE_SHARING), made)
        copy = alice.post(f"{BASE}/{made['id']}/duplicate").json()
        alice.request("PUT", f"{BASE}/default", json={"dashboard_id": made["id"]})
        alice.request("PUT", f"{BASE}/default", json={"dashboard_id": None})
        alice.delete(f"{BASE}/{copy['id']}")
        rows = audit_rows()
        assert [row.action for row in rows] == [
            "dashboard.create", "dashboard.update", "dashboard.rename", "dashboard.rename",
            "dashboard.update", "dashboard.share", "dashboard.duplicate",
            "dashboard.set_default", "dashboard.set_default", "dashboard.delete",
        ]
        assert renamed["revision"] == 2
        assert all(row.actor in {"alice"} for row in rows)
        assert all(row.object_type == "dashboard" and row.outcome == "SUCCESS" for row in rows)
        assert rows[0].object_id == made["id"]
        assert rows[0].detail == {"name": "Main", "revision": 1, "widget_count": 1}
        assert rows[1].detail == {"name": "Main2", "revision": 2, "widget_count": 2}
        assert rows[2].detail == rows[1].detail
        assert rows[3].detail["revision"] == 3
        assert rows[6].object_id == copy["id"] and rows[6].detail["name"] == "Copy of Main3"
        assert rows[8].object_id is None
        assert rows[9].object_id == copy["id"]
        for row in rows:
            assert "layout" not in row.detail and "widgets" not in str(row.detail)

    def test_a_put_that_renames_and_changes_the_layout_records_update_and_rename(self) -> None:
        alice = Client(ALICE)
        made = create(alice, "Main")
        put(alice, made, name="Main2", layout=layout(2))
        put(alice, alice.get(f"{BASE}/{made['id']}").json(), layout=layout(3))
        assert audit_actions() == [
            "dashboard.create", "dashboard.update", "dashboard.rename", "dashboard.update",
        ]

    def test_a_refused_write_leaves_no_row(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        Client(READER).post(BASE, json={"name": "x", "layout": layout()})
        Client(BOB).delete(f"{BASE}/{made['id']}")
        put(alice, {**made, "revision": 9})
        alice.post(BASE, json={"name": made["name"], "layout": layout()})
        alice.post(BASE, json={"name": "bad", "layout": layout(25)})
        assert audit_actions() == ["dashboard.create"]

    def test_reads_are_not_audited(self) -> None:
        alice = Client(ALICE)
        made = create(alice)
        alice.get(BASE)
        alice.get(f"{BASE}/{made['id']}")
        assert audit_actions() == ["dashboard.create"]


def test_the_stored_row_matches_the_response() -> None:
    alice = Client(ALICE)
    made = create(alice, "Main")

    async def _row(s):  # type: ignore[no-untyped-def]
        return await s.get(Dashboard, uuid.UUID(made["id"]))

    row = run(_row)
    assert row.owner_subject == "alice" and row.visibility == "PRIVATE"
    assert row.schema_version == 1 and row.layout == layout()
