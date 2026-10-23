"""Shared spelling helpers for the analyst-facing sentences the backend writes.

The UI draws its own percentages through ``formatPercent``/``formatShare``
(``frontend/src/lib/format.ts``): one decimal of the caller's choosing and
U+00A0 (NO-BREAK SPACE) between the number and the "%" sign, so a line never
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
