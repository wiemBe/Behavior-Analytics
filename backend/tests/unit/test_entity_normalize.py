"""Pure normalization rules for entity mentions and future entity search."""

from app.services.entity_mentions import (
    MAX_ENTITY_VALUE_LENGTH,
    classify_entity,
    normalize_entity,
    user_match_forms,
)


def test_ip_is_stripped_and_canonicalized() -> None:
    assert normalize_entity("IP", " 192.0.2.9 ") == "192.0.2.9"
    assert normalize_entity("IP", " 2001:0DB8:0:0::1 ") == "2001:db8::1"


def test_ipv4_mapped_ipv6_stays_distinct_from_ipv4() -> None:
    mapped = normalize_entity("IP", "::ffff:192.0.2.1")
    ipv4 = normalize_entity("IP", "192.0.2.1")

    assert mapped == "::ffff:192.0.2.1"
    assert ipv4 == "192.0.2.1"
    assert mapped != ipv4


def test_host_is_stripped_lowered_and_loses_one_trailing_dot() -> None:
    assert normalize_entity("HOST", "  App-01.Example.COM.  ") == "app-01.example.com"
    assert normalize_entity("HOST", "Host..") == "host."


def test_user_is_stripped_and_lowered_without_rewriting_domain() -> None:
    assert normalize_entity("USER", "  DOMAIN\\Analyst  ") == "domain\\analyst"
    assert normalize_entity("USER", " Analyst@Example.COM ") == "analyst@example.com"


def test_domain_user_match_forms_include_full_and_account() -> None:
    assert user_match_forms(" DOMAIN\\Analyst ") == ("domain\\analyst", "analyst")


def test_upn_user_match_forms_take_account_before_at() -> None:
    assert user_match_forms(" Analyst@Example.COM ") == (
        "analyst@example.com",
        "analyst",
    )


def test_plain_user_has_one_match_form() -> None:
    assert user_match_forms(" Analyst ") == ("analyst",)


def test_empty_and_overlong_text_is_not_normalized() -> None:
    overlong = "x" * (MAX_ENTITY_VALUE_LENGTH + 1)

    assert normalize_entity("HOST", " . ") is None
    assert normalize_entity("USER", "   ") is None
    assert normalize_entity("HOST", overlong) is None
    assert normalize_entity("USER", overlong) is None
    assert user_match_forms(overlong) == ()


def test_unparseable_ip_and_unknown_kind_are_not_normalized() -> None:
    assert normalize_entity("IP", "not-an-address") is None
    assert normalize_entity("DEVICE", "host.example") is None


def test_classification_only_identifies_ip_addresses() -> None:
    assert classify_entity(" 2001:db8::5 ") == "IP"
    assert classify_entity("192.0.2.5") == "IP"
    assert classify_entity("host.example") is None
    assert classify_entity("DOMAIN\\analyst") is None
    assert classify_entity("") is None
