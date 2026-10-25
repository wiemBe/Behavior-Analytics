"""Unit tests for app.api.request_meta — pure address / agent helpers."""

from __future__ import annotations

import ast
from pathlib import Path

from starlette.requests import Request

from app.api.request_meta import CLIENT_IP_MAX, USER_AGENT_MAX, client_ip, user_agent


def _request(*, client=None, headers=None):
    headers = headers or []
    scope = {"type": "http"}
    if client is not None:
        scope["client"] = client
    scope["headers"] = headers
    return Request(scope)


def test_client_ip_none_when_no_client():
    req = _request()
    assert client_ip(req) is None


def test_client_ip_none_when_host_empty():
    req = _request(client=("", 1234))
    assert client_ip(req) is None


def test_client_ip_truncates_at_64():
    host = "a" * 65
    req = _request(client=(host, 1234))
    assert client_ip(req) == "a" * CLIENT_IP_MAX


def test_client_ip_normal():
    req = _request(client=("192.0.2.1", 1234))
    assert client_ip(req) == "192.0.2.1"


def test_user_agent_none_when_absent():
    req = _request()
    assert user_agent(req) is None


def test_user_agent_none_when_empty():
    req = _request(headers=[(b"user-agent", b"")])
    assert user_agent(req) is None


def test_user_agent_truncates_at_512():
    value = "x" * 513
    req = _request(headers=[(b"user-agent", value.encode())])
    assert user_agent(req) == "x" * USER_AGENT_MAX


def test_user_agent_normal():
    req = _request(headers=[(b"user-agent", b"Mozilla/5.0")])
    assert user_agent(req) == "Mozilla/5.0"


def test_guard_no_route_reads_request_client():
    routes_dir = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"
    for file_path in sorted(routes_dir.glob("*.py")):
        source = file_path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "client"
                and isinstance(node.value, ast.Name)
                and node.value.id == "request"
            ):
                raise AssertionError(
                    f"{file_path.name}:{node.lineno} reads request.client; "
                    f"use app.api.request_meta.client_ip"
                )
