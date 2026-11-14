"""SEC-RO2 §3: the REST provider can write nothing to QRadar but its own Ariel searches.

Two pins. The static one reads `qradar_rest.py` and fails on any HTTP call that is not a GET,
the Ariel create or the Ariel cancel. The runtime one drives the Ariel lifecycle and the
access probes through a mock transport and checks the verbs on the wire.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import httpx
import pytest

import app.providers.qradar_rest as rest_module
from app.providers.access_catalog import ACCESS_CATALOG
from tests.unit.test_qradar_rest_provider import build_provider

SOURCE = Path(rest_module.__file__)
HTTP_ATTRS = {"request", "post", "put", "patch", "delete", "send", "stream", "get", "head",
              "options"}


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _fstring_head(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        head = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                head += part.value
            else:
                break
        return head
    return None


def test_static_every_request_call_is_a_get_or_one_of_the_two_ariel_writes() -> None:
    tree = ast.parse(SOURCE.read_text())
    allowed_writes = {("POST", "/ariel/searches"), ("DELETE", "/ariel/searches/")}
    seen_writes: set[tuple[str, str]] = set()
    enclosing: dict[ast.Call, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    enclosing[node] = fn.name
    for call in _calls(tree):
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        # self._request(method, path, ...)
        if func.attr == "_request":
            assert len(call.args) >= 2, f"line {call.lineno}: _request without literal args"
            method = call.args[0]
            assert isinstance(method, ast.Constant) and isinstance(method.value, str), (
                f"line {call.lineno}: the verb must be a literal"
            )
            if method.value == "GET":
                continue
            head = _fstring_head(call.args[1])
            assert (method.value, head) in allowed_writes, (
                f"line {call.lineno}: {method.value} {head} is a new write"
            )
            seen_writes.add((method.value, head))
        # any direct use of an HTTP client object
        elif func.attr in HTTP_ATTRS:
            receiver = ast.unparse(func.value)
            if "client" in receiver.lower():
                assert receiver == "self._client" and func.attr == "request", (
                    f"line {call.lineno}: direct {receiver}.{func.attr}"
                )
                first = call.args[0]
                if isinstance(first, ast.Name) and first.id == "method":
                    # The one retry loop in `_request`, whose verb the checked callers set.
                    assert enclosing[call] == "_request", f"line {call.lineno}"
                else:
                    assert isinstance(first, ast.Constant) and first.value == "GET", (
                        f"line {call.lineno}: direct client call that is not a literal GET"
                    )
    assert seen_writes == allowed_writes  # both writes still exist and are the only ones


def test_static_no_other_verb_string_appears_in_the_provider() -> None:
    text = SOURCE.read_text()
    for verb in ("PUT", "PATCH"):
        assert not re.search(rf"[\"']{verb}[\"']", text), f"{verb} appears in the provider"


def test_static_the_access_catalog_posts_only_to_the_probe_path() -> None:
    writes = [e for e in ACCESS_CATALOG if e.method.upper() != "GET"]
    assert {(e.method.upper(), e.path) for e in writes} == {("POST", "/ariel/searches")}


@pytest.mark.asyncio
async def test_runtime_only_the_expected_requests_leave_the_provider() -> None:
    wire: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        wire.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json={"search_id": "pin-1", "status": "WAIT"})
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={"events": []})
        if "/ariel/searches/" in request.url.path:
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json=[])

    provider, _ = build_provider(handler)
    handle = await provider.create_ariel_search("SELECT 1 FROM events")
    await provider.get_ariel_search_status(handle.search_id)
    await provider.get_ariel_search_results(handle.search_id, max_rows=5)
    await provider.cancel_ariel_search(handle.search_id)
    for path in ("/system/about", "/analytics/rules"):
        await provider.probe_access(path)
    await provider.list_rules()
    await provider.list_building_blocks()
    await provider.list_log_sources()
    await provider.list_log_source_types()
    await provider.list_log_source_groups()
    await provider.list_custom_properties()
    await provider.count_ariel_searches()
    await provider.list_managed_servers()
    await provider.list_deployment_hosts()
    await provider.list_offenses()
    writes = {(m, "/ariel/searches" if p.endswith("/ariel/searches")
              else "/ariel/searches/<id>") for m, p in wire if m != "GET"}
    assert writes == {("POST", "/ariel/searches"), ("DELETE", "/ariel/searches/<id>")}
    for method, path in wire:
        assert method in {"GET", "POST", "DELETE"}
        if method == "DELETE":
            assert path == "/api/ariel/searches/pin-1"
        if method == "POST":
            assert path == "/api/ariel/searches"
