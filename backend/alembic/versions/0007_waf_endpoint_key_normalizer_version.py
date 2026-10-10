"""Scope the WAF endpoint natural key to the normalizer version.

`WafEndpointCollector` looks an endpoint up by (instance, log source, virtual
host, endpoint, method, normalizer_version) and creates one when none matches,
but the unique key omitted `normalizer_version`. The first bump of
ENDPOINT_NORMALIZER_VERSION would therefore have failed to insert every endpoint
whose normalized form did not change: an IntegrityError on every bucket, a
watermark that never advances, and WAF collection stopped until someone
intervened. Metric rows and baselines are already version-scoped
(docs/PHASE-B-WAF-BEHAVIOR.md section 8.6); now the entity is too.

NULLS NOT DISTINCT is kept: `virtual_host` stays nullable, and a hostless
endpoint must still form exactly one entity per normalizer version.

Downgrade restores the five-column key. It fails, deliberately and loudly, if
endpoints exist under more than one normalizer version -- the state this
revision makes possible and the old key cannot represent.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE waf_endpoint DROP CONSTRAINT uq_waf_endpoint_natural_key")
    op.execute(
        "ALTER TABLE waf_endpoint ADD CONSTRAINT uq_waf_endpoint_natural_key "
        "UNIQUE NULLS NOT DISTINCT (instance_id, log_source_id, virtual_host, "
        "normalized_endpoint, http_method, normalizer_version)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE waf_endpoint DROP CONSTRAINT uq_waf_endpoint_natural_key")
    op.execute(
        "ALTER TABLE waf_endpoint ADD CONSTRAINT uq_waf_endpoint_natural_key "
        "UNIQUE NULLS NOT DISTINCT (instance_id, log_source_id, virtual_host, "
        "normalized_endpoint, http_method)"
    )
