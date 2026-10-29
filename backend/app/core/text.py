"""Shared spelling helpers for the analyst-facing sentences the backend writes.

The UI draws its own percentages through ``formatPercent``/``formatShare``
(``frontend/src/lib/format.ts``) and its own changes in percentage points
through ``formatPoints`` (``frontend/src/components/waf/wafFormat.ts``, also
re-exported by ``ipsFormat.ts``): one decimal of the caller's choosing and
U+00A0 (NO-BREAK SPACE) between the number and its unit, so a line never
breaks between them. The sentences the detectors, validators and planners
write sit beside those figures on screen and use the same spelling.
"""

from __future__ import annotations

import math


def percent(ratio: float | None, *, precision: int = 0, missing: str = "n/a") -> str:
    """Render a ratio as the shared analyst-facing percent spelling.

    ``percent(0.318, precision=1)`` reads ``31.8\u00a0%``: the number keeps the
    caller's precision, then U+00A0 separates it from the "%" sign. A missing
    measurement (``None``, or a non-finite leak) renders as ``missing`` — the
    caller's own word for "not measured", never a zero — because a percent of
    something unmeasured is not zero. A ratio is a fraction in [0, 1], as the
    ``:.N%`` spec this replaces took.
    """
    if ratio is None or not math.isfinite(ratio):
        return missing
    return f"{ratio:.{precision}%}".replace("%", "\u00a0%")


def points(
    fraction: float | None,
    *,
    precision: int = 1,
    signed: bool = True,
    missing: str = "n/a",
) -> str:
    """Render a change in percentage points as the shared analyst-facing spelling.

    ``points(0.297)`` reads ``+29.7\u00a0pp``: the fraction times 100 keeps the
    caller's precision, U+00A0 separates the number from "pp", and a positive
    value carries a "+" when ``signed`` (a negative value keeps its own "-", so
    a drop is visibly a drop). ``signed=False`` is for a threshold written as a
    plain magnitude, as the minimum-rise guards do. A zero prints without a
    sign, ``0.0\u00a0pp``: the UI's ``formatPoints`` signs only a value above
    zero, and a zero is not a rise. A missing measurement (``None``, or a
    non-finite leak) renders as ``missing`` — the caller's own word for "not
    measured", never a zero. A fraction is a ratio whose change is expressed in
    points, so ``0.297`` is 29.7 points; the API already sends points to the UI,
    which does not multiply them again either.
    """
    if fraction is None or not math.isfinite(fraction):
        return missing
    value = fraction * 100
    # ``-0.0`` is not a fall. Keep the arithmetic sign for a value that only
    # rounds to zero (the UI's ``toFixed`` prints "-0.0" there too), but print
    # an exact zero without one, as ``formatPoints`` does.
    if value == 0:
        value = 0.0
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{precision}f}\u00a0pp"
