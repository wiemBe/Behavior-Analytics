"""QRadarMCPProvider — read-only inventory retrieval over the IBM qradar-mcp service.

Deliberately restricted, in this order of precedence:

  1. Capabilities exclude AQL_EXECUTION. Running Ariel searches is a POST surface
     that we keep off the MCP path entirely; the REST provider owns query
     execution. See docs/mcp-capability-matrix.md section 1.2.

  2. A hardcoded read-only allowlist (`ALLOWED_TOOLS`) is checked before every
     call, independent of what the MCP server advertises. Even if the mounted
     feature-toggle file were misconfigured to re-enable writes, this provider
     would still refuse to invoke a write tool. This is defence-in-depth layer 5
     from the capability matrix, and it is the layer that actually holds.

  3. Capability negotiation fails *closed*. If the server advertises a tool set
     that contradicts the allowlist — notably by exposing a known write tool —
     the provider marks itself degraded and refuses to dispatch rather than
     quietly continuing against a server that is not in the expected posture.
     `call_tool` negotiates automatically, once per provider instance, before
     its first request; a refusal is terminal for that instance.

A denied write tool never falls back to REST. Silently satisfying a blocked
request through another path would defeat the entire control, so denial is
terminal and audited.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.base import (
    ProviderAuthError,
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import (
    AnalyticsRuleDTO,
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceTypeDTO,
    OffenseDTO,
)
from app.providers.normalize import (
    as_int,
    as_int_list,
    as_str_list,
    as_text,
    parse_qradar_time,
)
from app.providers.qradar_rest import MalformedUpstreamResponse
from app.providers.telemetry import EndpointCategory, ErrorClass, observe
from app.security.sanitizer import sanitize_text

logger = logging.getLogger("app.providers.qradar_mcp")

PROVIDER_NAME = "mcp"

# Read-only GET tools only. Any tool not on this list cannot be invoked through
# this provider, full stop. Mirrors the "Primary/High" read rows of the matrix.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "get_system_info",
        "list_servers",
        "list_log_sources",
        "get_log_source",
        "list_log_source_types",
        "list_rules",
        "get_rule",
        "list_building_blocks",
        "list_offenses",
        "get_offense",
        "get_offense_notes",
        "list_offense_types",
        "list_offense_closing_reasons",
        "list_saved_searches",
        "get_saved_search",
        "list_users",
        "list_user_roles",
        "list_qid_records",
        "list_dsm_event_mappings",
    }
)

# The 25 mutating tools the pinned qradar-mcp commit exposes (17 POST, 8 DELETE),
# enumerated from docs/mcp-capability-matrix.md section 3.
#
# The allowlist above is what enforces policy — this set exists so capability
# negotiation can *detect* a server running with writes re-enabled, and so the
# test suite can assert every known write tool is refused by name. Adding a tool
# here does not block it; removing one does not permit it.
KNOWN_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # --- POST (17) ---
        "create_ariel_search",
        "validate_aql",
        "update_offense",
        "add_offense_note",
        "create_reference_set",
        "update_reference_set",
        "add_to_reference_set",
        "create_reference_map",
        "add_to_reference_map",
        "create_reference_table",
        "add_to_reference_table",
        "create_qid_record",
        "update_qid_record",
        "create_dsm_event_mapping",
        "update_dsm_event_mapping",
        "dns_lookup",
        "whois_lookup",
        # --- DELETE (8) ---
        "delete_ariel_search",
        "delete_saved_search",
        "delete_reference_set",
        "remove_from_reference_set",
        "delete_reference_map",
        "remove_from_reference_map",
        "delete_reference_table",
        "remove_from_reference_table",
    }
)

#: Ariel tools must never be reachable over MCP, whatever the allowlist says.
#: Belt and braces against a future edit widening the allowlist by accident.
FORBIDDEN_TOOL_PREFIXES: tuple[str, ...] = ("create_ariel", "delete_ariel", "validate_aql")

#: The server speaks MCP Streamable HTTP: `initialize`, then `notifications/initialized`,
#: then requests that carry the `Mcp-Session-Id` the server issued. It refuses a client
#: whose `Accept` lacks either media type, and answers requests in `text/event-stream`.
#: Measured against the pinned image (docs/mcp-capability-matrix.md section 6).
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26")
_ACCEPT = "application/json, text/event-stream"

#: `tools/list` pages followed before capability discovery gives up. An unseen page
#: could hold a write tool, so running out of pages is a refusal, not a pass.
MAX_TOOL_LIST_PAGES = 20

#: Responses larger than this are refused unread. A hostile or broken MCP server
#: must not be able to exhaust worker memory.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class MCPToolNotAllowedError(ProviderError):
    """A code path attempted an MCP tool outside the read-only allowlist."""


class MCPCapabilityError(ProviderError):
    """Capability discovery returned a posture we refuse to operate against."""


class MCPResponseTooLarge(ProviderError):
    """The MCP server returned more data than we will read."""


class _SessionExpired(ProviderUnavailableError):
    """The server no longer knows our session id (HTTP 404). Handled inside the provider."""

    audit_label = "SessionExpired"

    def __init__(self) -> None:
        super().__init__("MCP session expired")


class QRadarMCPProvider(QRadarProvider):
    # Note: no AQL_EXECUTION. This is the whole point of the split.
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        sec_token: str | None = None,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
        audit_sink: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # The pinned server authenticates every request, `initialize` included, against
        # QRadar with the `SEC` header it receives (docs/mcp-capability-matrix.md section
        # 6). Held privately and sent per request, never on the client's default headers
        # and never in a log record, an error message or a repr.
        self._sec_token = sec_token or None
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        # Callable invoked with one audit record per tool call. The Celery/API
        # callers wire this to app.repositories.audit.record_audit; left None it
        # degrades to logging only, so the provider stays usable in unit tests.
        self._audit_sink = audit_sink
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Accept": _ACCEPT},
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )
        self._negotiated: frozenset[str] | None = None
        # Set when negotiation found a posture we refuse; terminal for this instance.
        self._refusal: str | None = None
        self._session_id: str | None = None
        self._protocol_version = PROTOCOL_VERSION
        self._initialized = False
        self._ready_lock = asyncio.Lock()
        self._request_id = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- guards
    def _guard(self, tool_name: str) -> None:
        """Refuse anything not explicitly allowlisted. Fails closed.

        Checked before argument validation and before any network I/O, so a
        blocked tool never produces a request at all.
        """
        if any(tool_name.startswith(prefix) for prefix in FORBIDDEN_TOOL_PREFIXES):
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} is permanently blocked: "
                "Ariel/AQL execution is REST-only by policy"
            )
        if tool_name not in ALLOWED_TOOLS:
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} is not on the read-only allowlist"
            )

    @staticmethod
    def _validate_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize tool arguments before they leave the process.

        Only scalars are permitted. Nested structures are the shape an injected
        instruction would take, and no read tool we call needs one.
        """
        clean: dict[str, Any] = {}
        for key, value in arguments.items():
            if not isinstance(key, str) or not key.isidentifier():
                raise ValueError(f"invalid MCP argument name for {tool_name}: {key!r}")
            if isinstance(value, (bool, int)):
                clean[key] = value
            elif isinstance(value, str):
                if len(value) > 512:
                    raise ValueError(f"MCP argument {key!r} exceeds the length limit")
                clean[key] = value
            elif value is None:
                continue
            else:
                raise ValueError(
                    f"MCP argument {key!r} must be a scalar; got {type(value).__name__}"
                )
        return clean

    # ------------------------------------------------------- session handshake
    @staticmethod
    def _fail(exc_type: type[ProviderError], message: str, label: str) -> ProviderError:
        """Build a provider error that carries the label the audit record uses."""
        exc = exc_type(message)
        exc.audit_label = label  # type: ignore[attr-defined]
        return exc

    def _request_headers(self, *, with_session: bool) -> dict[str, str]:
        # The Streamable HTTP transport answers 406 unless the client accepts both
        # media types, whichever one it then chooses to answer with.
        headers = {"Accept": _ACCEPT, "Content-Type": "application/json"}
        if self._sec_token is not None:
            headers["SEC"] = self._sec_token
        if with_session:
            headers["MCP-Protocol-Version"] = self._protocol_version
            if self._session_id is not None:
                headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(
        self, payload: dict[str, Any], headers: dict[str, str], outcome: dict
    ) -> tuple[int, httpx.Headers, bytes]:
        """POST to the MCP endpoint, reading at most `max_response_bytes` of the body."""
        limit = self._max_response_bytes
        too_large = MCPResponseTooLarge(f"MCP response exceeded the {limit} byte limit")
        async with self._client.stream(
            "POST", "/mcp", json=payload, headers=headers
        ) as response:
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                outcome["error_class"] = ErrorClass.MALFORMED
                raise too_large
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer += chunk
                if len(buffer) > limit:
                    outcome["error_class"] = ErrorClass.MALFORMED
                    raise too_large
            return response.status_code, response.headers, bytes(buffer)

    async def _exchange(
        self,
        payload: dict[str, Any],
        *,
        what: str,
        with_session: bool = True,
        expect_body: bool = True,
    ) -> tuple[Any, httpx.Headers]:
        """One request to the MCP endpoint, mapped onto the provider's error types.

        Raises `_SessionExpired` (internal) for a 404 on a request that carried a
        session id: the Streamable HTTP transport answers "Session not found" that way.
        """
        with observe(PROVIDER_NAME, EndpointCategory.MCP_TOOL) as outcome:
            headers = self._request_headers(with_session=with_session)
            try:
                status, resp_headers, raw = await self._post(payload, headers, outcome)
            except httpx.TimeoutException as exc:
                outcome["error_class"] = ErrorClass.TIMEOUT
                raise self._fail(
                    ProviderUnavailableError, f"{what} timed out", "Timeout"
                ) from exc
            except httpx.TransportError as exc:
                outcome["error_class"] = ErrorClass.NETWORK
                raise self._fail(
                    ProviderUnavailableError, "Could not reach the MCP service", "Transport"
                ) from exc

            if status in (401, 403):
                outcome["error_class"] = ErrorClass.AUTH
                raise self._fail(ProviderAuthError, "MCP service rejected the request", "Auth")
            if status == 404 and with_session and self._session_id is not None:
                outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
                raise _SessionExpired()
            if status >= 400:
                outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
                raise self._fail(
                    ProviderUnavailableError, f"{what} failed upstream", f"HTTP{status}"
                )
            if not expect_body:
                return None, resp_headers
            body = self._decode(
                raw, resp_headers.get("content-type", ""), payload.get("id"), outcome
            )
        return body, resp_headers

    async def _initialize(self) -> None:
        """`initialize` then `notifications/initialized`; keeps the session id."""
        self._session_id = None
        self._protocol_version = PROTOCOL_VERSION
        self._request_id += 1
        body, headers = await self._exchange(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "qradar-dash", "version": "0"},
                },
            },
            what="MCP session initialisation",
            with_session=False,
        )
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict) or (isinstance(body, dict) and body.get("error")):
            raise self._fail(
                ProviderUnavailableError, "MCP initialize was not answered", "InitializeFailed"
            )
        version = result.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise self._fail(
                ProviderUnavailableError,
                "MCP server speaks an unsupported protocol version",
                "ProtocolVersion",
            )
        self._protocol_version = str(version)
        self._session_id = headers.get("mcp-session-id")
        await self._exchange(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            what="MCP session initialisation",
            expect_body=False,
        )

    # --------------------------------------------------- capability handshake
    async def negotiate_capabilities(self) -> frozenset[str]:
        """Discover the server's tool set with `tools/list` and refuse an unexpected posture.

        Fails closed on two conditions:
          * the server advertises a known write tool (writes are enabled), or
          * it advertises none of our allowlisted tools (wrong server/version).

        A refusal is terminal for this instance: this and every later `call_tool`
        raise without network I/O. A transport or auth failure is not a refusal;
        the next attempt tries again. `call_tool` runs this automatically before
        the first tool call.
        """
        async with self._ready_lock:
            return await self._negotiate_locked()

    async def _negotiate_locked(self) -> frozenset[str]:
        if self._refusal is not None:
            raise MCPCapabilityError(self._refusal)
        for attempt in (1, 2):
            if not self._initialized:
                await self._initialize()
                self._initialized = True
            try:
                advertised = await self._list_tools()
                break
            except _SessionExpired:
                # The session went away between `initialize` and `tools/list`.
                self._initialized = False
                if attempt == 2:
                    raise
        try:
            self._check_posture(advertised)
        except MCPCapabilityError as exc:
            self._refusal = str(exc)
            self._negotiated = None
            raise
        self._negotiated = frozenset(advertised & ALLOWED_TOOLS)
        return self._negotiated

    async def _list_tools(self) -> set[str]:
        names: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_TOOL_LIST_PAGES):
            self._request_id += 1
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "tools/list",
            }
            if cursor is not None:
                payload["params"] = {"cursor": cursor}
            body, _ = await self._exchange(payload, what="MCP capability discovery")
            names |= self._extract_tool_names(self._unwrap_list(body))
            cursor = body["result"].get("nextCursor") if isinstance(body, dict) else None
            if not isinstance(cursor, str) or not cursor:
                return names
        # An unseen page could hold a write tool; do not guess.
        raise MCPCapabilityError("MCP capability discovery did not finish within its page limit")

    @staticmethod
    def _unwrap_list(body: Any) -> Any:
        if not isinstance(body, dict):
            raise MCPCapabilityError("MCP capability discovery returned an unexpected shape")
        if body.get("error") is not None:
            raise ProviderError("MCP capability discovery reported an error")
        result = body.get("result")
        if not isinstance(result, dict):
            raise MCPCapabilityError("MCP capability discovery returned an unexpected shape")
        return result

    @staticmethod
    def _check_posture(advertised: set[str]) -> None:
        exposed_writes = advertised & KNOWN_WRITE_TOOLS
        if exposed_writes:
            # Do not proceed. A server advertising write tools is not in the
            # posture this deployment was reviewed against, and continuing would
            # mean trusting the allowlist as the only remaining control.
            raise MCPCapabilityError(
                "MCP service advertises write tools; refusing to operate. "
                f"Offending tools: {', '.join(sorted(exposed_writes))}"
            )
        if not (advertised & ALLOWED_TOOLS):
            raise MCPCapabilityError(
                "MCP service advertises no allowlisted read tools; capability discovery "
                "is inconsistent with the pinned qradar-mcp commit"
            )

    @staticmethod
    def _extract_tool_names(body: Any) -> set[str]:
        if isinstance(body, dict):
            body = body.get("tools", [])
        if not isinstance(body, list):
            raise MCPCapabilityError("MCP capability discovery returned an unexpected shape")
        names: set[str] = set()
        for item in body:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
        return names

    async def _ensure_ready(self) -> None:
        """Handshake and negotiate once per instance, before the first tool call."""
        async with self._ready_lock:
            if self._refusal is not None:
                raise MCPCapabilityError(self._refusal)
            if self._initialized and self._negotiated is not None:
                return
            await self._negotiate_locked()

    async def _reinitialise(self) -> None:
        """A session the server no longer knows: start over, negotiating again.

        The server may have been restarted with other toggles, so the posture is
        re-checked rather than assumed.
        """
        async with self._ready_lock:
            self._initialized = False
            self._session_id = None
            self._negotiated = None
            await self._negotiate_locked()

    # ----------------------------------------------------------- invocation
    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None, *, caller: str = "system"
    ) -> Any:
        """Invoke one allowlisted MCP tool.

        Every call is audited with the tool name, duration, outcome and caller.
        Arguments are recorded only as key names, and the response body is never
        audited: both can carry QRadar data we have no reason to duplicate into
        the audit log.

        Order: allowlist guard, argument validation, capability negotiation (once
        per instance), then the request. A blocked tool never produces a request.
        """
        args = dict(arguments or {})
        started = datetime.now(UTC)

        try:
            self._guard(tool_name)
            clean_args = self._validate_arguments(tool_name, args)
        except (MCPToolNotAllowedError, ValueError) as exc:
            await self._audit(
                tool_name,
                caller=caller,
                outcome="DENIED",
                started=started,
                arg_keys=sorted(args),
                error=type(exc).__name__,
            )
            # Deliberately no REST fallback. A denied tool is a policy decision,
            # not a transport failure.
            raise

        arg_keys = sorted(clean_args)

        async def audit_failure(exc: ProviderError) -> None:
            label = getattr(exc, "audit_label", None) or type(exc).__name__
            await self._audit(
                tool_name, caller=caller,
                outcome="TIMEOUT" if label == "Timeout" else "FAILURE",
                started=started, arg_keys=arg_keys, error=label,
            )

        async def prepare(action: Callable[[], Awaitable[None]]) -> None:
            try:
                await action()
            except MCPCapabilityError:
                # Terminal: no request, no fallback, and the refusal is on record.
                await self._audit(
                    tool_name, caller=caller, outcome="DENIED", started=started,
                    arg_keys=arg_keys, error="CapabilityRefused",
                )
                raise
            except ProviderError as exc:
                await audit_failure(exc)
                raise

        await prepare(self._ensure_ready)

        if self._negotiated is not None and tool_name not in self._negotiated:
            await self._audit(
                tool_name,
                caller=caller,
                outcome="DENIED",
                started=started,
                arg_keys=sorted(args),
                error="NotNegotiated",
            )
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} was not advertised during capability negotiation"
            )

        body: Any = None
        for attempt in (1, 2):
            self._request_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": clean_args},
            }
            try:
                body, _ = await self._exchange(payload, what=f"MCP tool {tool_name}")
                break
            except _SessionExpired:
                if attempt == 2:
                    gone = self._fail(
                        ProviderUnavailableError,
                        "MCP session expired and could not be re-established",
                        "SessionExpired",
                    )
                    await audit_failure(gone)
                    raise gone from None
                await prepare(self._reinitialise)
                if tool_name not in (self._negotiated or frozenset()):
                    await self._audit(
                        tool_name, caller=caller, outcome="DENIED", started=started,
                        arg_keys=sorted(args), error="NotNegotiated",
                    )
                    raise MCPToolNotAllowedError(
                        f"MCP tool {tool_name!r} was not advertised during capability negotiation"
                    ) from None
            except ProviderError as exc:
                # Timeouts, transport and auth failures, upstream errors, oversized
                # and unparseable bodies: each is a completed call attempt, and
                # leaving one unaudited would hide the most common tool-side failures.
                await audit_failure(exc)
                raise

        try:
            result = self._unwrap(tool_name, body)
        except ProviderError as exc:
            # A JSON-RPC `error` member, or an envelope we cannot interpret.
            # This is how a tool reports invalid arguments or an internal
            # fault, so it is the failure an operator most needs recorded.
            await self._audit(
                tool_name, caller=caller, outcome="FAILURE", started=started,
                arg_keys=arg_keys, error=type(exc).__name__,
            )
            raise

        await self._audit(
            tool_name, caller=caller, outcome="SUCCESS", started=started,
            arg_keys=arg_keys, error=None,
        )
        return result

    def _decode(self, raw: bytes, content_type: str, want_id: Any, outcome: dict) -> Any:
        """Parse a JSON body, or pick the JSON-RPC response out of an event stream."""
        if len(raw) > self._max_response_bytes:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MCPResponseTooLarge(
                f"MCP response exceeded the {self._max_response_bytes} byte limit"
            )
        if not raw:
            return None
        if content_type.split(";", 1)[0].strip().lower() == "text/event-stream":
            return self._decode_event_stream(raw, want_id, outcome)
        try:
            return json.loads(raw)
        except ValueError as exc:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MalformedUpstreamResponse("MCP returned a body that was not valid JSON") from exc

    @staticmethod
    def _decode_event_stream(raw: bytes, want_id: Any, outcome: dict) -> Any:
        """The JSON-RPC response for `want_id` from a `text/event-stream` body.

        Events are separated by a blank line; an event's `data:` lines are joined
        with a newline. Other fields, comments and messages for other ids (server
        notifications) are skipped.
        """
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MalformedUpstreamResponse("MCP event stream was not valid UTF-8") from exc
        events: list[list[str]] = [[]]
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line == "":
                events.append([])
            elif line.startswith("data:"):
                data = line[5:]
                events[-1].append(data[1:] if data.startswith(" ") else data)
        for data_lines in events:
            if not data_lines:
                continue
            try:
                message = json.loads("\n".join(data_lines))
            except ValueError as exc:
                outcome["error_class"] = ErrorClass.MALFORMED
                raise MalformedUpstreamResponse(
                    "MCP event stream carried data that was not valid JSON"
                ) from exc
            if (
                isinstance(message, dict)
                and message.get("id") == want_id
                and ("result" in message or "error" in message)
            ):
                return message
        outcome["error_class"] = ErrorClass.MALFORMED
        raise MalformedUpstreamResponse("MCP event stream held no response to the request")

    @staticmethod
    def _unwrap(tool_name: str, body: Any) -> Any:
        """Normalize a JSON-RPC envelope into the tool's payload."""
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse(f"MCP tool {tool_name} returned a non-object envelope")
        if "error" in body and body["error"] is not None:
            # Upstream error text is untrusted; report the tool, not the message.
            raise ProviderError(f"MCP tool {tool_name} reported an error")

        result = body.get("result", body)
        if isinstance(result, dict) and result.get("isError") is True:
            # A tool failure arrives as HTTP 200 with the error prose in the content
            # blocks. That text is untrusted, so report the tool, not the message.
            raise ProviderError(f"MCP tool {tool_name} reported an error")
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            # MCP content blocks: concatenate the text parts and parse as JSON.
            texts: list[str] = [
                block["text"]
                for block in result["content"]
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if texts:
                try:
                    return json.loads("".join(texts))
                except ValueError as exc:
                    raise MalformedUpstreamResponse(
                        f"MCP tool {tool_name} returned unparseable content"
                    ) from exc
        return result

    async def _audit(
        self,
        tool_name: str,
        *,
        caller: str,
        outcome: str,
        started: datetime,
        arg_keys: list[str],
        error: str | None,
    ) -> None:
        duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        record = {
            "action": "mcp.tool_call",
            "actor": caller,
            "object_type": "mcp_tool",
            "object_id": tool_name,
            "outcome": outcome,
            "detail": {
                "tool": tool_name,
                "duration_ms": duration_ms,
                # Names only — values may contain QRadar identifiers.
                "argument_keys": arg_keys,
                "error": error,
                "provider": PROVIDER_NAME,
            },
        }
        detail: dict = dict(record["detail"])  # type: ignore[arg-type]
        logger.info("mcp tool call", extra={**detail, "outcome": outcome})
        if self._audit_sink is not None:
            await self._audit_sink(record)

    # ------------------------------------------------------------- read API
    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        if isinstance(payload, dict):
            for key in ("items", "results", "data"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    async def get_instance_info(self) -> InstanceInfoDTO:
        body = await self.call_tool("get_system_info")
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("get_system_info did not return an object")
        version = body.get("external_version") or body.get("version")
        return InstanceInfoDTO(
            version=str(version) if version is not None else None,
            build=str(body.get("build_version")) if body.get("build_version") else None,
            reachable=True,
            raw_about={
                k: v
                for k, v in body.items()
                if k in {"release_name", "external_version", "build_version"}
            },
        )

    async def list_log_sources(self) -> list[LogSourceDTO]:
        rows = self._rows(await self.call_tool("list_log_sources"))
        return [dto for dto in (self._to_log_source(r) for r in rows) if dto is not None]

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        body = await self.call_tool("get_log_source", {"log_source_id": int(qradar_id)})
        return self._to_log_source(body) if isinstance(body, dict) else None

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        rows = self._rows(await self.call_tool("list_log_source_types"))
        out: list[LogSourceTypeDTO] = []
        for row in rows:
            type_id = as_int(row.get("id"))
            name = row.get("name")
            if type_id is not None and isinstance(name, str):
                out.append(
                    LogSourceTypeDTO(
                        type_id=type_id,
                        name=name[:255],
                        internal=row.get("internal") is True,
                        custom=row.get("custom") is True,
                    )
                )
        return out

    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        rows = self._rows(await self.call_tool("list_rules"))
        return [dto for dto in (self._to_rule(r) for r in rows) if dto is not None]

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        rows = self._rows(await self.call_tool("list_building_blocks"))
        out: list[AnalyticsRuleDTO] = []
        for row in rows:
            dto = self._to_rule(row)
            if dto is not None:
                out.append(dto.model_copy(update={"is_building_block": True}))
        return out

    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        args: dict[str, Any] = {}
        if open_only:
            args["status"] = "OPEN"
        rows = self._rows(await self.call_tool("list_offenses", args))
        out = [dto for dto in (self._to_offense(r) for r in rows) if dto is not None]
        if updated_since is not None:
            # The MCP read tool exposes no watermark filter, so honour the
            # contract locally rather than returning rows that predate it.
            cutoff = updated_since.astimezone(UTC)
            out = [
                o for o in out
                if o.last_updated_time is not None and o.last_updated_time > cutoff
            ]
        return out

    # -------------------------------------------------------- normalization
    # The MCP service proxies QRadar's own JSON, so normalization matches the
    # REST provider's. Shared helpers keep the two from drifting apart.
    @staticmethod
    def _to_log_source(row: dict) -> LogSourceDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            logger.warning("dropping malformed log source row from MCP")
            return None
        raw_type = row.get("type")
        type_info: dict = raw_type if isinstance(raw_type, dict) else {}
        status = row.get("status")
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
            status=(
                str(status.get("status"))[:64]
                if isinstance(status, dict) and status.get("status") is not None
                else (str(status)[:64] if status is not None else None)
            ),
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
    def _to_rule(row: dict) -> AnalyticsRuleDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            logger.warning("dropping malformed rule row from MCP")
            return None
        rule_type = row.get("type")
        rule_type_str = str(rule_type)[:64] if rule_type is not None else None
        return AnalyticsRuleDTO(
            qradar_id=qradar_id,
            name=name.strip()[:512],
            description=(row.get("description") or None),
            rule_type=rule_type_str,
            enabled=bool(row.get("enabled", True)),
            origin=(str(row.get("origin"))[:64] if row.get("origin") is not None else None),
            is_building_block=(rule_type_str or "").upper() == "BUILDINGBLOCK",
            owner=(str(row.get("owner"))[:255] if row.get("owner") is not None else None),
            categories=as_str_list(row.get("categories"), limit=32),
            mitre_techniques=[],
            created_at=parse_qradar_time(row.get("creation_date")),
            modified_at=parse_qradar_time(row.get("modification_date")),
            building_block_ids=as_int_list(row.get("building_block_ids"), limit=64),
            log_source_type_ids=as_int_list(row.get("log_source_type_ids"), limit=64),
            last_triggered_at=parse_qradar_time(row.get("last_triggered_time")),
        )

    @staticmethod
    def _to_offense(row: dict) -> OffenseDTO | None:
        qradar_id = as_int(row.get("id"))
        if qradar_id is None:
            logger.warning("dropping offense row from MCP with no id")
            return None
        offense_type = row.get("offense_type")
        offense_type_id = as_int(offense_type)
        offense_type_name = None
        if isinstance(offense_type, dict):
            offense_type_id = as_int(offense_type.get("id"))
            raw = offense_type.get("name")
            offense_type_name = str(raw)[:255] if isinstance(raw, str) else None
        assigned = row.get("assigned_to")
        return OffenseDTO(
            qradar_id=qradar_id,
            description=(row.get("description") or None),
            status=(str(row.get("status"))[:32] if row.get("status") is not None else None),
            magnitude=as_int(row.get("magnitude")),
            severity=as_int(row.get("severity")),
            credibility=as_int(row.get("credibility")),
            relevance=as_int(row.get("relevance")),
            assigned_to=(
                assigned.strip()[:255]
                if isinstance(assigned, str) and assigned.strip()
                else None
            ),
            offense_type=offense_type_id,
            offense_type_name=offense_type_name,
            offense_source=(
                str(row.get("offense_source"))[:255]
                if row.get("offense_source") is not None
                else None
            ),
            source_network=(
                str(row.get("source_network"))[:255]
                if row.get("source_network") is not None
                else None
            ),
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
            closing_reason_id=as_int(row.get("closing_reason_id")),
            categories=as_str_list(row.get("categories"), limit=32),
            source_addresses=as_str_list(row.get("source_address_ids")),
            local_destination_addresses=as_str_list(row.get("local_destination_address_ids")),
            usernames=as_str_list(row.get("usernames"), limit=32),
            log_source_ids=as_int_list(row.get("log_sources"), limit=64),
            rule_ids=as_int_list(row.get("rules"), limit=64),
        )
