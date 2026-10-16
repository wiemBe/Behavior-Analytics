"""Escape a username before it is placed in a DN, a UPN, or a search filter.

RFC 4514 escapes a distinguished name. The same function also escapes the
filter metacharacters ``* ( ) \\`` and NUL, and it is the only escape used for
the bind template and for the later ``sAMAccountName`` filter. One function
means a character cannot be safe in one place and special in the other.
"""

from __future__ import annotations

# Characters that are special in a DN (RFC 4514) or in a filter (RFC 4515).
_HEX = {
    "\\": "\\5c",
    ",": "\\2c",
    "+": "\\2b",
    '"': "\\22",
    "<": "\\3c",
    ">": "\\3e",
    ";": "\\3b",
    "=": "\\3d",
    "*": "\\2a",
    "(": "\\28",
    ")": "\\29",
    "\x00": "\\00",
}


def escape_ldap_name(value: str) -> str:
    """Hex-escape a username for a DN, a UPN, or a filter.

    Leading and trailing spaces become ``\\20``. A leading ``#`` becomes
    ``\\23``. Other controls become ``\\hh``. The printable specials above are
    escaped wherever they occur.
    """
    if not value:
        return value
    out: list[str] = []
    last = len(value) - 1
    for index, ch in enumerate(value):
        if ch in _HEX:
            out.append(_HEX[ch])
            continue
        code = ord(ch)
        if ch == " " and (index == 0 or index == last):
            out.append("\\20")
        elif ch == "#" and index == 0:
            out.append("\\23")
        elif code < 32 or code == 127:
            out.append(f"\\{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def render_bind_name(template: str, username: str) -> str:
    """Substitute ``{username}`` with the escaped name. No other formatting.

    ``str.format`` is not used: a template containing ``{0}`` or a brace that
    an operator typed would raise, or would read a field it should not.
    """
    return template.replace("{username}", escape_ldap_name(username))
