"""Helpers for the integration tests that read a scope row's stored reason code (MSG-8a)."""

from __future__ import annotations

from typing import Any

from app.i18n.messages import MessageRef, render


def _ref(value: Any) -> Any:
    """A stored wire value as a `render` input: nested references and lists are rebuilt."""
    if isinstance(value, dict) and "code" in value:
        return MessageRef(value["code"], {k: _ref(v) for k, v in value["params"].items()})
    if isinstance(value, dict) and value.get("kind") == "list":
        return [_ref(item) for item in value["items"]]
    return value


def coded_text(stored: dict[str, Any] | None) -> str:
    """The English the stored reference renders to (fails if there is none)."""
    assert stored is not None, "the scope row has no reason_i18n"
    return render(_ref(stored))


def assert_coded(scope: Any, code: str | None = None) -> None:
    """The row's `reason_i18n` renders to exactly its stored `reason` (and names `code`)."""
    assert scope.reason_i18n is not None, f"uncoded reason: {scope.reason!r}"
    assert coded_text(scope.reason_i18n) == scope.reason, (scope.reason, scope.reason_i18n)
    if code is not None:
        assert scope.reason_i18n["code"] == code
