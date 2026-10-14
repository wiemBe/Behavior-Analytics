"""QRadarRestProvider — direct QRadar REST + Ariel APIs.

Owns deterministic background collection and the full scheduled-AQL lifecycle.
AQL execution lives here and only here: the MCP provider deliberately lacks the
capability (see docs/mcp-capability-matrix.md section 1.2).

Security posture baked in here:
  * TLS verification is always on. `verify_ssl=False` is refused rather than
    honoured — a privileged SIEM token must never cross an unverified channel.
    An internal CA bundle is the supported way to trust a private PKI.
  * The SEC token is attached per-request from the encrypted store. It is never
    logged, never echoed into an exception message, and never reaches a response
    body: `_sanitize_upstream_error` rebuilds error text from a fixed vocabulary
    rather than forwarding whatever QRadar said.
  * Retries are bounded, jittered, and applied only to idempotent verbs. The one
    POST we issue (Ariel search creation) is never retried after the request has
    been transmitted, because a retried create would leave an orphaned search
    consuming appliance capacity.
"""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.catchall.keyed_rows import parse_catchall_rows, validate_catchall_type_ids
from app.collectors.bucketed_metrics import (
    fold_log_source_metric_buckets,
    validate_bucketed_metric_window,
)
from app.geo.fold import GeoDirection, GeoKeyedResult, parse_geo_rows
from app.ips.keyed_rows import (
    parse_ips_keyed_rows,
    validate_ips_fields,
    validate_ips_sources,
)
from app.models.enums import IpsDimension
from app.providers.base import (
    IPS_NORMALIZED_FIELD_ALLOWLIST,
    IPS_PROBE_MAX_WINDOW_SECONDS,
    PROPERTY_NAME_PATTERN,
    WAF_PROBE_MAX_WINDOW_SECONDS,
    LogSourceTelemetry,
    ProviderAuthError,
    ProviderCapability,
    ProviderError,
    ProviderQueryRejectedError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    CatchallKeyedResult,
    CustomPropertyDTO,
    DimensionAggregate,
    DimensionValueCount,
    InstanceInfoDTO,
    IpsFieldProbe,
    IpsFieldProbeRequest,
    IpsKeyedResult,
    IpsProbeValue,
    IpsSourceProbe,
    LogSourceDTO,
    LogSourceGroupDTO,
    LogSourceMetricBucketBatch,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
    RuleMatchBatch,
    RuleMatchRow,
    WafBreakdownBatch,
    WafBreakdownBucketBatch,
    WafObservationBatch,
    WafObservationBucketBatch,
    WafPropertyProbe,
    WafPropertyValue,
    WafRejectionObservation,
    WafSourceProbe,
    WindowedRuleMatches,
)
from app.providers.normalize import (
    as_int,
    as_int_list,
    as_str_list,
    as_text,
    parse_qradar_time,
)
from app.providers.telemetry import EndpointCategory, ErrorClass, observe
from app.providers.tls import build_ssl_context
from app.rules.matches import fold_windowed_rows, window_starts
from app.security.sanitizer import sanitize_text
from app.services.backoff import backoff_delay
from app.waf.bucketed import (
    fold_breakdown_buckets,
    fold_observation_buckets,
    parse_breakdown_row,
    parse_group_row,
)

logger = logging.getLogger("app.providers.qradar_rest")

PROVIDER_NAME = "rest"

#: Verbs safe to replay. POST is absent by design.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "DELETE"})

#: QRadar caps a Range request; 500 keeps responses small enough to parse
#: without holding a whole inventory in memory at once.
DEFAULT_PAGE_SIZE = 500

#: Hard ceiling on pages walked in one call, so a pathological or hostile
#: upstream cannot turn a list call into an unbounded loop.
DEFAULT_MAX_PAGES = 200

#: Version of the stored rule-match AQL. Bump it with any change to the query
#: text, so a stored window can always be tied to the query that produced it.
RULE_MATCH_QUERY_VERSION = "rule_match/v1"
#: The keyed multi-window form of the same query (T-037). Its text differs, so
#: its windows carry their own version.
RULE_MATCH_WINDOWED_QUERY_VERSION = "rule_match/v2"


def _rule_id(value: Any) -> int | None:
    """A rule id exactly as QRadar sends it: a non-negative integer.

    Stricter than `as_int`, which truncates a float: a fractional id is a
    malformed row, not rule 5.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def parse_rule_match_rows(raw_rows: Sequence[dict]) -> tuple[list[RuleMatchRow], int]:
    """Parse `creEventList` rows defensively; returns (rows, malformed count).

    A null id is the events that matched no rule at all: excluded, and not
    malformed. A missing or non-integer id, or a count that is not a
    non-negative integer, drops the row and is counted, so a parsing change on
    the appliance shows up as a number rather than as quietly missing rules.
    """
    out: list[RuleMatchRow] = []
    malformed = 0
    for row in raw_rows:
        if "rule_id" in row and row["rule_id"] is None:
            continue
        rule_id = _rule_id(row.get("rule_id"))
        records = as_int(row.get("records"))
        events = as_int(row.get("events"))
        if (
            rule_id is None
            or records is None
            or events is None
            or records < 0
            or events < 0
        ):
            malformed += 1
            continue
        out.append(RuleMatchRow(qradar_rule_id=rule_id, records=records, events=events))
    return out, malformed


class MalformedUpstreamResponse(ProviderError):
    """QRadar returned something we could not safely interpret."""


def _sanitize_upstream_error(status_code: int) -> str:
    """Build operator-facing error text from the status code alone.

    Upstream error bodies are untrusted and have been observed to echo request
    headers. Rather than filter them, we never forward them: the status code
    plus a fixed phrase is all a caller gets.
    """
    if status_code in (401, 403):
        return "QRadar rejected the credentials or the token lacks the required capability"
    if status_code == 404:
        return "QRadar reported the requested object does not exist"
    if status_code == 409:
        return "QRadar reported a conflict with the current state"
    if status_code == 422:
        return "QRadar rejected the request as invalid"
    if status_code == 429:
        return "QRadar is rate-limiting requests"
    if status_code >= 500:
        return "QRadar returned an internal error"
    return "QRadar rejected the request"


class QRadarRestProvider(QRadarProvider):
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
            ProviderCapability.AQL_EXECUTION,
            ProviderCapability.CONFIG_SNAPSHOTS,
            ProviderCapability.LOG_SOURCE_GROUPS,
            ProviderCapability.CUSTOM_PROPERTY_CATALOG,
        }
    )
    #: Exactly what the one metric aggregate in `get_log_source_metrics`
    #: computes. `event_delay_seconds` is populated too, but it is the gap from
    #: the newest event to the bucket end -- bounded by the bucket width -- so it
    #: is deliberately not declared as INGESTION_DELAY.
    log_source_telemetry = frozenset(
        {LogSourceTelemetry.EVENT_VOLUME, LogSourceTelemetry.LAST_EVENT_TIME}
    )
    event_count_aggregation = "SUM(eventcount)"

    def __init__(
        self,
        *,
        base_url: str,
        sec_token: str,
        api_version: str = "20.0",
        verify_ssl: bool = True,
        ca_bundle: str | None = None,
        tls_allow_missing_aki: bool = False,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 30.0,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        client: httpx.AsyncClient | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        ariel_stop_pad_seconds: int = 0,
        clock: Callable[[], datetime] | None = None,
        ips_max_sources_per_search: int = 50,
    ) -> None:
        if not verify_ssl:
            # Hard failure, not a warning. See config._production_hardening.
            raise ValueError(
                "QRadarRestProvider refuses verify_ssl=False; TLS verification is mandatory."
            )
        if not base_url.startswith("https://"):
            raise ValueError("QRadar base_url must be https://")

        self._max_retries = max(0, max_retries)
        self._retry_base = retry_base_seconds
        self._retry_max = retry_max_seconds
        self._page_size = max(1, page_size)
        self._max_pages = max(1, max_pages)
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not cryptographic
        self._sleep = sleep or asyncio.sleep
        self._ariel_stop_pad_seconds = max(0, int(ariel_stop_pad_seconds))
        self._ips_max_sources_per_search = max(1, int(ips_max_sources_per_search))
        self._clock = clock or (lambda: datetime.now(UTC))

        if client is not None:
            self._client = client
            return

        # `ca_bundle` points at an internal CA so a private PKI is trusted
        # without ever disabling verification. Built as an explicit SSLContext
        # rather than passed as a path string, which httpx deprecates.
        verify: ssl.SSLContext = build_ssl_context(
            ca_bundle, allow_missing_aki=tls_allow_missing_aki
        )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api",
            headers={
                "SEC": sec_token,
                "Version": api_version,
                "Accept": "application/json",
            },
            verify=verify,
            # Bounded on every phase: a stalled read must not pin a worker.
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )

    def _ariel_scope(self, start_ms: int, end_ms: int) -> str:
        """Render the Ariel START/STOP clause with a padded STOP bound.

        The STOP bound is widened by `ariel_stop_pad_seconds` (from the
        provider factory, which reads the `ariel_stop_pad_seconds` setting;
        the constructor default of 0 means no pad) so that events stored
        slightly after the window end but with starttime inside the window
        are not dropped. The WHERE clause remains the authoritative half-open
        bound [start_ms, end_ms) and is never padded. The padded STOP is
        capped at the current time (from the injected clock) so it never
        reaches into the future, and it is floored at `end_ms` so it never
        shrinks below the WHERE bound.

        A pad of 0 reproduces the exact text used before this change.
        """
        pad_ms = self._ariel_stop_pad_seconds * 1000
        if pad_ms == 0:
            stop_ms = end_ms
        else:
            now_ms = int(self._clock().timestamp() * 1000)
            stop_ms = max(end_ms, min(end_ms + pad_ms, now_ms))
        return f"START {start_ms} STOP {stop_ms}"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- transport
    async def _request(
        self,
        method: str,
        path: str,
        *,
        category: EndpointCategory,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> Any:
        """One upstream call with bounded, jittered retries.

        Retries cover transport failures, 429 and 5xx. Non-idempotent methods
        are attempted exactly once: replaying a POST could create a second Ariel
        search we would then never track or cancel.
        """
        retryable = method.upper() in _IDEMPOTENT_METHODS
        attempts = self._max_retries + 1 if retryable else 1
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            with observe(PROVIDER_NAME, category) as outcome:
                try:
                    response = await self._client.request(
                        method, path, params=params, headers=headers
                    )
                except httpx.TimeoutException as exc:
                    outcome["error_class"] = ErrorClass.TIMEOUT
                    last_exc = ProviderUnavailableError("QRadar request timed out")
                    last_exc.__cause__ = exc
                except httpx.TransportError as exc:
                    # Covers TLS verification failures. The message is ours, not
                    # the library's, so a certificate path never reaches a caller.
                    outcome["error_class"] = ErrorClass.NETWORK
                    last_exc = ProviderUnavailableError("Could not reach QRadar")
                    last_exc.__cause__ = exc
                else:
                    status = response.status_code

                    if status in (401, 403):
                        outcome["error_class"] = ErrorClass.AUTH
                        # Not retryable: a bad token stays bad, and hammering an
                        # auth endpoint is how accounts get locked out.
                        raise ProviderAuthError(_sanitize_upstream_error(status))

                    if status == 404 and allow_404:
                        return None

                    if status == 429 or status >= 500:
                        outcome["error_class"] = (
                            ErrorClass.RATE_LIMITED if status == 429 else ErrorClass.UPSTREAM_ERROR
                        )
                        last_exc = ProviderUnavailableError(
                            _sanitize_upstream_error(status), status_code=status
                        )
                        retry_after = self._retry_after_seconds(response)
                        if attempt < attempts:
                            await self._sleep(
                                retry_after
                                if retry_after is not None
                                else self._backoff(attempt)
                            )
                            continue
                        raise last_exc

                    if status >= 400:
                        outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
                        if status == 422:
                            # A subclass, so every existing `except ProviderError`
                            # behaves as before. Callers that need to tell "QRadar
                            # refused this query" from "the query failed" can.
                            raise ProviderQueryRejectedError(
                                _sanitize_upstream_error(status), status_code=status
                            )
                        raise ProviderError(_sanitize_upstream_error(status), status_code=status)

                    return self._decode(response, outcome)

            if attempt < attempts:
                await self._sleep(self._backoff(attempt))

        raise last_exc or ProviderUnavailableError("QRadar request failed")

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        """Honour QRadar's own throttling hint when it sends one.

        Clamped to the configured maximum so a hostile or misconfigured header
        cannot park a worker for hours.
        """
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            return None
        if seconds < 0:
            return None
        return min(seconds, self._retry_max)

    def _backoff(self, attempt: int) -> float:
        return backoff_delay(
            attempt,
            base_seconds=self._retry_base,
            max_seconds=self._retry_max,
            rng=self._rng,
        )

    def _decode(self, response: httpx.Response, outcome: dict) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MalformedUpstreamResponse(
                "QRadar returned a response that was not valid JSON"
            ) from exc

    async def _paginate(
        self,
        path: str,
        *,
        category: EndpointCategory,
        params: dict[str, Any] | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> list[dict]:
        """Walk QRadar's `Range: items=start-end` pagination.

        Bounded twice over: `max_pages` caps the walk, and a short page ends it.
        QRadar returns a `Content-Range` we do not rely on, because it is absent
        on some endpoints and versions.
        """
        size = page_size or self._page_size
        pages = max_pages or self._max_pages
        collected: list[dict] = []

        for page in range(pages):
            start = page * size
            end = start + size - 1
            body = await self._request(
                "GET",
                path,
                category=category,
                params=params,
                headers={"Range": f"items={start}-{end}"},
            )
            if body is None:
                break
            if not isinstance(body, list):
                raise MalformedUpstreamResponse(
                    f"QRadar returned a non-list body for a collection endpoint ({category})"
                )
            # Drop non-object entries rather than failing the whole page.
            collected.extend(item for item in body if isinstance(item, dict))
            if len(body) < size:
                break
        else:
            logger.warning(
                "pagination stopped at the page ceiling; results may be incomplete",
                extra={"endpoint_category": str(category), "max_pages": pages},
            )
        return collected

    # ------------------------------------------------- health / inventory
    async def validate_connection(self) -> InstanceInfoDTO:
        """Cheapest authenticated call that proves credentials and TLS work."""
        return await self.get_instance_info()

    async def get_instance_info(self) -> InstanceInfoDTO:
        body = await self._request("GET", "/system/about", category=EndpointCategory.SYSTEM)
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar /system/about did not return an object")
        version = body.get("external_version") or body.get("version")
        return InstanceInfoDTO(
            version=str(version) if version is not None else None,
            build=str(body.get("build_version")) if body.get("build_version") else None,
            reachable=True,
            # Small, non-sensitive descriptor block only; not the full payload.
            raw_about={
                k: v
                for k, v in body.items()
                if k in {"release_name", "external_version", "build_version"}
            },
        )

    async def list_log_sources(self) -> list[LogSourceDTO]:
        rows = await self._paginate(
            "/config/event_sources/log_source_management/log_sources",
            category=EndpointCategory.LOG_SOURCE_LIST,
        )
        return [dto for dto in (self._to_log_source(r) for r in rows) if dto is not None]

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        body = await self._request(
            "GET",
            f"/config/event_sources/log_source_management/log_sources/{int(qradar_id)}",
            category=EndpointCategory.LOG_SOURCE_DETAIL,
            allow_404=True,
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar log-source detail was not an object")
        return self._to_log_source(body)

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        rows = await self._paginate(
            "/config/event_sources/log_source_management/log_source_types",
            category=EndpointCategory.LOG_SOURCE_TYPE_LIST,
        )
        out: list[LogSourceTypeDTO] = []
        for row in rows:
            type_id = as_int(row.get("id"))
            name = row.get("name")
            if type_id is None or not isinstance(name, str):
                continue
            out.append(
                LogSourceTypeDTO(
                    type_id=type_id,
                    name=name[:255],
                    internal=row.get("internal") is True,
                    custom=row.get("custom") is True,
                )
            )
        return out

    async def list_log_source_groups(self) -> list[LogSourceGroupDTO]:
        self.require(ProviderCapability.LOG_SOURCE_GROUPS)
        rows = await self._paginate(
            "/config/event_sources/log_source_management/log_source_groups",
            category=EndpointCategory.LOG_SOURCE_LIST,
        )
        out: list[LogSourceGroupDTO] = []
        for row in rows:
            qradar_id = as_int(row.get("id"))
            name = row.get("name")
            if qradar_id is None or not isinstance(name, str) or not name.strip():
                continue
            out.append(
                LogSourceGroupDTO(
                    qradar_id=qradar_id,
                    name=name.strip()[:255],
                    description=sanitize_text(as_text(row.get("description"), limit=4096)),
                    parent_qradar_id=as_int(row.get("parent_id")),
                    assignable=row.get("assignable") is True,
                )
            )
        return out

    async def list_custom_properties(
        self, *, log_source_type_id: int | None = None
    ) -> list[CustomPropertyDTO]:
        """Join regex properties to the types their enabled expressions cover.

        Two slim GETs, always with `fields=` so no regex or payload body is ever
        requested (a full row is ~1.4 KB against ~140 B). Only enabled expressions
        that cover a whole type (`log_source_id == -1`) count.
        """
        self.require(ProviderCapability.CUSTOM_PROPERTY_CATALOG)
        base = "/config/event_sources/custom_properties"
        props = await self._paginate(
            f"{base}/regex_properties",
            category=EndpointCategory.LOG_SOURCE_LIST,
            params={
                "fields": "id,identifier,name,property_type,use_for_rule_engine,auto_discovered"
            },
        )
        expr_params = {
            "fields": "regex_property_identifier,log_source_type_id,log_source_id,enabled"
        }
        if log_source_type_id is not None:
            expr_params["filter"] = f"log_source_type_id={int(log_source_type_id)}"
        exprs = await self._paginate(
            f"{base}/property_expressions",
            category=EndpointCategory.LOG_SOURCE_LIST,
            params=expr_params,
        )
        types_by_identifier: dict[str, set[int]] = {}
        for expr in exprs:
            identifier = expr.get("regex_property_identifier")
            type_id = as_int(expr.get("log_source_type_id"))
            if (
                not isinstance(identifier, str)
                or type_id is None
                or expr.get("enabled") is not True
                or as_int(expr.get("log_source_id")) != -1
            ):
                continue
            types_by_identifier.setdefault(identifier, set()).add(type_id)

        out: list[CustomPropertyDTO] = []
        for row in props:
            qradar_id = as_int(row.get("id"))
            identifier = row.get("identifier")
            name = row.get("name")
            if qradar_id is None or not isinstance(identifier, str) or not isinstance(name, str):
                continue
            out.append(
                CustomPropertyDTO(
                    qradar_id=qradar_id,
                    identifier=identifier,
                    name=name,
                    property_type=str(row.get("property_type") or "string")[:32],
                    use_for_rule_engine=row.get("use_for_rule_engine") is True,
                    auto_discovered=row.get("auto_discovered") is True,
                    log_source_type_ids=tuple(sorted(types_by_identifier.get(identifier, ()))),
                    name_allowed=PROPERTY_NAME_PATTERN.match(name) is not None,
                )
            )
        return out

    async def get_log_source_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[LogSourceMetricSample]:
        """Collect bounded per-log-source event volume through Ariel.

        QRadar exposes no REST metric endpoint. This is a read-only aggregate
        over ``events`` and stores no raw event or payload data. Search creation
        is attempted once, polling is bounded, and results are capped by the
        provider's pagination ceiling.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
        end = window_end if window_end.tzinfo else window_end.replace(tzinfo=UTC)
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        seconds = int((end - start).total_seconds())
        if seconds <= 0:
            raise ValueError("metric window end must be after its start")
        if seconds > 168 * 3600:
            raise ValueError("metric window exceeds the 168-hour safety bound")

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        aql = (
            # SUM(eventcount), not COUNT(*). QRadar coalesces identical events
            # into a single Ariel record carrying an `eventcount` multiplier, so
            # a row count is a count of *records*, not of events received. The
            # gap is not a rounding error: measured live on 2026-08-03, four
            # sources reported ~197 records for ~7,570 actual events -- a 38x
            # under-report -- while most sources sat at exactly 1.0. Because the
            # coalescing ratio varies per source and over time, COUNT(*) does
            # not even under-report *consistently*: a burst of identical events
            # coalesces harder, so true volume can rise sharply while the record
            # count stays flat. That is precisely the spike a volume detector
            # exists to catch, and it is the reading it would have missed.
            'SELECT logsourceid AS "qradar_id", SUM(eventcount) AS "event_count", '  # noqa: S608 - integer epochs only
            'MAX(starttime) AS "last_event_time" FROM events '
            f"WHERE starttime >= {start_ms} AND starttime < {end_ms} "
            "GROUP BY logsourceid "
            # START/STOP scopes the Ariel search itself. Without it QRadar
            # defaults the search to a recent window and intersects that with
            # the WHERE clause, so any bucket older than the default silently
            # returns zero rows -- indistinguishable from real silence. The
            # WHERE clause stays authoritative for the half-open bucket bound.
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        handle = await self.create_ariel_search(aql)
        status: ArielSearchStatusDTO | None = None
        # Two minutes maximum. The task's interval is at least one minute, and
        # a stuck appliance must never pin a worker indefinitely.
        for _ in range(120):
            status = await self.get_ariel_search_status(handle.search_id)
            if status.is_terminal:
                break
            await self._sleep(1.0)
        else:
            await self.cancel_ariel_search(handle.search_id)
            raise ProviderUnavailableError("QRadar Ariel metric search timed out")

        if status is None or not status.is_success:
            raise ProviderError("QRadar Ariel metric search did not complete")

        max_rows = self._page_size * self._max_pages
        results = await self.get_ariel_search_results(
            handle.search_id, max_rows=max_rows
        )
        if results.truncated:
            # A capped aggregate drops sources, and the collector would zero-fill
            # them as complete silence. Fail the interval instead.
            raise ProviderError(
                f"QRadar Ariel metric search was truncated at {max_rows} rows"
            )
        samples: list[LogSourceMetricSample] = []
        for row in results.rows:
            qradar_id = as_int(row.get("qradar_id"))
            count = as_int(row.get("event_count"))
            if qradar_id is None or count is None or count < 0:
                continue
            last_event = parse_qradar_time(row.get("last_event_time"))
            delay = (
                max(0.0, (end - last_event).total_seconds())
                if last_event is not None
                else None
            )
            samples.append(
                LogSourceMetricSample(
                    qradar_id=qradar_id,
                    bucket_start=start,
                    bucket_seconds=seconds,
                    event_count=count,
                    average_eps=count / seconds,
                    last_event_at=last_event,
                    event_delay_seconds=delay,
                    stored_event_count=count,
                )
            )
        return samples

    async def get_log_source_metrics_bucketed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        bucket_seconds: int,
        max_rows: int,
    ) -> LogSourceMetricBucketBatch:
        """Collect one keyed Ariel aggregate spanning multiple metric buckets."""
        self.require(ProviderCapability.AQL_EXECUTION)
        if max_rows < 1:
            raise ValueError("metric row limit must be positive")
        start, end = validate_bucketed_metric_window(
            window_start, window_end, bucket_seconds
        )
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        bucket_ms = bucket_seconds * 1000
        aql = (
            'SELECT logsourceid AS "qradar_id", '  # noqa: S608 - validated integers only
            f'starttime / {bucket_ms} AS "k", '
            'SUM(eventcount) AS "event_count", MAX(starttime) AS "last_event_time" '
            "FROM events "
            f"WHERE starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY logsourceid, starttime / {bucket_ms} "
            f"LIMIT {max_rows + 1} {self._ariel_scope(start_ms, end_ms)}"
        )
        handle = await self.create_ariel_search(aql)
        status: ArielSearchStatusDTO | None = None
        for _ in range(120):
            status = await self.get_ariel_search_status(handle.search_id)
            if status.is_terminal:
                break
            await self._sleep(1.0)
        else:
            await self.cancel_ariel_search(handle.search_id)
            raise ProviderUnavailableError("QRadar Ariel metric search timed out")

        if status is None or not status.is_success:
            raise ProviderError("QRadar Ariel metric search did not complete")

        results = await self.get_ariel_search_results(handle.search_id, max_rows=max_rows)
        buckets = fold_log_source_metric_buckets(
            results.rows,
            start,
            end,
            bucket_seconds=bucket_seconds,
        )
        return LogSourceMetricBucketBatch(
            buckets=buckets,
            truncated=results.truncated,
            rows_returned=len(results.rows),
            aql=aql,
        )

    # -------------------------------------------------- explanation evidence
    #: Explanation dimension -> the Ariel field that carries it, plus an
    #: optional companion field holding a human-readable label. Only fields
    #: QRadar normalizes across DSMs are listed; anything absent from a source's
    #: parser is reported UNAVAILABLE rather than guessed at.
    _DIMENSION_FIELDS: ClassVar[dict[str, tuple[str, str | None]]] = {
        "qid": ("qid", "QIDNAME(qid)"),
        "event_name": ("QIDNAME(qid)", None),
        "source_ip": ("sourceip", None),
        "destination_ip": ("destinationip", None),
        "destination_port": ("destinationport", None),
        "source_port": ("sourceport", None),
        "username": ("username", None),
        "action": ("eventdirection", None),
        "category": ("category", "CATEGORYNAME(category)"),
        "protocol": ("protocolid", "PROTOCOLNAME(protocolid)"),
    }

    async def get_dimension_aggregates(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        dimensions: Sequence[str],
        top_n: int,
    ) -> list[DimensionAggregate]:
        """One bounded aggregate Ariel query per requested dimension.

        Each query is `GROUP BY <field> ORDER BY count DESC LIMIT top_n` over a
        single log source and a bounded window. No raw events are retrieved and
        nothing is stored beyond the aggregate counts.

        A failure on one dimension is recorded on that dimension and does not
        abort the others: a partial explanation is useful, and losing the whole
        package because one DSM lacks `username` would be a poor trade.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        limit = max(1, min(top_n, self._page_size))

        out: list[DimensionAggregate] = []
        for dimension in dimensions:
            mapping = self._DIMENSION_FIELDS.get(dimension)
            if mapping is None:
                out.append(
                    DimensionAggregate(
                        dimension=dimension,
                        available=False,
                        error="dimension is not mapped to a QRadar field",
                    )
                )
                continue
            out.append(
                await self._aggregate_one_dimension(
                    dimension=dimension,
                    field=mapping[0],
                    label_field=mapping[1],
                    qradar_log_source_id=qradar_log_source_id,
                    start=start,
                    end=end,
                    limit=limit,
                )
            )
        return out

    def _bounded_window(
        self, window_start: datetime, window_end: datetime
    ) -> tuple[datetime, datetime]:
        start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
        end = window_end if window_end.tzinfo else window_end.replace(tzinfo=UTC)
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start:
            raise ValueError("explanation window end must be after its start")
        if (end - start).total_seconds() > 168 * 3600:
            raise ValueError("explanation window exceeds the 168-hour safety bound")
        return start, end

    async def _aggregate_one_dimension(
        self,
        *,
        dimension: str,
        field: str,
        label_field: str | None,
        qradar_log_source_id: int,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> DimensionAggregate:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        label_select = f', {label_field} AS "label"' if label_field else ""
        # Only integer epochs and a fixed field whitelist are interpolated; no
        # caller-supplied string reaches the query text.
        aql = (
            # SUM(eventcount), for the reason given in get_log_source_metrics:
            # a contributor's `count` is its share of *events*, and it is
            # compared directly against the metric total that now sums the same
            # way. Mixing the two would make contributor shares silently fail to
            # add up. Coalescing is also uneven *within* a dimension -- measured
            # live, one source IP on a coalescing source carried 1,056 events
            # across 673 records while its peers sat at exactly one event per
            # record -- so the distortion changes the ranking, not just the
            # scale. ORDER BY moves with it so "largest contributor" keeps
            # meaning largest by event volume.
            #
            # Cardinality semantics are deliberately untouched: `distinct_count`
            # and `truncated` below are derived from the number of GROUP BY rows
            # returned, which this change does not affect.
            f'SELECT {field} AS "value", SUM(eventcount) AS "count"{label_select} '  # noqa: S608
            "FROM events "
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY {field} ORDER BY SUM(eventcount) DESC LIMIT {limit} "
            # See get_log_source_metrics: without START/STOP the search falls
            # back to a recent default window, so contributor evidence for any
            # past anomaly interval would come back empty rather than absent.
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        try:
            rows = await self._run_bounded_aggregate(aql, max_rows=limit)
        except (ProviderError, ValueError) as exc:
            # Sanitized: the class name and our own message only, never a
            # provider response body that could carry payload or header data.
            return DimensionAggregate(
                dimension=dimension, available=True, query=aql,
                error=f"{type(exc).__name__}: {exc}"[:400],
            )

        values: list[DimensionValueCount] = []
        total = 0
        for row in rows:
            raw = row.get("value")
            count = as_int(row.get("count"))
            if raw is None or count is None or count < 0:
                continue
            label = row.get("label")
            values.append(
                DimensionValueCount(
                    value=str(raw)[:255],
                    count=count,
                    label=str(label)[:255] if label not in (None, "") else None,
                )
            )
            total += count

        # No rows at all is the signature of a field this DSM never populates.
        # Reporting it as "available with zero values" would assert we measured
        # an absence we did not measure.
        if not values:
            return DimensionAggregate(
                dimension=dimension, available=False, query=aql,
                error="field is not populated for this log source",
            )

        return DimensionAggregate(
            dimension=dimension,
            available=True,
            values=values,
            distinct_count=len(values),
            total_count=total,
            truncated=len(values) >= limit,
            query=aql,
        )

    async def get_rule_matches(
        self, window_start: datetime, window_end: datetime, *, max_rows: int
    ) -> RuleMatchBatch:
        """One bounded aggregate: events matched per rule or building block.

        Only the validated window bounds and the integer cap reach the query
        text. Read-only: one Ariel search over ``events``, no raw event kept.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        limit = max(1, min(int(max_rows), self._page_size * self._max_pages))
        aql = (
            # COUNT(*) is retained as provenance only; SUM(eventcount) is the
            # volume, for the coalescing reason given in get_log_source_metrics.
            # `creEventList` comes back as a bare integer id, one row per rule
            # or building block an event matched (T-015 section 11).
            'SELECT creEventList AS "rule_id", COUNT(*) AS "records", '  # noqa: S608
            'SUM(eventcount) AS "events" FROM events '
            f"WHERE starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY creEventList ORDER BY COUNT(*) DESC LIMIT {limit} "
            # START/STOP scopes the search itself; see get_log_source_metrics.
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        raw = await self._run_bounded_aggregate(aql, max_rows=limit)
        rows, malformed = parse_rule_match_rows(raw)
        return RuleMatchBatch(
            window_start=start,
            window_end=end,
            rows=rows,
            # Judged on the raw page, not the parsed rows: a page full of
            # dropped rows is still a full page.
            truncated=len(raw) >= limit,
            row_cap=limit,
            malformed_rows=malformed,
            query_version=RULE_MATCH_QUERY_VERSION,
            query=aql,
            ariel_searches=1,
        )

    async def get_rule_matches_windowed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        window_seconds: int,
        max_rows: int,
    ) -> WindowedRuleMatches:
        """One keyed aggregate: events matched per rule, per whole window.

        `starttime / {window_ms}` is integer division, so ``k * window_ms`` is a
        window's start in epoch-UTC milliseconds with no timezone involved
        (T-032 Q1, Q6). `LIMIT` caps the result as a whole, so it asks for
        ``max_rows + 1`` and reads more than ``max_rows`` rows as truncated;
        the caller then keeps nothing and splits. Only validated epochs and
        integers reach the query text. Read-only: one Ariel search over
        ``events``, no raw event kept.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        # Validates alignment and whole windows before any request. The 168 h
        # bound of `_bounded_window` is not applied: the span is a count of
        # windows chosen by the collector, and the row cap bounds the answer.
        window_starts(window_start, window_end, window_seconds)
        start, end = window_start.astimezone(UTC), window_end.astimezone(UTC)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        window_ms = int(window_seconds) * 1000
        # The same effective cap the single-window query records as `row_cap`.
        cap = max(1, min(int(max_rows), self._page_size * self._max_pages))
        aql = (
            # See get_rule_matches for COUNT(*) and SUM(eventcount).
            'SELECT creEventList AS "rule_id", '  # noqa: S608
            f'starttime / {window_ms} AS "k", '
            'COUNT(*) AS "records", SUM(eventcount) AS "events" FROM events '
            f"WHERE starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY creEventList, starttime / {window_ms} "
            f"ORDER BY COUNT(*) DESC LIMIT {cap + 1} "
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        raw = await self._run_bounded_aggregate(aql, max_rows=cap + 1)
        truncated = len(raw) > cap
        return WindowedRuleMatches(
            window_start=start,
            window_end=end,
            window_seconds=window_seconds,
            batches=fold_windowed_rows(
                raw,
                window_start=start,
                window_end=end,
                window_seconds=window_seconds,
                row_cap=cap,
                parse_rows=parse_rule_match_rows,
                query_version=RULE_MATCH_WINDOWED_QUERY_VERSION,
                query=aql,
            ),
            truncated=truncated,
            row_cap=cap,
            query=aql,
            ariel_searches=1,
        )

    # ------------------------------------------------------------ WAF (Phase B)
    #: Custom-property names are site-authored and reach the query text, so they
    #: are validated against this before interpolation. QRadar property names are
    #: letters, digits, underscore, space and hyphen; anything else -- a quote, a
    #: comma, a parenthesis -- is rejected rather than escaped, because an
    #: allowlist that a reviewer can verify by eye beats a quoting scheme that
    #: has to be trusted.
    _PROPERTY_NAME = PROPERTY_NAME_PATTERN

    @classmethod
    def _quote_property(cls, name: str) -> str:
        if not cls._PROPERTY_NAME.match(name):
            raise ValueError(f"unsafe QRadar property name: {name!r}")
        return f'"{name}"'

    async def get_waf_observations(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None = None,
        max_groups: int = 500,
    ) -> WafObservationBatch:
        """Two bounded aggregates: grouped request volume, and the window total.

        The second query is not redundant. Grouping by URL and method drops
        events whose properties did not extract, so the grouped sum alone cannot
        distinguish "this source sent 500 requests to three endpoints" from "this
        source sent 5,000 requests and we could only parse 500 of them". The
        ratio between them is the endpoint coverage that decides COMPLETE vs
        PARTIAL, and guessing it would be exactly the silent-zero failure the
        completeness model exists to prevent.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        limit = max(1, min(max_groups, self._page_size))

        url = self._quote_property(url_property)
        method = self._quote_property(method_property)
        vhost = (
            self._quote_property(virtual_host_property)
            if virtual_host_property
            else None
        )
        group_cols = f"{url}, {method}" + (f", {vhost}" if vhost else "")
        vhost_select = f", {vhost} AS \"raw_virtual_host\"" if vhost else ""

        # SUM(eventcount) for volume; COUNT(*) retained only as provenance so an
        # operator can see the coalescing ratio. See commit ae9932a.
        aql = (
            f'SELECT {url} AS "raw_url", {method} AS "raw_method"{vhost_select}, '  # noqa: S608
            'SUM(eventcount) AS "request_count", COUNT(*) AS "record_count", '
            'UNIQUECOUNT(sourceip) AS "distinct_clients" FROM events '
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY {group_cols} ORDER BY SUM(eventcount) DESC LIMIT {limit} "
            # START/STOP scopes the search itself; without it a historical bucket
            # silently returns nothing. See get_log_source_metrics.
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        rows = await self._run_bounded_aggregate(aql, max_rows=limit)

        groups = [g for g in map(parse_group_row, rows) if g is not None]

        total_aql = (
            'SELECT SUM(eventcount) AS "total_events" FROM events '  # noqa: S608
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"LIMIT 1 {self._ariel_scope(start_ms, end_ms)}"
        )
        total_rows = await self._run_bounded_aggregate(total_aql, max_rows=1)
        total = as_int(total_rows[0].get("total_events")) if total_rows else 0

        return WafObservationBatch(
            qradar_log_source_id=qradar_log_source_id,
            window_start=start,
            window_end=end,
            groups=groups,
            total_events=total or 0,
            truncated=len(groups) >= limit,
            query=aql,
            total_query=total_aql,
            ariel_searches=2,
        )

    async def get_waf_breakdown(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None,
        breakdown: str,
        breakdown_property: str | None,
        max_groups: int = 2000,
    ) -> WafBreakdownBatch:
        """One bounded aggregate: requests by raw endpoint key and one field.

        `IS NOT NULL` on the breakdown field defines the population explicitly,
        so the result does not depend on how Ariel groups a NULL value: for each
        endpoint, the denominator is the requests that carried the field, and
        numerator and denominator come from this same search.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        limit = max(1, min(max_groups, self._page_size * self._max_pages))

        if breakdown == "SOURCE_IP":
            # A normalized QRadar field, not a custom property: never quoted, and
            # never taken from configuration.
            value = "sourceip"
        elif breakdown in ("ACTION", "RESPONSE_CODE") and breakdown_property:
            value = self._quote_property(breakdown_property)
        else:
            raise ValueError(f"unsupported WAF breakdown {breakdown!r}")
        url = self._quote_property(url_property)
        method = self._quote_property(method_property)
        vhost = (
            self._quote_property(virtual_host_property) if virtual_host_property else None
        )
        group_cols = f"{url}, {method}" + (f", {vhost}" if vhost else "") + f", {value}"
        vhost_select = f", {vhost} AS \"raw_virtual_host\"" if vhost else ""
        aql = (
            f'SELECT {url} AS "raw_url", {method} AS "raw_method"{vhost_select}, '  # noqa: S608
            f'{value} AS "raw_value", SUM(eventcount) AS "request_count" FROM events '
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"AND {value} IS NOT NULL "
            f"GROUP BY {group_cols} ORDER BY SUM(eventcount) DESC LIMIT {limit} "
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        rows = await self._run_bounded_aggregate(aql, max_rows=limit)
        groups = [g for g in map(parse_breakdown_row, rows) if g is not None]
        return WafBreakdownBatch(
            breakdown=breakdown,
            qradar_log_source_id=qradar_log_source_id,
            window_start=start,
            window_end=end,
            groups=groups,
            truncated=len(rows) >= limit,
            query=aql,
            ariel_searches=1,
        )

    def _keyed_row_cap(self, max_rows: int) -> int:
        """The row budget of one keyed search, bounded as the results read is."""
        return max(1, min(int(max_rows), self._page_size * self._max_pages))

    async def get_waf_observations_bucketed(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None = None,
        max_groups: int = 500,
        max_rows: int,
    ) -> WafObservationBucketBatch:
        """`get_waf_observations` over many whole buckets: two keyed aggregates.

        Today's grouped query and totals query, each with ``starttime /
        {bucket_ms} AS "k"`` selected and grouped (integer division: ``k *
        bucket_ms`` is a bucket's start in epoch-UTC milliseconds, T-032 Q1) and
        ``LIMIT max_rows + 1``. The totals query loses its ``LIMIT 1`` and
        returns one row per bucket. More than ``max_rows`` rows in either read
        as truncated: no bucket is built, and the caller splits. When the
        grouped search was whole, `fold_observation_buckets` makes the
        per-bucket top-N cut that ``LIMIT`` cannot make per bucket.

        Only validated integers and allowlisted property names reach the query
        text. Read-only: Ariel searches over ``events``, nothing kept.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        bucket_ms = int(bucket_seconds) * 1000
        # The per-bucket cut is the one today's single-bucket query makes.
        limit = max(1, min(max_groups, self._page_size))
        cap = self._keyed_row_cap(max_rows)

        url = self._quote_property(url_property)
        method = self._quote_property(method_property)
        vhost = (
            self._quote_property(virtual_host_property) if virtual_host_property else None
        )
        group_cols = f"{url}, {method}" + (f", {vhost}" if vhost else "")
        vhost_select = f", {vhost} AS \"raw_virtual_host\"" if vhost else ""
        where = (
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
        )
        aql = (
            f'SELECT {url} AS "raw_url", {method} AS "raw_method"{vhost_select}, '  # noqa: S608
            f'starttime / {bucket_ms} AS "k", '
            'SUM(eventcount) AS "request_count", COUNT(*) AS "record_count", '
            'UNIQUECOUNT(sourceip) AS "distinct_clients" FROM events '
            f"{where}"
            f"GROUP BY {group_cols}, starttime / {bucket_ms} LIMIT {cap + 1} "
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        total_aql = (
            f'SELECT starttime / {bucket_ms} AS "k", '  # noqa: S608
            'SUM(eventcount) AS "total_events" FROM events '
            f"{where}"
            f"GROUP BY starttime / {bucket_ms} LIMIT {cap + 1} "
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        group_rows = await self._run_bounded_aggregate(aql, max_rows=cap + 1)
        if len(group_rows) > cap:
            return WafObservationBucketBatch(
                truncated=True,
                rows_returned=len(group_rows),
                query=aql,
                total_query=total_aql,
                ariel_searches=1,
            )
        total_rows = await self._run_bounded_aggregate(total_aql, max_rows=cap + 1)
        if len(total_rows) > cap:
            return WafObservationBucketBatch(
                truncated=True,
                rows_returned=len(group_rows) + len(total_rows),
                query=aql,
                total_query=total_aql,
                ariel_searches=2,
            )
        return WafObservationBucketBatch(
            buckets=fold_observation_buckets(
                group_rows,
                total_rows,
                qradar_log_source_id=qradar_log_source_id,
                window_start=start,
                window_end=end,
                bucket_seconds=bucket_seconds,
                max_groups=limit,
                query=aql,
                total_query=total_aql,
            ),
            rows_returned=len(group_rows) + len(total_rows),
            query=aql,
            total_query=total_aql,
            ariel_searches=2,
        )

    async def get_waf_breakdown_bucketed(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        url_property: str,
        method_property: str,
        virtual_host_property: str | None,
        breakdown: str,
        breakdown_property: str | None,
        max_groups: int = 2000,
        max_rows: int,
    ) -> WafBreakdownBucketBatch:
        """`get_waf_breakdown` over many whole buckets: one keyed aggregate.

        The same query with the bucket key selected and grouped and ``LIMIT
        max_rows + 1``; see `get_waf_observations_bucketed`.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        bucket_ms = int(bucket_seconds) * 1000
        limit = max(1, min(max_groups, self._page_size * self._max_pages))
        cap = self._keyed_row_cap(max_rows)

        if breakdown == "SOURCE_IP":
            value = "sourceip"
        elif breakdown in ("ACTION", "RESPONSE_CODE") and breakdown_property:
            value = self._quote_property(breakdown_property)
        else:
            raise ValueError(f"unsupported WAF breakdown {breakdown!r}")
        url = self._quote_property(url_property)
        method = self._quote_property(method_property)
        vhost = (
            self._quote_property(virtual_host_property) if virtual_host_property else None
        )
        group_cols = f"{url}, {method}" + (f", {vhost}" if vhost else "") + f", {value}"
        vhost_select = f", {vhost} AS \"raw_virtual_host\"" if vhost else ""
        aql = (
            f'SELECT {url} AS "raw_url", {method} AS "raw_method"{vhost_select}, '  # noqa: S608
            f'{value} AS "raw_value", starttime / {bucket_ms} AS "k", '
            'SUM(eventcount) AS "request_count" FROM events '
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"AND {value} IS NOT NULL "
            f"GROUP BY {group_cols}, starttime / {bucket_ms} LIMIT {cap + 1} "
            f"{self._ariel_scope(start_ms, end_ms)}"
        )
        rows = await self._run_bounded_aggregate(aql, max_rows=cap + 1)
        if len(rows) > cap:
            return WafBreakdownBucketBatch(
                breakdown=breakdown,
                truncated=True,
                rows_returned=len(rows),
                query=aql,
                ariel_searches=1,
            )
        return WafBreakdownBucketBatch(
            breakdown=breakdown,
            buckets=fold_breakdown_buckets(
                rows,
                breakdown=breakdown,
                qradar_log_source_id=qradar_log_source_id,
                window_start=start,
                window_end=end,
                bucket_seconds=bucket_seconds,
                max_groups=limit,
                query=aql,
            ),
            rows_returned=len(rows),
            query=aql,
            ariel_searches=1,
        )

    async def probe_waf_properties(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        property_names: Sequence[str],
        max_values: int = 50,
    ) -> WafSourceProbe:
        """One total plus at most two bounded aggregates per property.

        Read-only, scoped to one log source, `START`/`STOP`-bounded, `LIMIT`-capped
        and at most one hour wide. Property names pass the same allowlist as
        collection; a name that fails it is reported rejected without a query.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        if (end - start).total_seconds() > WAF_PROBE_MAX_WINDOW_SECONDS:
            raise ValueError("WAF property probe window exceeds the one-hour bound")
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        scope = (
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms}"
        )
        bound = self._ariel_scope(start_ms, end_ms)
        limit = max(1, min(max_values, self._page_size))

        total_aql = f'SELECT SUM(eventcount) AS "total_events" FROM events {scope} LIMIT 1 {bound}'  # noqa: S608
        total: int | None = None
        total_error: str | None = None
        try:
            rows = await self._run_bounded_aggregate(total_aql, max_rows=1)
            total = (as_int(rows[0].get("total_events")) if rows else 0) or 0
        except (ProviderError, ValueError) as exc:
            total_error = f"{type(exc).__name__}: {exc}"[:400]

        probes = [
            await self._probe_property(name, scope=scope, bound=bound, limit=limit)
            for name in dict.fromkeys(property_names)
        ]
        return WafSourceProbe(
            qradar_log_source_id=qradar_log_source_id,
            window_start=start,
            window_end=end,
            total_events=total,
            total_error=total_error,
            total_query=total_aql,
            properties=probes,
        )

    async def _probe_property(
        self, name: str, *, scope: str, bound: str, limit: int
    ) -> WafPropertyProbe:
        try:
            prop = self._quote_property(name)
        except ValueError as exc:
            return WafPropertyProbe(property_name=name, rejected=True, error=str(exc)[:400])

        population_aql = (
            f'SELECT SUM(eventcount) AS "populated", UNIQUECOUNT({prop}) AS "distinct_values" '  # noqa: S608
            f"FROM events {scope} AND {prop} IS NOT NULL LIMIT 1 {bound}"
        )
        queries = [population_aql]
        try:
            rows = await self._run_bounded_aggregate(population_aql, max_rows=1)
        except ProviderQueryRejectedError as exc:
            return WafPropertyProbe(
                property_name=name, rejected=True, error=str(exc)[:400], queries=queries
            )
        except (ProviderError, ValueError) as exc:
            return WafPropertyProbe(
                property_name=name, error=f"{type(exc).__name__}: {exc}"[:400], queries=queries
            )
        populated = (as_int(rows[0].get("populated")) if rows else 0) or 0
        distinct = as_int(rows[0].get("distinct_values")) if rows else None

        values_aql = (
            f'SELECT {prop} AS "value", SUM(eventcount) AS "events" FROM events {scope} '  # noqa: S608
            f"AND {prop} IS NOT NULL GROUP BY {prop} ORDER BY SUM(eventcount) DESC "
            f"LIMIT {limit} {bound}"
        )
        queries.append(values_aql)
        try:
            value_rows = await self._run_bounded_aggregate(values_aql, max_rows=limit)
        except (ProviderError, ValueError) as exc:
            return WafPropertyProbe(
                property_name=name,
                error=f"{type(exc).__name__}: {exc}"[:400],
                populated_events=populated,
                distinct_values=distinct,
                queries=queries,
            )
        values: list[WafPropertyValue] = []
        for row in value_rows:
            value = as_text(row.get("value"), limit=1024)
            events = as_int(row.get("events"))
            if value is None or events is None or events < 0:
                continue
            values.append(WafPropertyValue(value=value, events=events))
        return WafPropertyProbe(
            property_name=name,
            populated_events=populated,
            distinct_values=distinct,
            values=values,
            values_truncated=len(value_rows) >= limit,
            queries=queries,
        )

    @staticmethod
    def _ips_population_condition(logical_field: str, expression: str, name: str) -> str:
        if logical_field in {"SOURCE_IP", "DESTINATION_IP"}:
            return (
                # QRadar rejects LIKE on an IP-typed field (T-065); INCIDR is accepted.
                f"{expression} IS NOT NULL AND NOT INCIDR('127.0.0.0/8', {expression}) "
                f"AND {expression} != '::1'"
            )
        if logical_field == "DESTINATION_PORT":
            return f"{expression} > 0"
        if logical_field == "PROTOCOL":
            return f"{expression} > 0 AND {expression} < 255"
        if logical_field == "SIGNATURE" and name == "qid":
            return f"{expression} > 0"
        return f"{expression} IS NOT NULL"

    def _ips_expression(self, reference: IpsFieldProbeRequest) -> str:
        if reference.reference_kind == "NORMALIZED":
            if reference.name not in IPS_NORMALIZED_FIELD_ALLOWLIST:
                raise ValueError("normalized field is not in the IPS allowlist")
            return f'"{reference.name}"'
        if reference.reference_kind == "CUSTOM_PROPERTY":
            return self._quote_property(reference.name)
        raise ValueError("unknown IPS field reference kind")

    def _ips_reference(self, reference: tuple[str, str]) -> tuple[str, str]:
        """A validated reference as (quoted expression, name for the population filter).

        The name is passed on only for a normalized field: the `qid > 0` filter is
        about the numeric QID, never about a custom property that happens to be
        called ``qid``.
        """
        kind, name = reference
        if kind == "NORMALIZED":
            if name not in IPS_NORMALIZED_FIELD_ALLOWLIST:
                raise ValueError("normalized field is not in the IPS allowlist")
            return f'"{name}"', name
        if kind == "CUSTOM_PROPERTY":
            return self._quote_property(name), ""
        raise ValueError("unknown IPS field reference kind")

    def _ips_keyed_query(
        self,
        dimension: IpsDimension,
        fields: dict[str, tuple[str, str]],
        *,
        source_ids: Sequence[int],
        start_ms: int,
        end_ms: int,
        bucket_ms: int,
        limit: int,
    ) -> str:
        """The §D.5 AQL of one dimension. Only validated ints and quoted names enter it."""
        volume = 'SUM(eventcount) AS "events", COUNT(*) AS "records"'
        key = f'starttime / {bucket_ms} AS "k"'
        group_key = f"starttime / {bucket_ms}"
        order = "SUM(eventcount)"
        conditions: list[str] = []
        select: list[str] = []
        group: list[str] = []

        def use(logical: str, alias: str, *, grouped: bool = True) -> str:
            expression, name = self._ips_reference(fields[logical])
            conditions.append(self._ips_population_condition(logical, expression, name))
            if grouped:
                select.append(f'{expression} AS "{alias}"')
                group.append(expression)
            return expression

        if dimension is IpsDimension.DESTINATION_PORT:
            use("DESTINATION_PORT", "dport")
            if "PROTOCOL" in fields:
                # Not filtered: protocol 255 (absent) stays a row, folded to `unknown`.
                expression, _ = self._ips_reference(fields["PROTOCOL"])
                select.append(f'{expression} AS "proto"')
                group.append(expression)
        elif dimension is IpsDimension.DESTINATION_IP:
            use("DESTINATION_IP", "dip")
        elif dimension is IpsDimension.SIGNATURE:
            is_qid = fields["SIGNATURE"] == ("NORMALIZED", "qid")
            expression = use("SIGNATURE", "qid" if is_qid else "sig")
            if is_qid:
                select.append(f'QIDNAME({expression}) AS "qname"')
        elif dimension is IpsDimension.SOURCE_IP:
            use("SOURCE_IP", "sip")
        elif dimension is IpsDimension.PORT_FANOUT:
            use("SOURCE_IP", "sip")
            # The port is filtered and counted, not grouped.
            port = use("DESTINATION_PORT", "dport", grouped=False)
            select.append(f'UNIQUECOUNT({port}) AS "distinct_ports"')
            order = f"UNIQUECOUNT({port})"
        elif dimension is IpsDimension.ACTION:
            use("ACTION", "act")
        elif dimension is not IpsDimension.TOTALS:
            raise ValueError("unknown IPS dimension")

        ids = ", ".join(str(int(i)) for i in source_ids)
        where = (
            f"WHERE logsourceid IN ({ids}) "
            f"AND starttime >= {start_ms} AND starttime < {end_ms}"
        )
        for condition in conditions:
            where += f" AND {condition}"
        columns = ['logsourceid AS "log_source_id"', *select, key, volume]
        group_by = ", ".join(["logsourceid", *group, group_key])
        return (
            f"SELECT {', '.join(columns)} FROM events {where} "  # noqa: S608
            f"GROUP BY {group_by} ORDER BY {order} DESC LIMIT {limit} "
            f"{self._ariel_scope(start_ms, end_ms)}"
        )

    async def get_ips_keyed(
        self,
        dimension: IpsDimension,
        *,
        qradar_log_source_ids: Sequence[int],
        fields: Mapping[str, tuple[str, str]],
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        max_rows: int,
    ) -> IpsKeyedResult:
        """One fleet-wide keyed IPS search (design D.5); see the base class.

        ``LIMIT cap + 1`` with ``cap = _keyed_row_cap(max_rows)``; volume is
        ``SUM(eventcount)``. Read-only: an Ariel search over ``events``.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        ids = validate_ips_sources(
            qradar_log_source_ids, max_sources=self._ips_max_sources_per_search
        )
        checked = validate_ips_fields(dimension, fields)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        cap = self._keyed_row_cap(max_rows)
        aql = self._ips_keyed_query(
            dimension,
            checked,
            source_ids=ids,
            start_ms=start_ms,
            end_ms=end_ms,
            bucket_ms=int(bucket_seconds) * 1000,
            limit=cap + 1,
        )
        raw = await self._run_bounded_aggregate(aql, max_rows=cap + 1)
        rows, truncated = parse_ips_keyed_rows(
            dimension,
            raw,
            source_ids=ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            max_rows=cap,
            qid_normalized=checked.get("SIGNATURE") == ("NORMALIZED", "qid"),
        )
        return IpsKeyedResult(
            dimension=dimension,
            qradar_log_source_ids=ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            rows=rows,
            truncated=truncated,
            query=aql,
        )

    @staticmethod
    def _catchall_query(
        type_ids: Sequence[int], *, start_ms: int, end_ms: int, bucket_ms: int, limit: int
    ) -> str:
        """The §C.8 AQL. Only validated ints enter it; the sender is selected, never filtered."""
        ids = ", ".join(str(int(i)) for i in type_ids)
        return (
            'SELECT logsourceid, logsourceidentifier AS "sender", '  # noqa: S608
            f'starttime / {bucket_ms} AS "k", '
            'SUM(eventcount) AS "events", COUNT(*) AS "records" '
            f"FROM events WHERE devicetype IN ({ids}) "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY logsourceid, logsourceidentifier, starttime / {bucket_ms} "
            f"ORDER BY SUM(eventcount) DESC LIMIT {limit}"
        )

    async def get_catchall_senders_keyed(
        self,
        *,
        devicetype_ids: Sequence[int],
        window_start: datetime,
        window_end: datetime,
        bucket_seconds: int,
        max_rows: int,
    ) -> CatchallKeyedResult:
        """One keyed search of the catch-all senders (design C.8); see the base class.

        ``LIMIT cap + 1`` with ``cap = _keyed_row_cap(max_rows)``; volume is
        ``SUM(eventcount)``. Read-only: an Ariel search over ``events``.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
        ids = validate_catchall_type_ids(devicetype_ids)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        cap = self._keyed_row_cap(max_rows)
        aql = (
            self._catchall_query(
                ids,
                start_ms=start_ms,
                end_ms=end_ms,
                bucket_ms=int(bucket_seconds) * 1000,
                limit=cap + 1,
            )
            + f" {self._ariel_scope(start_ms, end_ms)}"
        )
        raw = await self._run_bounded_aggregate(aql, max_rows=cap + 1)
        rows, truncated = parse_catchall_rows(
            raw,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            max_rows=cap,
        )
        return CatchallKeyedResult(
            device_type_ids=ids,
            window_start=start,
            window_end=end,
            bucket_seconds=bucket_seconds,
            rows=rows,
            truncated=truncated,
            query=aql,
        )

    #: The only address field a geo search may name; the direction picks it, never a caller.
    _GEO_FIELDS: ClassVar[dict[GeoDirection, str]] = {
        GeoDirection.SOURCE: "sourceip",
        GeoDirection.DESTINATION: "destinationip",
    }

    @classmethod
    def _geo_query(
        cls,
        direction: GeoDirection,
        *,
        start_ms: int,
        end_ms: int,
        bucket_ms: int,
        limit: int,
    ) -> str:
        """The D-10 AQL. It selects the lookup string and the bucket, never an address."""
        lookup = f"GEO::LOOKUP({cls._GEO_FIELDS[direction]}, 'country', 'iso_code')"
        return (
            f'SELECT {lookup} AS "geo", '  # noqa: S608
            f'starttime / {bucket_ms} AS "k", '
            'SUM(eventcount) AS "events" '
            f"FROM events WHERE starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY {lookup}, starttime / {bucket_ms} "
            f"ORDER BY SUM(eventcount) DESC LIMIT {limit}"
        )

    async def get_geo_keyed(
        self,
        start: datetime,
        end: datetime,
        *,
        direction: GeoDirection,
        bucket_seconds: int,
        max_rows: int,
    ) -> GeoKeyedResult:
        """One keyed search of events per country (D-10); see the base class.

        ``LIMIT cap + 1`` with ``cap = _keyed_row_cap(max_rows)``. Read-only: an Ariel
        search over ``events``.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        window_start, window_end = validate_bucketed_metric_window(start, end, bucket_seconds)
        checked = GeoDirection(direction)
        start_ms = int(window_start.timestamp() * 1000)
        end_ms = int(window_end.timestamp() * 1000)
        cap = self._keyed_row_cap(max_rows)
        aql = (
            self._geo_query(
                checked,
                start_ms=start_ms,
                end_ms=end_ms,
                bucket_ms=int(bucket_seconds) * 1000,
                limit=cap + 1,
            )
            + f" {self._ariel_scope(start_ms, end_ms)}"
        )
        raw = await self._run_bounded_aggregate(aql, max_rows=cap + 1)
        rows, truncated = parse_geo_rows(
            raw,
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            max_rows=cap,
        )
        return GeoKeyedResult(
            direction=checked,
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            rows=rows,
            truncated=truncated,
            query=aql,
        )

    async def probe_ips_fields(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        fields: Sequence[IpsFieldProbeRequest],
        max_action_values: int = 20,
    ) -> IpsSourceProbe:
        """At most ten bounded searches: total, seven populations and two extras."""
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        if (end - start).total_seconds() > IPS_PROBE_MAX_WINDOW_SECONDS:
            raise ValueError("IPS field probe window exceeds the one-hour bound")
        unique = list({field.logical_field: field for field in fields}.values())
        known_logical = {
            "SOURCE_IP",
            "DESTINATION_IP",
            "DESTINATION_PORT",
            "PROTOCOL",
            "SIGNATURE",
            "ACTION",
            "SEVERITY",
        }
        if len(unique) > len(known_logical) or any(
            field.logical_field not in known_logical for field in unique
        ):
            raise ValueError("IPS probe contains an unknown or duplicate logical field")
        action_limit = max(1, min(int(max_action_values), 20, self._page_size))
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        scope = (
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms}"
        )
        bound = self._ariel_scope(start_ms, end_ms)
        searches = 0

        async def run(aql: str, *, max_rows: int) -> list[dict]:
            nonlocal searches
            searches += 1
            return await self._run_bounded_aggregate(aql, max_rows=max_rows)

        total_aql = (
            f'SELECT SUM(eventcount) AS "total_events" FROM events {scope} '  # noqa: S608
            f"LIMIT 1 {bound}"
        )
        total: int | None = None
        total_error: str | None = None
        try:
            rows = await run(total_aql, max_rows=1)
            total = (as_int(rows[0].get("total_events")) if rows else 0) or 0
        except (ProviderError, ValueError) as exc:
            total_error = f"{type(exc).__name__}: {exc}"[:400]

        probes: list[IpsFieldProbe] = []
        truncated = False
        for field in unique:
            try:
                expression = self._ips_expression(field)
            except ValueError as exc:
                probes.append(
                    IpsFieldProbe(
                        logical_field=field.logical_field,
                        reference_kind=field.reference_kind,
                        name=field.name,
                        rejected=True,
                        error=str(exc)[:400],
                    )
                )
                continue
            condition = self._ips_population_condition(
                field.logical_field, expression, field.name
            )
            population_aql = (
                f'SELECT SUM(eventcount) AS "populated" FROM events {scope} '  # noqa: S608
                f"AND {condition} LIMIT 1 {bound}"
            )
            try:
                rows = await run(population_aql, max_rows=1)
            except ProviderQueryRejectedError as exc:
                probes.append(
                    IpsFieldProbe(
                        logical_field=field.logical_field,
                        reference_kind=field.reference_kind,
                        name=field.name,
                        rejected=True,
                        error=str(exc)[:400],
                    )
                )
                continue
            except (ProviderError, ValueError) as exc:
                probes.append(
                    IpsFieldProbe(
                        logical_field=field.logical_field,
                        reference_kind=field.reference_kind,
                        name=field.name,
                        error=f"{type(exc).__name__}: {exc}"[:400],
                    )
                )
                continue
            populated = (as_int(rows[0].get("populated")) if rows else 0) or 0
            generic_events: int | None = None
            values: list[IpsProbeValue] = []
            field_truncated = False
            try:
                if (
                    field.logical_field == "SIGNATURE"
                    and field.reference_kind == "NORMALIZED"
                    and field.name == "qid"
                ):
                    generic_aql = (
                        f'SELECT SUM(eventcount) AS "generic_events" FROM events {scope} '  # noqa: S608
                        f"AND {condition} AND QIDNAME({expression}) LIKE 'Unknown %' "
                        f"LIMIT 1 {bound}"
                    )
                    generic_rows = await run(generic_aql, max_rows=1)
                    generic_events = (
                        (as_int(generic_rows[0].get("generic_events")) if generic_rows else 0)
                        or 0
                    )
                if field.logical_field == "ACTION":
                    values_aql = (
                        f'SELECT {expression} AS "value", SUM(eventcount) AS "events" '  # noqa: S608
                        f"FROM events {scope} AND {condition} GROUP BY {expression} "
                        f"ORDER BY SUM(eventcount) DESC LIMIT {action_limit + 1} {bound}"
                    )
                    value_rows = await run(values_aql, max_rows=action_limit + 1)
                    field_truncated = len(value_rows) > action_limit
                    truncated = truncated or field_truncated
                    for row in value_rows[:action_limit]:
                        value = as_text(row.get("value"), limit=64)
                        events = as_int(row.get("events"))
                        if value is not None and events is not None and events >= 0:
                            values.append(IpsProbeValue(value=value, events=events))
            except (ProviderError, ValueError) as exc:
                probes.append(
                    IpsFieldProbe(
                        logical_field=field.logical_field,
                        reference_kind=field.reference_kind,
                        name=field.name,
                        populated_events=populated,
                        error=f"{type(exc).__name__}: {exc}"[:400],
                    )
                )
                continue
            probes.append(
                IpsFieldProbe(
                    logical_field=field.logical_field,
                    reference_kind=field.reference_kind,
                    name=field.name,
                    populated_events=populated,
                    generic_name_events=generic_events,
                    action_values=values,
                    values_truncated=field_truncated,
                )
            )
        return IpsSourceProbe(
            qradar_log_source_id=qradar_log_source_id,
            window_start=start,
            window_end=end,
            total_events=total,
            total_error=total_error,
            fields=probes,
            truncated=truncated,
            ariel_searches=searches,
        )

    async def observe_property_rejection(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        property_names: Sequence[str],
    ) -> list[WafRejectionObservation]:
        """One bounded search per property; the outcome is recorded, results are not fetched.

        Exists to verify an assumption rather than to rely on one: validation
        treats HTTP 422 as "the property does not exist". Whatever QRadar really
        does -- 422, another 4xx, a search that is created and then errors, or a
        search that completes -- is recorded here as observed.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        if (end - start).total_seconds() > WAF_PROBE_MAX_WINDOW_SECONDS:
            raise ValueError("rejection probe window exceeds the one-hour bound")
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        out: list[WafRejectionObservation] = []
        bound = self._ariel_scope(start_ms, end_ms)
        for name in dict.fromkeys(property_names):
            prop = self._quote_property(name)
            aql = (
                f'SELECT {prop} AS "v" FROM events '  # noqa: S608
                f"WHERE logsourceid = {int(qradar_log_source_id)} "
                f"AND starttime >= {start_ms} AND starttime < {end_ms} "
                f"LIMIT 1 {bound}"
            )
            try:
                handle = await self.create_ariel_search(aql)
            except ProviderUnavailableError as exc:
                out.append(WafRejectionObservation(
                    property_name=name, outcome="CREATE_FAILED",
                    create_http_status=exc.status_code, query=aql,
                ))
                continue
            except ProviderError as exc:
                status = exc.status_code
                outcome = (
                    "REJECTED_AT_CREATE" if status is not None and 400 <= status < 500
                    else "CREATE_FAILED"
                )
                out.append(WafRejectionObservation(
                    property_name=name, outcome=outcome, create_http_status=status, query=aql,
                ))
                continue
            status_dto: ArielSearchStatusDTO | None = None
            for _ in range(120):
                status_dto = await self.get_ariel_search_status(handle.search_id)
                if status_dto.is_terminal:
                    break
                await self._sleep(1.0)
            else:
                await self.cancel_ariel_search(handle.search_id)
                out.append(WafRejectionObservation(
                    property_name=name, outcome="TIMED_OUT", search_created=True,
                    search_status=status_dto.status if status_dto else None, query=aql,
                ))
                continue
            assert status_dto is not None
            out.append(WafRejectionObservation(
                property_name=name,
                outcome="SEARCH_COMPLETED" if status_dto.is_success else "SEARCH_FAILED",
                search_created=True,
                search_status=status_dto.status,
                record_count=status_dto.record_count,
                query=aql,
            ))
        return out

    async def _run_bounded_aggregate(self, aql: str, *, max_rows: int) -> list[dict]:
        """Create, poll and fetch one aggregate search under a hard time bound."""
        handle = await self.create_ariel_search(aql)
        status: ArielSearchStatusDTO | None = None
        for _ in range(120):
            status = await self.get_ariel_search_status(handle.search_id)
            if status.is_terminal:
                break
            await self._sleep(1.0)
        else:
            await self.cancel_ariel_search(handle.search_id)
            raise ProviderUnavailableError("Ariel explanation search timed out")

        if status is None or not status.is_success:
            raise ProviderError("Ariel explanation search did not complete")
        results = await self.get_ariel_search_results(handle.search_id, max_rows=max_rows)
        return list(results.rows)

    # ------------------------------------------------------------- rules
    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        """Every analytics rule, building blocks included.

        QRadar models building blocks as rules with `type == "BUILDINGBLOCK"`,
        so one call returns both and the DTO carries the distinction.
        """
        rows = await self._paginate("/analytics/rules", category=EndpointCategory.RULE_LIST)
        return [dto for dto in (self._to_rule(r) for r in rows) if dto is not None]

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        rows = await self._paginate(
            "/analytics/building_blocks", category=EndpointCategory.BUILDING_BLOCK_LIST
        )
        out: list[AnalyticsRuleDTO] = []
        for row in rows:
            dto = self._to_rule(row)
            if dto is not None:
                out.append(dto.model_copy(update={"is_building_block": True}))
        return out

    async def get_rule(self, qradar_id: int) -> AnalyticsRuleDTO | None:
        body = await self._request(
            "GET",
            f"/analytics/rules/{int(qradar_id)}",
            category=EndpointCategory.RULE_DETAIL,
            allow_404=True,
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar rule detail was not an object")
        return self._to_rule(body)

    # ---------------------------------------------------------- offenses
    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        """Offenses, optionally restricted to those changed since a watermark.

        `updated_since` drives incremental collection: QRadar's filter syntax
        does the narrowing server-side so we never page through the full offense
        history to find the handful that moved.
        """
        filters: list[str] = []
        if open_only:
            filters.append('status="OPEN"')
        if updated_since is not None:
            epoch_ms = int(updated_since.astimezone(UTC).timestamp() * 1000)
            filters.append(f"last_persisted_time>{epoch_ms}")

        params: dict[str, Any] = {}
        if filters:
            params["filter"] = " and ".join(filters)

        rows = await self._paginate(
            "/siem/offenses",
            category=EndpointCategory.OFFENSE_LIST,
            params=params or None,
            max_pages=max_pages,
        )
        return [dto for dto in (self._to_offense(r) for r in rows) if dto is not None]

    async def get_offense(self, qradar_id: int) -> OffenseDTO | None:
        body = await self._request(
            "GET",
            f"/siem/offenses/{int(qradar_id)}",
            category=EndpointCategory.OFFENSE_DETAIL,
            allow_404=True,
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar offense detail was not an object")
        return self._to_offense(body)

    async def list_offense_types(self) -> dict[int, str]:
        rows = await self._paginate(
            "/siem/offense_types", category=EndpointCategory.OFFENSE_TYPE_LIST
        )
        out: dict[int, str] = {}
        for row in rows:
            type_id = as_int(row.get("id"))
            name = row.get("name")
            if type_id is not None and isinstance(name, str):
                out[type_id] = name[:255]
        return out

    async def list_offense_closing_reasons(self) -> dict[int, str]:
        rows = await self._paginate(
            "/siem/offense_closing_reasons",
            category=EndpointCategory.OFFENSE_CLOSING_REASON_LIST,
        )
        out: dict[int, str] = {}
        for row in rows:
            reason_id = as_int(row.get("id"))
            text = row.get("text")
            if reason_id is not None and isinstance(text, str):
                out[reason_id] = text[:512]
        return out

    # ------------------------------------------------------ Ariel lifecycle
    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        """Start an Ariel search. AQL execution is REST-only, never MCP.

        Deliberately not retried. A create that timed out may still have been
        accepted; replaying it would strand a second search on the appliance
        with no handle to cancel it.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        body = await self._request(
            "POST",
            "/ariel/searches",
            category=EndpointCategory.ARIEL_CREATE,
            params={"query_expression": aql},
        )
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar did not return an Ariel search handle")
        search_id = body.get("search_id") or body.get("cursor_id")
        if not isinstance(search_id, str) or not search_id:
            raise MalformedUpstreamResponse("QRadar returned an Ariel handle without a search id")
        return ArielSearchHandleDTO(
            search_id=search_id,
            status=str(body.get("status") or "WAIT"),
            progress=as_int(body.get("progress")) or 0,
        )

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        body = await self._request(
            "GET",
            f"/ariel/searches/{search_id}",
            category=EndpointCategory.ARIEL_STATUS,
        )
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar Ariel status was not an object")
        errors = body.get("error_messages")
        return ArielSearchStatusDTO(
            search_id=search_id,
            status=str(body.get("status") or "ERROR"),
            progress=as_int(body.get("progress")) or 0,
            record_count=as_int(body.get("record_count")),
            # Upstream error text is untrusted; keep it out of the DTO and let
            # the caller report the status instead.
            error_messages=["QRadar reported the search failed"] if errors else [],
        )

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        body = await self._request(
            "GET",
            f"/ariel/searches/{search_id}/results",
            category=EndpointCategory.ARIEL_RESULTS,
            # One row past the cap: the server honours the range, so seeing the
            # extra row is the only way to tell a capped result from a whole one.
            headers={"Range": f"items=0-{max(0, max_rows)}"},
        )
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar Ariel results were not an object")
        # QRadar keys the row array by the queried database ("events"/"flows").
        rows: list[dict] = []
        for value in body.values():
            if isinstance(value, list):
                rows = [r for r in value if isinstance(r, dict)]
                break
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        columns = sorted({key for row in rows for key in row}) if rows else []
        return ArielSearchResultsDTO(
            search_id=search_id,
            columns=columns,
            rows=rows,
            total_count=len(rows),
            truncated=truncated,
        )

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.require(ProviderCapability.AQL_EXECUTION)
        # DELETE is idempotent, so a retry is safe and a 404 means "already gone".
        await self._request(
            "DELETE",
            f"/ariel/searches/{search_id}",
            category=EndpointCategory.ARIEL_CANCEL,
            allow_404=True,
        )

    # -------------------------------------------------------- normalization
    def _to_log_source(self, row: dict) -> LogSourceDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            # A source without an id or name cannot be keyed or displayed; drop
            # it rather than inventing a placeholder.
            logger.warning("dropping malformed log source row from QRadar")
            return None
        raw_type = row.get("type")
        type_info: dict = raw_type if isinstance(raw_type, dict) else {}
        identifiers = row.get("log_source_identifiers")
        first_identifier = identifiers[0] if isinstance(identifiers, list) and identifiers else {}
        identifier = (
            first_identifier.get("identifier") if isinstance(first_identifier, dict) else None
        )
        return LogSourceDTO(
            qradar_id=qradar_id,
            name=name.strip()[:255],
            description=(row.get("description") or None),
            type_id=as_int(row.get("type_id")) or as_int(type_info.get("id")),
            type_name=(type_info.get("name") if isinstance(type_info.get("name"), str) else None),
            protocol_type_id=as_int(row.get("protocol_type_id")),
            enabled=bool(row.get("enabled", True)),
            status=self._log_source_status(row),
            credibility=as_int(row.get("credibility")),
            target_event_collector_id=as_int(row.get("target_event_collector_id")),
            average_eps=None,
            last_event_time=parse_qradar_time(row.get("last_event_time")),
            qradar_created_at=parse_qradar_time(row.get("creation_date")),
            qradar_modified_at=parse_qradar_time(row.get("modified_date")),
            auto_discovered=(
                row.get("auto_discovered")
                if isinstance(row.get("auto_discovered"), bool)
                else None
            ),
            group_ids=as_int_list(row.get("group_ids")),
            identifier=sanitize_text(as_text(identifier, limit=255)),
            internal=row.get("internal") if isinstance(row.get("internal"), bool) else None,
        )

    @staticmethod
    def _log_source_status(row: dict) -> str | None:
        status = row.get("status")
        if isinstance(status, dict):
            value = status.get("status")
            return str(value)[:64] if value is not None else None
        return str(status)[:64] if status is not None else None

    def _to_rule(self, row: dict) -> AnalyticsRuleDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            logger.warning("dropping malformed rule row from QRadar")
            return None

        rule_type = row.get("type")
        rule_type_str = str(rule_type)[:64] if rule_type is not None else None
        responses = row.get("rule_responses")
        response_actions = as_str_list(responses, limit=32) if responses else []
        # QRadar's rule payload does not consistently expose a boolean for
        # offense creation, so it is derived from the response set and left
        # None when the payload gives us nothing to derive from.
        generates_offense: bool | None = None
        if responses is not None:
            generates_offense = any(
                "OFFENSE" in action.upper() for action in response_actions
            )

        return AnalyticsRuleDTO(
            qradar_id=qradar_id,
            name=name.strip()[:512],
            description=(row.get("description") or None),
            rule_type=rule_type_str,
            enabled=bool(row.get("enabled", True)),
            origin=(str(row.get("origin"))[:64] if row.get("origin") is not None else None),
            is_building_block=(rule_type_str or "").upper() == "BUILDINGBLOCK",
            owner=(str(row.get("owner"))[:255] if row.get("owner") is not None else None),
            average_capacity=(
                float(row["average_capacity"])
                if isinstance(row.get("average_capacity"), (int, float))
                and not isinstance(row.get("average_capacity"), bool)
                else None
            ),
            base_capacity=(
                float(row["base_capacity"])
                if isinstance(row.get("base_capacity"), (int, float))
                and not isinstance(row.get("base_capacity"), bool)
                else None
            ),
            base_host_id=as_int(row.get("base_host_id")),
            capacity_timestamp=parse_qradar_time(row.get("capacity_timestamp")),
            categories=as_str_list(row.get("categories"), limit=32),
            # MITRE mapping is SOC-owned; nothing is inferred from a rule
            # payload here. The collector records inferences separately.
            mitre_techniques=[],
            created_at=parse_qradar_time(row.get("creation_date")),
            modified_at=parse_qradar_time(row.get("modification_date")),
            generates_offense=generates_offense,
            response_actions=response_actions,
            building_block_ids=as_int_list(row.get("building_block_ids"), limit=64),
            log_source_type_ids=as_int_list(row.get("log_source_type_ids"), limit=64),
            last_triggered_at=parse_qradar_time(row.get("last_triggered_time")),
            event_contribution_count=as_int(row.get("event_count")),
            offense_contribution_count=as_int(row.get("offense_count")),
        )

    def _to_offense(self, row: dict) -> OffenseDTO | None:
        qradar_id = as_int(row.get("id"))
        if qradar_id is None:
            logger.warning("dropping offense row from QRadar with no id")
            return None

        # QRadar returns offense_type and closing_reason either as a bare id or
        # as an expanded object, depending on API version and `fields`.
        offense_type = row.get("offense_type")
        offense_type_id = as_int(offense_type)
        offense_type_name: str | None = None
        if isinstance(offense_type, dict):
            offense_type_id = as_int(offense_type.get("id"))
            offense_type_name = as_text(offense_type.get("name"), limit=255)

        closing_reason = row.get("closing_reason")
        closing_reason_id = as_int(row.get("closing_reason_id"))
        closing_reason_text: str | None = None
        if isinstance(closing_reason, dict):
            closing_reason_id = closing_reason_id or as_int(closing_reason.get("id"))
            closing_reason_text = as_text(closing_reason.get("text"), limit=512)
        elif isinstance(closing_reason, str):
            closing_reason_text = as_text(closing_reason, limit=512)

        return OffenseDTO(
            qradar_id=qradar_id,
            description=(row.get("description") or None),
            status=as_text(row.get("status"), limit=32),
            magnitude=as_int(row.get("magnitude")),
            severity=as_int(row.get("severity")),
            credibility=as_int(row.get("credibility")),
            relevance=as_int(row.get("relevance")),
            assigned_to=as_text(row.get("assigned_to"), limit=255),
            offense_type=offense_type_id,
            offense_type_name=offense_type_name,
            offense_source=as_text(row.get("offense_source"), limit=255),
            source_network=as_text(row.get("source_network"), limit=255),
            event_count=as_int(row.get("event_count")),
            flow_count=as_int(row.get("flow_count")),
            device_count=as_int(row.get("device_count")),
            source_count=as_int(row.get("source_count")),
            destination_count=as_int(row.get("local_destination_count")),
            start_time=parse_qradar_time(row.get("start_time")),
            last_updated_time=parse_qradar_time(
                row.get("last_persisted_time") or row.get("last_updated_time")
            ),
            close_time=parse_qradar_time(row.get("close_time")),
            closing_reason_id=closing_reason_id,
            closing_reason=closing_reason_text,
            categories=as_str_list(row.get("categories"), limit=32),
            source_addresses=as_str_list(row.get("source_address_ids")),
            local_destination_addresses=as_str_list(row.get("local_destination_address_ids")),
            usernames=as_str_list(row.get("usernames"), limit=32),
            log_source_ids=as_int_list(row.get("log_sources"), limit=64),
            rule_ids=as_int_list(row.get("rules"), limit=64),
        )
