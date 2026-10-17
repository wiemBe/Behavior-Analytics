"""Scheduled property-watch pipeline: collect, then (PW-3b) evaluate and train.

PW-2b builds the collection step. The pipeline is the seam PW-3b extends: evaluation and
training read the buckets this step wrote, in that order, so a bucket cannot teach the
baseline that judges it (the IPS and WAF ordering, design §G.6).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.pw_collector import PwCollectionReport, PwCollector
from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.providers.base import QRadarProvider


@dataclass
class PwPipelineReport:
    instance_id: uuid.UUID
    collection: PwCollectionReport
    #: The steps that ran, in order. PW-3b appends `evaluate` and `train`.
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {**self.collection.as_dict(), "pipeline": {"steps": self.steps}}


class PwInstancePipeline:
    """Run every step of the property-watch family for one instance."""

    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self, instance: QRadarInstance) -> PwPipelineReport:
        collection = await PwCollector(
            self.session, self.provider, settings=self.settings, clock=self._clock
        ).collect(instance)
        return PwPipelineReport(instance.id, collection, steps=["collect"])
