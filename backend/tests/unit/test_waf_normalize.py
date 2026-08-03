"""The WAF normalization contract.

This file *is* the contract. Every worked example in
docs/PHASE-B-WAF-BEHAVIOR.md section 8.2 appears here verbatim, so a change to a
normalization rule cannot land without a visible diff here -- and, per section
8.6, without bumping ENDPOINT_NORMALIZER_VERSION.

The security assertions are the point: a query string that survives into a
stored endpoint is a data-leak finding, and an attacker-settable segment that
survives is an unbounded-cardinality finding. Both are asserted directly.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.waf.normalize import (
    ENDPOINT_NORMALIZER_VERSION,
    MAX_ENDPOINT_LENGTH,
    SUPPORTED_METHODS,
    normalize_endpoint,
    normalize_method,
    normalize_response_code,
    normalize_virtual_host,
)


def ep(raw: str | None) -> str | None:
    return normalize_endpoint(raw).value


class TestWorkedExamples:
    """docs/PHASE-B-WAF-BEHAVIOR.md section 8.2, executed."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # The three live lab URLs. The first is a SQL-injection payload in a
            # query string; the endpoint must come back clean.
            ("/download?file=../../etc/passwd", "/download"),
            ("/search?q=%3Cscript%3Elab%3C/script%3E", "/search"),
            ("/login?id=1%27OR%271%27=%271", "/login"),
            ("/api/v1/report?id=1%27+OR+%271%27%3D%271", "/api/v1/report"),
            # API versions are preserved and deliberately not merged.
            ("/api/v1/report", "/api/v1/report"),
            ("/api/v2/report", "/api/v2/report"),
            # Slashes.
            ("//portal///login/", "/portal/login"),
            # Identifier templating.
            ("/users/12345/orders/98", "/users/{id}/orders/{id}"),
            ("/o/3f2504e0-4f89-11d3-9a0c-0305e82c3301/edit", "/o/{uuid}/edit"),
            ("/blob/a3f19c8e4d2b7f60", "/blob/{hex}"),
            # File extensions are never templated.
            ("/static/app.css", "/static/app.css"),
            ("/static/app.js", "/static/app.js"),
            # Traversal, decoded exactly once.
            ("/%2e%2e/%2e%2e/admin", "/admin"),
            # Unicode.
            ("/caf%C3%A9/men%C3%BC", "/café/menü"),
            # Root and trailing slash are the same endpoint.
            ("/", "/"),
            ("/a/", "/a"),
            ("/a", "/a"),
        ],
    )
    def test_documented_example(self, raw: str, expected: str) -> None:
        assert ep(raw) == expected

    def test_case_is_preserved(self) -> None:
        """Lower-casing would merge distinct endpoints on a case-sensitive server."""
        assert ep("/Admin") == "/Admin"
        assert ep("/admin") == "/admin"
        assert ep("/Admin") != ep("/admin")

    def test_double_encoding_is_not_collapsed_to_traversal(self) -> None:
        """Decoding twice would turn %252e%252e into '..' and merge endpoints.

        `%252e` decodes once to the literal text `%2e`, which is not a dot
        segment, so the path keeps a distinct identity.
        """
        once = ep("/%252e%252e/admin")
        assert once != "/admin"
        assert once is not None and "admin" in once

    def test_invalid_bytes_do_not_raise(self) -> None:
        assert ep("/%FF%FE/x") is not None

    def test_deep_path_is_truncated_with_a_marker(self) -> None:
        result = ep("/a/b/c/d/e/f/g/h/i/j/k/l/m/n")
        assert result is not None
        assert result.endswith("/{truncated}")
        assert result.count("/") == 13  # 12 kept segments + the marker

    def test_overlong_result_collapses(self) -> None:
        """Reachable via many long-but-untemplated segments, not one huge one.

        A single 300-character segment is hex-shaped and collapses to {hex}
        first, which is the bounded outcome we want. Overlong is the backstop
        for the case templating does not catch: the maximum kept depth of 12
        segments at the maximum kept length of 64 is 780 characters.
        """
        word = "abcdefgh" * 8  # 64 chars, letters only: not numeric/hex/opaque
        assert ep("/" + "/".join([word] * 12)) == "{overlong}"

    def test_a_single_huge_hex_segment_collapses_to_hex(self) -> None:
        assert ep("/" + "a" * 300) == "/{hex}"


class TestAbsenceIsNotTheRootPath:
    """The single most dangerous mistake this module can make.

    Mapping an unpopulated URL field to "/" would invent traffic on the root
    endpoint that nobody requested, and that invented traffic would enter a
    baseline.
    """

    @pytest.mark.parametrize("raw", [None, "", "   ", "?a=1", "#frag"])
    def test_absent_or_pathless_input_is_not_usable(self, raw: str | None) -> None:
        result = normalize_endpoint(raw)
        assert result.value is None
        assert result.is_usable is False

    def test_an_actual_root_request_is_usable(self) -> None:
        result = normalize_endpoint("/")
        assert result.value == "/"
        assert result.is_usable is True


class TestQueryStringsNeverSurvive:
    """Section 20: query strings may carry tokens, session IDs and personal data.

    They are stripped before anything else happens and must not appear in the
    returned value under any input shape.
    """

    @pytest.mark.parametrize(
        "raw",
        [
            "/x?token=eyJhbGciOiJIUzI1NiJ9.secret",
            "/x?password=hunter2&next=/y",
            "/x?a=1?b=2",
            "https://portal.lab.test/x?session=abc#frag",
            "/x#?notaquery=1",
        ],
    )
    def test_no_query_or_fragment_content_survives(self, raw: str) -> None:
        result = normalize_endpoint(raw)
        assert result.value == "/x"
        for leak in ("token", "password", "session", "secret", "hunter2", "notaquery"):
            assert leak not in (result.value or "")


class TestAttackerControlledCardinality:
    """Section 8.4: a scanner must not be able to mint unbounded entities."""

    def test_enumerated_numeric_ids_collapse_to_one_endpoint(self) -> None:
        assert len({ep(f"/users/{i}") for i in range(500)}) == 1

    def test_enumerated_uuids_collapse_to_one_endpoint(self) -> None:
        raws = [
            f"/o/3f2504e0-4f89-11d3-9a0c-0305e82c33{i:02x}/edit" for i in range(200)
        ]
        assert len({ep(r) for r in raws}) == 1

    def test_session_like_tokens_collapse(self) -> None:
        raws = [f"/s/eyJhbGciOiJIUzI1NiJ9x{i}abc123XYZ" for i in range(100)]
        assert len({ep(r) for r in raws}) == 1

    def test_ordinary_route_words_are_not_templated(self) -> None:
        """Over-normalization is also a failure: distinct routes must stay distinct."""
        assert ep("/dashboard") == "/dashboard"
        assert ep("/notifications") == "/notifications"
        assert ep("/api/v1/status") == "/api/v1/status"
        assert ep("/portal/login") == "/portal/login"
        assert len({ep("/dashboard"), ep("/notifications"), ep("/portal/login")}) == 3

    def test_result_never_exceeds_the_column_width(self) -> None:
        for raw in ("/" + "a" * 5000, "/" + "/".join("x" * 70 for _ in range(50))):
            assert len(ep(raw) or "") <= MAX_ENDPOINT_LENGTH

    def test_leading_double_slash_is_a_path_not_an_authority(self) -> None:
        """A schemeless "//host/x" must not normalize to "/x".

        RFC 3986 reads a leading "//" as an authority, but an HTTP origin-form
        request target is a path. Deferring to URL parsing here is exploitable:
        a request for "//evil.example/admin" would be counted against the
        baseline of the unrelated endpoint "/admin".
        """
        assert ep("//evil.example/admin") == "/evil.example/admin"
        assert ep("//evil.example/admin") != ep("/admin")
        assert ep("//portal///login/") == "/portal/login"

    def test_an_explicit_scheme_still_drops_the_authority(self) -> None:
        """Absolute-form targets are real; the host belongs to the entity, not the path."""
        assert ep("https://portal.lab.test/portal/login") == "/portal/login"
        assert ep("http://portal.lab.test:8443/api/v1/status") == "/api/v1/status"


class TestDeterminismAndIdempotence:
    RAWS: ClassVar[list[str]] = [
        "/api/v1/report?id=1",
        "//portal///login/",
        "/users/12345/orders/98",
        "/%2e%2e/admin",
        "/caf%C3%A9",
        "/static/app.css",
        "/",
    ]

    @pytest.mark.parametrize("raw", RAWS)
    def test_repeated_calls_agree(self, raw: str) -> None:
        assert ep(raw) == ep(raw) == ep(raw)

    @pytest.mark.parametrize("raw", RAWS)
    def test_normalizing_a_normalized_endpoint_is_a_no_op(self, raw: str) -> None:
        """Required because a re-collected bucket re-normalizes stored values."""
        once = ep(raw)
        assert once is not None
        assert ep(once) == once

    def test_version_is_a_positive_integer(self) -> None:
        assert isinstance(ENDPOINT_NORMALIZER_VERSION, int)
        assert ENDPOINT_NORMALIZER_VERSION >= 1


class TestTraversalIsReportedNotHidden:
    def test_escaping_the_root_is_flagged(self) -> None:
        result = normalize_endpoint("/%2e%2e/%2e%2e/admin")
        assert result.traversal_attempt is True
        assert result.value == "/admin"

    def test_ordinary_parent_segment_inside_the_tree_is_not_flagged(self) -> None:
        result = normalize_endpoint("/a/b/../c")
        assert result.value == "/a/c"
        assert result.traversal_attempt is False


class TestControlCharacters:
    def test_control_characters_are_removed(self) -> None:
        """A newline or terminal escape in a rendered endpoint is log injection."""
        for raw in ("/a\nb", "/a\rb", "/a\x1b[31mb", "/a\x00b"):
            value = ep(raw)
            assert value is not None
            assert not any(c in value for c in "\n\r\x1b\x00")


class TestMethodNormalization:
    @pytest.mark.parametrize("raw,expected", [("get", "GET"), ("Post", "POST"),
                                              (" put ", "PUT"), ("DELETE", "DELETE")])
    def test_known_methods_upper_case(self, raw: str, expected: str) -> None:
        assert normalize_method(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "BREW", "GET POST", "'; DROP--",
                                     "G3T", "\x00GET"])
    def test_unknown_method_is_none_not_empty_string(self, raw: str | None) -> None:
        """None makes the observation unsupported; "" would silently form an entity."""
        assert normalize_method(raw) is None

    def test_every_supported_method_round_trips(self) -> None:
        for method in SUPPORTED_METHODS:
            assert normalize_method(method.lower()) == method


class TestResponseCodeNormalization:
    """QRadar exposes WAF_ResponseCode as a string property, verified live."""

    @pytest.mark.parametrize("raw,expected", [("200", 200), ("403", 403), ("599", 599),
                                              ("100", 100), (404, 404)])
    def test_valid_three_digit_status(self, raw: str | int, expected: int) -> None:
        assert normalize_response_code(raw) == expected

    @pytest.mark.parametrize(
        "raw", [None, "", "  ", "20", "1000", "abc", "2xx", "-200", "200.0",
                "200 OK", 99, 600, 0, True],
    )
    def test_invalid_stays_missing_and_never_becomes_zero(self, raw: object) -> None:
        """0 would be indistinguishable from a measurement and would skew ratios."""
        assert normalize_response_code(raw) is None  # type: ignore[arg-type]


class TestVirtualHostNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("portal.lab.test", "portal.lab.test"),
            ("Portal.LAB.Test", "portal.lab.test"),
            ("portal.lab.test:8443", "portal.lab.test"),
            ("portal.lab.test.", "portal.lab.test"),
            (" portal.lab.test ", "portal.lab.test"),
        ],
    )
    def test_hosts_normalize(self, raw: str, expected: str) -> None:
        assert normalize_virtual_host(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_absent_host_is_none_not_empty_string(self, raw: str | None) -> None:
        """"" is a value; two hostless sources would look like one application."""
        assert normalize_virtual_host(raw) is None
