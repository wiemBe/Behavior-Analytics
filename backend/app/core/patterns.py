"""Shared validation patterns with no application imports.

This module is a leaf: ``app.core.config`` and ``app.providers.base`` both need
these patterns, and ``app.providers`` imports ``app.core.config``, so a pattern
that lived in the providers package would make the settings module import the
providers package and fail in a fresh interpreter.
"""

from __future__ import annotations

import re

#: The only shape a site-authored QRadar property name may take before it is
#: interpolated into AQL. Letters, digits, underscore, space and hyphen; anything
#: else -- a quote, a comma, a parenthesis -- is rejected rather than escaped,
#: because an allowlist a reviewer can verify by eye beats a quoting scheme that
#: has to be trusted. Property names are never expressions.
PROPERTY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$")
