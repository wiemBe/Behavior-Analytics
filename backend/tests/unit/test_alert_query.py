"""The alert list's filter vocabulary: escaping, validation and family mapping."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy import select

from app.alerts.query import (
    FAMILY_BY_SOURCE_TYPE,
    MAX_Q_LENGTH,
    AlertFamily,
    AlertFilters,
    AlertSort,
    SortOrder,
    alert_family,
    escape_like,
    normalise_q,
)
from app.api.routes.suppressions import _ALERT_FAMILY, _BREAKTHROUGH_SOURCE_TYPES
from app.identity.engine import ACTOR
from app.models.alert import Alert
from app.models.email import EMAIL_ALERT_TYPES, EMAIL_RECIPIENT_ALERT_TYPES
from app.models.enums import AnomalyFamily, Severity
from app.schemas.notification_rule import FamilyName
from app.services.digest import DigestItem, _family_alerting
from app.services.email_templates import _SAMPLE_BY_TYPE, ALERT_TYPE_LABELS
from app.services.reports import _FAMILY_TABLES, ENGINE_ACTORS, RETENTION_SETTING


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("volume", "volume"),
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("%_\\", "\\%\\_\\\\"),
    ],
)
def test_escape_like_neutralises_every_metacharacter(raw: str, escaped: str) -> None:
    assert escape_like(raw) == escaped


def test_normalise_q_keeps_the_text_and_drops_surrounding_space() -> None:
    assert normalise_q(None) is None
    assert normalise_q("  volume drop ") == "volume drop"
    assert normalise_q("x" * MAX_Q_LENGTH) == "x" * MAX_Q_LENGTH


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_normalise_q_refuses_a_blank_search(blank: str) -> None:
    with pytest.raises(ValueError, match="blank"):
        normalise_q(blank)


def test_normalise_q_refuses_more_than_the_bound() -> None:
    with pytest.raises(ValueError, match="at most"):
        normalise_q("x" * (MAX_Q_LENGTH + 1))


@pytest.mark.parametrize(
    ("source_type", "family"),
    [
        ("log_source", AlertFamily.LOG_SOURCE),
        ("waf_endpoint", AlertFamily.WAF),
        ("ips_entity", AlertFamily.IPS),
        ("ips_source", AlertFamily.IPS),
        ("pw_value", AlertFamily.PROPERTY_WATCH),
        ("catchall_sender", AlertFamily.CATCHALL_SENDER),
        ("custom_rule", AlertFamily.CUSTOM_RULE),
        ("scheduled_search", AlertFamily.SCHEDULED_SEARCH),
        ("qradar_instance", AlertFamily.QRADAR_INSTANCE),
        ("identity_source", AlertFamily.IDENTITY),
    ],
)
def test_alert_family_maps_every_producer(source_type: str, family: AlertFamily) -> None:
    assert alert_family(source_type) == family


def test_alert_family_invents_nothing_for_an_unknown_producer() -> None:
    assert alert_family("something_new") is None


def test_the_family_map_covers_exactly_the_alert_producers_the_app_sends() -> None:
    """The email allowlist and the family map name the same producers.

    When a producer is added to one list and not the other, a family filter
    would silently drop its alerts. Pinning the two lists together makes that a
    failing test instead.

    ``digest`` (SOC-17c) belongs to the allowlist as a mail template key, not as
    an alert producer: it has no ``alert.source_type`` and no family, so it is
    excluded here rather than mapped to a made-up family.
    """
    assert {
        alert_family(source_type)
        for source_type in EMAIL_ALERT_TYPES
        if source_type != "digest"
    } == set(AlertFamily)


def test_no_filter_and_the_default_order_keep_the_previous_query() -> None:
    """The default order is still `opened_at desc, id desc`, and nothing else runs."""
    assert AlertFilters().clauses() == []
    default = [str(part) for part in AlertFilters().order_by(AlertSort.CREATED_AT, SortOrder.DESC)]
    assert default == [
        str(Alert.opened_at.desc()),
        str(Alert.id.desc()),
    ]
    assert str(select(Alert).order_by(*AlertFilters().order_by(
        AlertSort.CREATED_AT, SortOrder.DESC
    ))) == str(
        select(Alert).order_by(Alert.opened_at.desc(), Alert.id.desc())
    )


def test_every_filter_contributes_exactly_one_clause() -> None:
    import uuid
    from datetime import UTC, datetime

    from app.models.enums import AlertStatus

    filters = AlertFilters(
        status=AlertStatus.OPEN,
        severities=(Severity.HIGH,),
        log_source_ids=(uuid.uuid4(),),
        family=AlertFamily.WAF,
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 1, 2, tzinfo=UTC),
        title_contains="drop",
    )
    assert len(filters.clauses()) == 7


def _module_strings(tree: ast.AST) -> dict[str, str]:
    """Module-level string assignments, so ``source_type=SOURCE_TYPE`` resolves."""
    found: dict[str, str] = {}
    if not isinstance(tree, ast.Module):
        return found
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _literals(node: ast.AST, constants: dict[str, str]) -> list[str] | None:
    """Fixed strings this expression produces, or None when it copies a value."""
    if isinstance(node, ast.IfExp):
        body = _literals(node.body, constants)
        orelse = _literals(node.orelse, constants)
        if body is None or orelse is None:
            return None
        return [*body, *orelse]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in constants:
        return [constants[node.id]]
    return None


def _source_type_value(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg == "source_type":
                return keyword.value
        return None
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "source_type":
                return value
    return None


def _produced_source_types() -> tuple[set[str], set[tuple[str, str]]]:
    """Producers are keyword arguments and dict keys, including module constants.

    A comparison such as ``Alert.source_type == "log_source"`` is a reader and
    is not collected. A value that is not a string in this file is a passthrough
    of an alert the caller already built.
    """
    root = Path(__file__).resolve().parents[2] / "app"
    produced: set[str] = set()
    unresolved: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _module_strings(tree)
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            value = _source_type_value(node)
            if value is None:
                continue
            literals = _literals(value, constants)
            if literals is None:
                unresolved.add((relative, ast.unparse(value)))
            else:
                produced.update(literals)
    return produced, unresolved


#: Copies of an alert's existing type. A new producer written as one of these
#: expressions fails this test until it is a literal or a module string.
_SOURCE_TYPE_PASSTHROUGH = frozenset({
    ("alerts/service.py", "data.source_type"),
    ("alerts/notifiers/webhook_v1.py", "source_type"),
})

#: `ips_source` opens alerts and has no e-mail template key. `digest` is a
#: template key and not a producer. `analytics_rule` has a label and still uses
#: the generic preview sample.
_TEMPLATE_ONLY = frozenset({"digest"})
_PRODUCER_WITHOUT_TEMPLATE = frozenset({"ips_source"})
_SAMPLE_WITHOUT_OWN_TEXT = frozenset({"digest", "analytics_rule"})


def test_every_produced_source_type_is_on_every_source_type_map() -> None:
    """The next family fails here when one catalogue omits its source type.

    The scan is the pipeline, not the catalogues: a type that is only added to
    a map, or only written as ``source_type=SOURCE_TYPE``, still has to appear
    on the others.
    """
    produced, unresolved = _produced_source_types()
    assert "identity_source" in produced
    assert unresolved == _SOURCE_TYPE_PASSTHROUGH
    assert produced == set(FAMILY_BY_SOURCE_TYPE)
    assert set(EMAIL_ALERT_TYPES) - _TEMPLATE_ONLY == produced - _PRODUCER_WITHOUT_TEMPLATE
    assert set(EMAIL_RECIPIENT_ALERT_TYPES) == set(EMAIL_ALERT_TYPES) - _TEMPLATE_ONLY
    assert set(ALERT_TYPE_LABELS) == set(EMAIL_ALERT_TYPES)
    assert set(_SAMPLE_BY_TYPE) == set(EMAIL_ALERT_TYPES) - _SAMPLE_WITHOUT_OWN_TEXT
    anomaly_values = {member.value for member in AnomalyFamily}
    anomaly_producers = {
        source_type
        for source_type, family in FAMILY_BY_SOURCE_TYPE.items()
        if family.value in anomaly_values
    }
    assert set(_ALERT_FAMILY) == anomaly_producers
    assert set(_BREAKTHROUGH_SOURCE_TYPES) == set(AnomalyFamily)
    covered = {
        source_type
        for group in _BREAKTHROUGH_SOURCE_TYPES.values()
        for source_type in group
    }
    assert covered == set(_ALERT_FAMILY)
    assert {family.value for family in FAMILY_BY_SOURCE_TYPE.values()} <= set(get_args(FamilyName))
    assert {family for family, _model, _detector in _FAMILY_TABLES} == set(AnomalyFamily)
    assert set(RETENTION_SETTING) == set(AnomalyFamily)
    assert ACTOR in ENGINE_ACTORS


def _digest_item(family: AnomalyFamily) -> DigestItem:
    when = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    return DigestItem(
        family=family,
        source_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
        source_name="dc-01",
        source_class=None,
        detector="SOME_DETECTOR",
        count=1,
        severity=Severity.HIGH,
        worst_ratio=8.0,
        first_bucket=when,
        last_bucket=when,
    )


def test_every_anomaly_family_has_a_digest_link_that_names_its_own_subject() -> None:
    """Identity links to its alerts. Any other family that falls through to a
    property-watch link fails, and an unknown family has no link at all."""
    source = "00000000-0000-0000-0000-0000000000aa"
    for family in AnomalyFamily:
        link = _digest_item(family).link("https://soc.example/")
        if family is AnomalyFamily.IDENTITY:
            assert link == (
                f"https://soc.example/alerts?family=identity&log_source_id={source}"
            )
            assert "anomaly_type=" not in link
            assert "username" not in link
        else:
            assert link.startswith("https://soc.example/anomalies?")
        if family is AnomalyFamily.PROPERTY_WATCH:
            assert "property_watch_id=" in link
        else:
            assert "property_watch_id=" not in link
    rogue = _digest_item(AnomalyFamily.LOG_SOURCE)
    object.__setattr__(rogue, "family", "future")
    with pytest.raises(ValueError, match="no subject"):
        rogue.link("https://soc.example")


def test_opt_in_families_do_not_fall_through_to_phase_a_digest_alerting() -> None:
    """Phase A is the only family whose digest is on without a switch. A new
    family that returns True here would be mailed while still in shadow mode."""
    settings = SimpleNamespace(
        waf_alerting_enabled=False,
        ips_alerting_enabled=False,
        pw_alerting_enabled=False,
        rule_alerting_enabled=False,
        identity_alerting_enabled=False,
    )
    assert _family_alerting(settings, AnomalyFamily.LOG_SOURCE) is True
    for family in AnomalyFamily:
        if family is AnomalyFamily.LOG_SOURCE:
            continue
        assert _family_alerting(settings, family) is False, family
