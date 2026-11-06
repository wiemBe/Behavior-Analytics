"""Template and recipient administration (MAIL-1) through the API, with a database.

Access control, the sanitised round trip, the shipped fallback, reset by DELETE,
recipient validation and audit rows. DB-gated.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from app.models.email import EmailRecipient, EmailTemplate
from app.models.identity import AuditLog
from app.services import email_templates as tpl
from tests.integration.test_fleet_api import ADMIN, NOTHING, VIEWER, Client, audit_rows, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

BASE = "/api/v1/admin/email"
MISSING = "00000000-0000-0000-0000-000000000000"
TEMPLATE = {
    "subject": "[{{ alert.severity }}]   {{ alert.title }}",
    "html_body": '<h1 onclick="x()" style="color: #123456; position: fixed">{{ alert.title }}</h1>'
    "<script>alert(1)</script>",
    "text_body": "{{ alert.title }}",
    "enabled": True,
}


def _rows(model):  # type: ignore[no-untyped-def]
    async def _q(session):  # type: ignore[no-untyped-def]
        return list((await session.scalars(select(model))).all())

    return run(_q)


def test_everything_needs_admin_notifications_and_nothing_is_written_without_it(
    db_schema,
) -> None:
    for principal in (VIEWER, NOTHING):
        client = Client(principal)
        assert client.get(f"{BASE}/templates").status_code == 403
        assert client.get(f"{BASE}/recipients").status_code == 403
        assert client.request("PUT", f"{BASE}/templates/default", json=TEMPLATE).status_code == 403
        assert client.delete(f"{BASE}/templates/default").status_code == 403
        assert client.post(
            f"{BASE}/recipients", json={"address": "a@example.org"}
        ).status_code == 403
    assert _rows(EmailTemplate) == [] and _rows(EmailRecipient) == []
    assert audit_rows("email_template.put") == []


def test_the_template_list_covers_the_default_and_every_type(db_schema) -> None:
    body = Client(ADMIN).get(f"{BASE}/templates").json()
    assert [t["key"] for t in body["templates"]] == [
        "default", "log_source", "waf_endpoint", "ips_entity", "custom_rule", "pw_value",
        "catchall_sender", "qradar_instance", "scheduled_search", "digest",
    ]
    assert all(t["customised"] is False for t in body["templates"])
    assert [t["key"] for t in body["alert_types"]] == [t["key"] for t in body["templates"]][1:]


def test_a_type_without_a_row_shows_the_shipped_default_and_says_so(db_schema) -> None:
    body = Client(ADMIN).get(f"{BASE}/templates/default").json()
    assert body["customised"] is False and body["effective_source"] == "shipped"
    assert body["subject"] == tpl.SHIPPED_SUBJECT
    assert body["html_body"] == tpl.SHIPPED_HTML and body["text_body"] == tpl.SHIPPED_TEXT
    typed = Client(ADMIN).get(f"{BASE}/templates/waf_endpoint").json()
    assert typed["effective_source"] == "shipped" and typed["label"] == "WAF endpoint"
    assert Client(ADMIN).get(f"{BASE}/templates/not_a_type").status_code == 422


def test_save_sanitises_returns_the_stored_text_and_audits(db_schema) -> None:
    admin = Client(ADMIN)
    saved = admin.request("PUT", f"{BASE}/templates/default", json=TEMPLATE)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["html_body"] == '<h1 style="color: #123456">{{ alert.title }}</h1>'
    assert body["subject"] == "[{{ alert.severity }}] {{ alert.title }}"
    assert body["customised"] is True and body["effective_source"] == "own"
    assert body["updated_by"] == "admin" and body["updated_at"]
    [row] = _rows(EmailTemplate)
    assert row.alert_type is None and row.html_body == body["html_body"]
    assert "alert(1)" not in row.html_body and "onclick" not in row.html_body
    assert admin.get(f"{BASE}/templates/default").json()["html_body"] == body["html_body"]

    [entry] = audit_rows("email_template.put")
    assert entry.actor == "admin" and entry.object_id == "default"
    assert entry.detail["before"] is None
    assert entry.detail["after"]["html_length"] == len(row.html_body)
    assert "<h1" not in str(entry.detail)  # sizes, not the markup

    again = admin.request("PUT", f"{BASE}/templates/default", json={**TEMPLATE, "subject": "S2"})
    assert again.status_code == 200
    assert len(_rows(EmailTemplate)) == 1
    [_, second] = sorted(audit_rows("email_template.put"), key=lambda r: r.created_at)
    assert second.detail["before"]["subject"] == "[{{ alert.severity }}] {{ alert.title }}"


def test_save_refuses_an_unknown_placeholder_naming_it_and_stores_nothing(db_schema) -> None:
    response = Client(ADMIN).request(
        "PUT", f"{BASE}/templates/log_source",
        json={**TEMPLATE, "subject": "{{ alert.titel }}", "text_body": "{{ nope }}"},
    )
    assert response.status_code == 422
    assert response.json()["detail_codes"] == [
        {"code": "email.template.unknown_placeholder", "params": {"name": "alert.titel"}},
        {"code": "email.template.unknown_placeholder", "params": {"name": "nope"}},
    ]
    assert _rows(EmailTemplate) == []
    assert audit_rows("email_template.put") == []


def test_save_refuses_an_unknown_type_an_empty_body_and_an_unknown_field(db_schema) -> None:
    admin = Client(ADMIN)
    assert admin.request("PUT", f"{BASE}/templates/made_up", json=TEMPLATE).status_code == 422
    assert admin.request(
        "PUT", f"{BASE}/templates/default", json={**TEMPLATE, "html_body": "<script>x</script>"}
    ).status_code == 422
    assert admin.request(
        "PUT", f"{BASE}/templates/default", json={**TEMPLATE, "html_body": ""}
    ).status_code == 422
    assert admin.request(
        "PUT", f"{BASE}/templates/default", json={**TEMPLATE, "alert_type": "log_source"}
    ).status_code == 422
    assert admin.request(
        "PUT", f"{BASE}/templates/default", json={**TEMPLATE, "html_body": "x" * 100_001}
    ).status_code == 422
    assert _rows(EmailTemplate) == []


def test_a_type_template_beats_the_default_in_the_api_view_and_delete_resets(db_schema) -> None:
    admin = Client(ADMIN)
    admin.request("PUT", f"{BASE}/templates/default", json={**TEMPLATE, "subject": "DEFAULT"})
    assert admin.get(f"{BASE}/templates/ips_entity").json()["effective_source"] == "default"
    assert admin.get(f"{BASE}/templates/ips_entity").json()["subject"] == "DEFAULT"
    admin.request("PUT", f"{BASE}/templates/ips_entity", json={**TEMPLATE, "subject": "OWN"})
    typed = admin.get(f"{BASE}/templates/ips_entity").json()
    assert typed["effective_source"] == "own" and typed["subject"] == "OWN"

    off = admin.request(
        "PUT", f"{BASE}/templates/ips_entity", json={**TEMPLATE, "subject": "OWN", "enabled": False}
    ).json()
    assert off["customised"] is True and off["enabled"] is False
    assert off["effective_source"] == "default"
    summary = {t["key"]: t for t in admin.get(f"{BASE}/templates").json()["templates"]}
    assert summary["ips_entity"]["customised"] and not summary["ips_entity"]["enabled"]

    assert admin.delete(f"{BASE}/templates/ips_entity").status_code == 204
    assert admin.delete(f"{BASE}/templates/default").status_code == 204
    assert _rows(EmailTemplate) == []
    assert admin.get(f"{BASE}/templates/default").json()["effective_source"] == "shipped"
    assert admin.delete(f"{BASE}/templates/default").status_code == 404
    assert sorted(e.object_id for e in audit_rows("email_template.delete")) == [
        "default", "ips_entity"
    ]


# --- recipients -------------------------------------------------------------------------


def test_recipient_crud_normalises_validates_and_audits(db_schema, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "env1@example.org, env2@example.org")
    admin = Client(ADMIN)
    created = admin.post(f"{BASE}/recipients", json={
        "address": "  SOC@Example.org ", "min_severity": "HIGH",
        "alert_types": ["waf_endpoint", "log_source", "waf_endpoint"],
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["address"] == "soc@example.org" and body["min_severity"] == "HIGH"
    assert body["alert_types"] == ["log_source", "waf_endpoint"] and body["enabled"] is True
    rid = body["id"]

    listed = admin.get(f"{BASE}/recipients").json()
    assert [r["address"] for r in listed["recipients"]] == ["soc@example.org"]
    assert listed["from_env"] == ["env1@example.org", "env2@example.org"]

    dup = admin.post(f"{BASE}/recipients", json={"address": "SOC@example.org"})
    assert dup.status_code == 409
    assert dup.json()["detail_code"] == "email.recipient.duplicate"
    assert dup.json()["detail_params"] == {"address": "soc@example.org"}

    updated = admin.request("PUT", f"{BASE}/recipients/{rid}", json={
        "address": "soc@example.org", "min_severity": "LOW", "alert_types": None, "enabled": False,
    })
    assert updated.status_code == 200
    assert updated.json()["alert_types"] is None and updated.json()["enabled"] is False

    other = admin.post(f"{BASE}/recipients", json={"address": "other@example.org"}).json()
    clash = admin.request(
        "PUT", f"{BASE}/recipients/{other['id']}", json={"address": "soc@example.org"}
    )
    assert clash.status_code == 409

    assert admin.request("PUT", f"{BASE}/recipients/{MISSING}", json={"address": "x@example.org"}
                         ).status_code == 404
    assert admin.delete(f"{BASE}/recipients/{MISSING}").status_code == 404
    assert admin.delete(f"{BASE}/recipients/{rid}").status_code == 204
    assert [r.address for r in _rows(EmailRecipient)] == ["other@example.org"]

    assert len(audit_rows("email_recipient.create")) == 2
    assert len(audit_rows("email_recipient.update")) == 1
    [deleted] = audit_rows("email_recipient.delete")
    assert deleted.detail["before"]["address"] == "soc@example.org"
    assert deleted.detail["after"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"address": "not-an-address"},
        {"address": "Name <a@example.org>"},
        {"address": "a@example.org\r\nBcc: b@example.org"},
        {"address": "a@example.org, b@example.org"},
        {"address": "a@example.org", "min_severity": "URGENT"},
        {"address": "a@example.org", "alert_types": ["made_up"]},
        {"address": "a@example.org", "alert_types": []},
        {"address": "a@example.org", "extra": 1},
    ],
)
def test_recipient_validation_refuses_and_stores_nothing(db_schema, payload: dict) -> None:
    assert Client(ADMIN).post(f"{BASE}/recipients", json=payload).status_code == 422
    assert _rows(EmailRecipient) == []


def test_every_write_leaves_exactly_one_audit_row_per_action(db_schema) -> None:
    admin = Client(ADMIN)
    admin.request("PUT", f"{BASE}/templates/default", json=TEMPLATE)
    admin.post(f"{BASE}/recipients", json={"address": "a@example.org"})
    admin.delete(f"{BASE}/templates/default")

    async def _q(session):  # type: ignore[no-untyped-def]
        rows = await session.scalars(select(AuditLog.action).where(AuditLog.action.like("email%")))
        return sorted(rows.all())

    assert run(_q) == ["email_recipient.create", "email_template.delete", "email_template.put"]


def test_a_random_uuid_for_a_recipient_is_a_404_not_a_500(db_schema) -> None:
    assert Client(ADMIN).delete(f"{BASE}/recipients/{uuid.uuid4()}").status_code == 404


def test_the_digest_template_key_never_falls_back_to_the_alert_default(db_schema) -> None:
    """SOC-17c: the digest owns a body of its own, even in the admin view."""
    admin = Client(ADMIN)
    admin.request(
        "PUT", f"{BASE}/templates/default", json={**TEMPLATE, "subject": "ALERT DEFAULT"}
    )

    body = admin.get(f"{BASE}/templates/digest").json()
    assert body["effective_source"] == "shipped"
    assert body["subject"] == tpl.SHIPPED_DIGEST_SUBJECT
    assert body["html_body"] == tpl.SHIPPED_DIGEST_HTML
    assert body["text_body"] == tpl.SHIPPED_DIGEST_TEXT

    own = admin.request(
        "PUT",
        f"{BASE}/templates/digest",
        json={
            "subject": "Digest {{ instance.name }}",
            "html_body": "<p>own digest</p>",
            "text_body": "own digest",
            "enabled": True,
        },
    )
    assert own.status_code == 200
    body = admin.get(f"{BASE}/templates/digest").json()
    assert body["effective_source"] == "own"
    assert body["subject"] == "Digest {{ instance.name }}"

    # A disabled digest row falls back to the shipped digest, never the alert default.
    admin.request(
        "PUT",
        f"{BASE}/templates/digest",
        json={
            "subject": "Disabled digest",
            "html_body": "<p>x</p>",
            "text_body": None,
            "enabled": False,
        },
    )
    body = admin.get(f"{BASE}/templates/digest").json()
    assert body["effective_source"] == "shipped"
    assert body["subject"] == tpl.SHIPPED_DIGEST_SUBJECT
