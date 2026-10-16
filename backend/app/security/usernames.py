"""Username shape shared by login, administration and the break-glass command."""

from __future__ import annotations


def normalize_username(raw: str) -> str | None:
    """Lower-case a username, or return None when the shape is refused.

    Control characters are refused before stripping, so a newline cannot be
    trimmed into a real name. Internal whitespace is refused. The stored form
    is lower-case and at most 255 characters, matching the column and the
    lower-case check constraint.
    """
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    name = raw.strip().lower()
    if not name or any(ch.isspace() for ch in name) or len(name) > 255:
        return None
    return name


USERNAME_SHAPE = (
    "username must be 1 to 255 characters without spaces or control characters"
)
