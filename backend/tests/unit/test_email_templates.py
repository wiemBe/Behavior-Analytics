"""Alert e-mail templates (MAIL-1): placeholders, escaping, sanitising, fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.alerts.notifiers.base import NotificationMessage, ResolvedTemplate
from app.core.config import Settings
from app.models.enums import AlertTransition, Severity
from app.services import email_templates as tpl

HOSTILE = '<script>alert("x")</script> & "quoted" \'single\' <img src=x onerror=y>'


def _settings(**over: object) -> Settings:
    return Settings(**over)  # type: ignore[arg-type]


def _message(**over: object) -> NotificationMessage:
    base: dict[str, object] = {
        "title": "Spike on fw-edge-01",
        "body": "Volume rose to 6 times the baseline.",
        "severity": Severity.HIGH,
        "transition": AlertTransition.OPENED,
        "alert_id": "11111111-1111-1111-1111-111111111111",
        "fingerprint": "anomaly:abc",
        "fields": {
            "occurrences": 3,
            "first_seen": "2026-11-03T09:05:00+00:00",
            "observed_value": 936.0,
            "expected_value": 180.5,
            "deviation_score": 6.2,
            "reason": "robust z above threshold",
        },
        "alert_type": "log_source",
        "opened_at": "2026-11-03T09:20:30+00:00",
        "instance": "lab-console",
    }
    base.update(over)
    return NotificationMessage(**base)  # type: ignore[arg-type]


def _render(html: str, subject: str = "s", text: str = "", **over: object) -> tpl.RenderedEmail:
    message = _message(**over)
    return tpl.render_for_message(ResolvedTemplate(subject, html, text), message, _settings())


# --- placeholders ----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(tpl.PLACEHOLDER_NAMES))
def test_every_placeholder_renders_in_all_three_parts(name: str) -> None:
    out = _render(f"<p>{{{{ {name} }}}}</p>", subject=f"{{{{ {name} }}}}", text=f"{{{{{name}}}}}")
    value = tpl.placeholder_values(_message(), _settings())[name]
    assert value and value != tpl.MISSING_VALUE
    assert out.subject == value.replace("\n", " ")
    assert out.text == value
    assert "{{" not in out.html and "{{" not in out.subject and "{{" not in out.text


def test_placeholder_values_are_the_documented_ones() -> None:
    values = tpl.placeholder_values(_message(), _settings(public_origin="https://dash.example"))
    assert values["alert.title"] == "Spike on fw-edge-01"
    assert values["alert.severity"] == "HIGH"
    assert values["alert.transition"] == "OPENED"
    assert values["alert.type"] == "Log source"
    assert values["alert.occurrences"] == "3"
    assert values["alert.first_seen"] == "2026-11-03 09:05 UTC"
    assert values["alert.opened_at"] == "2026-11-03 09:20 UTC"
    assert values["alert.url"] == (
        "https://dash.example/alerts/11111111-1111-1111-1111-111111111111"
    )
    assert values["evidence.observed"] == "936"
    assert values["evidence.expected"] == "180.5"
    assert values["evidence.deviation"] == "6.2"
    assert values["instance.name"] == "lab-console"
    assert values["site.name"] == "QRadar Behavior Analytics"
    assert set(values) == tpl.PLACEHOLDER_NAMES


def test_a_missing_value_is_an_em_dash_never_none_or_zero() -> None:
    message = _message(fields={}, instance=None, opened_at=None, alert_type=None)
    values = tpl.placeholder_values(message, _settings())
    for name in (
        "alert.occurrences", "alert.first_seen", "alert.opened_at", "alert.type",
        "evidence.observed", "evidence.expected", "evidence.deviation", "evidence.reason",
        "instance.name",
    ):
        assert values[name] == tpl.MISSING_VALUE, name
    out = tpl.render_for_message(
        ResolvedTemplate("{{ evidence.observed }}", "<p>{{ instance.name }}</p>", ""),
        message,
        _settings(),
    )
    assert "None" not in out.subject + out.html + out.text
    assert out.subject == tpl.MISSING_VALUE
    # A measured zero is a measurement, not a missing value.
    zero = tpl.placeholder_values(_message(fields={"observed_value": 0}), _settings())
    assert zero["evidence.observed"] == "0"
    nan = tpl.placeholder_values(_message(fields={"observed_value": float("nan")}), _settings())
    assert nan["evidence.observed"] == tpl.MISSING_VALUE


def test_unknown_placeholders_are_found_including_misspelt_ones() -> None:
    assert tpl.unknown_placeholders("{{ alert.title }}", "{{ evidence.reason }}") == []
    assert tpl.unknown_placeholders("{{ alert.titel }} {{alert-title}} {{ a b }}") == [
        "a b", "alert-title", "alert.titel"
    ]
    assert tpl.unknown_placeholders("subject {{ site.nam }}", "<p>{{ alert.url }}</p>") == [
        "site.nam"
    ]


def test_a_value_that_looks_like_a_placeholder_is_not_expanded_again() -> None:
    out = _render("<p>{{ alert.title }}</p>", title="{{ site.name }}")
    assert out.html == "<p>{{ site.name }}</p>"


# --- escaping --------------------------------------------------------------------


def test_hostile_alert_text_is_escaped_in_html_and_plain_in_text() -> None:
    out = _render(
        '<p title="{{ alert.title }}">{{ alert.description }}</p>',
        text="{{ alert.description }}",
        title=HOSTILE,
        body=HOSTILE,
    )
    assert "<script>" not in out.html and "<img" not in out.html
    assert "&lt;script&gt;" in out.html and "&quot;quoted&quot;" in out.html
    assert 'title="&lt;script&gt;' in out.html
    assert out.text == HOSTILE


def test_subject_has_no_cr_lf_or_control_characters() -> None:
    out = _render(
        "<p>x</p>", subject="[{{ alert.severity }}] {{ alert.title }}",
        title="line one\r\nBcc: attacker@example.org\nline\x00 three\x1b",
    )
    assert "\r" not in out.subject and "\n" not in out.subject
    assert "\x00" not in out.subject and "\x1b" not in out.subject
    assert out.subject == "[HIGH] line one Bcc: attacker@example.org line three"


def test_a_template_subject_with_a_newline_is_one_line() -> None:
    assert _render("<p>x</p>", subject="a\r\nb").subject == "a  b"


def test_text_part_keeps_newlines_of_a_value() -> None:
    out = _render("<p>x</p>", text="{{ alert.description }}", body="one\ntwo\x00")
    assert out.text == "one\ntwo"


# --- sanitising ------------------------------------------------------------------


def test_the_shipped_template_survives_the_sanitiser_byte_for_byte() -> None:
    assert tpl.sanitize_email_html(tpl.SHIPPED_HTML) == tpl.SHIPPED_HTML
    assert tpl.unknown_placeholders(tpl.SHIPPED_HTML, tpl.SHIPPED_SUBJECT, tpl.SHIPPED_TEXT) == []
    # Idempotent: a stored sanitised body does not change on the next save.
    once = tpl.sanitize_email_html('<p style="COLOR: red">x</p>')
    assert tpl.sanitize_email_html(once) == once


def test_the_shipped_template_is_email_client_safe() -> None:
    html = tpl.SHIPPED_HTML
    assert 'width="600"' in html and "max-width: 600px" in html
    for forbidden in ("<style", "<script", "<img", "<link", "http://", "url(", "@import"):
        assert forbidden not in html
    assert html.count("<table") == html.count("</table>")
    # No external resource: the only URL is the alert link placeholder.
    assert "https://" not in html


@pytest.mark.parametrize(
    ("authored", "gone"),
    [
        ("<script>alert(1)</script><p>keep</p>", "alert(1)"),
        ("<style>p{color:red}</style><p>keep</p>", "color:red"),
        ('<p onclick="x()">keep</p>', "onclick"),
        ('<p onmouseover=x>keep</p>', "onmouseover"),
        ('<a href="javascript:alert(1)">keep</a>', "javascript"),
        ('<a href="JaVaScRiPt:alert(1)">keep</a>', "alert"),
        ('<a href="data:text/html,x">keep</a>', "data:"),
        ('<a href="//evil.example/x">keep</a>', "evil.example"),
        ('<a href="relative/path">keep</a>', "relative"),
        ('<a href="http://plain.example/">keep</a>', "plain.example"),
        ('<a href="{{ alert.title }}">keep</a>', "alert.title"),
        ('<img src="http://x.example/p.png">keep', "x.example"),
        ('<img src="{{ alert.url }}">keep', "alert.url"),
        ("<iframe src=https://x.example></iframe>keep", "iframe"),
        ("<form action=x><input name=a></form>keep", "form"),
        ("<object data=x></object>keep", "object"),
        ("<embed src=x>keep", "embed"),
        ("<link rel=stylesheet href=https://x.example/a.css>keep", "stylesheet"),
        ('<meta http-equiv="refresh" content="0;url=x">keep', "refresh"),
        ("<svg onload=x><circle/></svg>keep", "svg"),
        ("<!-- hidden --><p>keep</p>", "hidden"),
        ('<p style="background:url(https://x.example/p.png)">keep</p>', "url("),
        ('<p style="background-image:url(x)">keep</p>', "background-image"),
        ('<p style="width:expression(alert(1))">keep</p>', "expression"),
        ('<p style="color:red;@import \'x\'">keep</p>', "@import"),
        ('<p style="color:\\72 ed">keep</p>', "\\"),
        ('<p style="position:absolute;color:red">keep</p>', "position"),
        ('<p style="behavior:url(x.htc)">keep</p>', "behavior"),
        ("<p style='color:red'>keep</p>", "color"),
        ('<p STYLE="color:red" data-mail-style="0">keep</p>', "data-mail-style"),
        ('<p style="color:&#x75;rl(x)">keep</p>', "url"),
        ('<p style="font-family:x;javascript:y">keep</p>', "javascript"),
    ],
)
def test_each_forbidden_construct_is_stripped(authored: str, gone: str) -> None:
    cleaned = tpl.sanitize_email_html(authored)
    assert "keep" in cleaned, cleaned
    assert gone.lower() not in cleaned.lower(), cleaned


def test_allowed_constructs_survive() -> None:
    authored = (
        '<table width="600" cellpadding="0" cellspacing="0" role="presentation">'
        "<tbody><tr><td align=\"left\" bgcolor=\"#ffffff\" "
        'style="padding: 8px; color: #112233; font-family: \'Segoe UI\',Arial; '
        'border: 1px solid #ccc; width: 50%">'
        '<a href="https://ok.example/p?a=1&amp;b=2">go</a> <a href="mailto:a@b.example">m</a> '
        '<a href="{{ alert.url }}">alert</a>'
        '<img src="https://ok.example/logo.png" alt="logo" width="10">'
        "<h2>h</h2><ul><li>i</li></ul><b>b</b><br><hr></td></tr></tbody></table>"
    )
    cleaned = tpl.sanitize_email_html(authored)
    for kept in (
        'width="600"', 'role="presentation"', 'bgcolor="#ffffff"', "padding: 8px",
        "color: #112233", "font-family: 'Segoe UI',Arial", "border: 1px solid #ccc",
        'href="https://ok.example/p?a=1&amp;b=2"', 'href="mailto:a@b.example"',
        'href="{{ alert.url }}"', 'src="https://ok.example/logo.png"', "<h2>", "<li>",
    ):
        assert kept in cleaned, (kept, cleaned)


def test_a_body_with_nothing_left_is_empty() -> None:
    assert tpl.sanitize_email_html("<script>x</script>").strip() == ""


def test_a_link_placeholder_other_than_the_alert_url_cannot_build_an_href() -> None:
    # Alert text is attacker-influenced: as a whole link target it could say javascript:.
    cleaned = tpl.sanitize_email_html('<a href="{{ evidence.reason }}">x</a>')
    out = tpl.render_template(
        ResolvedTemplate("s", cleaned, ""),
        {**tpl.placeholder_values(_message(), _settings()), "evidence.reason": "javascript:x"},
    )
    assert "javascript" not in out.html


# --- text derived from HTML -----------------------------------------------------


def test_text_is_derived_from_the_html_when_the_template_has_none() -> None:
    out = _render(
        '<h1>{{ alert.title }}</h1><table><tr><td>Type</td><td>{{ alert.type }}</td></tr></table>'
        '<p><a href="{{ alert.url }}">Open</a></p>',
    )
    assert out.text.splitlines()[0] == "Spike on fw-edge-01"
    assert "Type Log source" in out.text
    assert "Open (http" in out.text and "/alerts/11111111" in out.text
    assert "<" not in out.text


def test_shipped_text_and_html_name_the_same_placeholders() -> None:
    html_names = tpl.find_placeholders(tpl.SHIPPED_HTML)
    text_names = tpl.find_placeholders(tpl.SHIPPED_TEXT)
    assert html_names <= text_names | {"alert.url"}
    assert text_names <= html_names | {"alert.transition", "alert.url"}


# --- choosing a template ---------------------------------------------------------


def _row(alert_type: str | None, subject: str, enabled: bool = True, text: str | None = None):
    return SimpleNamespace(
        alert_type=alert_type, subject=subject, html_body=f"<p>{subject}</p>",
        text_body=text, enabled=enabled,
    )


def test_a_template_for_the_type_beats_the_default() -> None:
    rows = [_row(None, "default"), _row("log_source", "own", text="own text")]
    chosen = tpl.choose_template(rows, "log_source")  # type: ignore[arg-type]
    assert chosen == ResolvedTemplate("own", "<p>own</p>", "own text")
    assert tpl.choose_template(rows, "waf_endpoint").subject == "default"  # type: ignore[arg-type]
    assert tpl.choose_template(rows, None).subject == "default"  # type: ignore[arg-type]


def test_a_disabled_template_for_the_type_falls_back_to_the_default() -> None:
    rows = [_row(None, "default"), _row("log_source", "own", enabled=False)]
    assert tpl.choose_template(rows, "log_source").subject == "default"  # type: ignore[arg-type]


def test_a_disabled_default_falls_back_to_the_shipped_template() -> None:
    rows = [_row(None, "default", enabled=False)]
    assert tpl.choose_template(rows, "log_source") == tpl.shipped_template()  # type: ignore[arg-type]
    assert tpl.choose_template([], "log_source") == tpl.shipped_template()


def test_the_sample_alerts_cover_every_type_and_use_no_real_data() -> None:
    settings = _settings()
    for alert_type in (None, *tpl.ALERT_TYPE_LABELS):
        values = tpl.sample_values(alert_type, settings)
        assert set(values) == tpl.PLACEHOLDER_NAMES, alert_type
        assert values["alert.url"].startswith("https://qradar.example.internal/")
    assert tpl.sample_values("waf_endpoint", settings)["alert.type"] == "WAF endpoint"
    assert tpl.sample_values("bogus", settings)["alert.type"] == "Log source"
