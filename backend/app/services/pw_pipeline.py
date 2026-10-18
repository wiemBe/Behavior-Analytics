"""Scheduled property-watch pipeline: collect, evaluate, then train.

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

from app.alerts.dispatcher import AlertEnqueuer, NotificationDispatcher
from app.alerts.routing import default_policy
from app.collectors.pw_collector import PwCollectionReport, PwCollector
from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.property_watch.engine import PwAnomalyEngine, PwEvaluationReport
from app.property_watch.training import PwBaselineTrainer, PwTrainingReport
from app.providers.base import QRadarProvider
from app.services.waf_pipeline import sanitized_error


@dataclass
class PwPipelineReport:
    instance_id: uuid.UUID
    collection: PwCollectionReport
    evaluation: PwEvaluationReport | None = None
    training: PwTrainingReport | None = None
    #: Sanitized class-and-message of a failed step; collection is already kept by then.
    evaluation_error: str | None = None
    training_error: str | None = None
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            **self.collection.as_dict(),
            # Preserve the PW-2b dispatch payload consumed by operations tooling.
            "pipeline": {"steps": ["collect"]},
            # The steps that ran, in order. `pipeline.steps` above is the PW-2b dispatch
            # payload and stays as it was; this is the truthful list.
            "steps_run": list(self.steps),
            "evaluation": self.evaluation.as_dict() if self.evaluation else None,
            "evaluation_error": self.evaluation_error,
            "training": self.training.as_dict() if self.training else None,
            "training_error": self.training_error,
        }


class PwInstancePipeline:
    """Run every step of the property-watch family for one instance."""

    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        engine_factory: Callable[..., PwAnomalyEngine] | None = None,
        trainer_factory: Callable[..., PwBaselineTrainer] | None = None,
        enqueuer: AlertEnqueuer | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._engine_factory = engine_factory or PwAnomalyEngine
        self._trainer_factory = trainer_factory or PwBaselineTrainer
        # Notifications follow alerts: built only while alerting is on, so shadow mode
        # constructs nothing that could enqueue.
        if enqueuer is None and self.settings.pw_alerting_enabled:
            enqueuer = NotificationDispatcher(
                session, notifiers={}, policy=default_policy(), settings=self.settings
            )
        self._enqueuer = enqueuer

    async def run(self, instance: QRadarInstance) -> PwPipelineReport:
        collection = await PwCollector(
            self.session, self.provider, settings=self.settings, clock=self._clock
        ).collect(instance)
        report = PwPipelineReport(instance.id, collection, steps=["collect"])
        # Each later step runs in its own savepoint: a failure leaves what collection stored
        # (and the evaluation watermark where it was) instead of rolling the whole tick back,
        # the IPS pipeline's isolation. A failed evaluation also skips training: baselines are
        # trained only from buckets that were judged first.
        try:
            async with self.session.begin_nested():
                # Handed over only when there is one, so a narrower factory keeps working.
                extra = {"enqueuer": self._enqueuer} if self._enqueuer is not None else {}
                report.evaluation = await self._engine_factory(
                    self.session, settings=self.settings, clock=self._clock, **extra
                ).evaluate(instance)
        except Exception as exc:
            report.evaluation_error = sanitized_error(exc)
            return report
        report.steps.append("evaluate")
        try:
            async with self.session.begin_nested():
                report.training = await self._trainer_factory(
                    self.session, settings=self.settings
                ).rebuild_due(instance, now=self._clock())
        except Exception as exc:
            report.training_error = sanitized_error(exc)
            return report
        report.steps.append("train")
        return report
