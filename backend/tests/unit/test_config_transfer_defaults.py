"""Pin the import's hand-copied SOC defaults to the model columns (CFG-1g r1)."""

from __future__ import annotations

from sqlalchemy import Column

from app.config_transfer.export_bundle import _SOC_FIELDS
from app.config_transfer.import_bundle import SOC_DEFAULTS
from app.models.log_source import LogSource


def _default(column: Column) -> object:
    """The value a newly constructed LogSource gets for one column."""
    default = column.default
    if default is not None:
        if not default.is_callable:
            return default.arg
        try:
            return default.arg()
        except TypeError:
            # Mapped lambdas are wrapped to take an execution context; builtins like
            # ``dict`` are not, and are handled by the branch above.
            return default.arg(object())
    server_default = column.server_default
    return None if server_default is None else server_default.arg


def test_soc_defaults_match_model_column_defaults() -> None:
    columns = LogSource.__table__.columns
    assert set(SOC_DEFAULTS) == set(_SOC_FIELDS)
    for key, expected in SOC_DEFAULTS.items():
        assert key in columns, key
        assert _default(columns[key]) == expected, key
