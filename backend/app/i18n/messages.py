"""Registry and English renderer for backend-authored analyst messages.

The API keeps the rendered English sentence and adds a stable code plus raw
parameters. English clients keep the sentence byte for byte; another locale
may render the code and parameters through its catalog.

Formatting styles are deliberately closed, and they shape the backend's English render only:
the frontend never renders English from params, and in Turkish it groups every count and
every number (``1.204``, ``1.260,0``) whatever the style. Counts accept ``grouped`` (the
default) or ``plain`` (no grouping; it shapes the backend's English render only, the
frontend groups every count in the reader's locale); numbers accept ``general`` (the
default), ``grouped``, ``fixed`` or ``repr`` (Python's ``str()`` of the number, which keeps
a trailing ``.0``); points accept ``signed`` (the default) or ``unsigned``; lists accept
``comma`` (the default), ``and`` or ``semicolon`` (``"; "``, for a list of clauses that carry
commas); durations accept ``timedelta`` (Python's ``str()`` of a ``timedelta``, ``0:30:00`` or
``1 day, 2:00:00``, for a sentence that always printed it so) or ``age`` (unbounded hours plus
minutes and seconds, as the custom-rule stale reason printed them); timestamps accept the default
(date, second, ``UTC``), ``minute`` (date, minute, ``UTC``) or ``iso``
(``datetime.isoformat()``, for a sentence that always embedded it); enums accept ``lower`` (the
value in lowercase, for a sentence that always embedded it so; the frontend labels the value from
an enum group whose labels are lowercase). Percent precision, fixed-number precision, and point
precision are declared separately. Ref and text params have one spelling and therefore no
alternate style.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from string import Formatter

from app.core.text import percent, points

CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class MessageRegistryError(ValueError):
    """The static message registry is internally inconsistent."""


class MessageRenderError(ValueError):
    """A message reference cannot be rendered against the registry."""


class ParamKind(StrEnum):
    COUNT = "count"
    NUMBER = "number"
    PERCENT = "percent"
    POINTS = "points"
    DURATION = "duration"
    TIMESTAMP = "timestamp"
    ENUM = "enum"
    LIST = "list"
    REF = "ref"
    TEXT = "text"


@dataclass(frozen=True)
class ParamSpec:
    """One template parameter and the information both renderers need."""

    kind: ParamKind
    enum_group: str | None = None
    ref_codes: tuple[str, ...] = ()
    style: str | None = None
    precision: int | None = None

    def to_artifact(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind.value}
        if self.enum_group is not None:
            out["enum_group"] = self.enum_group
        if self.ref_codes:
            out["ref_codes"] = list(self.ref_codes)
        if self.style is not None:
            out["style"] = self.style
        if self.precision is not None:
            out["precision"] = self.precision
        return out


@dataclass(frozen=True)
class MessageSpec:
    """One stable code, its legacy English template, and representative data."""

    code: str
    template: str
    params: Mapping[str, ParamSpec]
    sample: Mapping[str, object]


@dataclass(frozen=True)
class MessageRef:
    """A code and raw semantic values that can be rendered or sent on the wire."""

    code: str
    params: Mapping[str, object] = field(default_factory=dict)

    def to_wire(self) -> dict[str, object]:
        return _to_wire(self, REGISTRY)


def _fixed(code: str, template: str) -> MessageSpec:
    """A sentence with no parameters."""
    return MessageSpec(code=code, template=template, params={}, sample={})


def _text(code: str, template: str, **samples: str) -> MessageSpec:
    """A sentence whose parameters are all identifiers or names, shown verbatim."""
    return MessageSpec(
        code=code,
        template=template,
        params={name: ParamSpec(ParamKind.TEXT) for name in samples},
        sample=samples,
    )


def _mixed(
    code: str,
    template: str,
    *,
    text: Mapping[str, str] | None = None,
    count: Mapping[str, int] | None = None,
    **extra: tuple[ParamSpec, object],
) -> MessageSpec:
    """A sentence of text parameters (names, quoted user values), plain counts and extras."""
    params: dict[str, ParamSpec] = {}
    sample: dict[str, object] = {}
    for name, value in (text or {}).items():
        params[name] = ParamSpec(ParamKind.TEXT)
        sample[name] = value
    for name, number in (count or {}).items():
        params[name] = ParamSpec(ParamKind.COUNT, style="plain")
        sample[name] = number
    for name, (spec, example) in extra.items():
        params[name] = spec
        sample[name] = example
    return MessageSpec(code=code, template=template, params=params, sample=sample)


_PW_ACTIONS = (
    "propertywatch.action.change_pins",
    "propertywatch.action.change_scope",
    "propertywatch.action.unpin",
    "propertywatch.action.delete",
)

_RH_LABELS = (
    "rule_health.label.named",
    "rule_health.label.building_block",
    "rule_health.label.log_source_type",
)

# Shorthands of the MSG-4b block (property-watch sentences). Counts print without grouping,
# as the f-strings they replace did; fixed-point numbers keep their `:.Nf` precision.
_PLAIN = ParamSpec(ParamKind.COUNT, style="plain")
_FIXED0 = ParamSpec(ParamKind.NUMBER, style="fixed", precision=0)
_FIXED1 = ParamSpec(ParamKind.NUMBER, style="fixed", precision=1)
_FIXED2 = ParamSpec(ParamKind.NUMBER, style="fixed", precision=2)
_REPR = ParamSpec(ParamKind.NUMBER, style="repr")
_TEXT = ParamSpec(ParamKind.TEXT)
_ISO = ParamSpec(ParamKind.TIMESTAMP, style="iso")
_PERCENT = ParamSpec(ParamKind.PERCENT)

_PW_AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

_PW_REFERENCE_REASONS = (
    "pw.reference.normalized_not_allowed",
    "pw.reference.name_invalid",
    "pw.reference.kind_unknown",
    "pw.reference.not_an_object",
    "pw.reference.name_not_text",
    "pw.reference.pin_not_text",
    "pw.reference.pin_empty",
    "pw.reference.pin_length",
    "pw.reference.pin_characters",
)

_PW_SCOPE_REASONS = (
    "pw.scope.source_missing",
    "pw.scope.names_no_type",
    "pw.scope.type_empty",
    "pw.scope.names_no_group",
    "pw.scope.group_missing",
    "pw.scope.group_stale",
    "pw.scope.group_empty",
    "pw.scope.too_large",
)

_PW_HISTORY_CHECKS = (
    "pw.history_check.not_divisible",
    "pw.history_check.bucket_never_collected",
    "pw.history_check.record_count",
    "pw.history_check.bucket_width",
    "pw.history_check.bucket_not_collected",
    "pw.history_check.discovery_truncated",
    "pw.history_check.admission_truncated",
    "pw.history_check.bucket_version",
    "pw.history_check.bucket_coverage",
    "pw.history_check.first_bucket_never_collected",
    "pw.history_check.outside_window",
    "pw.history_check.incomplete",
)

#: The sentences a guard can wrap in "...; <scope>, so a normal verdict cannot be concluded".
_PW_NOT_PROVEN = (
    "pw.detector.spike_min_events",
    "pw.detector.ratio_not_reached",
    "pw.detector.spike_delta_below_min",
    "pw.detector.z_not_reached_high",
    "pw.detector.mad_within_p95",
    "pw.detector.drop_median_floor",
    "pw.detector.drop_ratio_above",
    "pw.detector.drop_delta_below_min",
    "pw.detector.z_not_reached_low",
    "pw.detector.mad_within_p05",
    "pw.detector.not_active",
    "pw.detector.disappear_median_floor",
    "pw.detector.observed_positive",
    "pw.detector.streak_short",
)

_PW_BASES = (
    "pw.detector.basis_z_high",
    "pw.detector.basis_z_low",
    "pw.detector.basis_above_p95",
    "pw.detector.basis_below_p05",
)

_PW_CLAIMS = (
    "pw.detector.claim_new_value",
    "pw.detector.claim_new_combination",
)

# Shorthands and reference groups of the MSG-6 block (WAF anomaly sentences, Family 3).
_PCT1 = ParamSpec(ParamKind.PERCENT, precision=1)
_POINTS1 = ParamSpec(ParamKind.POINTS)
_POINTS0U = ParamSpec(ParamKind.POINTS, style="unsigned", precision=0)
_WAF_AT = datetime(2026, 8, 3, 19, 25, tzinfo=UTC)

#: The scope decision's sentences (`app.waf.measurement.verdict_scope`) and the detectors' own
#: fallbacks for a scope that carries no reason.
_WAF_SCOPES = (
    "waf.scope.unstated",
    "waf.scope.lower_bound_increase",
    "waf.scope.needs_exact",
    "waf.scope.unattributed",
    "waf.scope.unclassified",
    "waf.scope.inputs_unsupported",
    "waf.scope.composition_exact",
)

_WAF_HISTORY_CHECKS = (
    "waf.history_check.not_divisible",
    "waf.history_check.never_collected",
    "waf.history_check.record_count",
    "waf.history_check.bucket_width",
    "waf.history_check.discovery_truncated",
    "waf.history_check.admission_truncated",
    "waf.history_check.profile_version",
    "waf.history_check.normalizer_version",
    "waf.history_check.not_loaded",
    "waf.history_check.appearance_never_collected",
    "waf.history_check.appearance_no_profile",
    "waf.history_check.appearance_normalizer",
)

#: The guard sentences of the request-rate detector a lower bound may wrap.
_WAF_RATE_NOT_PROVEN = (
    "waf.rate.below_min_volume",
    "waf.rate.ratio_not_reached",
    "waf.rate.delta_below_min",
    "waf.rate.z_not_reached",
    "waf.rate.mad_within_band",
)

#: The bases a firing sentence ends with.
_WAF_BASES = (
    "waf.basis.z",
    "waf.basis.mad_outside_band",
    "waf.basis.mad_above_p95",
)

#: The guard sentences of the ratio and concentration detectors a lower bound may wrap.
_WAF_GUARD_NOT_PROVEN = (
    "waf.ratio.numerator_below_min",
    "waf.ratio.rise_below_min",
    "waf.ratio.multiple_below_min",
    "waf.concentration.top_below_min",
    "waf.concentration.rise_below_min",
    "waf.basis.z_not_reached",
    "waf.basis.mad_within_p95",
)

_WAF_NOUNS = (
    "waf.noun.blocked_requests",
    "waf.noun.waf_action_requests",
    "waf.noun.http_5xx",
    "waf.noun.http_4xx",
    "waf.noun.response_code_requests",
)

_WAF_SUBJECTS = ("waf.subject.named", "waf.subject.this_endpoint")

_WAF_VOLUMES = (
    "waf.concentration.volume_with_baseline",
    "waf.concentration.volume_no_baseline",
)

_WAF_RATIO_TEXTS = ("waf.rate.ratio_multiple", "waf.rate.ratio_none")

_WAF_NOVELTY_CAUSES = (
    *_WAF_HISTORY_CHECKS,
    *_WAF_SCOPES,
    "waf.novelty.history_incomplete",
)


# Shorthands of the MSG-5 block (Phase A detector sentences and lifecycle transition reasons).
_A_COMPLETENESS = ParamSpec(ParamKind.ENUM, enum_group="bucketCompleteness", style="lower")
_A_RATIO_TEXTS = (
    "anomaly.detector.ratio_times_expected",
    "anomaly.detector.ratio_no_traffic",
)
_A_BASES = (
    "anomaly.detector.basis_z",
    "anomaly.detector.basis_mad_outside",
)
_A_VOLUME_BASIS_SAMPLE = MessageRef(
    "anomaly.detector.basis_z", {"score": 6.2, "threshold": 3.5}
)
_A_VOLUME_RATIO_SAMPLE = MessageRef("anomaly.detector.ratio_times_expected", {"ratio": 5.2})


def _msg(
    code: str,
    template: str,
    params: Mapping[str, ParamSpec] | None = None,
    sample: Mapping[str, object] | None = None,
) -> MessageSpec:
    """A sentence with declared parameters (MSG-4b); no parameters is `_fixed`."""
    return MessageSpec(code=code, template=template, params=params or {}, sample=sample or {})


REGISTRY: tuple[MessageSpec, ...] = (
    _fixed(
        "explanation.dimension.unpopulated",
        "field is not populated for this log source",
    ),
    # --- MSG-1: refusal and validation sentences (I18N-B1 Family 15) -- keep contiguous ---
    # The permission refusal, every router.
    _text(
        "auth.permission.missing",
        "missing required permission: {permission}",
        permission="read:*",
    ),
    # Passwords and usernames.
    _fixed("auth.password.too_short", "password must be at least 12 characters"),
    _fixed("auth.password.too_long", "password must be at most 72 bytes"),
    _fixed("auth.password.equals_username", "password must not equal the username"),
    _fixed(
        "auth.password.ldap_directory",
        "LDAP accounts change their password in the directory, not here.",
    ),
    _fixed("auth.password.local_only", "only a local account can change its password"),
    _fixed("auth.password.current_wrong", "current password is wrong"),
    _fixed(
        "auth.username.shape",
        "username must be 1 to 255 characters without spaces or control characters",
    ),
    # Sign-in and sessions.
    _fixed("auth.login.invalid_credentials", "Invalid username or password"),
    _fixed(
        "auth.login.directory_unavailable",
        "The directory server is unavailable. Sign-in was not completed.",
    ),
    _fixed("auth.session.required", "Authentication required."),
    _fixed(
        "auth.session.must_change_password",
        "A password change is required before any other request.",
    ),
    _fixed(
        "auth.csrf.origin_missing",
        "Cross-site request refused: the request has no Origin or Referer header.",
    ),
    _text(
        "auth.csrf.origin_mismatch",
        "Cross-site request refused: origin {origin} is not the configured public origin.",
        origin="https://other.example",
    ),
    # Account administration.
    _fixed(
        "auth.user.last_admin",
        "the last active user holding admin:* cannot be disabled or lose that role",
    ),
    _fixed("auth.user.not_found", "No user has that id."),
    _fixed("auth.user.ldap_no_password", "An LDAP user has no password here."),
    _fixed("auth.user.local_needs_password", "A local user needs an initial password."),
    _fixed("auth.user.source_invalid", "auth_source must be LOCAL or LDAP"),
    _fixed("auth.user.exists", "A user with that username or email already exists."),
    _fixed("auth.user.username_taken", "A user with that username already exists."),
    _fixed("auth.user.email_taken", "A user with that email already exists."),
    _fixed("auth.user.email_unusable", "email is not usable"),
    _fixed(
        "auth.user.reset_local_only",
        "Only a local account can have its password reset here.",
    ),
    _text("auth.user.role_unknown", "unknown role: {name}", name="analyst"),
    # Directory configuration.
    _fixed(
        "auth.directory.stale",
        "The directory configuration is stale. Reload it and retry.",
    ),
    _fixed("auth.directory.test_needs_url", "the test needs a directory URL"),
    MessageSpec(
        code="auth.directory.urls_max",
        template="at most {max} directory URLs are allowed",
        params={"max": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"max": 8},
    ),
    _fixed("auth.directory.url_empty", "a directory URL is empty"),
    _fixed("auth.directory.url_too_long", "a directory URL is longer than 512 characters"),
    _text("auth.directory.url_scheme", "{url} must use ldap:// or ldaps://", url="http://dc"),
    _text("auth.directory.url_credentials", "{url} must not contain credentials", url="ldap://u@dc"),
    _text("auth.directory.url_host", "{url} must name a host", url="ldap://"),
    _text("auth.directory.url_path", "{url} must not have a path", url="ldap://dc/x"),
    _text("auth.directory.url_query", "{url} must not have a query", url="ldap://dc?x"),
    _text("auth.directory.url_fragment", "{url} must not have a fragment", url="ldap://dc#x"),
    _fixed(
        "auth.directory.start_tls_with_ldaps",
        "start_tls must be false for an ldaps:// URL",
    ),
    _fixed("auth.directory.start_tls_needs_ldap", "start_tls requires an ldap:// URL"),
    _fixed("auth.directory.ldap_needs_start_tls", "an ldap:// URL requires start_tls"),
    _fixed(
        "auth.directory.enabled_needs_url",
        "an enabled configuration needs at least one directory URL",
    ),
    _fixed(
        "auth.directory.enabled_needs_valid_url",
        "an enabled configuration needs at least one valid directory URL",
    ),
    _fixed(
        "auth.directory.template_too_long",
        "bind_template is longer than 512 characters",
    ),
    _fixed(
        "auth.directory.template_username",
        "bind_template must contain {{username}}",
    ),
    _fixed("auth.directory.timeout_range", "timeout_seconds must be between 1 and 30"),
    _fixed("auth.directory.ca_too_long", "ca_pem is too long"),
    _fixed(
        "auth.directory.ca_private_key",
        "ca_pem must be a certificate, not a private key",
    ),
    _fixed("auth.directory.ca_invalid", "ca_pem is not a PEM X.509 certificate"),
    _text(
        "auth.directory.cert_not_yet_valid",
        "certificate {subject} is not yet valid (not_before {not_before})",
        subject="CN=ca",
        not_before="2030-01-01T00:00:00+00:00",
    ),
    _text(
        "auth.directory.cert_expired",
        "certificate {subject} is expired (not_after {not_after})",
        subject="CN=ca",
        not_after="2020-01-01T00:00:00+00:00",
    ),
    _fixed(
        "auth.directory.base_too_long",
        "user_search_base is longer than 1024 characters",
    ),
    _fixed(
        "auth.directory.base_control_character",
        "user_search_base contains a control character",
    ),
    # Dashboards.
    _fixed("dashboard.not_found", "dashboard not found"),
    _text(
        "dashboard.name_taken",
        "you already have a dashboard named '{name}'",
        name="Overview",
    ),
    MessageSpec(
        code="dashboard.owner_limit",
        template="the limit is {limit} dashboards per owner",
        params={"limit": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"limit": 20},
    ),
    _fixed(
        "dashboard.owner_only",
        "only the owner or an administrator can change this dashboard",
    ),
    _text(
        "dashboard.visibility_needs_permission",
        "changing visibility needs the {permission} permission",
        permission="dashboard:share",
    ),
    _fixed("dashboard.changed_since_opened", "this dashboard changed since you opened it"),
    # Property watches and custom rules.
    _text(
        "propertywatch.name_exists",
        "a property watch named '{name}' already exists",
        name="Admin logons",
    ),
    _text(
        "customrule.name_exists",
        "a custom rule named '{name}' already exists",
        name="Volume drop",
    ),
    _fixed(
        "customrule.watch_value_gone",
        "the property-watch value no longer exists (its watch changed or was deleted "
        "while the rule was saved); reload and choose a value again",
    ),
    _fixed("propertywatch.action.change_pins", "change the scope, properties or pins"),
    _fixed(
        "propertywatch.action.change_scope",
        "change the scope or properties (the watch version would change)",
    ),
    _fixed("propertywatch.action.unpin", "unpin a value"),
    _fixed("propertywatch.action.delete", "delete the watch"),
    MessageSpec(
        code="propertywatch.dependency.refused",
        template=(
            "cannot {action}: custom rule(s) {names} pin a value of this watch; "
            "detach or delete the rule(s) first"
        ),
        params={
            "action": ParamSpec(ParamKind.REF, ref_codes=_PW_ACTIONS),
            "names": ParamSpec(ParamKind.TEXT),
        },
        sample={"action": MessageRef("propertywatch.action.delete"), "names": "'Rule A'"},
    ),
    MessageSpec(
        code="propertywatch.dependency.raced",
        template=(
            "cannot {action}: a custom rule pinned a value of this watch while "
            "the request ran; reload and try again"
        ),
        params={"action": ParamSpec(ParamKind.REF, ref_codes=_PW_ACTIONS)},
        sample={"action": MessageRef("propertywatch.action.unpin")},
    ),
    # --- end MSG-1 ---
    # --- MSG-2: rule statistics (Family 9) and detection coverage (Family 10) ----------------
    MessageSpec(
        code="rule_statistics.window.none",
        template=(
            "No rule-match window has been collected for this instance: collection is disabled "
            "or has not run yet, so nothing is measured."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.window.unsettled",
        template="No rule-match window in the requested span has settled yet.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.window.incomplete",
        template=(
            "Only {complete} of {expected} windows in the span are complete "
            "({missing} missing, {truncated} truncated, {failed} failed)."
            " Absence from an incomplete window proves nothing."
        ),
        params={
            "complete": ParamSpec(ParamKind.COUNT, style="plain"),
            "expected": ParamSpec(ParamKind.COUNT, style="plain"),
            "missing": ParamSpec(ParamKind.COUNT, style="plain"),
            "truncated": ParamSpec(ParamKind.COUNT, style="plain"),
            "failed": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"complete": 23, "expected": 24, "missing": 1, "truncated": 0, "failed": 0},
    ),
    MessageSpec(
        code="rule_statistics.top.no_complete_window",
        template="No complete window to measure.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.silent.excluded",
        template=(
            "Only EVENT rules and EVENT building blocks are judged: an event search sees every "
            "one of their matches. COMMON rules also match on flows, which are not searched, so "
            "their silence is unknown. FLOW and OFFENSE rules are not searched at all."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.cost.label",
        template=(
            "QRadar capacity benchmark: how fast the rule could be evaluated. "
            "Lower is costlier to evaluate. This is not a firing rate."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.cost.unit",
        template="millions of events per second (QRadar's unit)",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.unmapped",
        template="No rule is mapped to this technique.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.all_excluded",
        template=(
            "Every rule mapped to this technique is disabled or discounted, "
            "so there is no active detection."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.none_detecting",
        template="{degraded} of {enabled} enabled rules are not currently detecting.",
        params={
            "degraded": ParamSpec(ParamKind.COUNT, style="plain"),
            "enabled": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"degraded": 2, "enabled": 3},
    ),
    MessageSpec(
        code="coverage.rollup.not_established",
        template="Rule health has not been established for the mapped rules.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.some_degraded",
        template="{working} of {enabled} enabled rules are detecting; {degraded} are degraded.",
        params={
            "working": ParamSpec(ParamKind.COUNT, style="plain"),
            "enabled": ParamSpec(ParamKind.COUNT, style="plain"),
            "degraded": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"working": 1, "enabled": 3, "degraded": 2},
    ),
    MessageSpec(
        code="coverage.rollup.some_unestablished",
        template=(
            "{working} of {enabled} enabled rules are detecting; the rest are not yet established."
        ),
        params={
            "working": ParamSpec(ParamKind.COUNT, style="plain"),
            "enabled": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"working": 2, "enabled": 3},
    ),
    MessageSpec(
        code="coverage.rollup.all_detecting",
        template="All {enabled} enabled rules mapped to this technique are detecting.",
        params={"enabled": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"enabled": 4},
    ),
    MessageSpec(
        code="coverage.evidence.below_floor",
        template=(
            "Mapping confidence {confidence} is below the {floor} floor; recorded "
            "but not counted toward coverage."
        ),
        params={
            "confidence": ParamSpec(ParamKind.NUMBER, style="fixed", precision=2),
            "floor": ParamSpec(ParamKind.NUMBER, style="fixed", precision=2),
        },
        sample={"confidence": 0.3, "floor": 0.5},
    ),
    MessageSpec(
        code="coverage.evidence.disabled",
        template="The rule is disabled, so it provides no active coverage.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.acceptable",
        template="The rule is enabled and its health is acceptable.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.dependency_degraded",
        template="The rule's required telemetry is unavailable, so it cannot fire.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.never_observed",
        template="The rule has never been observed firing.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.inactive",
        template="The rule has not fired within the inactivity window.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.not_established",
        template="The rule's health has not been established yet.",
        params={},
        sample={},
    ),
    # --- end MSG-2 ---------------------------------------------------------------------------
    # --- MSG-3: rule-health verdict reasons (Family 8) -- keep contiguous ---------------------
    _fixed(
        "rule_health.disabled",
        "The rule is disabled in QRadar, so it cannot produce detections.",
    ),
    _fixed(
        "rule_health.age_unknown",
        "The rule's age is unknown, so inactivity cannot be assessed.",
    ),
    MessageSpec(
        code="rule_health.grace",
        template="The rule is {age_days} days old, inside the {grace_days}-day grace period.",
        params={
            "age_days": ParamSpec(ParamKind.NUMBER, style="fixed", precision=1),
            "grace_days": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"age_days": 2.5, "grace_days": 7},
    ),
    _fixed("rule_health.min_history", "Not enough observed history to classify this rule."),
    # A dependency is named by its QRadar name, or, when it has none, by a coded fallback.
    _text("rule_health.label.named", "{name}", name="Failed Logins BB"),
    _text("rule_health.label.building_block", "building block {ref}", ref="bb-42"),
    _text("rule_health.label.log_source_type", "log source type {ref}", ref="12"),
    MessageSpec(
        code="rule_health.dependency.disabled",
        template="{label} is disabled",
        params={"label": ParamSpec(ParamKind.REF, ref_codes=_RH_LABELS)},
        sample={"label": MessageRef("rule_health.label.named", {"name": "Failed Logins BB"})},
    ),
    MessageSpec(
        code="rule_health.dependency.not_delivering",
        template="{label} is not delivering events",
        params={"label": ParamSpec(ParamKind.REF, ref_codes=_RH_LABELS)},
        sample={
            "label": MessageRef("rule_health.label.log_source_type", {"ref": "12"}),
        },
    ),
    MessageSpec(
        code="rule_health.dependency.not_observed",
        template="{label} has not been observed",
        params={"label": ParamSpec(ParamKind.REF, ref_codes=_RH_LABELS)},
        sample={
            "label": MessageRef("rule_health.label.building_block", {"ref": "bb-42"}),
        },
    ),
    MessageSpec(
        code="rule_health.dependency_degraded",
        template=(
            "The rule cannot fire reliably: {dependencies}. "
            "Its silence is not evidence about the rule itself."
        ),
        params={"dependencies": ParamSpec(ParamKind.LIST, style="semicolon")},
        sample={
            "dependencies": [
                MessageRef(
                    "rule_health.dependency.disabled",
                    {"label": MessageRef("rule_health.label.named", {"name": "Failed Logins BB"})},
                ),
                MessageRef(
                    "rule_health.dependency.not_delivering",
                    {
                        "label": MessageRef(
                            "rule_health.label.log_source_type", {"ref": "12"}
                        )
                    },
                ),
            ]
        },
    ),
    MessageSpec(
        code="rule_health.dependency_unobserved",
        template=(
            "A dependency has not been observed, so the rule cannot be judged: {dependencies}."
        ),
        params={"dependencies": ParamSpec(ParamKind.LIST, style="semicolon")},
        sample={
            "dependencies": [
                MessageRef(
                    "rule_health.dependency.not_observed",
                    {"label": MessageRef("rule_health.label.building_block", {"ref": "bb-42"})},
                ),
            ]
        },
    ),
    MessageSpec(
        code="rule_health.noisy",
        template=(
            "The rule fired {per_day} times a day over the last {window_days} days, "
            "above the expected {threshold}."
        ),
        params={
            "per_day": ParamSpec(ParamKind.NUMBER, style="fixed", precision=1),
            "window_days": ParamSpec(ParamKind.NUMBER, style="fixed", precision=0),
            "threshold": ParamSpec(ParamKind.NUMBER, style="fixed", precision=1),
        },
        sample={"per_day": 812.4, "window_days": 30.0, "threshold": 500.0},
    ),
    MessageSpec(
        code="rule_health.never_observed.measured",
        template=(
            "The rule is enabled and its dependencies are healthy, but no matching event was "
            "measured in any of the {expected} COMPLETE windows of the evaluation span."
        ),
        params={"expected": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"expected": 24},
    ),
    MessageSpec(
        code="rule_health.never_observed.unmeasured",
        template=(
            "The rule is enabled and its dependencies are healthy, but it has never been "
            "observed firing in {age_days} days."
        ),
        params={"age_days": ParamSpec(ParamKind.NUMBER, style="fixed", precision=0)},
        sample={"age_days": 45.0},
    ),
    MessageSpec(
        code="rule_health.inactive",
        template=(
            "The rule last fired {quiet_days} days ago, beyond the "
            "{inactivity_days}-day inactivity window."
        ),
        params={
            "quiet_days": ParamSpec(ParamKind.NUMBER, style="fixed", precision=0),
            "inactivity_days": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"quiet_days": 20.0, "inactivity_days": 14},
    ),
    MessageSpec(
        code="rule_health.healthy.lower_bound",
        template=(
            "At least {count} matching events were measured in the evaluation window with "
            "healthy dependencies."
        ),
        params={"count": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"count": 12},
    ),
    MessageSpec(
        code="rule_health.healthy.inferred",
        template=(
            "At least {count} offense-contributing firings were observed in the evaluation "
            "window with healthy dependencies."
        ),
        params={"count": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"count": 3},
    ),
    MessageSpec(
        code="rule_health.healthy.direct",
        template="The rule fired {count} times in the evaluation window with healthy dependencies.",
        params={"count": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"count": 87},
    ),
    # The incomplete-coverage sentence. English decides singular and plural, so every English
    # variant has its own code: the reader formats a count to a string before the catalog sees
    # it (no ICU plural is possible), and Turkish does not inflect a noun after a numeral.
    _fixed(
        "rule_health.incomplete.no_collection",
        "No completed rule-metric collection covers the evaluation window, so the absence of "
        "observed firings is not evidence that the rule has never fired.",
    ),
    MessageSpec(
        code="rule_health.incomplete.head",
        template=(
            "Measured rule-match coverage of the evaluation window is incomplete "
            "({complete} of {expected} windows are COMPLETE), so the absence of measured "
            "matches is not evidence that the rule has never fired."
        ),
        params={
            "complete": ParamSpec(ParamKind.COUNT, style="plain"),
            "expected": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"complete": 22, "expected": 24},
    ),
    MessageSpec(
        code="rule_health.incomplete.cause.failed_one",
        template="1 window failed to collect",
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_health.incomplete.cause.failed_many",
        template="{count} windows failed to collect",
        params={"count": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"count": 3},
    ),
    MessageSpec(
        code="rule_health.incomplete.cause.failed_one_class",
        template="1 window failed to collect (latest error class: {error_class})",
        params={"error_class": ParamSpec(ParamKind.TEXT)},
        sample={"error_class": "TimeoutError"},
    ),
    MessageSpec(
        code="rule_health.incomplete.cause.failed_many_class",
        template="{count} windows failed to collect (latest error class: {error_class})",
        params={
            "count": ParamSpec(ParamKind.COUNT, style="plain"),
            "error_class": ParamSpec(ParamKind.TEXT),
        },
        sample={"count": 3, "error_class": "TimeoutError"},
    ),
    MessageSpec(
        code="rule_health.incomplete.cause.skipped_one",
        template="1 window was skipped beyond the catch-up horizon and never searched",
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_health.incomplete.cause.skipped_many",
        template="{count} windows were skipped beyond the catch-up horizon and never searched",
        params={"count": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"count": 4},
    ),
    MessageSpec(
        code="rule_health.incomplete.detail_one",
        template=(
            "Measured rule-match coverage of the evaluation window is incomplete "
            "({complete} of {expected} windows are COMPLETE): {causes}. "
            "Affected window: {first_start}. "
            "The absence of measured matches is not evidence that the rule has never fired. "
            "The evaluation window stops holding the last affected window at {until}."
        ),
        params={
            "complete": ParamSpec(ParamKind.COUNT, style="plain"),
            "expected": ParamSpec(ParamKind.COUNT, style="plain"),
            "causes": ParamSpec(ParamKind.LIST, style="semicolon"),
            "first_start": ParamSpec(ParamKind.TIMESTAMP, style="minute"),
            "until": ParamSpec(ParamKind.TIMESTAMP, style="minute"),
        },
        sample={
            "complete": 23,
            "expected": 24,
            "causes": [MessageRef("rule_health.incomplete.cause.failed_one")],
            "first_start": "2026-10-27T09:00:00+00:00",
            "until": "2026-10-28T09:00:00+00:00",
        },
    ),
    MessageSpec(
        code="rule_health.incomplete.detail_many",
        template=(
            "Measured rule-match coverage of the evaluation window is incomplete "
            "({complete} of {expected} windows are COMPLETE): {causes}. "
            "Affected windows: {first_start} to {last_start}. "
            "The absence of measured matches is not evidence that the rule has never fired. "
            "The evaluation window stops holding the last affected window at {until}."
        ),
        params={
            "complete": ParamSpec(ParamKind.COUNT, style="plain"),
            "expected": ParamSpec(ParamKind.COUNT, style="plain"),
            "causes": ParamSpec(ParamKind.LIST, style="semicolon"),
            "first_start": ParamSpec(ParamKind.TIMESTAMP, style="minute"),
            "last_start": ParamSpec(ParamKind.TIMESTAMP, style="minute"),
            "until": ParamSpec(ParamKind.TIMESTAMP, style="minute"),
        },
        sample={
            "complete": 20,
            "expected": 24,
            "causes": [
                MessageRef(
                    "rule_health.incomplete.cause.failed_many_class",
                    {"count": 3, "error_class": "TimeoutError"},
                ),
                MessageRef("rule_health.incomplete.cause.skipped_one"),
            ],
            "first_start": "2026-10-27T09:00:00+00:00",
            "last_start": "2026-10-27T12:00:00+00:00",
            "until": "2026-10-28T12:00:00+00:00",
        },
    ),
    # --- end MSG-3 ---------------------------------------------------------------------------
    # --- MSG-4a: WAF/IPS profile validation and ineligibility (Family 4) -- keep contiguous ---
    MessageSpec(
        code="validation.field.population_not_measured",
        template="population was not measured",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.total_not_measured",
        template="the source's total event volume was not measured",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.no_events",
        template="the source produced no events in the window; nothing can be concluded",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.insufficient_coverage",
        template="populated on {populated} of events, below the required {required}",
        params={
            "populated": ParamSpec(ParamKind.PERCENT, precision=1),
            "required": ParamSpec(ParamKind.PERCENT),
        },
        sample={"populated": 0.318, "required": 0.9},
    ),
    MessageSpec(
        code="validation.waf.field.not_mapped",
        template="no QRadar property is mapped to this field",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.field.property_rejected",
        template="QRadar rejected a query naming this property; it does not exist as named",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.field.unpopulated",
        template="the property exists but no event in the window carried a value",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.field.implausible_ratio",
        template=(
            "{plausible} of sampled events carry a plausible {logical} value, "
            "below the required {required}"
        ),
        params={
            "plausible": ParamSpec(ParamKind.PERCENT, precision=1),
            "logical": ParamSpec(ParamKind.TEXT),
            "required": ParamSpec(ParamKind.PERCENT),
        },
        sample={"plausible": 0.125, "logical": "method", "required": 0.9},
    ),
    MessageSpec(
        code="validation.waf.field.implausible_no_values",
        template=(
            "no values of sampled events carry a plausible {logical} value, "
            "below the required {required}"
        ),
        params={
            "logical": ParamSpec(ParamKind.TEXT),
            "required": ParamSpec(ParamKind.PERCENT),
        },
        sample={"logical": "method", "required": 0.9},
    ),
    MessageSpec(
        code="validation.ips.field.not_mapped",
        template="no QRadar field is mapped to this logical field",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.ips.field.rejected",
        template="QRadar rejected the mapped field as named",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.ips.field.unpopulated",
        template="the field exists but no event in the window carried a usable value",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.lookup_failed",
        template="the log source lookup failed ({error})",
        params={"error": ParamSpec(ParamKind.TEXT)},
        sample={"error": "ProviderUnavailableError: timeout"},
    ),
    MessageSpec(
        code="validation.profile.source_missing",
        template="the log source does not exist in QRadar",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.total_not_measured",
        template="total event volume was not measured ({error})",
        params={"error": ParamSpec(ParamKind.TEXT)},
        sample={"error": "ProviderUnavailableError: timeout"},
    ),
    MessageSpec(
        code="validation.ips.profile.truncated",
        template="the bounded validation probe was truncated",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.field_reason",
        template="{field}: {status} ({detail})",
        params={
            "field": ParamSpec(ParamKind.TEXT),
            "status": ParamSpec(ParamKind.TEXT),
            "detail": ParamSpec(
                ParamKind.REF,
                ref_codes=(
                    "validation.field.population_not_measured",
                    "validation.field.total_not_measured",
                    "validation.field.no_events",
                    "validation.field.insufficient_coverage",
                    "validation.waf.field.not_mapped",
                    "validation.waf.field.property_rejected",
                    "validation.waf.field.unpopulated",
                    "validation.waf.field.implausible_ratio",
                    "validation.waf.field.implausible_no_values",
                    "validation.ips.field.not_mapped",
                    "validation.ips.field.rejected",
                    "validation.ips.field.unpopulated",
                ),
            ),
        },
        sample={
            "field": "URL",
            "status": "INSUFFICIENT_COVERAGE",
            "detail": MessageRef(
                "validation.field.insufficient_coverage",
                {"populated": 0.318, "required": 0.9},
            ),
        },
    ),
    MessageSpec(
        code="validation.waf.ineligible.collection_disabled",
        template="WAF collection is disabled (WAF_COLLECTION_ENABLED=false)",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.ineligible.source_missing",
        template="the profile's log source no longer exists",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.ineligible.disabled",
        template="the profile is disabled",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.ineligible.monitoring_disabled",
        template="monitoring is disabled for the log source",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.ineligible.version_never_validated",
        template="profile version {profile_version} has never been validated",
        params={"profile_version": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"profile_version": 12000},
    ),
    MessageSpec(
        code="validation.profile.ineligible.version_not_validated",
        template=(
            "profile version {profile_version} has not been validated "
            "(last validated: {validated_profile_version})"
        ),
        params={
            "profile_version": ParamSpec(ParamKind.COUNT, style="plain"),
            "validated_profile_version": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"profile_version": 12000, "validated_profile_version": 9999},
    ),
    MessageSpec(
        code="validation.profile.ineligible.status",
        template="profile validation is {validation_status}",
        params={
            "validation_status": ParamSpec(
                ParamKind.ENUM,
                enum_group="profileValidationStatus",
            )
        },
        sample={"validation_status": "INVALID"},
    ),
    # --- end MSG-4a ---
    # --- MSG-4b: property-watch sentences (Family 6) -- keep contiguous ---------------------
    # Watch status (stored in property_watch.last_status_reason_i18n).
    _msg(
        "pw.status.reading_history",
        "Reading history from QRadar: {done} of {total} days; nothing is judged until it finishes",
        {"done": _FIXED1, "total": _FIXED1},
        {"done": 3.46, "total": 7.0},
    ),
    _msg(
        "pw.status.not_applicable",
        "the property parsed on no event in scope; the values are not measured",
    ),
    _msg(
        "pw.status.latest_search_failed",
        "the latest search failed; bucket {bucket} is stored NOT_COLLECTED",
        {"bucket": _ISO},
        {"bucket": _PW_AT},
    ),
    _msg(
        "pw.status.disabled_by",
        "disabled by {actor}",
        {"actor": _TEXT},
        {"actor": "efe"},
    ),
    _msg("pw.status.open", "one or more value incidents are open"),
    _msg("pw.status.recovering", "one or more value incidents are recovering"),
    _msg("pw.status.evaluated", "the latest collected bucket was evaluated"),
    # Scope resolution.
    _msg("pw.scope.source_missing", "the log source is not in the inventory"),
    _msg("pw.scope.names_no_type", "the watch names no log source type"),
    _msg("pw.scope.type_empty", "no synced log source has this type"),
    _msg("pw.scope.names_no_group", "the watch names no log source group"),
    _msg("pw.scope.group_missing", "the log source group is not in the inventory"),
    _msg(
        "pw.scope.group_stale",
        "the group inventory was last synced at {synced}, older than {limit}",
        {"synced": _ISO, "limit": ParamSpec(ParamKind.DURATION, style="timedelta")},
        {"synced": _PW_AT, "limit": 1800},
    ),
    _msg(
        "pw.scope.group_empty",
        "the group and its descendants hold no synced log source",
    ),
    _msg(
        "pw.scope.too_large",
        "{sources} sources need {searches} searches; the limit is {limit}",
        {"sources": _PLAIN, "searches": _PLAIN, "limit": _PLAIN},
        {"sources": 700, "searches": 7, "limit": 5},
    ),
    # History reading (computed) and the planner's gap (stored in history_progress).
    _msg("pw.history.switched_off", "history reading is switched off"),
    _msg("pw.history.starts_next", "the reading starts at the next collection"),
    _msg(
        "pw.history.unreadable",
        "the stored history progress could not be read back",
    ),
    _msg(
        "history.gap.search_failed",
        "search failed {failures} times at the minimum width",
        {"failures": _PLAIN},
        {"failures": 3},
    ),
    # Transition reasons (stored in pw_anomaly_transition.reason_i18n).
    _msg("pw.transition.recovered", "condition recovered"),
    _msg("pw.transition.definition_changed", "watch definition changed"),
    _msg("pw.transition.value_retired", "value retired; no longer evaluated"),
    _msg("pw.transition.watch_deleted", "the watch was deleted"),
    _msg("pw.transition.watch_disabled", "watch disabled; no longer evaluated"),
    # What a property reference or a pinned value may be.
    _msg(
        "pw.reference.normalized_not_allowed",
        "normalized field is not in the property-watch allowlist",
    ),
    _msg(
        "pw.reference.name_invalid",
        "invalid QRadar property name: letters, digits, underscore, space and hyphen only, "
        "at most 64 characters",
    ),
    _msg("pw.reference.kind_unknown", "unknown property reference kind"),
    _msg(
        "pw.reference.not_an_object",
        "a property reference is an object with a kind and a name",
    ),
    _msg("pw.reference.name_not_text", "property reference name must be text"),
    _msg("pw.reference.pin_not_text", "a pinned value is text"),
    _msg("pw.reference.pin_empty", "a pinned value cannot be empty"),
    _msg(
        "pw.reference.pin_length",
        "a pinned value is at most {max} characters",
        {"max": _PLAIN},
        {"max": 255},
    ),
    _msg(
        "pw.reference.pin_characters",
        "a pinned value cannot hold a quote, a backslash, a control character or "
        "leading or trailing whitespace",
    ),
    # Draft validation and the other 422 reasons of the watch routes.
    _msg(
        "pw.draft.scope_required",
        "{column} is required for a {kind} scope",
        {"column": _TEXT, "kind": _TEXT},
        {"column": "log_source_id", "kind": "LOG_SOURCE"},
    ),
    _msg(
        "pw.draft.scope_must_be_empty",
        "{column} must be empty for a {kind} scope",
        {"column": _TEXT, "kind": _TEXT},
        {"column": "scope_type_id", "kind": "LOG_SOURCE"},
    ),
    _msg(
        "pw.draft.scope_negative",
        "{column} must not be negative",
        {"column": _TEXT},
        {"column": "scope_group_id"},
    ),
    _msg(
        "pw.draft.property_count",
        "a watch names one or two properties, not {count}",
        {"count": _PLAIN},
        {"count": 3},
    ),
    _msg(
        "pw.draft.property_invalid",
        "property {index}: {reason}",
        {"index": _PLAIN, "reason": ParamSpec(ParamKind.REF, ref_codes=_PW_REFERENCE_REASONS)},
        {"index": 2, "reason": MessageRef("pw.reference.kind_unknown")},
    ),
    _msg("pw.draft.property_twice", "a watch cannot name the same property twice"),
    _msg(
        "pw.draft.pin_count",
        "a watch pins at most {max} values, not {count}",
        {"max": _PLAIN, "count": _PLAIN},
        {"max": 10, "count": 11},
    ),
    _msg(
        "pw.draft.pin_parts",
        "pinned value {index}: needs {count} string(s), one per property",
        {"index": _PLAIN, "count": _PLAIN},
        {"index": 1, "count": 2},
    ),
    _msg(
        "pw.draft.pin_invalid",
        "pinned value {index}, part {part}: {reason}",
        {
            "index": _PLAIN,
            "part": _PLAIN,
            "reason": ParamSpec(ParamKind.REF, ref_codes=_PW_REFERENCE_REASONS),
        },
        {"index": 1, "part": 2, "reason": MessageRef("pw.reference.pin_empty")},
    ),
    _msg(
        "pw.draft.pin_twice",
        "pinned value {index}: pinned twice",
        {"index": _PLAIN},
        {"index": 2},
    ),
    _msg("pw.draft.detector_required", "a watch evaluates at least one detector"),
    _msg(
        "pw.draft.combination_needs_two",
        "PW_NEW_COMBINATION needs a watch with two properties",
    ),
    _msg(
        "pw.draft.log_source_missing",
        "log source {id} does not exist",
        {"id": _TEXT},
        {"id": "5b0c8b0e-0000-4000-8000-000000000001"},
    ),
    _msg(
        "pw.draft.type_missing",
        "log source type {id} is not in the synced inventory",
        {"id": _PLAIN},
        {"id": 11},
    ),
    _msg(
        "pw.draft.group_missing",
        "log source group {id} is not in the synced inventory",
        {"id": _PLAIN},
        {"id": 12},
    ),
    _msg(
        "pw.draft.history_days_null",
        "history_days must not be null; use 0 for no history",
    ),
    _msg(
        "pw.draft.history_days_range",
        "history_days must be 0 (no history) or between {low} and {high}, not {days}",
        {"low": _PLAIN, "high": _PLAIN, "days": _PLAIN},
        {"low": 7, "high": 30, "days": 3},
    ),
    _msg(
        "pw.draft.instance_differs",
        "instance_id differs from the log source's instance",
    ),
    _msg("pw.draft.no_instance", "no QRadar instance is registered"),
    _msg(
        "pw.draft.pinned_rows",
        "the pinned values can return up to {rows} rows per bucket, above the {target} rows "
        "one history search may carry; unpin values or request no history",
        {"rows": _PLAIN, "target": _PLAIN},
        {"rows": 9000, "target": 5000},
    ),
    _msg(
        "pw.draft.field_null",
        "{name} must not be null",
        {"name": _TEXT},
        {"name": "severity"},
    ),
    _msg(
        "pw.draft.scope_id_uuid",
        "scope_id of a LOG_SOURCE scope is the source's id",
    ),
    _msg(
        "pw.draft.scope_id_number",
        "scope_id of a {kind} scope is a number",
        {"kind": _TEXT},
        {"kind": "LOG_SOURCE_TYPE"},
    ),
    _msg(
        "pw.draft.series_max",
        "at most {max} values per request, not {count}",
        {"max": _PLAIN, "count": _PLAIN},
        {"max": 12, "count": 13},
    ),
    _msg(
        "pw.draft.series_untracked",
        "value {value} is not a tracked value of this watch's current version",
        {"value": _TEXT},
        {"value": "5b0c8b0e-0000-4000-8000-000000000002"},
    ),
    # Changing the history request (409) and the bounded preview.
    _msg(
        "pw.history_change.already_requested",
        "history is {state} on this watch and cannot be requested again; "
        "create a new watch to read history",
        {"state": ParamSpec(ParamKind.ENUM, enum_group="propertyWatchHistoryState")},
        {"state": "READING"},
    ),
    _msg(
        "pw.history_change.already_collected",
        "this watch has already collected ({marks} set); create a new watch to read history",
        {"marks": ParamSpec(ParamKind.LIST, style="and")},
        {"marks": ["collected_through", "evaluated_through"]},
    ),
    _msg(
        "pw.history_change.holds_buckets",
        "this watch holds {stored} stored bucket(s) of its current version; "
        "create a new watch to read history",
        {"stored": _PLAIN},
        {"stored": 288},
    ),
    _msg(
        "pw.preview.scope_unresolved",
        "{status}: {reason}",
        {
            "status": ParamSpec(ParamKind.ENUM, enum_group="propertyWatchStatus"),
            "reason": ParamSpec(ParamKind.REF, ref_codes=_PW_SCOPE_REASONS),
        },
        {
            "status": "SCOPE_EMPTY",
            "reason": MessageRef("pw.scope.group_empty"),
        },
    ),
    _msg(
        "pw.preview.group_too_large",
        "the group resolves to {members} sources and a preview covers one search chunk of "
        "at most {max}; preview a smaller group, a type or a source",
        {"members": _PLAIN, "max": _PLAIN},
        {"members": 700, "max": 500},
    ),
    _msg(
        "pw.preview.unsearchable",
        "the stored watch cannot be searched: {error}",
        {"error": _TEXT},
        {"error": "unknown property reference kind"},
    ),
    _msg(
        "pw.preview.search_failed",
        "a search failed, so its buckets were not measured",
    ),
    _msg(
        "pw.preview.cut_at_row_limit",
        "the grouped search was cut at the row limit and a preview does not split it; "
        "narrow the scope or preview a type or a source",
    ),
    _msg(
        "pw.preview.lower_bound",
        "a bucket was cut at the row limit: its counts are lower bounds",
    ),
    # The novelty history verifier (property_watch/history.py and the engine).
    _msg(
        "pw.history_check.not_divisible",
        "the history window is not divisible by the collection bucket width",
    ),
    _msg(
        "pw.history_check.bucket_never_collected",
        "history bucket {bucket} was never collected ({missing} of {expected} missing)",
        {"bucket": _ISO, "missing": _PLAIN, "expected": _PLAIN},
        {"bucket": _PW_AT, "missing": 2, "expected": 2016},
    ),
    _msg(
        "pw.history_check.record_count",
        "the history contains {records} bucket records for {expected} expected buckets",
        {"records": _PLAIN, "expected": _PLAIN},
        {"records": 2017, "expected": 2016},
    ),
    _msg(
        "pw.history_check.bucket_width",
        "history bucket {bucket} used a different collection bucket width",
        {"bucket": _ISO},
        {"bucket": _PW_AT},
    ),
    _msg(
        "pw.history_check.bucket_not_collected",
        "history bucket {bucket} was NOT_COLLECTED",
        {"bucket": _ISO},
        {"bucket": _PW_AT},
    ),
    _msg(
        "pw.history_check.discovery_truncated",
        "history bucket {bucket} had truncated value discovery",
        {"bucket": _ISO},
        {"bucket": _PW_AT},
    ),
    _msg(
        "pw.history_check.admission_truncated",
        "history bucket {bucket} had truncated value admission",
        {"bucket": _ISO},
        {"bucket": _PW_AT},
    ),
    _msg(
        "pw.history_check.bucket_version",
        "history bucket {bucket} used another watch version, "
        "not first-appearance version {version}",
        {"bucket": _ISO, "version": _PLAIN},
        {"bucket": _PW_AT, "version": 2},
    ),
    _msg(
        "pw.history_check.bucket_coverage",
        "history bucket {bucket} had parsing coverage below the floor",
        {"bucket": _ISO},
        {"bucket": _PW_AT},
    ),
    _msg(
        "pw.history_check.first_bucket_never_collected",
        "the first-appearance bucket was never collected",
    ),
    _msg("pw.history_check.outside_window", "outside the novelty window"),
    _msg("pw.history_check.incomplete", "the history is incomplete"),
    # Detector guards.
    _msg(
        "pw.detector.no_status",
        "the watch has never established a collection status",
    ),
    _msg(
        "pw.detector.watch_blocked",
        "the watch is {status}: no value verdict is possible",
        {"status": ParamSpec(ParamKind.ENUM, enum_group="propertyWatchStatus")},
        {"status": "SCOPE_STALE"},
    ),
    _msg("pw.detector.bucket_missing", "the bucket is missing: no count was measured"),
    _msg(
        "pw.detector.bucket_not_collected",
        "the bucket was NOT_COLLECTED: its count is not zero",
    ),
    _msg("pw.detector.bucket_membership", "membership changed during this bucket"),
    _msg(
        "pw.detector.bucket_not_parsed",
        "the property parsed on none of this bucket's events: NOT_APPLICABLE",
    ),
    _msg(
        "pw.detector.no_baseline",
        "no value-events baseline for this weekday/hour cell yet",
    ),
    _msg(
        "pw.detector.baseline_version",
        "the baseline uses watch version {baseline}, not the bucket's {bucket}",
        {"baseline": _PLAIN, "bucket": _PLAIN},
        {"baseline": 1, "bucket": 2},
    ),
    _msg(
        "pw.detector.baseline_few_visits",
        "the value-events baseline has {samples} sample(s), but they come from too few "
        "separate weekly visits of this weekday and hour: a baseline needs the same hour "
        "from at least {visits} different weeks",
        {"samples": _PLAIN, "visits": _PLAIN},
        {"samples": 12, "visits": 2},
    ),
    _msg(
        "pw.detector.baseline_few_visits_one",
        "the value-events baseline has {samples} sample(s), but they come from too few "
        "separate weekly visits of this weekday and hour: a baseline needs the same hour "
        "from at least 1 different week",
        {"samples": _PLAIN},
        {"samples": 12},
    ),
    _msg(
        "pw.detector.baseline_samples",
        "the value-events baseline has {samples} sample(s), below the minimum of {minimum}",
        {"samples": _PLAIN, "minimum": _PLAIN},
        {"samples": 5, "minimum": 8},
    ),
    _msg(
        "pw.detector.count_unknown",
        "the value's count is unknown for this bucket, not zero",
    ),
    _msg(
        "pw.detector.inputs_unsupported",
        "the declared inputs do not support a verdict",
    ),
    _msg(
        "pw.detector.coverage_below",
        "property coverage was {seen}, below the {floor} floor",
        {"seen": _PERCENT, "floor": _PERCENT},
        {"seen": 0.62, "floor": 0.9},
    ),
    _msg(
        "pw.detector.coverage_unknown",
        "property coverage was unknown, below the {floor} floor",
        {"floor": _PERCENT},
        {"floor": 0.9},
    ),
    _msg(
        "pw.detector.not_proven_lower_bound",
        "{reason}; {scope}, so a normal verdict cannot be concluded",
        {
            "reason": ParamSpec(ParamKind.REF, ref_codes=_PW_NOT_PROVEN),
            "scope": _TEXT,
        },
        {
            "reason": MessageRef("pw.detector.mad_within_p95", {"bound": 130.0}),
            "scope": "an input is a lower bound",
        },
    ),
    # Value spike.
    _msg(
        "pw.detector.spike_min_events",
        "{observed} events is below the {minimum} minimum",
        {"observed": _FIXED0, "minimum": _FIXED0},
        {"observed": 12.0, "minimum": 20.0},
    ),
    _msg(
        "pw.detector.ratio_not_reached",
        "observed/expected ratio {ratio} has not reached {threshold}",
        {"ratio": _FIXED2, "threshold": _FIXED2},
        {"ratio": 1.4, "threshold": 2.0},
    ),
    _msg(
        "pw.detector.spike_delta_below_min",
        "increase of {delta} events is below the {minimum} minimum absolute delta",
        {"delta": _FIXED0, "minimum": _FIXED0},
        {"delta": 30.0, "minimum": 50.0},
    ),
    _msg(
        "pw.detector.z_not_reached_high",
        "robust z={score} has not reached +{threshold}",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 2.1, "threshold": 3.5},
    ),
    _msg(
        "pw.detector.mad_within_p95",
        "robust score is unavailable (MAD=0) and the value is within p95 {bound}",
        {"bound": _FIXED2},
        {"bound": 130.0},
    ),
    _msg(
        "pw.detector.basis_z_high",
        "robust z={score} (threshold +{threshold})",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 6.2, "threshold": 3.5},
    ),
    _msg(
        "pw.detector.basis_above_p95",
        "robust score is unavailable (MAD=0); the value is above p95 {bound}",
        {"bound": _FIXED2},
        {"bound": 130.0},
    ),
    _msg(
        "pw.detector.spike_fired",
        "{observed} events is above the median {median} (+{delta}, {ratio}x); {basis}",
        {
            "observed": _FIXED0,
            "median": _FIXED1,
            "delta": _FIXED0,
            "ratio": _FIXED2,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_PW_BASES),
        },
        {
            "observed": 520.0,
            "median": 100.0,
            "delta": 420.0,
            "ratio": 5.2,
            "basis": MessageRef(
                "pw.detector.basis_z_high", {"score": 6.2, "threshold": 3.5}
            ),
        },
    ),
    _msg(
        "pw.detector.spike_fired_lower",
        "at least {observed} events is above the median {median} (+{delta}, {ratio}x); {basis}",
        {
            "observed": _FIXED0,
            "median": _FIXED1,
            "delta": _FIXED0,
            "ratio": _FIXED2,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_PW_BASES),
        },
        {
            "observed": 520.0,
            "median": 100.0,
            "delta": 420.0,
            "ratio": 5.2,
            "basis": MessageRef(
                "pw.detector.basis_z_high", {"score": 6.2, "threshold": 3.5}
            ),
        },
    ),
    _msg(
        "pw.detector.spike_zero_median",
        "{observed} events rose from a zero median; {basis}",
        {"observed": _FIXED0, "basis": ParamSpec(ParamKind.REF, ref_codes=_PW_BASES)},
        {
            "observed": 90.0,
            "basis": MessageRef("pw.detector.basis_above_p95", {"bound": 4.0}),
        },
    ),
    _msg(
        "pw.detector.spike_zero_median_lower",
        "at least {observed} events rose from a zero median; {basis}",
        {"observed": _FIXED0, "basis": ParamSpec(ParamKind.REF, ref_codes=_PW_BASES)},
        {
            "observed": 90.0,
            "basis": MessageRef("pw.detector.basis_above_p95", {"bound": 4.0}),
        },
    ),
    # Value drop.
    _msg(
        "pw.detector.drop_median_floor",
        "baseline median {median} is below the {floor} traffic floor",
        {"median": _FIXED1, "floor": _FIXED1},
        {"median": 12.0, "floor": 20.0},
    ),
    _msg(
        "pw.detector.drop_ratio_above",
        "observed/expected ratio {ratio} is above the {line} drop line",
        {"ratio": _FIXED2, "line": _FIXED2},
        {"ratio": 0.8, "line": 0.5},
    ),
    _msg(
        "pw.detector.drop_delta_below_min",
        "fall of {delta} events is below the {minimum} minimum absolute delta",
        {"delta": _FIXED0, "minimum": _FIXED0},
        {"delta": 30.0, "minimum": 50.0},
    ),
    _msg(
        "pw.detector.z_not_reached_low",
        "robust z={score} has not reached -{threshold}",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": -2.1, "threshold": 3.5},
    ),
    _msg(
        "pw.detector.mad_within_p05",
        "robust score is unavailable (MAD=0) and the value is within p05 {bound}",
        {"bound": _FIXED2},
        {"bound": 80.0},
    ),
    _msg(
        "pw.detector.basis_z_low",
        "robust z={score} (threshold -{threshold})",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": -6.2, "threshold": 3.5},
    ),
    _msg(
        "pw.detector.basis_below_p05",
        "robust score is unavailable (MAD=0); the value is below p05 {bound}",
        {"bound": _FIXED2},
        {"bound": 80.0},
    ),
    _msg(
        "pw.detector.drop_fired",
        "{observed} exact events is below the median {median} (-{delta}, {ratio}x); {basis}",
        {
            "observed": _FIXED0,
            "median": _FIXED1,
            "delta": _FIXED0,
            "ratio": _FIXED2,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_PW_BASES),
        },
        {
            "observed": 10.0,
            "median": 100.0,
            "delta": 90.0,
            "ratio": 0.1,
            "basis": MessageRef(
                "pw.detector.basis_z_low", {"score": -6.2, "threshold": 3.5}
            ),
        },
    ),
    # Value disappeared.
    _msg(
        "pw.detector.last_seen_unknown",
        "when the value was last observed is not known",
    ),
    _msg(
        "pw.detector.not_active",
        "the value was not observed within the last {days} days",
        {"days": _PLAIN},
        {"days": 7},
    ),
    _msg(
        "pw.detector.disappear_median_floor",
        "baseline median {median} is below the {floor} disappearance floor",
        {"median": _FIXED1, "floor": _FIXED1},
        {"median": 2.0, "floor": 5.0},
    ),
    _msg(
        "pw.detector.observed_positive",
        "the value carried {observed} exact events in this bucket",
        {"observed": _FIXED0},
        {"observed": 14.0},
    ),
    _msg(
        "pw.detector.window_bucket_missing",
        "the bucket at {at} inside the disappearance window was not supplied: "
        "its count is not zero",
        {"at": _ISO},
        {"at": _PW_AT},
    ),
    _msg(
        "pw.detector.window_membership",
        "membership changed inside the disappearance window",
    ),
    _msg(
        "pw.detector.window_count_unknown",
        "the value count at {at} is unknown, not zero",
        {"at": _ISO},
        {"at": _PW_AT},
    ),
    _msg(
        "pw.detector.window_coverage",
        "property coverage at {at} was below the floor",
        {"at": _ISO},
        {"at": _PW_AT},
    ),
    _msg(
        "pw.detector.streak_short",
        "the value has {streak} consecutive measured-zero bucket(s), below the "
        "required {required}",
        {"streak": _PLAIN, "required": _PLAIN},
        {"streak": 3, "required": 6},
    ),
    _msg(
        "pw.detector.disappeared_fired",
        "the value had zero parsed events for {streak} consecutive exact buckets; "
        "the seasonal median is {median} events",
        {"streak": _PLAIN, "median": _FIXED1},
        {"streak": 6, "median": 120.0},
    ),
    # New value and new combination.
    _msg(
        "pw.detector.novelty_unestablished",
        "the value's first appearance was not established",
    ),
    _msg(
        "pw.detector.established_hand_off",
        "established value: first seen {first_seen} in the history read before the "
        "watch went live at {hand_off}",
        {"first_seen": _ISO, "hand_off": _ISO},
        {"first_seen": _PW_AT, "hand_off": _PW_AT},
    ),
    _msg(
        "pw.detector.established_outside_window",
        "established value: first admitted {first_seen}, outside the "
        "{buckets}-bucket novelty window",
        {"first_seen": _ISO, "buckets": _PLAIN},
        {"first_seen": _PW_AT, "buckets": 12},
    ),
    _msg(
        "pw.detector.pinned_never_new",
        "pinned values are operator-named and are never classified as new",
    ),
    _msg(
        "pw.detector.learning_unknown",
        "learning: the watch's first collected bucket is unknown",
    ),
    _msg(
        "pw.detector.learning_day",
        "learning: day {day} of {days}",
        {"day": _PLAIN, "days": _PLAIN},
        {"day": 3, "days": 7},
    ),
    _msg(
        "pw.detector.grace_membership",
        "membership changed inside the first-appearance grace window",
    ),
    _msg(
        "pw.detector.history_incomplete",
        "novelty cannot be established: {reason}",
        {"reason": ParamSpec(ParamKind.REF, ref_codes=_PW_HISTORY_CHECKS)},
        {"reason": MessageRef("pw.history_check.not_divisible")},
    ),
    _msg(
        "pw.detector.novelty_inputs_unsupported",
        "the declared novelty inputs do not support a verdict",
    ),
    _msg(
        "pw.detector.combination_inputs_unsupported",
        "the declared combination inputs do not support a verdict",
    ),
    _msg(
        "pw.detector.admission_truncated",
        "the first-appearance bucket had truncated admission: the value appeared in a "
        "flood and is not individually new",
    ),
    _msg(
        "pw.detector.materiality_short",
        "{events} event(s) in {buckets} bucket(s) since first appearance has not reached "
        "{min_events} events in {min_buckets} buckets",
        {"events": _PLAIN, "buckets": _PLAIN, "min_events": _PLAIN, "min_buckets": _PLAIN},
        {"events": 12, "buckets": 1, "min_events": 20, "min_buckets": 2},
    ),
    _msg(
        "pw.detector.materiality_lower_bound",
        "{reason}; the materiality counts are lower bounds",
        {
            "reason": ParamSpec(
                ParamKind.REF, ref_codes=("pw.detector.materiality_short",)
            )
        },
        {
            "reason": MessageRef(
                "pw.detector.materiality_short",
                {"events": 12, "buckets": 1, "min_events": 20, "min_buckets": 2},
            )
        },
    ),
    _msg(
        "pw.detector.new_fired",
        "{events} events across {buckets} buckets since first appearance; {claim}",
        {
            "events": _PLAIN,
            "buckets": _PLAIN,
            "claim": ParamSpec(ParamKind.REF, ref_codes=_PW_CLAIMS),
        },
        {
            "events": 40,
            "buckets": 3,
            "claim": MessageRef("pw.detector.claim_new_value", {"min_events": 3}),
        },
    ),
    _msg(
        "pw.detector.new_fired_lower",
        "at least {events} events across {buckets} buckets since first appearance; {claim}",
        {
            "events": _PLAIN,
            "buckets": _PLAIN,
            "claim": ParamSpec(ParamKind.REF, ref_codes=_PW_CLAIMS),
        },
        {
            "events": 40,
            "buckets": 3,
            "claim": MessageRef("pw.detector.claim_new_value", {"min_events": 3}),
        },
    ),
    _msg(
        "pw.detector.claim_new_value",
        "the value stayed below {min_events} events in every bucket of the verified history",
        {"min_events": _PLAIN},
        {"min_events": 3},
    ),
    _msg(
        "pw.detector.claim_new_combination",
        "both components were tracked before, but never together in the verified history",
    ),
    _msg(
        "pw.detector.new_value_is_combination",
        "both tuple components were tracked before: this is classified only as a "
        "new combination",
    ),
    _msg("pw.detector.one_property", "a one-property watch has nothing to combine"),
    _msg(
        "pw.detector.components_missing",
        "the tuple's two component histories were not supplied",
    ),
    _msg(
        "pw.detector.component_untracked",
        "at least one component was not previously tracked: "
        "this is classified only as a new value",
    ),
    _msg(
        "pw.detector.component_untracked_below_admission",
        "at least one component was not previously tracked "
        "(seen only below the admission threshold): this is classified only as a new value",
    ),
    # --- end MSG-4b ---
    # --- MSG-7: alerts and custom-rule status reasons (Families 13 and 14) -------------------
    _text(
        "alert.threshold.crossed",
        "{name} crossed its threshold",
        name="Failed sign-ins",
    ),
    _msg(
        "alert.search.threshold.exact",
        "{name} returned {observed} ({operator} {threshold})",
        {
            "name": _TEXT,
            "observed": ParamSpec(ParamKind.NUMBER, style="general"),
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {"name": "Daily failures", "observed": 1204.0, "operator": "GT", "threshold": 50.0},
    ),
    _msg(
        "alert.search.threshold.lower_bound",
        "{name} returned at least {observed} ({operator} {threshold})",
        {
            "name": _TEXT,
            "observed": ParamSpec(ParamKind.NUMBER, style="general"),
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {"name": "Daily failures", "observed": 1204.0, "operator": "GT", "threshold": 50.0},
    ),
    _text(
        "alert.search.failure.title",
        "{name} is failing to run",
        name="Daily failures",
    ),
    _msg(
        "alert.search.failure.description",
        "{name} failed {failures} consecutive runs ({error_type})",
        {"name": _TEXT, "failures": _PLAIN, "error_type": _TEXT},
        {"name": "Daily failures", "failures": 1204, "error_type": "ConnectTimeout"},
    ),
    _fixed("alert.search.threshold.recovered", "result back within threshold"),
    _fixed("alert.search.failure.recovered", "search executed successfully"),
    _msg(
        "alert.custom_rule.description.exact",
        "{name} observed {observed} ({operator} {threshold})",
        {
            "name": _TEXT,
            "observed": ParamSpec(ParamKind.NUMBER, style="general"),
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {"name": "High traffic", "observed": 1204.0, "operator": "GT", "threshold": 50.0},
    ),
    _msg(
        "alert.custom_rule.description.exact_unit",
        "{name} observed {observed} {unit} ({operator} {threshold} {unit})",
        {
            "name": _TEXT,
            "observed": ParamSpec(ParamKind.NUMBER, style="general"),
            "unit": _TEXT,
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {
            "name": "High traffic",
            "observed": 1204.0,
            "unit": "events",
            "operator": "GT",
            "threshold": 50.0,
        },
    ),
    _msg(
        "alert.custom_rule.description.lower_bound",
        "{name} observed at least {observed} ({operator} {threshold})",
        {
            "name": _TEXT,
            "observed": ParamSpec(ParamKind.NUMBER, style="general"),
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {"name": "High traffic", "observed": 1204.0, "operator": "GT", "threshold": 50.0},
    ),
    _msg(
        "alert.custom_rule.description.lower_bound_unit",
        "{name} observed at least {observed} {unit} ({operator} {threshold} {unit})",
        {
            "name": _TEXT,
            "observed": ParamSpec(ParamKind.NUMBER, style="general"),
            "unit": _TEXT,
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {
            "name": "High traffic",
            "observed": 1204.0,
            "unit": "events",
            "operator": "GT",
            "threshold": 50.0,
        },
    ),
    _msg(
        "alert.custom_rule.description.unmeasured",
        "{name} observed unmeasured ({operator} {threshold})",
        {
            "name": _TEXT,
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
        },
        {"name": "High traffic", "operator": "GT", "threshold": 50.0},
    ),
    _msg(
        "alert.custom_rule.description.unmeasured_unit",
        "{name} observed unmeasured ({operator} {threshold} {unit})",
        {
            "name": _TEXT,
            "operator": ParamSpec(ParamKind.ENUM, enum_group="customRuleOperator"),
            "threshold": ParamSpec(ParamKind.NUMBER, style="general"),
            "unit": _TEXT,
        },
        {
            "name": "High traffic",
            "operator": "GT",
            "threshold": 50.0,
            "unit": "events",
        },
    ),
    _fixed("alert.custom_rule.recovered", "condition recovered"),
    _fixed("alert.custom_rule.deleted", "the rule was deleted"),
    _text(
        "alert.offense_collection.stale.title",
        "Offense collection is stale for {instance}",
        instance="Lab console",
    ),
    _fixed(
        "alert.offense_collection.stale.description",
        "The read-only offense collector has not completed within its configured freshness "
        "threshold.",
    ),
    _fixed(
        "alert.offense_collection.recovered",
        "Offense collection freshness recovered.",
    ),
    _text("alert.inventory.log_source.title", "New log source: {name}", name="Edge firewall"),
    _fixed(
        "alert.inventory.log_source.description",
        "A new non-internal log source requires SOC review.",
    ),
    _text(
        "alert.inventory.catchall_sender.title",
        "New sender on {source}: {identifier}",
        source="Catch-all source",
        identifier="host-1204",
    ),
    _fixed(
        "alert.inventory.catchall_sender.description",
        "A device that is not a configured log source is sending to a catch-all log source and "
        "requires SOC review.",
    ),
    _msg(
        "alert.inventory.catchall_sender.reviewed",
        "catch-all sender {target}",
        {
            "target": ParamSpec(
                ParamKind.ENUM,
                enum_group="catchallReviewStatus",
                style="lower",
            )
        },
        {"target": "REVIEWED"},
    ),
    _fixed("alert.inventory.log_source.reviewed", "log source reviewed"),
    _fixed("custom_rule.status.platform_off", "CUSTOM_RULES_ENABLED is off"),
    _fixed("custom_rule.status.disabled", "the rule is disabled"),
    _fixed("custom_rule.status.subject_retired", "the subject is retired"),
    _fixed(
        "custom_rule.status.family_not_collected",
        "the subject's family is not collected",
    ),
    _fixed("custom_rule.status.maintenance", "the source is in maintenance"),
    _fixed("custom_rule.status.bucket_not_measured", "the bucket was not measured"),
    _fixed("custom_rule.status.threshold_not_finite", "threshold_value is not finite"),
    _text(
        "custom_rule.status.operator_unknown",
        "operator {operator} is not known",
        operator="'XX'",
    ),
    _fixed("custom_rule.status.no_verdict", "the bucket supports no verdict"),
    _fixed(
        "custom_rule.status.lower_bound_below_threshold",
        "a lower bound below the threshold proves nothing",
    ),
    _fixed(
        "custom_rule.status.lower_bound_cannot_prove_under",
        "a lower bound cannot prove the reading is under the threshold",
    ),
    _msg(
        "custom_rule.status.stale.minute",
        "no bucket collected for this kind since {bucket} ({age})",
        {
            "bucket": ParamSpec(ParamKind.TIMESTAMP, style="minute"),
            "age": ParamSpec(ParamKind.DURATION, style="age"),
        },
        {"bucket": _PW_AT, "age": 5400},
    ),
    _msg(
        "custom_rule.status.stale.second",
        "no bucket collected for this kind since {bucket} ({age})",
        {
            "bucket": ParamSpec(ParamKind.TIMESTAMP),
            "age": ParamSpec(ParamKind.DURATION, style="age"),
        },
        {"bucket": _PW_AT.replace(second=7), "age": 90061},
    ),
    _fixed("custom_rule.reading.subject_missing", "no row for this subject in the bucket"),
    _fixed("custom_rule.reading.not_finite", "the reading is not finite"),
    _fixed("custom_rule.reading.bucket_partial", "the bucket is PARTIAL"),
    _fixed("custom_rule.reading.bucket_not_complete", "the bucket is not COMPLETE"),
    _fixed(
        "custom_rule.reading.class_no_enabled_source",
        "the class has no enabled, monitored source",
    ),
    _fixed(
        "custom_rule.reading.class_no_complete_source",
        "the class has no COMPLETE source bucket",
    ),
    _fixed(
        "custom_rule.reading.request_count_semantics_missing",
        "the bucket does not state whether the request count is exact",
    ),
    _fixed("custom_rule.reading.numerator_missing", "the numerator was not collected"),
    _fixed(
        "custom_rule.reading.unmapped_remainder_missing",
        "the unmapped remainder was not collected",
    ),
    _fixed(
        "custom_rule.reading.action_breakdown_missing",
        "the action breakdown was not collected",
    ),
    _fixed(
        "custom_rule.reading.action_denominator_lower_bound",
        "the action denominator is a lower bound",
    ),
    _fixed(
        "custom_rule.reading.action_denominator_zero",
        "the action denominator is zero",
    ),
    _fixed(
        "custom_rule.reading.response_breakdown_missing",
        "the response-code breakdown was not collected",
    ),
    _fixed(
        "custom_rule.reading.response_denominator_lower_bound",
        "the response-code denominator is a lower bound",
    ),
    _fixed(
        "custom_rule.reading.response_denominator_zero",
        "the response-code denominator is zero",
    ),
    _fixed("custom_rule.reading.dimension_missing", "the dimension was not collected"),
    _fixed(
        "custom_rule.reading.count_semantics_unknown",
        "the bucket does not state whether its counts are exact or lower bounds",
    ),
    _fixed(
        "custom_rule.reading.exact_required",
        "the detector needs exact counts and this count is a lower bound",
    ),
    _text(
        "custom_rule.family.collection_off",
        "{setting} is off",
        setting="waf_collection_enabled",
    ),
    _text(
        "custom_rule.family.unknown",
        "no collection family is known for subject kind {kind}",
        kind="UNKNOWN",
    ),
    _fixed("custom_rule.subject.missing", "the subject no longer exists"),
    _msg(
        "custom_rule.subject.endpoint_retired_at",
        "the endpoint was retired at {retired_at}",
        {"retired_at": _ISO},
        {"retired_at": _PW_AT},
    ),
    _fixed("custom_rule.subject.endpoint_retired", "the endpoint is retired"),
    _msg(
        "custom_rule.subject.entity_retired_at",
        "the entity was retired at {retired_at}",
        {"retired_at": _ISO},
        {"retired_at": _PW_AT},
    ),
    _fixed("custom_rule.subject.entity_retired", "the entity is retired"),
    _fixed("custom_rule.subject.value_unpinned", "the value is no longer pinned"),
    _fixed("custom_rule.subject.value_retired", "the value is retired"),
    _text(
        "custom_rule.subject.unknown_retirement",
        "no retirement rule is known for subject kind {kind}",
        kind="UNKNOWN",
    ),
    # --- end MSG-7 ---------------------------------------------------------------------------
    # --- MSG-1b: authoring reasons and remaining conflicts (Families 15, 11) -- keep contiguous ---
    # Custom-rule draft validation (`app/custom_rules/validation.py`, the rule routes). A quoted
    # user value (`{metric}`, `{value}`, `{operator}`) arrives already quoted by `repr`.
    _mixed(
        "ruledraft.subject_needs_column",
        "{kind} rules need {column} set",
        text={"kind": "LOG_SOURCE", "column": "log_source_id"},
    ),
    _mixed(
        "ruledraft.column_must_be_empty",
        "{column} must stay empty for a {kind} rule",
        text={"column": "waf_endpoint_id", "kind": "LOG_SOURCE"},
    ),
    _mixed(
        "ruledraft.source_class_unknown",
        "source_class {value} is not a known source class",
        text={"value": "'NOT_A_CLASS'"},
    ),
    _mixed(
        "ruledraft.metric_unknown",
        "metric {metric} is not in the custom-rule catalog",
        text={"metric": "'events'"},
    ),
    _mixed(
        "ruledraft.metric_wrong_kind",
        "metric {metric} measures a {metric_kind} subject, not a {kind} one",
        text={"metric": "events", "metric_kind": "WAF_ENDPOINT", "kind": "LOG_SOURCE"},
    ),
    _fixed("ruledraft.threshold_not_finite", "threshold_value must be a finite number"),
    _fixed("ruledraft.threshold_negative", "threshold_value must be at least 0"),
    _mixed(
        "ruledraft.threshold_share",
        "a {metric} threshold is a share and must lie strictly between 0 and 1",
        text={"metric": "waf_block_ratio"},
    ),
    _mixed(
        "ruledraft.operator_unknown",
        "operator {operator} is not one of GT, GE, LT, LE, EQ",
        text={"operator": "'NE'"},
    ),
    _fixed("ruledraft.sustain_min", "sustain_buckets must be at least 1"),
    _fixed("ruledraft.recovery_min", "recovery_buckets must be at least 1"),
    _mixed(
        "ruledraft.enabled_cap",
        "at most {max_enabled} rules may be enabled at once ({enabled_count} already are)",
        count={"max_enabled": 100, "enabled_count": 100},
    ),
    _mixed(
        "ruledraft.null_field",
        "{field} must not be null",
        text={"field": "metric"},
    ),
    _mixed(
        "ruledraft.log_source_missing",
        "log source {id} does not exist",
        text={"id": "00000000-0000-0000-0000-000000000001"},
    ),
    _mixed(
        "ruledraft.waf_endpoint_missing",
        "waf endpoint {id} does not exist",
        text={"id": "00000000-0000-0000-0000-000000000001"},
    ),
    _mixed(
        "ruledraft.ips_entity_missing",
        "ips entity {id} does not exist",
        text={"id": "00000000-0000-0000-0000-000000000001"},
    ),
    _mixed(
        "ruledraft.pw_value_missing",
        "property-watch value {id} does not exist",
        text={"id": "00000000-0000-0000-0000-000000000001"},
    ),
    _mixed(
        "ruledraft.pw_value_not_pinned",
        "property-watch value {id} is not pinned; a rule can watch only a "
        "pinned value, so pin it on its property watch first",
        text={"id": "00000000-0000-0000-0000-000000000001"},
    ),
    # Search authoring (`app/services/aql_validator.py`, `app/schemas/search.py`). The keyword and
    # dataset lists arrive already joined by ", " (identifiers); a window length keeps the Python
    # spelling of the float it was computed as (`48.0`).
    _fixed("search.aql.empty", "query is empty"),
    _fixed("search.aql.multiple_statements", "multiple statements are not allowed"),
    _fixed("search.aql.not_select", "query must be a single SELECT statement"),
    _mixed(
        "search.aql.disallowed_keywords",
        "disallowed keyword(s): {keywords}",
        text={"keywords": "DROP, INSERT"},
    ),
    _fixed("search.aql.no_dataset", "query must select FROM events or flows"),
    _mixed(
        "search.aql.dataset_not_permitted",
        "dataset(s) not permitted: {datasets}",
        text={"datasets": "USERS"},
    ),
    _fixed(
        "search.aql.no_time_window",
        "query must include a bounded time window (LAST N ..., or START/STOP)",
    ),
    _mixed(
        "search.aql.time_range_exceeded",
        "time range {window_hours}h exceeds the maximum {max_hours}h",
        text={"window_hours": "48.0"},
        count={"max_hours": 24},
    ),
    _mixed(
        "search.aql.limit_exceeded",
        "LIMIT {limit} exceeds the maximum {max_rows} rows",
        count={"limit": 20000, "max_rows": 10000},
    ),
    _mixed(
        "search.threshold.unreachable",
        "threshold {operator} {threshold} can never be reached: a run counts "
        "at most max_result_rows ({max_result_rows}) rows; raise max_result_rows above "
        "the threshold or lower the threshold",
        text={"operator": "GT", "threshold": "10001"},
        count={"max_result_rows": 10000},
    ),
    _mixed(
        "search.enabled_cap",
        "the limit is {limit} enabled searches",
        count={"limit": 50},
    ),
    # Classification rule conflicts (`app/api/routes/classification_rules.py`).
    _fixed(
        "classification.rule.duplicate",
        "a rule with this instance, kind and pattern already exists",
    ),
    _fixed(
        "classification.rule.built_in_edit",
        "a built-in rule can only be enabled or disabled",
    ),
    _fixed(
        "classification.rule.built_in_delete",
        "a built-in rule cannot be deleted; disable it instead",
    ),
    # WAF and IPS profile conflicts (`_conflict_detail` in `routes/waf.py` and `routes/ips.py`,
    # the profile services). The message of the conflict body gains `code` and `params`.
    _fixed(
        "validation.profile.conflict.missing",
        "the profile does not exist; create it with expected_profile_version null",
    ),
    _mixed(
        "validation.profile.conflict.version",
        "profile version conflict; current version is {current}",
        count={"current": 4},
    ),
    _mixed(
        "validation.profile.conflict.running",
        "validation of version {profile_version} is already running since {since}",
        text={"since": "2026-10-28T10:00:00+00:00"},
        count={"profile_version": 4},
    ),
    _mixed(
        "validation.profile.conflict.not_validated",
        "profile version {profile_version} has not validated "
        "(status {validation_status}); run validation first",
        count={"profile_version": 4},
        validation_status=(
            ParamSpec(ParamKind.ENUM, enum_group="profileValidationStatus"),
            "INVALID",
        ),
    ),
    # Profile input refusals (`app/waf/profile.py`, `app/ips/profile.py`): a 422 `{message}` body.
    _mixed(
        "validation.waf.input.property_name",
        "invalid QRadar property name {name}: letters, digits, underscore, "
        "space and hyphen only, at most 64 characters",
        text={"name": "'bad;name'"},
    ),
    _mixed(
        "validation.waf.input.action_value",
        "invalid {outcome} action value {value}: a printable token of at most "
        "{max_length} characters",
        text={"outcome": "BLOCKED", "value": "''"},
        count={"max_length": 64},
    ),
    _mixed(
        "validation.waf.input.action_overlap",
        "action values mapped to both BLOCKED and ALLOWED: {values}",
        text={"values": "['deny']"},
    ),
    _fixed("validation.waf.input.action_needs_property", "an action map needs an action property"),
    _fixed(
        "validation.ips.input.normalized_field",
        "normalized field is not in the IPS allowlist",
    ),
    _fixed(
        "validation.ips.input.property_name",
        "invalid QRadar property name: letters, digits, underscore, space and "
        "hyphen only, at most 64 characters",
    ),
    _mixed(
        "validation.ips.input.action_count",
        "{outcome} action values exceed the {max_count}-value limit",
        text={"outcome": "BLOCKED"},
        count={"max_count": 20},
    ),
    _mixed(
        "validation.ips.input.action_value",
        "invalid {outcome} action value: use a printable token of at most "
        "{max_length} characters",
        text={"outcome": "ALLOWED"},
        count={"max_length": 64},
    ),
    _fixed(
        "validation.ips.input.action_overlap",
        "an action value is mapped to both BLOCKED and ALLOWED",
    ),
    _fixed(
        "validation.ips.input.action_needs_field",
        "an action map needs an ACTION field mapping",
    ),
    # --- end MSG-1b ---
    # --- MSG-5: Phase A detector sentences and lifecycle transition reasons (Families 1, 2) ---
    # --- keep contiguous. The detectors are `app/anomaly/detectors.py`; the lifecycle reasons
    # --- come from `app/anomaly/lifecycle.py`, which every engine shares.
    # Silence (`detect_no_events`).
    _msg(
        "anomaly.detector.no_events_bucket_incomplete",
        "bucket is {completeness}; absence of events is not evidence of silence",
        {"completeness": _A_COMPLETENESS},
        {"completeness": "PARTIAL"},
    ),
    _msg(
        "anomaly.detector.no_events_not_expected",
        "source is not expected to be active this interval",
    ),
    _msg(
        "anomaly.detector.no_events_normally_inactive",
        "this source is normally inactive at this hour",
    ),
    _msg(
        "anomaly.detector.no_events_no_baseline",
        "no reliable activity baseline for this weekday/hour cell yet",
    ),
    _msg(
        "anomaly.detector.no_events_grace",
        "empty bucket within the {grace}-bucket silence grace period",
        {"grace": _PLAIN},
        {"grace": 1},
    ),
    _msg(
        "anomaly.detector.no_events_streak",
        "no events for {streak} consecutive bucket(s) where the baseline "
        "expects about {expected} events",
        {"streak": _PLAIN, "expected": _FIXED0},
        {"streak": 3, "expected": 120.0},
    ),
    # Volume drop and spike (`_volume`).
    _msg(
        "anomaly.detector.volume_bucket_incomplete",
        "metric bucket is {completeness}, not a whole observation",
        {"completeness": _A_COMPLETENESS},
        {"completeness": "MISSING"},
    ),
    _msg(
        "anomaly.detector.volume_no_baseline",
        "no volume baseline for this weekday/hour cell yet",
    ),
    _msg(
        "anomaly.detector.volume_baseline_samples",
        "baseline has {samples} sample(s), below the minimum required "
        "for a defensible verdict",
        {"samples": _PLAIN},
        {"samples": 4},
    ),
    _msg(
        "anomaly.detector.volume_min_events",
        "{events} events/bucket is below the {minimum} minimum for a defensible volume verdict",
        {"events": _FIXED0, "minimum": _FIXED0},
        {"events": 12.0, "minimum": 50.0},
    ),
    _msg(
        "anomaly.detector.volume_drop_undefined",
        "baseline expects no traffic; a drop is not defined",
    ),
    _msg(
        "anomaly.detector.volume_ratio_not_reached",
        "observed/expected ratio {ratio} has not reached {limit}",
        {"ratio": _FIXED2, "limit": _FIXED2},
        {"ratio": 1.4, "limit": 2.0},
    ),
    _msg(
        "anomaly.detector.volume_ratio_not_fallen",
        "observed/expected ratio {ratio} has not fallen to {limit}",
        {"ratio": _FIXED2, "limit": _FIXED2},
        {"ratio": 0.8, "limit": 0.5},
    ),
    _msg(
        "anomaly.detector.volume_delta_below_min",
        "change of {delta} events/bucket is below the {minimum} minimum absolute delta",
        {"delta": _FIXED0, "minimum": _FIXED0},
        {"delta": 30.0, "minimum": 50.0},
    ),
    _msg(
        "anomaly.detector.volume_z_not_reached",
        "robust z={score} has not reached the ±{threshold} threshold",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 2.1, "threshold": 3.5},
    ),
    _msg(
        "anomaly.detector.volume_mad_inside_band",
        "robust score unavailable (MAD=0) and observed value is inside "
        "the expected band [{lower}, {upper}]",
        {"lower": _FIXED2, "upper": _FIXED2},
        {"lower": 1.5, "upper": 2.5},
    ),
    _msg(
        "anomaly.detector.basis_z",
        "robust z={score} (threshold ±{threshold})",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 6.2, "threshold": 3.5},
    ),
    _msg(
        "anomaly.detector.basis_mad_outside",
        "robust score unavailable (MAD=0); observed value is outside "
        "the expected band [{lower}, {upper}]",
        {"lower": _FIXED2, "upper": _FIXED2},
        {"lower": 1.5, "upper": 2.5},
    ),
    _msg(
        "anomaly.detector.ratio_times_expected",
        "{ratio}x expected",
        {"ratio": _FIXED2},
        {"ratio": 5.2},
    ),
    _msg("anomaly.detector.ratio_no_traffic", "expected no traffic"),
    _msg(
        "anomaly.detector.volume_above",
        "average EPS {observed} is above the baseline median {expected} "
        "({ratio_text}, {delta} events/bucket); {basis}",
        {
            "observed": _FIXED2,
            "expected": _FIXED2,
            "ratio_text": ParamSpec(ParamKind.REF, ref_codes=_A_RATIO_TEXTS),
            "delta": _FIXED0,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_A_BASES),
        },
        {
            "observed": 12.4,
            "expected": 2.4,
            "ratio_text": _A_VOLUME_RATIO_SAMPLE,
            "delta": 3600.0,
            "basis": _A_VOLUME_BASIS_SAMPLE,
        },
    ),
    _msg(
        "anomaly.detector.volume_below",
        "average EPS {observed} is below the baseline median {expected} "
        "({ratio_text}, {delta} events/bucket); {basis}",
        {
            "observed": _FIXED2,
            "expected": _FIXED2,
            "ratio_text": ParamSpec(ParamKind.REF, ref_codes=_A_RATIO_TEXTS),
            "delta": _FIXED0,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_A_BASES),
        },
        {
            "observed": 0.4,
            "expected": 2.4,
            "ratio_text": MessageRef("anomaly.detector.ratio_times_expected", {"ratio": 0.17}),
            "delta": 600.0,
            "basis": _A_VOLUME_BASIS_SAMPLE,
        },
    ),
    # Parsing degradation, unknown-event spike, timestamp delay.
    _msg("anomaly.detector.parsing_no_data", "no parse-ratio data"),
    _msg(
        "anomaly.detector.parsing_below_floor",
        "field-extraction ratio {ratio} fell below floor {floor}",
        {"ratio": _FIXED2, "floor": _FIXED2},
        {"ratio": 0.62, "floor": 0.9},
    ),
    _msg("anomaly.detector.unknown_no_events", "no events to classify"),
    _msg(
        "anomaly.detector.unknown_above_ceiling",
        "unknown-event ratio {ratio} exceeds ceiling {ceiling}",
        {"ratio": _FIXED2, "ceiling": _FIXED2},
        {"ratio": 0.4, "ceiling": 0.2},
    ),
    _msg("anomaly.detector.delay_no_measurement", "no delay measurement"),
    _msg(
        "anomaly.detector.delay_exceeds",
        "event delay {delay}s exceeds {limit}s",
        {"delay": _FIXED0, "limit": _FIXED0},
        {"delay": 900.0, "limit": 300.0},
    ),
    # Cardinality drop, collection error, repeated payload.
    _msg("anomaly.detector.cardinality_no_baseline", "no reliable cardinality baseline"),
    _msg(
        "anomaly.detector.cardinality_dropped",
        "distinct source IPs {count} dropped below {fraction} of baseline median {median}",
        {"count": _PLAIN, "fraction": _PERCENT, "median": _FIXED0},
        {"count": 3, "fraction": 0.5, "median": 40.0},
    ),
    _msg(
        "anomaly.detector.collection_errors",
        "{count} collection error(s) in the interval",
        {"count": _PLAIN},
        {"count": 2},
    ),
    _msg("anomaly.detector.payload_no_signature", "no payload signature"),
    _msg(
        "anomaly.detector.payload_repeated",
        "identical payload signature repeated for {streak} consecutive intervals",
        {"streak": _PLAIN},
        {"streak": 4},
    ),
    # The healthy default and the engine's own overwrite of a demoted verdict.
    _msg("anomaly.detector.within_range", "within expected range"),
    _msg(
        "anomaly.detector.suppressed_by_no_events",
        "suppressed: no events this interval",
    ),
    # Lifecycle transition reasons (`app/anomaly/lifecycle.py`, shared by every engine).
    _msg("anomaly.lifecycle.already_suppressed", "already suppressed"),
    _msg("anomaly.lifecycle.suppressed", "suppressed"),
    _msg("anomaly.lifecycle.suppression_lifted", "suppression lifted"),
    _msg("anomaly.lifecycle.no_verdict", "no verdict possible"),
    _msg("anomaly.lifecycle.hold_not_judgeable", "holding state; bucket not judgeable"),
    _msg("anomaly.lifecycle.hold_unknown", "holding state; detector returned unknown"),
    _msg(
        "anomaly.lifecycle.opened",
        "{count} consecutive abnormal bucket(s)",
        {"count": _PLAIN},
        {"count": 1},
    ),
    _msg("anomaly.lifecycle.first_abnormal", "first abnormal bucket"),
    _msg(
        "anomaly.lifecycle.confirmed",
        "{count} consecutive abnormal bucket(s) reached the confirmation threshold of {threshold}",
        {"count": _PLAIN, "threshold": _PLAIN},
        {"count": 3, "threshold": 3},
    ),
    _msg("anomaly.lifecycle.still_confirming", "still confirming"),
    _msg("anomaly.lifecycle.relapsed", "relapsed before recovery completed"),
    _msg("anomaly.lifecycle.still_abnormal", "still abnormal"),
    _msg("anomaly.lifecycle.returned_to_normal", "returned to normal before opening"),
    _msg("anomaly.lifecycle.resolved_one_normal", "1 normal bucket observed"),
    _msg("anomaly.lifecycle.normal_observed", "normal bucket observed"),
    _msg(
        "anomaly.lifecycle.recovered",
        "{count} consecutive normal bucket(s) reached the recovery threshold of {threshold}",
        {"count": _PLAIN, "threshold": _PLAIN},
        {"count": 2, "threshold": 2},
    ),
    _msg("anomaly.lifecycle.still_recovering", "still recovering"),
    _msg("anomaly.lifecycle.baseline_adequate", "baseline became adequate"),
    _msg("anomaly.lifecycle.within_range", "within expected range"),
    # Phase A engine side effects (`app/anomaly/engine.py`).
    _msg("anomaly.suppression.maintenance", "maintenance window"),
    _msg("anomaly.resolution.recovered", "condition recovered"),
    # --- end MSG-5 ---
    # --- MSG-6: WAF anomaly sentences (Family 3) -- keep contiguous --------------------------
    # The scope decision (`app.waf.measurement`) and the detectors' own scope fallbacks.
    _msg(
        "waf.scope.unstated",
        "the bucket does not state whether its counts are exact or lower bounds",
    ),
    _msg(
        "waf.scope.lower_bound_increase",
        "the count is a lower bound: an increase can be proven, normal behaviour cannot",
    ),
    _msg(
        "waf.scope.needs_exact",
        "the detector needs exact counts and this count is a lower bound",
    ),
    _msg("waf.scope.unattributed", "some events could not be attributed"),
    _msg("waf.scope.unclassified", "some values were unclassified"),
    _msg("waf.scope.inputs_unsupported", "the inputs do not support a verdict"),
    _msg("waf.scope.composition_exact", "composition needs exact measurements"),
    # Why a baseline cannot judge a bucket (`app.waf.semantics`).
    _msg(
        "waf.semantics.bucket_unidentified",
        "the bucket carries no metric-semantics identity; it was collected before "
        "identities were recorded and cannot be judged",
    ),
    _msg(
        "waf.semantics.baseline_unidentified",
        "the baseline cell carries no metric-semantics identity; rebuild required",
    ),
    _msg(
        "waf.semantics.mismatch",
        "the baseline was built under '{baseline}' but the bucket was collected "
        "under '{bucket}'; rebuild required",
        {"baseline": _TEXT, "bucket": _TEXT},
        {
            "baseline": "request_count/v1;normalizer/v1",
            "bucket": "request_count/v2;normalizer/v1",
        },
    ),
    # Request-rate spike.
    _msg(
        "waf.rate.bucket_not_whole",
        "bucket is {completeness}, not a whole observation",
        {"completeness": ParamSpec(ParamKind.ENUM, enum_group="wafBucketCompleteness")},
        {"completeness": "partial"},
    ),
    _msg(
        "waf.rate.not_proven_lower_bound",
        "at least {observed} requests were attributed to this endpoint, but the count is a "
        "lower bound and {reason}; a normal verdict cannot be concluded from a lower bound",
        {
            "observed": _FIXED0,
            "reason": ParamSpec(ParamKind.REF, ref_codes=_WAF_RATE_NOT_PROVEN),
        },
        {
            "observed": 120.0,
            "reason": MessageRef(
                "waf.rate.below_min_volume", {"observed": 12.0, "minimum": 20.0}
            ),
        },
    ),
    _msg(
        "waf.rate.coverage_low",
        "only {covered} of this source's events carried a parseable endpoint in this bucket",
        {"covered": _PERCENT},
        {"covered": 0.62},
    ),
    _msg("waf.rate.no_baseline", "no request-rate baseline for this weekday/hour cell yet"),
    _msg(
        "waf.rate.baseline_samples",
        "baseline has {samples} sample(s), below the minimum required for a defensible verdict",
        {"samples": _PLAIN},
        {"samples": 3},
    ),
    _msg(
        "waf.rate.below_min_volume",
        "{observed} requests/bucket is below the {minimum} minimum for a defensible verdict",
        {"observed": _FIXED0, "minimum": _FIXED0},
        {"observed": 12.0, "minimum": 20.0},
    ),
    _msg(
        "waf.rate.ratio_not_reached",
        "observed/expected ratio {ratio} has not reached {threshold}",
        {"ratio": _FIXED2, "threshold": _FIXED2},
        {"ratio": 1.4, "threshold": 2.0},
    ),
    _msg(
        "waf.rate.delta_below_min",
        "increase of {delta} requests/bucket is below the {minimum} minimum absolute delta",
        {"delta": _FIXED0, "minimum": _FIXED0},
        {"delta": 30.0, "minimum": 50.0},
    ),
    _msg(
        "waf.rate.z_not_reached",
        "robust z={score} has not reached the +{threshold} threshold",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 2.1, "threshold": 3.5},
    ),
    _msg(
        "waf.rate.mad_within_band",
        "robust score unavailable (MAD=0) and observed value is inside the expected band "
        "[{lower}, {upper}]",
        {"lower": _FIXED2, "upper": _FIXED2},
        {"lower": 80.0, "upper": 120.0},
    ),
    _msg(
        "waf.rate.ratio_multiple",
        "{ratio}x expected",
        {"ratio": _FIXED2},
        {"ratio": 3.0},
    ),
    _msg("waf.rate.ratio_none", "expected no traffic"),
    _msg(
        "waf.rate.fired",
        "{observed} requests is above the baseline median {expected} "
        "({ratio_text}, +{delta} requests/bucket); {basis}",
        {
            "observed": _FIXED0,
            "expected": _FIXED1,
            "ratio_text": ParamSpec(ParamKind.REF, ref_codes=_WAF_RATIO_TEXTS),
            "delta": _FIXED0,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "observed": 520.0,
            "expected": 100.0,
            "ratio_text": MessageRef("waf.rate.ratio_multiple", {"ratio": 5.2}),
            "delta": 420.0,
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    _msg(
        "waf.rate.fired_lower",
        "at least {observed} requests (a lower bound: {scope}) is above the baseline "
        "median {expected} ({ratio_text}, +{delta} requests/bucket); {basis}",
        {
            "observed": _FIXED0,
            "scope": ParamSpec(ParamKind.REF, ref_codes=_WAF_SCOPES),
            "expected": _FIXED1,
            "ratio_text": ParamSpec(ParamKind.REF, ref_codes=_WAF_RATIO_TEXTS),
            "delta": _FIXED0,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "observed": 520.0,
            "scope": MessageRef("waf.scope.lower_bound_increase"),
            "expected": 100.0,
            "ratio_text": MessageRef("waf.rate.ratio_multiple", {"ratio": 5.2}),
            "delta": 420.0,
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    # The bases a firing sentence ends with, and the score guard's refusals.
    _msg(
        "waf.basis.z",
        "robust z={score} (threshold +{threshold})",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 6.2, "threshold": 3.5},
    ),
    _msg(
        "waf.basis.mad_outside_band",
        "robust score unavailable (MAD=0); observed value is outside the expected band "
        "[{lower}, {upper}]",
        {"lower": _FIXED2, "upper": _FIXED2},
        {"lower": 80.0, "upper": 120.0},
    ),
    _msg(
        "waf.basis.z_not_reached",
        "robust z={score} has not reached +{threshold}",
        {"score": _FIXED2, "threshold": _REPR},
        {"score": 2.1, "threshold": 3.5},
    ),
    _msg(
        "waf.basis.mad_within_p95",
        "robust score unavailable (MAD=0) and {observed} is within the historical p95 {bound}",
        {"observed": _PCT1, "bound": _PCT1},
        {"observed": 0.31, "bound": 0.35},
    ),
    _msg(
        "waf.basis.mad_above_p95",
        "robust score unavailable (MAD=0); {observed} is above the historical p95 {bound}",
        {"observed": _PCT1, "bound": _PCT1},
        {"observed": 0.41, "bound": 0.35},
    ),
    # Guards shared by the ratio and concentration detectors.
    _msg(
        "waf.guard.breakdown_not_collected",
        "the {breakdown} breakdown was not collected for this bucket: the value is "
        "unavailable, not zero",
        {"breakdown": _TEXT},
        {"breakdown": "ACTION"},
    ),
    _msg(
        "waf.guard.breakdown_failed",
        "the {breakdown} breakdown failed for this bucket: the value is unavailable, not zero",
        {"breakdown": _TEXT},
        {"breakdown": "ACTION"},
    ),
    _msg(
        "waf.guard.source_coverage_low",
        "only {covered} of this source's events parsed to an endpoint, below the {floor} "
        "coverage floor: the unparsed requests may differ in kind from the parsed ones, "
        "so the visible proportion bounds nothing",
        {"covered": _PERCENT, "floor": _PERCENT},
        {"covered": 0.62, "floor": 0.9},
    ),
    _msg(
        "waf.guard.no_baseline",
        "no {metric} baseline for this weekday/hour cell yet",
        {"metric": _TEXT},
        {"metric": "block_ratio"},
    ),
    _msg(
        "waf.guard.baseline_samples",
        "the {metric} baseline has {samples} material bucket(s), below the minimum for a "
        "defensible verdict",
        {"metric": _TEXT, "samples": _PLAIN},
        {"metric": "block_ratio", "samples": 3},
    ),
    _msg(
        "waf.guard.baseline_no_denominator",
        "the {metric} baseline has no pooled denominator",
        {"metric": _TEXT},
        {"metric": "block_ratio"},
    ),
    _msg(
        "waf.guard.not_proven_lower_bound",
        "{reason}; the numerator is a lower bound ({scope}), so a normal verdict cannot be "
        "concluded from it",
        {
            "reason": ParamSpec(ParamKind.REF, ref_codes=_WAF_GUARD_NOT_PROVEN),
            "scope": ParamSpec(ParamKind.REF, ref_codes=_WAF_SCOPES),
        },
        {
            "reason": MessageRef("waf.basis.z_not_reached", {"score": 2.1, "threshold": 3.5}),
            "scope": MessageRef("waf.scope.unclassified"),
        },
    ),
    # Noun phrases the ratio detectors insert into their sentences.
    _msg("waf.noun.blocked_requests", "blocked requests"),
    _msg("waf.noun.waf_action_requests", "requests carrying a WAF action"),
    _msg("waf.noun.http_5xx", "HTTP 5xx responses"),
    _msg("waf.noun.http_4xx", "HTTP 4xx responses"),
    _msg("waf.noun.response_code_requests", "requests carrying a response code"),
    # The endpoint a sentence is about.
    _msg(
        "waf.subject.named",
        "{method} {endpoint}",
        {"method": _TEXT, "endpoint": _TEXT},
        {"method": "GET", "endpoint": "/api/login"},
    ),
    _msg("waf.subject.this_endpoint", "this endpoint"),
    # Ratio detectors (block, 5xx and 4xx).
    _msg(
        "waf.ratio.field_coverage_low",
        "only {covered} of the endpoint's requests carried the {field} field, below the "
        "{floor} coverage floor",
        {"covered": _PERCENT, "field": _TEXT, "floor": _PERCENT},
        {"covered": 0.7, "field": "action", "floor": 0.9},
    ),
    _msg(
        "waf.ratio.numerator_below_min",
        "{count} {noun} is below the {minimum} minimum; no material rise in the "
        "proportion is possible",
        {
            "count": _PLAIN,
            "noun": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "minimum": _PLAIN,
        },
        {"count": 4, "noun": MessageRef("waf.noun.blocked_requests"), "minimum": 10},
    ),
    _msg(
        "waf.ratio.denominator_below_min",
        "{count} {noun} is below the {minimum} minimum denominator for a ratio verdict",
        {
            "count": _PLAIN,
            "noun": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "minimum": _PLAIN,
        },
        {
            "count": 30,
            "noun": MessageRef("waf.noun.waf_action_requests"),
            "minimum": 50,
        },
    ),
    _msg(
        "waf.ratio.rise_below_min",
        "{observed} is {rise} from the expected {expected}, below the {minimum} minimum rise",
        {
            "observed": _PCT1,
            "rise": _POINTS1,
            "expected": _PCT1,
            "minimum": _POINTS0U,
        },
        {"observed": 0.35, "rise": 0.05, "expected": 0.3, "minimum": 0.1},
    ),
    _msg(
        "waf.ratio.multiple_below_min",
        "{observed} is {multiple}x the expected {expected}, below {minimum}x",
        {"observed": _PCT1, "multiple": _FIXED2, "expected": _PCT1, "minimum": _FIXED2},
        {"observed": 0.45, "multiple": 1.5, "expected": 0.3, "minimum": 2.0},
    ),
    _msg(
        "waf.ratio.fired",
        "{count} of {total} {denominator} to {subject} were {numerator} ({share}), against "
        "an expected {expected} from {history} historical requests ({rise}, {multiple}x); "
        "{basis}",
        {
            "count": _PLAIN,
            "total": _PLAIN,
            "denominator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "numerator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "share": _PCT1,
            "expected": _PCT1,
            "history": _FIXED0,
            "rise": _POINTS1,
            "multiple": _FIXED1,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "count": 80,
            "total": 100,
            "denominator": MessageRef("waf.noun.waf_action_requests"),
            "subject": MessageRef("waf.subject.this_endpoint"),
            "numerator": MessageRef("waf.noun.blocked_requests"),
            "share": 0.8,
            "expected": 0.2,
            "history": 5000.0,
            "rise": 0.6,
            "multiple": 4.0,
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    _msg(
        "waf.ratio.fired_lower",
        "at least {count} of {total} {denominator} to {subject} were {numerator} (at least "
        "{share}), against an expected {expected} from {history} historical requests "
        "({rise}, {multiple}x); {basis}",
        {
            "count": _PLAIN,
            "total": _PLAIN,
            "denominator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "numerator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "share": _PCT1,
            "expected": _PCT1,
            "history": _FIXED0,
            "rise": _POINTS1,
            "multiple": _FIXED1,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "count": 80,
            "total": 100,
            "denominator": MessageRef("waf.noun.waf_action_requests"),
            "subject": MessageRef("waf.subject.this_endpoint"),
            "numerator": MessageRef("waf.noun.blocked_requests"),
            "share": 0.8,
            "expected": 0.2,
            "history": 5000.0,
            "rise": 0.6,
            "multiple": 4.0,
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    _msg(
        "waf.ratio.fired_no_multiple",
        "{count} of {total} {denominator} to {subject} were {numerator} ({share}), against "
        "an expected {expected} from {history} historical requests ({rise}); {basis}",
        {
            "count": _PLAIN,
            "total": _PLAIN,
            "denominator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "numerator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "share": _PCT1,
            "expected": _PCT1,
            "history": _FIXED0,
            "rise": _POINTS1,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "count": 80,
            "total": 100,
            "denominator": MessageRef("waf.noun.waf_action_requests"),
            "subject": MessageRef("waf.subject.this_endpoint"),
            "numerator": MessageRef("waf.noun.blocked_requests"),
            "share": 0.8,
            "expected": 0.0,
            "history": 5000.0,
            "rise": 0.8,
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    _msg(
        "waf.ratio.fired_lower_no_multiple",
        "at least {count} of {total} {denominator} to {subject} were {numerator} (at least "
        "{share}), against an expected {expected} from {history} historical requests "
        "({rise}); {basis}",
        {
            "count": _PLAIN,
            "total": _PLAIN,
            "denominator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "numerator": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOUNS),
            "share": _PCT1,
            "expected": _PCT1,
            "history": _FIXED0,
            "rise": _POINTS1,
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "count": 80,
            "total": 100,
            "denominator": MessageRef("waf.noun.waf_action_requests"),
            "subject": MessageRef("waf.subject.this_endpoint"),
            "numerator": MessageRef("waf.noun.blocked_requests"),
            "share": 0.8,
            "expected": 0.0,
            "history": 5000.0,
            "rise": 0.8,
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    # Client concentration.
    _msg(
        "waf.concentration.attributed_low",
        "only {covered} of the endpoint's requests were attributed to a source IP, below "
        "the {floor} coverage floor",
        {"covered": _PERCENT, "floor": _PERCENT},
        {"covered": 0.7, "floor": 0.9},
    ),
    _msg(
        "waf.concentration.top_below_min",
        "the largest source IP sent {top} requests, below the {minimum} minimum for a "
        "material concentration",
        {"top": _PLAIN, "minimum": _PLAIN},
        {"top": 12, "minimum": 20},
    ),
    _msg(
        "waf.concentration.endpoint_below_min",
        "{count} requests is below the {minimum} minimum for a composition verdict",
        {"count": _PLAIN, "minimum": _PLAIN},
        {"count": 30, "minimum": 50},
    ),
    _msg(
        "waf.concentration.rise_below_min",
        "the largest source IP's share {share} is {rise} from the expected {expected}, "
        "below the {minimum} minimum rise",
        {"share": _PCT1, "rise": _POINTS1, "expected": _PCT1, "minimum": _POINTS0U},
        {"share": 0.35, "rise": 0.05, "expected": 0.3, "minimum": 0.2},
    ),
    _msg(
        "waf.concentration.volume_with_baseline",
        "endpoint volume {requests} requests against an expected {expected}",
        {"requests": _PLAIN, "expected": _REPR},
        {"requests": 480, "expected": 100.0},
    ),
    _msg(
        "waf.concentration.volume_no_baseline",
        "endpoint volume {requests} requests (no reliable volume baseline)",
        {"requests": _PLAIN},
        {"requests": 480},
    ),
    _msg(
        "waf.concentration.fired",
        "the largest source IP sent {top} of {total} requests to {subject} ({share}), "
        "against an expected share of {expected} ({rise}); the five largest sent "
        "{top5}; {volume}; {basis}",
        {
            "top": _PLAIN,
            "total": _PLAIN,
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "share": _PCT1,
            "expected": _PCT1,
            "rise": _POINTS1,
            "top5": _PCT1,
            "volume": ParamSpec(ParamKind.REF, ref_codes=_WAF_VOLUMES),
            "basis": ParamSpec(ParamKind.REF, ref_codes=_WAF_BASES),
        },
        {
            "top": 400,
            "total": 500,
            "subject": MessageRef("waf.subject.this_endpoint"),
            "share": 0.8,
            "expected": 0.2,
            "rise": 0.6,
            "top5": 0.95,
            "volume": MessageRef(
                "waf.concentration.volume_with_baseline", {"requests": 500, "expected": 100.0}
            ),
            "basis": MessageRef("waf.basis.z", {"score": 6.2, "threshold": 3.5}),
        },
    ),
    # New endpoint.
    _msg("waf.novelty.not_established", "the endpoint's first appearance was not established"),
    _msg(
        "waf.novelty.established_hand_off",
        "established endpoint: first admitted {first_seen} before the history hand-off "
        "{hand_off}",
        {"first_seen": _ISO, "hand_off": _ISO},
        {"first_seen": _WAF_AT, "hand_off": _WAF_AT},
    ),
    _msg(
        "waf.novelty.established_outside_window",
        "established endpoint: first admitted {first_seen}, outside the {buckets}-bucket "
        "novelty window",
        {"first_seen": _ISO, "buckets": _PLAIN},
        {"first_seen": _WAF_AT, "buckets": 12},
    ),
    _msg("waf.novelty.history_incomplete", "the history is incomplete"),
    _msg(
        "waf.novelty.cannot_establish",
        "novelty cannot be established: {cause}",
        {"cause": ParamSpec(ParamKind.REF, ref_codes=_WAF_NOVELTY_CAUSES)},
        {"cause": MessageRef("waf.history_check.not_divisible")},
    ),
    _msg(
        "waf.novelty.flood_discovery",
        "endpoint discovery was truncated in the first-appearance bucket",
    ),
    _msg(
        "waf.novelty.flood_admission",
        "endpoint admission was truncated in the first-appearance bucket",
    ),
    _msg(
        "waf.novelty.below_materiality",
        "{requests} request(s) in {buckets} bucket(s) since first appearance has not "
        "reached {min_requests} requests in {min_buckets} buckets",
        {
            "requests": _PLAIN,
            "buckets": _PLAIN,
            "min_requests": _PLAIN,
            "min_buckets": _PLAIN,
        },
        {"requests": 8, "buckets": 1, "min_requests": 20, "min_buckets": 2},
    ),
    _msg(
        "waf.novelty.below_materiality_lower",
        "{reason}; the counts are lower bounds",
        {"reason": ParamSpec(ParamKind.REF, ref_codes=("waf.novelty.below_materiality",))},
        {
            "reason": MessageRef(
                "waf.novelty.below_materiality",
                {"requests": 8, "buckets": 1, "min_requests": 20, "min_buckets": 2},
            )
        },
    ),
    _msg(
        "waf.novelty.fired",
        "{subject} received {requests} requests across {buckets} buckets since it first "
        "appeared at {first_seen}; in the verified history {history_start} to {history_end} "
        "it was never requested {admission} or more times in one bucket",
        {
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "requests": _PLAIN,
            "buckets": _PLAIN,
            "first_seen": _ISO,
            "history_start": _ISO,
            "history_end": _ISO,
            "admission": _PLAIN,
        },
        {
            "subject": MessageRef("waf.subject.this_endpoint"),
            "requests": 90,
            "buckets": 3,
            "first_seen": _WAF_AT,
            "history_start": _WAF_AT,
            "history_end": _WAF_AT,
            "admission": 3,
        },
    ),
    _msg(
        "waf.novelty.fired_lower",
        "{subject} received at least {requests} requests across {buckets} buckets since it "
        "first appeared at {first_seen}; in the verified history {history_start} to "
        "{history_end} it was never requested {admission} or more times in one bucket",
        {
            "subject": ParamSpec(ParamKind.REF, ref_codes=_WAF_SUBJECTS),
            "requests": _PLAIN,
            "buckets": _PLAIN,
            "first_seen": _ISO,
            "history_start": _ISO,
            "history_end": _ISO,
            "admission": _PLAIN,
        },
        {
            "subject": MessageRef("waf.subject.this_endpoint"),
            "requests": 90,
            "buckets": 3,
            "first_seen": _WAF_AT,
            "history_start": _WAF_AT,
            "history_end": _WAF_AT,
            "admission": 3,
        },
    ),
    # History verification: why the window before an endpoint's first appearance is not proof.
    _msg(
        "waf.history_check.not_divisible",
        "the history window is not divisible by the collection bucket width",
    ),
    _msg(
        "waf.history_check.never_collected",
        "{missing} of {expected} history buckets were never collected",
        {"missing": _PLAIN, "expected": _PLAIN},
        {"missing": 3, "expected": 168},
    ),
    _msg(
        "waf.history_check.record_count",
        "the history contains {records} bucket records for {expected} expected buckets",
        {"records": _PLAIN, "expected": _PLAIN},
        {"records": 169, "expected": 168},
    ),
    _msg(
        "waf.history_check.bucket_width",
        "history bucket {bucket} used a different collection bucket width",
        {"bucket": _ISO},
        {"bucket": _WAF_AT},
    ),
    _msg(
        "waf.history_check.discovery_truncated",
        "history bucket {bucket} had truncated endpoint discovery",
        {"bucket": _ISO},
        {"bucket": _WAF_AT},
    ),
    _msg(
        "waf.history_check.admission_truncated",
        "history bucket {bucket} had truncated endpoint admission",
        {"bucket": _ISO},
        {"bucket": _WAF_AT},
    ),
    _msg(
        "waf.history_check.profile_version",
        "history bucket {bucket} used profile version {found}, not first-appearance "
        "version {expected}",
        {"bucket": _ISO, "found": _TEXT, "expected": _PLAIN},
        {"bucket": _WAF_AT, "found": "2", "expected": 3},
    ),
    _msg(
        "waf.history_check.normalizer_version",
        "history bucket {bucket} is incompatible with endpoint normalizer version {version}",
        {"bucket": _ISO, "version": _PLAIN},
        {"bucket": _WAF_AT, "version": 2},
    ),
    _msg(
        "waf.history_check.not_loaded",
        "history was not loaded for an established endpoint",
    ),
    _msg(
        "waf.history_check.appearance_never_collected",
        "the first-appearance bucket was never collected",
    ),
    _msg(
        "waf.history_check.appearance_no_profile",
        "the first-appearance bucket carries no profile version",
    ),
    _msg(
        "waf.history_check.appearance_normalizer",
        "the first-appearance bucket is incompatible with endpoint normalizer version "
        "{version}",
        {"version": _PLAIN},
        {"version": 2},
    ),
    # The fixed interpretive caveats, one per detector family.
    _msg(
        "waf.caveat.block_ratio",
        "Blocking is judged through the profile's action map. A higher block ratio "
        "describes WAF decisions; it does not establish that an attack succeeded.",
    ),
    _msg(
        "waf.caveat.server_error_ratio",
        "HTTP 5xx responses describe server-side failures. They do not establish that "
        "the traffic was malicious.",
    ),
    _msg(
        "waf.caveat.client_error_ratio",
        "HTTP 4xx responses are response codes, not WAF decisions: a 403 is not by "
        "itself a block, and WAF blocking is reported separately.",
    ),
    _msg(
        "waf.caveat.client_concentration",
        "A dominant source IP describes traffic composition, not intent.",
    ),
    _msg(
        "waf.caveat.new_endpoint",
        "A newly requested endpoint may be a deployment as easily as a probe.",
    ),
    # Suppression.
    _msg("waf.suppression.maintenance_window", "maintenance window"),
    # --- end MSG-6 ---------------------------------------------------------------------------
)


_STYLES: dict[ParamKind, set[str | None]] = {
    ParamKind.COUNT: {None, "grouped", "plain"},
    ParamKind.NUMBER: {None, "general", "grouped", "fixed", "repr"},
    ParamKind.PERCENT: {None},
    ParamKind.POINTS: {None, "signed", "unsigned"},
    ParamKind.DURATION: {None, "timedelta", "age"},
    ParamKind.TIMESTAMP: {None, "minute", "iso"},
    ParamKind.ENUM: {None, "lower"},
    ParamKind.LIST: {None, "comma", "and", "semicolon"},
    ParamKind.REF: {None},
    ParamKind.TEXT: {None},
}


def _template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    try:
        parsed = Formatter().parse(template)
        for _literal, name, format_spec, conversion in parsed:
            if name is None:
                continue
            if not name or "." in name or "[" in name or format_spec or conversion:
                raise MessageRegistryError(
                    "templates use bare placeholders; formatting belongs in ParamSpec"
                )
            fields.add(name)
    except ValueError as exc:
        raise MessageRegistryError(f"invalid Python format template: {exc}") from exc
    return fields


def validate_registry(registry: Sequence[MessageSpec]) -> None:
    """Reject a registry that cannot be represented or rendered unambiguously."""
    codes = [spec.code for spec in registry]
    if len(codes) != len(set(codes)):
        raise MessageRegistryError("message codes must be unique")
    for code in codes:
        if CODE_PATTERN.fullmatch(code) is None:
            raise MessageRegistryError(f"invalid message code: {code}")

    segment_codes = [(code, tuple(code.split("."))) for code in codes]
    for code, segments in segment_codes:
        for other, other_segments in segment_codes:
            if (
                code != other
                and len(segments) < len(other_segments)
                and other_segments[: len(segments)] == segments
            ):
                raise MessageRegistryError(
                    f"message code {code} is a segment prefix of {other}"
                )

    known = set(codes)
    refs: dict[str, set[str]] = {code: set() for code in codes}
    for spec in registry:
        declared = set(spec.params)
        present = _template_fields(spec.template)
        if declared != present:
            missing = sorted(declared - present)
            undeclared = sorted(present - declared)
            raise MessageRegistryError(
                f"template params for {spec.code} differ: "
                f"missing={missing}, undeclared={undeclared}"
            )
        if set(spec.sample) != declared:
            raise MessageRegistryError(
                f"sample params for {spec.code} do not match its declaration"
            )
        for name, param in spec.params.items():
            if not isinstance(param.kind, ParamKind):
                raise MessageRegistryError(f"{spec.code}.{name} has an invalid param kind")
            if param.style not in _STYLES[param.kind]:
                raise MessageRegistryError(
                    f"{spec.code}.{name} has unsupported {param.kind.value} style {param.style!r}"
                )
            if param.precision is not None and (
                isinstance(param.precision, bool) or not 0 <= param.precision <= 6
            ):
                raise MessageRegistryError(f"{spec.code}.{name} has invalid precision")
            if param.kind is ParamKind.ENUM:
                if not param.enum_group:
                    raise MessageRegistryError(f"enum param {spec.code}.{name} needs a group")
            elif param.enum_group is not None:
                raise MessageRegistryError(
                    f"only enum params may name an enum group: {spec.code}.{name}"
                )
            if param.kind is ParamKind.REF:
                if not param.ref_codes:
                    raise MessageRegistryError(f"ref param {spec.code}.{name} needs allowed codes")
                unknown = set(param.ref_codes) - known
                if unknown:
                    raise MessageRegistryError(
                        f"ref param {spec.code}.{name} names unknown codes: {sorted(unknown)}"
                    )
                refs[spec.code].update(param.ref_codes)
            elif param.ref_codes:
                raise MessageRegistryError(f"only ref params may name codes: {spec.code}.{name}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(code: str) -> None:
        if code in visiting:
            raise MessageRegistryError(f"message refs contain a cycle at {code}")
        if code in visited:
            return
        visiting.add(code)
        for target in refs[code]:
            visit(target)
        visiting.remove(code)
        visited.add(code)

    for code in codes:
        visit(code)


def _registry_map(registry: Sequence[MessageSpec]) -> dict[str, MessageSpec]:
    return {spec.code: spec for spec in registry}


def _specs_by_code(registry: Sequence[MessageSpec]) -> Mapping[str, MessageSpec]:
    """The code map. The module ``REGISTRY`` is static, so its map is built once."""
    if registry is REGISTRY:
        return _REGISTRY_BY_CODE
    return _registry_map(registry)


def _numeric(value: object, *, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MessageRenderError(f"{name} must be a number")
    if not math.isfinite(value):
        raise MessageRenderError(f"{name} must be finite")
    return value


def _duration(value: object, *, name: str, style: str | None = None) -> str:
    seconds = _numeric(value, name=name)
    if seconds < 0:
        raise MessageRenderError(f"{name} must not be negative")
    if style == "timedelta":
        return str(timedelta(seconds=seconds))
    if style == "age":
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3_600)
        minutes, seconds_left = divmod(remainder, 60)
        age_parts: list[str] = []
        if hours:
            age_parts.append(f"{hours} h")
        if minutes:
            age_parts.append(f"{minutes} min")
        if seconds_left or not age_parts:
            age_parts.append(f"{seconds_left} s")
        return " ".join(age_parts)
    total = round(seconds)
    if total == 0:
        return "0 s"
    parts: list[str] = []
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds_left = divmod(remainder, 60)
    for amount, unit in ((days, "d"), (hours, "h"), (minutes, "min"), (seconds_left, "s")):
        if amount:
            parts.append(f"{amount} {unit}")
    return " ".join(parts)


def _timestamp(value: object, *, name: str, style: str | None = None) -> str:
    instant: datetime
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, str):
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MessageRenderError(f"{name} must be an ISO-8601 timestamp") from exc
    else:
        raise MessageRenderError(f"{name} must be an ISO-8601 timestamp")
    if instant.tzinfo is None:
        raise MessageRenderError(f"{name} must include a timezone")
    if style == "iso":
        return instant.isoformat()
    instant = instant.astimezone(UTC)
    return instant.strftime("%Y-%m-%d %H:%M UTC" if style == "minute" else "%Y-%m-%d %H:%M:%S UTC")


def _format_param(
    name: str,
    spec: ParamSpec,
    value: object,
    registry: Sequence[MessageSpec],
) -> str:
    if spec.kind is ParamKind.COUNT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MessageRenderError(f"{name} must be an integer count")
        return str(value) if spec.style == "plain" else f"{value:,}"
    if spec.kind is ParamKind.NUMBER:
        number = _numeric(value, name=name)
        if spec.style == "fixed":
            return f"{number:.{spec.precision if spec.precision is not None else 1}f}"
        if spec.style == "grouped":
            precision = spec.precision if spec.precision is not None else 0
            return f"{number:,.{precision}f}"
        if spec.style == "repr":
            return str(number)
        return f"{number:g}"
    if spec.kind is ParamKind.PERCENT:
        number = _numeric(value, name=name)
        return percent(number, precision=spec.precision or 0)
    if spec.kind is ParamKind.POINTS:
        number = _numeric(value, name=name)
        return points(
            number,
            precision=spec.precision if spec.precision is not None else 1,
            signed=spec.style != "unsigned",
        )
    if spec.kind is ParamKind.DURATION:
        return _duration(value, name=name, style=spec.style)
    if spec.kind is ParamKind.TIMESTAMP:
        return _timestamp(value, name=name, style=spec.style)
    if spec.kind in (ParamKind.ENUM, ParamKind.TEXT):
        if not isinstance(value, str):
            raise MessageRenderError(f"{name} must be text")
        return value.lower() if spec.style == "lower" else value
    if spec.kind is ParamKind.LIST:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise MessageRenderError(f"{name} must be a list")
        rendered = [
            render(item, registry=registry) if isinstance(item, MessageRef) else str(item)
            for item in value
        ]
        if spec.style == "semicolon":
            return "; ".join(rendered)
        if spec.style == "and" and len(rendered) > 1:
            return f"{', '.join(rendered[:-1])} and {rendered[-1]}"
        return ", ".join(rendered)
    if spec.kind is ParamKind.REF:
        if isinstance(value, str):
            return value
        if not isinstance(value, MessageRef):
            raise MessageRenderError(f"{name} must be a message reference or text")
        if value.code not in spec.ref_codes:
            raise MessageRenderError(f"{name} does not allow ref code {value.code}")
        return render(value, registry=registry)
    raise MessageRenderError(f"{name} has unsupported kind {spec.kind}")


def render(
    ref: MessageRef,
    *,
    registry: Sequence[MessageSpec] = REGISTRY,
) -> str:
    """Render a reference in the exact legacy English spelling."""
    spec = _specs_by_code(registry).get(ref.code)
    if spec is None:
        raise MessageRenderError(f"unknown message code: {ref.code}")
    expected = set(spec.params)
    actual = set(ref.params)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise MessageRenderError(f"params for {ref.code} differ: missing={missing}, extra={extra}")
    values = {
        name: _format_param(name, param, ref.params[name], registry)
        for name, param in spec.params.items()
    }
    return spec.template.format(**values)


def _wire_param(
    name: str,
    spec: ParamSpec,
    value: object,
    registry: Sequence[MessageSpec],
) -> object:
    # Validate through the same formatter first; wire values must never be more
    # permissive than the English sentence stored beside them.
    _format_param(name, spec, value, registry)
    if spec.kind is ParamKind.DURATION:
        return {"kind": "duration", "seconds": value}
    if spec.kind is ParamKind.PERCENT:
        return {
            "kind": "percent",
            "ratio": value,
            "precision": spec.precision or 0,
        }
    if spec.kind is ParamKind.POINTS:
        number = _numeric(value, name=name)
        return {
            "kind": "points",
            "points": number * 100,
            "precision": spec.precision if spec.precision is not None else 1,
            "signed": spec.style != "unsigned",
        }
    if spec.kind is ParamKind.LIST:
        assert isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        return {
            "kind": "list",
            "items": [
                _to_wire(item, registry) if isinstance(item, MessageRef) else item
                for item in value
            ],
        }
    if spec.kind is ParamKind.REF:
        return _to_wire(value, registry) if isinstance(value, MessageRef) else value
    if spec.kind is ParamKind.TIMESTAMP and isinstance(value, datetime):
        return value.isoformat()
    return value


def _to_wire(ref: MessageRef, registry: Sequence[MessageSpec]) -> dict[str, object]:
    spec = _specs_by_code(registry).get(ref.code)
    if spec is None:
        raise MessageRenderError(f"unknown message code: {ref.code}")
    # This pins missing/extra and kind errors before building the payload.
    render(ref, registry=registry)
    return {
        "code": ref.code,
        "params": {
            name: _wire_param(name, param, ref.params[name], registry)
            for name, param in spec.params.items()
        },
    }


def sample_english_params(spec: MessageSpec) -> dict[str, str]:
    """The already-English parameter strings used by the ICU artifact check."""
    return {
        name: _format_param(name, param, spec.sample[name], REGISTRY)
        for name, param in spec.params.items()
    }


validate_registry(REGISTRY)
_REGISTRY_BY_CODE: dict[str, MessageSpec] = _registry_map(REGISTRY)

