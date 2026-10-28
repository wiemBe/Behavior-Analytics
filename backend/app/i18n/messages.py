"""Registry and English renderer for backend-authored analyst messages.

The API keeps the rendered English sentence and adds a stable code plus raw
parameters. English clients keep the sentence byte for byte; another locale
may render the code and parameters through its catalog.

Formatting styles are deliberately closed. Counts accept ``grouped`` (the
default) or ``plain`` (no grouping; it shapes the backend's English render only, the
frontend groups every count in the reader's locale); numbers accept ``general`` (the
default), ``grouped`` or ``fixed``; points accept ``signed`` (the default) or
``unsigned``; lists accept ``comma`` (the default), ``and`` or ``semicolon`` (``"; "``, for a
list of clauses that carry commas); timestamps accept the default (date, second, ``UTC``) or
``minute`` (date, minute, ``UTC``). Percent precision, fixed-number precision, and point precision
are declared separately. Duration, enum, ref, and text params have one spelling and therefore no
alternate style.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
)


_STYLES: dict[ParamKind, set[str | None]] = {
    ParamKind.COUNT: {None, "grouped", "plain"},
    ParamKind.NUMBER: {None, "general", "grouped", "fixed"},
    ParamKind.PERCENT: {None},
    ParamKind.POINTS: {None, "signed", "unsigned"},
    ParamKind.DURATION: {None},
    ParamKind.TIMESTAMP: {None, "minute"},
    ParamKind.ENUM: {None},
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


def _duration(value: object, *, name: str) -> str:
    seconds = _numeric(value, name=name)
    if seconds < 0:
        raise MessageRenderError(f"{name} must not be negative")
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
        return _duration(value, name=name)
    if spec.kind is ParamKind.TIMESTAMP:
        return _timestamp(value, name=name, style=spec.style)
    if spec.kind in (ParamKind.ENUM, ParamKind.TEXT):
        if not isinstance(value, str):
            raise MessageRenderError(f"{name} must be text")
        return value
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
        if not isinstance(value, MessageRef):
            raise MessageRenderError(f"{name} must be a message reference")
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
        assert isinstance(value, MessageRef)
        return _to_wire(value, registry)
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

