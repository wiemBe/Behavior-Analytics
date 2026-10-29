"""Reading a stored ``{"code", "params"}`` message reference off a JSONB column.

A sentence the backend writes is stored as English text beside an ``<field>_i18n`` column
(MSG-0 decision 5). The API serves the pair as ``<field>_code`` / ``<field>_params``; a row
stored before codes, or a malformed value, reads as no code, never as a guess.
"""

from __future__ import annotations

from typing import Any


def stored_code(value: object) -> str | None:
    """The code of a stored message reference; None when absent or malformed."""
    from app.schemas.messages import stored_message_parts

    if not isinstance(value, dict):
        return None
    return stored_message_parts(value.get("code"), value.get("params"))[0]


def stored_params(value: object) -> dict[str, Any] | None:
    """The params of a stored message reference; None when absent or malformed."""
    from app.schemas.messages import stored_message_parts

    if not isinstance(value, dict):
        return None
    return stored_message_parts(value.get("code"), value.get("params"))[1]
