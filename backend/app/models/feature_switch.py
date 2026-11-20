"""Operator-set runtime family switches, one row per ``Settings`` field name.

A row wins over the ``.env`` value; an absent row leaves ``.env`` in force. The
``CHECK`` allow-list is ``FEATURE_KEYS`` below, so a typo cannot create a row
that silently switches nothing. The migration keeps a literal copy: migrations
never import application code.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: The switches an operator may set at runtime, in the OPS-FLAGS design order:
#: every name is a boolean ``Settings`` field.
FEATURE_KEYS: tuple[str, ...] = (
    "waf_collection_enabled",
    "waf_alerting_enabled",
    "waf_outcome_collection_enabled",
    "waf_client_collection_enabled",
    "ips_collection_enabled",
    "ips_alerting_enabled",
    "rule_match_collection_enabled",
    "rule_alerting_enabled",
    "pw_collection_enabled",
    "pw_alerting_enabled",
    "custom_rules_enabled",
    "custom_rules_alerting_enabled",
    "identity_collection_enabled",
    "identity_alerting_enabled",
    "catchall_collection_enabled",
    "catchall_new_sender_alerts_enabled",
    "geo_collection_enabled",
    "qradar_health_collection_enabled",
    "explanation_enabled",
    "inventory_new_source_alerts_enabled",
    # JDBC-1 S0 (D-95): independent rollout gates for the two new tracks.
    "jdbc_collection_enabled",
    "qradar_source_pause_enabled",
)

_ALLOWED_KEYS = ", ".join(f"'{key}'" for key in FEATURE_KEYS)


class FeatureSwitch(Base):
    __tablename__ = "feature_switch"
    __table_args__ = (CheckConstraint(f"key IN ({_ALLOWED_KEYS})", name="key_allowed"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Username, "installer" or "cli".
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
