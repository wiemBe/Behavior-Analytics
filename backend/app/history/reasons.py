"""Carry a registry reference beside the sentence it renders (MSG-8a).

The history core builds some sentences by joining clauses: a first-step window reason
plus the estimate's own reason, a finish verdict plus its source counts, and so on. The
legacy English joined them with ``"; "``; the code beside the text must express the same
join, so these helpers produce both together.

A clause without a code makes the whole joined sentence uncoded: a view must never show a
code that re-renders only part of what it was given. English stays byte for byte either way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.i18n.messages import MessageRef

ReasonPart = tuple[str, MessageRef | Mapping[str, object] | None]


def message_wire(
    message: MessageRef | Mapping[str, object] | None,
) -> dict[str, object] | None:
    """The JSONB/wire value of a message, accepting one that is already wire-shaped."""
    if message is None:
        return None
    if isinstance(message, MessageRef):
        return message.to_wire()
    return dict(message)


def semicolon_reason(parts: Sequence[ReasonPart]) -> tuple[str, dict[str, object] | None]:
    """Join clauses with ``"; "`` and return the wire reference beside the text."""
    kept = [(text, message) for text, message in parts if text]
    text = "; ".join(clause for clause, _ in kept)
    if not text:
        return "", None
    wires: list[dict[str, object]] = []
    for _, message in kept:
        wire = message_wire(message)
        if wire is None:
            return text, None
        wires.append(wire)
    if len(wires) == 1:
        return text, wires[0]
    return text, {
        "code": "history.reason.joined",
        "params": {"clauses": {"kind": "list", "items": wires}},
    }


def semicolon_ref(
    parts: Sequence[tuple[str, MessageRef | None]],
) -> tuple[str, MessageRef | None]:
    """Join clauses in memory, keeping one reference; an uncoded clause drops the code."""
    kept = [(text, message) for text, message in parts if text]
    text = "; ".join(clause for clause, _ in kept)
    if not text:
        return "", None
    messages = [message for _, message in kept]
    if any(message is None for message in messages):
        return text, None
    if len(messages) == 1:
        return text, messages[0]
    return text, MessageRef(
        "history.reason.joined",
        {"clauses": [message for message in messages if message is not None]},
    )
