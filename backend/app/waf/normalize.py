"""Deterministic, versioned normalization of WAF request fields.

Two jobs, both of which exist to keep an *entity key* bounded and safe:

  * `normalize_endpoint` turns a raw request URL into a stable endpoint. The
    entity key is what a baseline is built per, so anything attacker-settable
    that reaches it becomes unbounded storage and a poisoned statistic. Numeric,
    UUID, hex and high-entropy segments are therefore templated, and the query
    string is discarded before anything else happens.
  * `normalize_method` upper-cases a known HTTP method and refuses everything
    else.

Both are pure, total functions: they return a value or an explicit "unsupported"
marker and never raise. Input is attacker-controlled by definition -- it is the
request line of a web request that a WAF saw -- so the parsing layer degrades
rather than trusting shape.

Every rule here is specified in docs/PHASE-B-WAF-BEHAVIOR.md section 8, and the
worked examples in that section are executed verbatim by the unit tests.

Changing any rule REQUIRES bumping ENDPOINT_NORMALIZER_VERSION. Stored rows
carry the version that produced them, so a rule change makes historical cells
stale rather than silently wrong.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

#: Bumped whenever any rule below changes. Persisted on every observation and
#: every baseline cell; a baseline is only ever built from observations sharing
#: its version.
ENDPOINT_NORMALIZER_VERSION = 1

#: Longest normalized endpoint we store. Matches the column width.
MAX_ENDPOINT_LENGTH = 255
#: A segment longer than this is opaque by construction, not a route.
MAX_SEGMENT_LENGTH = 64
#: Beyond this depth a path is not a route an operator reasons about.
MAX_PATH_SEGMENTS = 12

#: Placeholders. Braces cannot appear in a percent-decoded path segment without
#: having been sent literally, and a literal-brace segment is templated by the
#: opaque rule anyway, so these cannot be forged by a caller.
ID = "{id}"
UUID = "{uuid}"
HEX = "{hex}"
OPAQUE = "{opaque}"
INVALID = "{invalid}"
LONG = "{long}"
TRUNCATED = "{truncated}"
OVERLONG = "{overlong}"

_NUMERIC = re.compile(r"^\d{1,19}$")
_UUID = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)
_HEX = re.compile(r"^[0-9a-fA-F]{8,}$")
_API_VERSION = re.compile(r"^v\d+(\.\d+)?$", re.IGNORECASE)
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")
#: Control characters have no place in a path and are a log-injection vector.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: Minimum length and Shannon entropy for the opaque catch-all. Tuned so that
#: base64url tokens and account identifiers collapse while ordinary route words
#: ("dashboard", "notifications2") survive.
_OPAQUE_MIN_LENGTH = 16
_OPAQUE_MIN_ENTROPY = 3.5

#: The methods we will key an entity on. Anything else -- an unparsed field, a
#: fuzzing payload, a verb this DSM does not emit -- is unsupported rather than
#: coerced, because a bad method value would mint a permanent bogus entity.
SUPPORTED_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)


@dataclass(frozen=True)
class NormalizedEndpoint:
    """A normalized endpoint, or an explicit statement that there is none.

    `value is None` means the raw input carried no usable path. That is a
    MISSING observation, never the root path: mapping an absent field to "/"
    would invent traffic on an endpoint that was never requested.
    """

    value: str | None
    #: Rule identifiers that fired, for evidence and for debugging a surprise.
    rules: tuple[str, ...] = ()
    #: The path tried to escape its root. A finding, not a route.
    traversal_attempt: bool = False

    @property
    def is_usable(self) -> bool:
        return self.value is not None


def _shannon_entropy(text: str) -> float:
    """Bits per character. Zero for a single repeated character."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    total = len(text)
    return -sum(
        (n / total) * math.log2(n / total) for n in counts.values()
    )


def _is_high_entropy(segment: str) -> bool:
    """A long, mixed-alphabet, high-entropy segment is an identifier, not a route.

    All three conditions are required. Length alone would template a long route
    name; entropy alone would template a short random-looking word.
    """
    if len(segment) < _OPAQUE_MIN_LENGTH:
        return False
    if not (_HAS_LETTER.search(segment) and _HAS_DIGIT.search(segment)):
        return False
    return _shannon_entropy(segment) >= _OPAQUE_MIN_ENTROPY


def _template_segment(segment: str, rules: set[str]) -> str:
    """Collapse one segment to a placeholder when it is an identifier.

    Order matters. API versions are checked first because `v1` would otherwise
    never match anything else but the check documents the intent; numeric,
    UUID and hex are checked before the entropy catch-all because they are
    exact and cheap.
    """
    if _API_VERSION.match(segment):
        # Explicitly preserved: /api/v1/x and /api/v2/x are different endpoints
        # and merging them destroys the very signal Phase B exists to watch.
        rules.add("api_version_preserved")
        return segment
    if _NUMERIC.match(segment):
        rules.add("numeric_segment")
        return ID
    if _UUID.match(segment):
        rules.add("uuid_segment")
        return UUID
    if _HEX.match(segment):
        rules.add("hex_segment")
        return HEX
    if _is_high_entropy(segment):
        rules.add("opaque_segment")
        return OPAQUE
    if len(segment) > MAX_SEGMENT_LENGTH:
        rules.add("long_segment")
        return LONG
    return segment


def _decode_once(raw: str, rules: set[str]) -> str:
    """Percent-decode exactly once, then repair invalid bytes.

    Decoding twice is a security bug, not a convenience: `%252e%252e` would
    become `..` and a path that was never a traversal would be treated as one,
    merging unrelated endpoints. `unquote` leaves malformed escapes literal,
    which is the behaviour we want.
    """
    decoded = unquote(raw, errors="replace")
    if decoded != raw:
        rules.add("percent_decoded")
    if "�" in decoded:
        rules.add("invalid_bytes")
    return unicodedata.normalize("NFC", decoded)


def normalize_endpoint(raw_url: str | None) -> NormalizedEndpoint:
    """Normalize a raw WAF request URL to a bounded, stable endpoint.

    The query string is discarded first and never returned, so no caller can
    accidentally persist it. See docs/PHASE-B-WAF-BEHAVIOR.md section 8 for the
    numbered rules and their worked examples.
    """
    if raw_url is None:
        return NormalizedEndpoint(None, ("absent",))

    rules: set[str] = set()
    text = raw_url.strip()
    if not text:
        # An empty string is an unpopulated field, not a request for "/".
        return NormalizedEndpoint(None, ("empty",))

    # Rule 1/2/3: drop scheme, authority, fragment and query.
    #
    # Authority parsing is applied ONLY when an explicit scheme is present. RFC
    # 3986 reads a leading "//" as an authority, but an HTTP origin-form request
    # target is a path, and "//portal///login" from a WAF is a duplicated slash,
    # not a protocol-relative URL. Letting urlsplit decide would be exploitable:
    # a request for "//evil.example/x" would normalize to "/x" and be counted
    # against a completely unrelated endpoint's baseline.
    if "://" in text:
        split = urlsplit(text)
        if split.scheme or split.netloc:
            rules.add("authority_stripped")
        if split.query:
            rules.add("query_stripped")
        if split.fragment:
            rules.add("fragment_stripped")
        path = split.path
    else:
        path = text
        if "#" in path:
            rules.add("fragment_stripped")
            path = path.split("#", 1)[0]
        if "?" in path:
            rules.add("query_stripped")
            path = path.split("?", 1)[0]

    # Rule 4/5: decode once, normalize Unicode, drop control characters. Control
    # characters are removed rather than templated: they are a log-injection and
    # terminal-escape vector and must not survive into anything rendered.
    path = _decode_once(path, rules)
    if _CONTROL.search(path):
        rules.add("control_stripped")
        path = _CONTROL.sub("", path)

    if not path:
        # A URL that was nothing but a query string ("?a=1") has no endpoint.
        return NormalizedEndpoint(None, tuple(sorted(rules | {"empty_path"})))

    # Rule 6/7/8: collapse repeated slashes, then resolve dot segments against a
    # stack so `..` can never escape the root.
    if "//" in path:
        rules.add("repeated_slashes")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if any(p == "." for p in path.split("/")):
        rules.add("dot_segment")
    stack: list[str] = []
    traversal = False
    for part in parts:
        if part == "..":
            rules.add("parent_segment")
            if stack:
                stack.pop()
            else:
                # Attempted to climb above the document root.
                traversal = True
            continue
        stack.append(part)

    # Rule 18: bound depth before templating, so a 10,000-segment path cannot
    # make the entropy check the expensive part of collection.
    truncated = False
    if len(stack) > MAX_PATH_SEGMENTS:
        stack = stack[:MAX_PATH_SEGMENTS]
        truncated = True
        rules.add("path_truncated")

    # Rules 11-17: template identifier-shaped segments. Extensions are never
    # touched -- /app.css and /app.js are different endpoints.
    segments = [_template_segment(seg, rules) for seg in stack]
    if truncated:
        segments.append(TRUNCATED)

    # Rules 8/9: exactly one leading slash, and no trailing slash except root.
    endpoint = "/" + "/".join(segments) if segments else "/"

    # Rule 20: a normalized form that is still enormous is not a route.
    if len(endpoint) > MAX_ENDPOINT_LENGTH:
        rules.add("overlong")
        endpoint = OVERLONG

    return NormalizedEndpoint(
        endpoint, tuple(sorted(rules)), traversal_attempt=traversal
    )


def normalize_method(raw_method: str | None) -> str | None:
    """Upper-case a known HTTP method, or return None.

    None means "no usable method", which makes the observation unsupported. It
    is deliberately not "" and deliberately not "UNKNOWN": an empty string is a
    value that would silently form an entity, and a sentinel would assert we
    observed a method we did not. The caller drops the row and records the
    field as unavailable.
    """
    if raw_method is None:
        return None
    candidate = raw_method.strip().upper()
    if candidate not in SUPPORTED_METHODS:
        return None
    return candidate


def normalize_response_code(raw_code: str | int | None) -> int | None:
    """Strict three-digit HTTP status, or None.

    QRadar exposes WAF_ResponseCode as a *string* custom property (verified live
    on 2026-08-03), so the value arrives as text and must be validated rather
    than cast. Anything that is not a plausible status code -- an empty field, a
    vendor string, a float, a four-digit number -- stays missing. It must never
    become 0, which would be indistinguishable from a real measurement and would
    drag an error-ratio numerator downwards. Only ASCII digits qualify, and the
    function never raises: its callers fold whole buckets.
    """
    if raw_code is None:
        return None
    if isinstance(raw_code, bool):
        return None
    if isinstance(raw_code, int):
        value = raw_code
    else:
        text = str(raw_code).strip()
        # ASCII only: str.isdigit() also accepts "²", which int() rejects with a
        # ValueError, and int() accepts other scripts' decimal digits (Arabic-Indic,
        # full-width), none of which is a status code QRadar would send.
        if not (text.isascii() and text.isdigit()):
            return None
        value = int(text)
    return value if 100 <= value <= 599 else None


def normalize_virtual_host(raw_host: str | None) -> str | None:
    """Lower-cased hostname without a port, or None.

    None is the honest representation of "this WAF source does not expose a
    virtual host". It is never "": an empty string is a value, and two log
    sources with an empty host would look like the same application. The
    uniqueness contract makes NULLs equal so hostless observations still
    converge on one logical entity -- see the migration's NULLS NOT DISTINCT.
    """
    if raw_host is None:
        return None
    host = raw_host.strip().lower()
    if not host:
        return None
    host = _CONTROL.sub("", host)
    # Strip a port, but not an IPv6 literal's colons.
    if not host.startswith("[") and host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.rstrip(".")
    return host[:255] or None
