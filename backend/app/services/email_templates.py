"""Alert e-mail templates: placeholders, the shipped default, sanitising, rendering.

No template engine and no new dependency: a template is subject text plus HTML
plus optional plain text, with ``{{ name }}`` placeholders drawn from a fixed
allowlist. Rendering substitutes, escapes and never runs logic (MAIL-1 design).

Falling back, in order: the enabled row for the alert's type, the enabled
default row, the shipped default held here. A disabled row is skipped, so
switching a template off is a safe way to return to the next layer, and an
e-mail is never refused because an administrator left a row disabled.
"""

from __future__ import annotations

import math
import re
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape, unescape
from html.parser import HTMLParser

import bleach
from bleach.sanitizer import NoCssSanitizerWarning

from app.alerts.notifiers.base import NotificationMessage, ResolvedTemplate
from app.core.config import Settings
from app.models.email import EMAIL_ALERT_TYPES, EmailTemplate

#: The value a missing placeholder renders as. Never "None", never a zero
#: (AGENTS.md: a missing measurement is not a zero).
MISSING_VALUE = "—"

#: English labels for ``alert.source_type`` (D-41: the interface is English only).
ALERT_TYPE_LABELS: dict[str, str] = {
    "log_source": "Log source",
    "waf_endpoint": "WAF endpoint",
    "ips_entity": "IPS entity",
    "custom_rule": "Custom rule",
    "pw_value": "Property-watch value",
    "catchall_sender": "Catch-all sender",
    "qradar_instance": "QRadar instance",
    "scheduled_search": "Scheduled search",
    "analytics_rule": "Analytics rule",
    "identity_source": "Identity source",
    "digest": "Daily digest",
}


@dataclass(frozen=True)
class Placeholder:
    name: str
    description: str


#: Every placeholder a template may name, with the English description the
#: admin API serves. Ordered for display.
PLACEHOLDERS: tuple[Placeholder, ...] = (
    Placeholder("alert.title", "The alert's title."),
    Placeholder("alert.description", "The alert's description."),
    Placeholder(
        "alert.severity", "The severity: CRITICAL, HIGH, MEDIUM, LOW or INFO."
    ),
    Placeholder(
        "alert.transition",
        "The transition that sent this e-mail: OPENED, ACKNOWLEDGED or RESOLVED.",
    ),
    Placeholder("alert.type", "The alert type's label, for example Log source."),
    Placeholder(
        "alert.occurrences",
        "How many times the condition fired while this alert stayed open.",
    ),
    Placeholder("alert.first_seen", "When the condition was first seen, in UTC."),
    Placeholder("alert.opened_at", "When the alert opened, in UTC."),
    Placeholder("alert.url", "The alert's page on this site."),
    Placeholder(
        "evidence.observed", "The measured value, when the alert carries evidence."
    ),
    Placeholder(
        "evidence.expected", "The expected value, when the alert carries evidence."
    ),
    Placeholder(
        "evidence.deviation", "The deviation score, when the alert carries evidence."
    ),
    Placeholder(
        "evidence.reason", "The detector's reason, when the alert carries evidence."
    ),
    Placeholder(
        "instance.name", "The QRadar console's name, when the alert carries one."
    ),
    Placeholder("site.name", "This site's name (SITE_NAME)."),
)

PLACEHOLDER_NAMES: frozenset[str] = frozenset(item.name for item in PLACEHOLDERS)

#: Any ``{{ ... }}`` group. The name inside is trimmed and checked against the
#: allowlist, so a misspelling with a dash or a space is refused on save rather
#: than shipped as literal braces.
_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")
_SCRIPT_BLOCK_RE = re.compile(
    r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL
)
_STYLE_ATTR_RE = re.compile(r'(?<=\s)style="([^"]*)"', re.IGNORECASE)
_STASHED_STYLE_ATTR_RE = re.compile(r'\s+data-mail-style="([0-9]+)"')
_EXISTING_STASH_RE = re.compile(r'\s+data-mail-style="[^"]*"')

# --- the shipped default template (English only, D-41) -----------------------
#
# E-mail-client HTML: a 600 px table layout with inline styles only — no
# images, no external resources, no tracking pixels, no <style> block. The
# severity label is a coloured chip with a neutral slate background: a chip
# colour chosen by severity would need logic a template must not have, and a
# red chip on a LOW alert would lie about urgency. Style values are written in
# the sanitiser's canonical form ("prop: value; prop: value"), so saving the
# shipped template changes nothing.
SHIPPED_SUBJECT = "[{{ alert.severity }}] {{ alert.title }} ({{ alert.transition }})"

_FONT = "font-family: Arial,Helvetica,sans-serif"
_LABEL_CELL = (
    f"padding: 8px 12px; {_FONT}; font-size: 13px; line-height: 20px; "
    "color: #5a5b61; background-color: #f4f4f6; border: 1px solid #d9dade; width: 150px"
)
_VALUE_CELL = (
    f"padding: 8px 12px; {_FONT}; font-size: 13px; line-height: 20px; "
    "color: #1c1d22; border: 1px solid #d9dade"
)
_FIELD_ROWS: tuple[tuple[str, str], ...] = (
    ("Alert type", "alert.type"),
    ("Occurrences", "alert.occurrences"),
    ("First seen", "alert.first_seen"),
    ("Opened", "alert.opened_at"),
    ("Observed", "evidence.observed"),
    ("Expected", "evidence.expected"),
    ("Deviation", "evidence.deviation"),
    ("Reason", "evidence.reason"),
    ("Console", "instance.name"),
)


def _field_row(label: str, name: str) -> str:
    return (
        "          <tr>\n"
        f'            <td style="{_LABEL_CELL}">{label}</td>\n'
        f'            <td style="{_VALUE_CELL}">{{{{ {name} }}}}</td>\n'
        "          </tr>\n"
    )


# Every table carries an explicit <tbody>: the sanitiser's HTML parser inserts
# one, and the shipped template must come back from it byte for byte.
SHIPPED_HTML = (
    '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
    'style="width: 600px; max-width: 600px; background-color: #ffffff; '
    'border-collapse: collapse">\n'
    "  <tbody>\n"
    "    <tr>\n"
    f'      <td style="padding: 24px; {_FONT}">\n'
    # The severity label is a one-cell table: Outlook ignores padding and
    # background on an inline-block paragraph but honours them on a cell.
    '        <table role="presentation" cellpadding="0" cellspacing="0" '
    'style="border-collapse: collapse">\n'
    "          <tbody>\n"
    "          <tr>\n"
    '            <td style="padding: 3px 10px; background-color: #3d3d42; '
    f"color: #ffffff; {_FONT}; font-size: 12px; font-weight: bold; "
    'line-height: 18px">{{ alert.severity }}</td>\n'
    "          </tr>\n"
    "          </tbody>\n"
    "        </table>\n"
    '        <p style="margin: 12px 0 4px 0; font-size: 12px; line-height: 18px; '
    'color: #5a5b61">{{ site.name }}</p>\n'
    '        <h1 style="margin: 0 0 12px 0; font-size: 20px; line-height: 28px; '
    'color: #1c1d22">{{ alert.title }}</h1>\n'
    '        <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 22px; '
    'color: #33343b">{{ alert.description }}</p>\n'
    '        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="width: 100%; border-collapse: collapse">\n'
    "          <tbody>\n"
    + "".join(_field_row(label, name) for label, name in _FIELD_ROWS)
    + "          </tbody>\n"
    "        </table>\n"
    # A bulletproof button: the colour sits on the cell, the link fills it.
    '        <table role="presentation" cellpadding="0" cellspacing="0" '
    'style="border-collapse: collapse; margin: 24px 0 0 0">\n'
    "          <tbody>\n"
    "          <tr>\n"
    '            <td bgcolor="#0b5cad" style="background-color: #0b5cad">\n'
    '              <a href="{{ alert.url }}" style="display: block; padding: 10px 18px; '
    f"color: #ffffff; {_FONT}; font-size: 14px; line-height: 20px; "
    'text-decoration: none">Open the alert</a>\n'
    "            </td>\n"
    "          </tr>\n"
    "          </tbody>\n"
    "        </table>\n"
    "      </td>\n"
    "    </tr>\n"
    "  </tbody>\n"
    "</table>"
)

SHIPPED_TEXT = """{{ site.name }}

[{{ alert.severity }}] {{ alert.title }} ({{ alert.transition }})

{{ alert.description }}

Alert type:   {{ alert.type }}
Occurrences:  {{ alert.occurrences }}
First seen:   {{ alert.first_seen }}
Opened:       {{ alert.opened_at }}
Observed:     {{ evidence.observed }}
Expected:     {{ evidence.expected }}
Deviation:    {{ evidence.deviation }}
Reason:       {{ evidence.reason }}
Console:      {{ instance.name }}

Open the alert: {{ alert.url }}"""


@dataclass(frozen=True)
class RenderedEmail:
    """A fully rendered mail: values substituted and escaped per part."""

    subject: str
    html: str
    text: str


def shipped_template() -> ResolvedTemplate:
    return ResolvedTemplate(SHIPPED_SUBJECT, SHIPPED_HTML, SHIPPED_TEXT)


# --- the shipped digest template (SOC-17c) -----------------------------------
#
# The digest is one mail for a whole day, not one alert: it has its own frame and
# never falls back to the alert default above. The digest service appends its
# grouped table after this preamble.
SHIPPED_DIGEST_SUBJECT = "Daily digest: {{ instance.name }}"
SHIPPED_DIGEST_HTML = (
    "<p>Anomalies on {{ instance.name }} recorded in the previous 24 hours whose "
    "alert policy is DIGEST.</p>"
)
SHIPPED_DIGEST_TEXT = (
    "Anomalies on {{ instance.name }} recorded in the previous 24 hours whose "
    "alert policy is DIGEST.\n"
)


def shipped_digest_template() -> ResolvedTemplate:
    return ResolvedTemplate(
        SHIPPED_DIGEST_SUBJECT, SHIPPED_DIGEST_HTML, SHIPPED_DIGEST_TEXT
    )


def find_placeholders(*texts: str) -> set[str]:
    """Every placeholder name the texts name, known or not."""
    found: set[str] = set()
    for text in texts:
        found.update(name.strip() for name in _PLACEHOLDER_RE.findall(text))
    return found


def unknown_placeholders(*texts: str) -> list[str]:
    """Sorted names that are not in the allowlist. Save refuses on these."""
    return sorted(find_placeholders(*texts) - PLACEHOLDER_NAMES)


# --- sanitising ---------------------------------------------------------------

#: E-mail-safe tags: table layout plus the common inline and text tags. No
#: forms, no iframes, no script/style blocks (the sanitizer strips those before
#: bleach runs so their body text cannot survive either).
_EMAIL_TAGS: frozenset[str] = frozenset(
    {
        "table", "thead", "tbody", "tfoot", "tr", "td", "th",
        "div", "span", "p", "a", "b", "strong", "i", "em", "u",
        "br", "hr", "h1", "h2", "h3", "h4", "ul", "ol", "li", "img",
    }
)

#: The one placeholder an ``href`` may be built from. Every other value is
#: alert text that QRadar events can influence; as a whole link target it could
#: spell ``javascript:`` or any scheme the save-time check never saw.
_LINK_PLACEHOLDER_RE = re.compile(r"^\{\{\s*alert\.url\s*\}\}$")
_HTTPS_URL_RE = re.compile(r"^https://[^\s{}]+$", re.IGNORECASE)
_MAILTO_RE = re.compile(r"^mailto:[^\s{}]+$", re.IGNORECASE)


def _href_allowed(tag: str, name: str, value: str) -> bool:
    """``href``: https, mailto, or exactly ``{{ alert.url }}``; ``title`` any text."""
    if name == "title":
        return True
    if name != "href":
        return False
    text = value.strip()
    return bool(
        _LINK_PLACEHOLDER_RE.match(text) or _HTTPS_URL_RE.match(text) or _MAILTO_RE.match(text)
    )


def _src_allowed(tag: str, name: str, value: str) -> bool:
    """``img src`` only a literal https URL; no placeholder can pick the image."""
    if name == "src":
        return _HTTPS_URL_RE.match(value.strip()) is not None
    return name in ("alt", "width", "height")


_AttributeRule = list[str] | Callable[[str, str, str], bool]

#: A raw ``style`` is never allowed here: styles go through the stash below, so
#: a spelling the stash does not recognise (``STYLE='...'``) is dropped.
_EMAIL_ATTRIBUTES: dict[str, _AttributeRule] = {
    "a": _href_allowed,
    "img": _src_allowed,
    "td": ["colspan", "rowspan", "align", "valign", "bgcolor", "width"],
    "th": ["colspan", "rowspan", "align", "valign", "bgcolor", "width"],
    "table": ["border", "cellpadding", "cellspacing", "width", "role", "align"],
    "tr": ["align", "valign", "bgcolor"],
}

#: Schemes bleach lets through its own URI check; the per-attribute callables
#: above are the stricter filter (they also refuse a scheme-less value, which
#: bleach treats as a relative link).
_EMAIL_PROTOCOLS: list[str] = ["https", "mailto"]

#: CSS properties an inline ``style`` may set. Layout and colour only: no
#: positioning, no behaviour, nothing a client would execute.
_STYLE_PROPERTIES: frozenset[str] = frozenset(
    {
        "color", "background-color", "background",
        "font-family", "font-size", "font-weight", "font-style",
        "text-align", "text-decoration",
        "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
        "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
        "border", "border-top", "border-right", "border-bottom", "border-left",
        "border-collapse", "border-spacing",
        "width", "max-width", "min-width",
        "line-height", "display", "vertical-align",
    }
)

#: Declaration values are limited to these characters. No backslash, no colon
#: beyond the separator, no semicolon beyond the split, no double quote (the
#: value is written back inside ``style="..."``), so no quoted string can
#: smuggle a delimiter back in.
_STYLE_VALUE_RE = re.compile(r"^[A-Za-z0-9 #%.,()'+\-]+$")

#: Words that make a value a loader or a script hook, refused even though their
#: letters pass the character class: ``url(`` fetches a resource (a tracking
#: pixel), ``expression(`` and ``javascript`` execute in old clients,
#: ``@import`` cannot be spelled without ``@``, which the class refuses.
_STYLE_FORBIDDEN_RE = re.compile(r"url|expression|javascript|behavior|binding|import", re.I)


def _clean_style(value: str) -> str:
    """Keep only allowlisted property/value declarations of one style attribute."""
    kept: list[str] = []
    for declaration in value.split(";"):
        name, separator, raw = declaration.partition(":")
        if not separator:
            continue
        prop = name.strip().lower()
        val = raw.strip()
        if prop not in _STYLE_PROPERTIES:
            continue
        if not val or _STYLE_VALUE_RE.fullmatch(val) is None:
            continue
        if _STYLE_FORBIDDEN_RE.search(val) is not None:
            continue
        kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def _attributes_with_style_token() -> dict[str, _AttributeRule]:
    """The attribute map with the style stash token allowed on every tag."""
    out: dict[str, _AttributeRule] = {"*": ["data-mail-style"]}
    for tag, rule in _EMAIL_ATTRIBUTES.items():
        out[tag] = _with_token(rule) if callable(rule) else [*rule, "data-mail-style"]
    return out


def _with_token(rule: Callable[[str, str, str], bool]) -> Callable[[str, str, str], bool]:
    def allowed(tag: str, name: str, value: str) -> bool:
        return name == "data-mail-style" or rule(tag, name, value)

    return allowed


def sanitize_email_html(html: str) -> str:
    """Reduce authored HTML to the e-mail allowlist.

    ``<script>``/``<style>`` blocks are removed whole (body included) before
    bleach runs, so their contents cannot survive as text. bleach then strips
    every tag outside the allowlist, drops every attribute outside the map and
    every non-https/mailto URL. Inline ``style`` values are stashed out as
    index tokens before bleach (which would otherwise empty them, having no CSS
    sanitizer — we deliberately add none) and restored afterwards through the
    declaration allowlist above. The result is idempotent: sanitising the
    shipped template returns it byte for byte.
    """
    without_blocks = _SCRIPT_BLOCK_RE.sub("", html)
    # A authored token attribute could otherwise point at a stashed style; the
    # stash is the only writer of these, so remove any that arrived in the HTML.
    without_blocks = _EXISTING_STASH_RE.sub("", without_blocks)
    stashed_styles: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stashed_styles.append(_clean_style(unescape(match.group(1))))
        return f'data-mail-style="{len(stashed_styles) - 1}"'

    stashed = _STYLE_ATTR_RE.sub(_stash, without_blocks)
    attributes = _attributes_with_style_token()
    # bleach warns that it has no CSS sanitizer (we deliberately add none: the
    # declaration allowlist above is the in-house MAIL-1 filter). Scoped and
    # restored, so no caller's warning filters change.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NoCssSanitizerWarning)
        cleaned = bleach.clean(
            stashed,
            tags=sorted(_EMAIL_TAGS),
            attributes=attributes,
            protocols=_EMAIL_PROTOCOLS,
            strip=True,
        )

    def _restore(match: re.Match[str]) -> str:
        index = int(match.group(1))
        value = stashed_styles[index] if index < len(stashed_styles) else ""
        return f' style="{value}"'

    return _STASHED_STYLE_ATTR_RE.sub(_restore, cleaned)


class _TextExtractor(HTMLParser):
    """Derived plain text: a newline per block element, no markup."""

    _BLOCKS = frozenset(
        {
            "p", "div", "tr", "table", "h1", "h2", "h3", "h4",
            "ul", "ol", "li", "br", "hr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCKS:
            self._parts.append("\n")
        if tag == "a":
            self._hrefs.append(dict(attrs).get("href") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._hrefs:
            href = self._hrefs.pop()
            if href:
                # A link whose target is not in its words would be lost.
                self._parts.append(f" ({href})")
        if tag in self._BLOCKS or tag in ("td", "th"):
            self._parts.append("\n" if tag in self._BLOCKS else " ")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def derive_text_from_html(html: str) -> str:
    """The plain-text alternative derived from a sanitised HTML body.

    A fallback for templates saved without a text body; the shipped template
    carries its own hand-written text.
    """
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.text()


# --- rendering ----------------------------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
#: Body parts keep a tab and a newline; a bare CR and every other control go.
_BODY_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _strip_controls(value: str) -> str:
    """Remove control characters, keeping newline and tab out of subjects."""
    return _CONTROL_CHARS_RE.sub("", value)


def _subject_value(value: str) -> str:
    # Header injection: a subject may not carry CR/LF or any other control
    # character. Newlines become spaces so words do not run together.
    return _strip_controls(value.replace("\r", " ").replace("\n", " "))


def _format_number(value: object) -> str:
    """A measurement as text; absent or non-finite values are missing, never zero."""
    if value is None or isinstance(value, bool):
        return MISSING_VALUE
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return MISSING_VALUE
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text if text else "0"
    return _text_or_missing(value)


def _format_timestamp(value: object) -> str:
    """An ISO timestamp from the frozen payload, as "<date> <HH:MM> UTC"."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return MISSING_VALUE
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _text_or_missing(value: object) -> str:
    if value is None:
        return MISSING_VALUE
    text = str(value)
    return text if text else MISSING_VALUE


def placeholder_values(
    message: NotificationMessage, settings: Settings
) -> dict[str, str]:
    """Render every placeholder from a notification message and Settings.

    The message's ``fields`` are the frozen payload of the alert notification:
    occurrences, first_seen, the evidence snapshot's four values and the
    message's own ``alert_type``, ``opened_at`` and ``instance``. A value that is absent
    or empty renders as an em dash; a measurement that exists is never turned
    into a zero.
    """
    fields: Mapping[str, object] = message.fields or {}
    source_type = message.alert_type or ""
    return {
        "alert.title": _text_or_missing(message.title),
        "alert.description": _text_or_missing(message.body),
        "alert.severity": message.severity.value,
        "alert.transition": message.transition.value,
        "alert.type": ALERT_TYPE_LABELS.get(source_type, source_type) or MISSING_VALUE,
        "alert.occurrences": _format_number(fields.get("occurrences")),
        "alert.first_seen": _format_timestamp(fields.get("first_seen")),
        "alert.opened_at": _format_timestamp(message.opened_at),
        "alert.url": (
            f"{settings.public_origin.rstrip('/')}/alerts/{message.alert_id}"
            if message.alert_id
            else MISSING_VALUE
        ),
        "evidence.observed": _format_number(fields.get("observed_value")),
        "evidence.expected": _format_number(fields.get("expected_value")),
        "evidence.deviation": _format_number(fields.get("deviation_score")),
        "evidence.reason": _text_or_missing(fields.get("reason")),
        "instance.name": _text_or_missing(message.instance),
        "site.name": settings.site_name or MISSING_VALUE,
    }


def _substitute(text: str, values: Mapping[str, str], *, escape_html: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = values.get(match.group(1).strip())
        if raw is None or raw == "":
            return MISSING_VALUE
        value = _BODY_CONTROL_RE.sub("", raw)
        return escape(value, quote=True) if escape_html else value

    return _PLACEHOLDER_RE.sub(replace, text)


def render_template(
    template: ResolvedTemplate, values: Mapping[str, str]
) -> RenderedEmail:
    """Substitute one template. Values are escaped in the HTML part, plain in
    the text part, CR/LF-stripped in the subject; a missing value is an em dash."""
    text_body = template.text or derive_text_from_html(template.html)
    return RenderedEmail(
        subject=_subject_value(_substitute(template.subject, values, escape_html=False)),
        html=_substitute(template.html, values, escape_html=True),
        text=_substitute(text_body, values, escape_html=False),
    )


def render_for_message(
    template: ResolvedTemplate, message: NotificationMessage, settings: Settings
) -> RenderedEmail:
    return render_template(template, placeholder_values(message, settings))


# --- template resolution ------------------------------------------------------

def choose_template(rows: Sequence[EmailTemplate], alert_type: str | None) -> ResolvedTemplate:
    """Pick the template for one alert type from stored rows.

    An enabled per-type row wins; otherwise an enabled default row; otherwise
    the shipped default. A disabled row is never chosen: disabling a per-type
    row falls back to the default, disabling the default row ships the built-in
    template.
    """
    default: EmailTemplate | None = None
    typed: EmailTemplate | None = None
    for row in rows:
        if not row.enabled:
            continue
        if row.alert_type is None:
            default = default or row
        elif row.alert_type == alert_type:
            typed = typed or row
    chosen = typed or default
    if chosen is None:
        return shipped_template()
    return ResolvedTemplate(
        subject=chosen.subject, html=chosen.html_body, text=chosen.text_body or ""
    )


# --- the fixed sample alerts (admin preview; never a real alert) --------------

_SAMPLE_BASE: dict[str, str] = {
    "alert.severity": "HIGH",
    "alert.transition": "OPENED",
    "alert.occurrences": "3",
    "alert.first_seen": "2026-11-03 09:05 UTC",
    "alert.opened_at": "2026-11-03 09:20 UTC",
    "alert.url": "https://qradar.example.internal/alerts/"
    "00000000-0000-0000-0000-000000000001",
    "instance.name": "lab-console",
    "site.name": "QRadar Behavior Analytics",
}

_SAMPLE_BY_TYPE: dict[str, dict[str, str]] = {
    "log_source": {
        "alert.title": "Log source volume spike: fw-edge-01",
        "alert.description": (
            "Log source fw-edge-01 sent 5.2 times its expected events per bucket; "
            "the rise held for two consecutive buckets."
        ),
        "evidence.observed": "936",
        "evidence.expected": "180",
        "evidence.deviation": "6.2",
        "evidence.reason": "robust z-score 6.2 above the 3.5 threshold",
    },
    "waf_endpoint": {
        "alert.title": "WAF endpoint block ratio spike: /api/login",
        "alert.description": (
            "Endpoint /api/login moved from 4% to 39% blocked requests within five "
            "minutes; the denominator stayed above the minimum."
        ),
        "evidence.observed": "0.39",
        "evidence.expected": "0.04",
        "evidence.deviation": "5.1",
        "evidence.reason": "blocked-request proportion rose 35 percentage points",
    },
    "ips_entity": {
        "alert.title": "IPS entity spike: destination port 8080",
        "alert.description": (
            "Destination port 8080 received 6.1 times its expected events from one "
            "IPS-protected source in a single bucket."
        ),
        "evidence.observed": "482",
        "evidence.expected": "79",
        "evidence.deviation": "5.8",
        "evidence.reason": "spike multiple 6.1 above the median",
    },
    "custom_rule": {
        "alert.title": "Custom rule breach: Failed VPN logons",
        "alert.description": (
            "The custom rule 'Failed VPN logons above 40 per hour' held its "
            "threshold for two consecutive buckets."
        ),
        "evidence.observed": "67",
        "evidence.expected": "40",
        "evidence.deviation": "1.7",
        "evidence.reason": "measured 67 against the configured bound 40",
    },
    "pw_value": {
        "alert.title": "Property-watch value spike: username svc-backup",
        "alert.description": (
            "The watched property 'Username' saw the value svc-backup 4.4 times as "
            "often as its baseline in one bucket."
        ),
        "evidence.observed": "220",
        "evidence.expected": "50",
        "evidence.deviation": "5.3",
        "evidence.reason": "value spike multiple 4.4 above the median",
    },
    "catchall_sender": {
        "alert.title": "New catch-all sender: printer-jet-3",
        "alert.description": (
            "The catch-all source lab-sim-generic admitted a sender not seen "
            "before: printer-jet-3."
        ),
        "evidence.observed": "—",
        "evidence.expected": "—",
        "evidence.deviation": "—",
        "evidence.reason": "first appearance of an unseen sender",
    },
    "qradar_instance": {
        "alert.title": "Console unreachable: lab-console",
        "alert.description": (
            "Console lab-console did not answer the reachability probe; reads from "
            "it are not running."
        ),
        "evidence.observed": "—",
        "evidence.expected": "—",
        "evidence.deviation": "—",
        "evidence.reason": "connection failed or certificate not trusted",
    },
    "scheduled_search": {
        "alert.title": "Scheduled search failed: Hourly DMZ summary",
        "alert.description": (
            "The scheduled search 'Hourly DMZ summary' failed three consecutive "
            "runs and raised this alert."
        ),
        "evidence.observed": "3",
        "evidence.expected": "—",
        "evidence.deviation": "—",
        "evidence.reason": "consecutive failures reached the alert threshold",
    },
    # A log-source name and a Windows event id. No username: the identity
    # allow-list is for stored top-N evidence, not for a preview fixture.
    "identity_source": {
        "alert.title": "Failed logon spike on dc-01 (4625)",
        "alert.description": (
            "Event 4625 is above its seasonal band "
            "(observed 48, median 6, ratio 8.00)."
        ),
        "evidence.observed": "48",
        "evidence.expected": "6",
        "evidence.deviation": "8",
        "evidence.reason": (
            "Event 4625 is above its seasonal band "
            "(observed 48, median 6, ratio 8.00)."
        ),
    },
}

_SAMPLE_DEFAULT: dict[str, str] = {
    "alert.title": "Alert: this is a preview",
    "alert.description": (
        "This is a fixed sample alert, rendered so an administrator can see the "
        "template without a real alert."
    ),
    "alert.type": "Log source",
    "evidence.observed": "936",
    "evidence.expected": "180",
    "evidence.deviation": "6.2",
    "evidence.reason": "robust z-score 6.2 above the 3.5 threshold",
    **_SAMPLE_BASE,
}


def sample_values(alert_type: str | None, settings: Settings) -> dict[str, str]:
    """The fixed sample values for one alert type (MAIL-1: never a real alert).

    An unknown type renders the generic sample with its own label in
    ``alert.type`` — the preview must show something honest, not fail.
    """
    if alert_type is None or alert_type not in _SAMPLE_BY_TYPE:
        values = dict(_SAMPLE_DEFAULT)
        values["site.name"] = settings.site_name or values["site.name"]
        return values
    values = {
        "alert.type": ALERT_TYPE_LABELS[alert_type],
        **_SAMPLE_BY_TYPE[alert_type],
        **_SAMPLE_BASE,
    }
    values["site.name"] = settings.site_name or values["site.name"]
    return values


def known_alert_type(value: str | None) -> bool:
    """True when ``value`` names a type that may carry its own template."""
    return value is not None and value in EMAIL_ALERT_TYPES
