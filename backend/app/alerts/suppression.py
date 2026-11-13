"""Pure resolution of the SOC-17d suppression gate, and its per-run cache.

Three answers live here, all pure functions of their inputs (design §5.3, §5.4):

  * :func:`active_suppression` -- which open row governs one condition at one
    instant. A detector-specific row beats the subject-wide row (``detector_type``
    NULL); an expired row (``ends_at <= at``) never applies, and neither does a
    row that has not started yet.
  * :func:`breaks_through` -- whether this bucket's evidence is extreme enough
    to alert *through* the suppression. The constants table is §5.4 and is
    documented beside the function.
  * :class:`SuppressionGate` -- the rows loaded once per engine run and kept in
    memory, so the gate costs one statement per run whatever the fleet size
    (AGENTS.md rule 9). With zero rows the single ``SELECT`` returns nothing and
    every verdict is "no suppression": the pre-17d path, unchanged.

When a gate stops an alert, the engine writes one
:class:`~app.models.alert_suppression.AlertSuppressionHit` row per incident
(:func:`record_hit`), idempotent on the unique key. On break-through the alert
opens as today -- the existing dedup bumps ``occurrence_count`` instead of
paging again -- and the suppression stays in force, so one storm cannot
silently end a 30-day window.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import (
    AnomalyFamily,
    AnomalyType,
    Criticality,
    IdentityAnomalyType,
    IpsAnomalyType,
    PwAnomalyType,
    RuleAnomalyType,
    WafAnomalyType,
)


class SuppressionRow(Protocol):
    """The shape :func:`active_suppression` reads.

    Duck-typed so the ORM row and a test double both qualify without importing
    the model.
    """

    family: AnomalyFamily
    subject_id: uuid.UUID
    detector_type: str | None
    starts_at: datetime
    ends_at: datetime
    ended_at: datetime | None


class Evidence(Protocol):
    """The evidence shape :func:`breaks_through` reads: the ``extra`` facts."""

    extra: dict


#: One §5.4 rule: (evidence, criticality, settings) -> whether it breaks through.
type _Rule = Callable[[Evidence, Criticality | None, Settings], bool]


def active_suppression[RowT: SuppressionRow](
    rows: Iterable[RowT],
    *,
    family: AnomalyFamily,
    subject_id: uuid.UUID,
    detector: str,
    at: datetime,
) -> RowT | None:
    """The open row that governs one condition at ``at``, or None.

    A row applies when it names this family, this subject, and either this
    detector or every detector of the subject (``detector_type`` NULL), is not
    ended, has started, and has not expired (``ends_at <= at`` never applies).
    The most specific row wins: a detector row before the subject-wide row. The
    unique index makes two matches at one level impossible; the tie-break below
    keeps the answer deterministic anyway.
    """
    best: RowT | None = None
    best_rank = -1
    for row in rows:
        if row.ended_at is not None:
            continue
        if row.family != family or row.subject_id != subject_id:
            continue
        if row.detector_type not in (None, detector):
            continue
        if not (row.starts_at <= at < row.ends_at):
            continue
        rank = 0 if row.detector_type is None else 1
        if rank > best_rank or (
            rank == best_rank and best is not None and row.ends_at > best.ends_at
        ):
            best, best_rank = row, rank
    return best


# --------------------------------------------------------------------- §5.4
#: A volume/count spike breaks through at ten times its normal (D-54).
SPIKE_RATIO_BREAKTHROUGH = 10.0
#: A volume drop breaks through at a tenth of its normal (a 90 % fall).
DROP_RATIO_BREAKTHROUGH = 0.1
#: A share detector breaks through at half of the population AND three times
#: the baseline share: a share cannot reach 10x, half of everything failing is
#: the extreme (D-54).
SHARE_BREAKTHROUGH = 0.5
#: One client or source owning the endpoint/source is the extreme at 80 %.
CONCENTRATION_SHARE = 0.8
#: The multiple every share-based rule demands beside its absolute floor.
SHARE_MULTIPLE = 3.0


def _number(value: object) -> float | None:
    """A real float, or None: a string or a bool is not positive evidence."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _ratio(evidence: Evidence) -> float | None:
    return _number((evidence.extra or {}).get("ratio"))


def _proportion(evidence: Evidence) -> float | None:
    observed = (evidence.extra or {}).get("observed")
    if not isinstance(observed, dict):
        return None
    return _number(observed.get("proportion"))


def _spike(evidence: Evidence) -> bool:
    ratio = _ratio(evidence)
    return ratio is not None and ratio >= SPIKE_RATIO_BREAKTHROUGH


def _share(evidence: Evidence, *, floor: float, multiple: float = SHARE_MULTIPLE) -> bool:
    """Both halves of a share rule: the absolute floor AND the multiple."""
    share = _proportion(evidence)
    ratio = _ratio(evidence)
    return (
        share is not None
        and ratio is not None
        and share >= floor
        and ratio >= multiple
    )


def _volume_spike(evidence: Evidence, criticality: Criticality | None,
                  settings: Settings) -> bool:
    """Phase A spike: ratio >= 10, or -- when the baseline is zero and there is
    no ratio -- at least ``10 x anomaly_min_bucket_events`` observed events.

    The absolute fallback mirrors the detector's own expected-zero guard
    (``extra["ratio_basis"] == "expected_zero"``, `app/anomaly/detectors.py:287`).
    """
    if _spike(evidence):
        return True
    facts = evidence.extra or {}
    if facts.get("ratio_basis") != "expected_zero":
        return False
    observed = _number(facts.get("observed_events"))
    return observed is not None and observed >= (
        SPIKE_RATIO_BREAKTHROUGH * settings.anomaly_min_bucket_events
    )


def _volume_drop(evidence: Evidence) -> bool:
    ratio = _ratio(evidence)
    return ratio is not None and ratio <= DROP_RATIO_BREAKTHROUGH


def _critical_silence(evidence: Evidence, criticality: Criticality | None,
                      settings: Settings) -> bool:
    """The task's named case: a CRITICAL source's total silence always alerts."""
    return criticality is Criticality.CRITICAL


def _never(evidence: Evidence, criticality: Criticality | None,
           settings: Settings) -> bool:
    return False


def _always(evidence: Evidence, criticality: Criticality | None,
            settings: Settings) -> bool:
    return True


def _spike_rule(evidence: Evidence, criticality: Criticality | None,
                settings: Settings) -> bool:
    return _spike(evidence)


def _drop_rule(evidence: Evidence, criticality: Criticality | None,
               settings: Settings) -> bool:
    return _volume_drop(evidence)


def _share_rule(evidence: Evidence, criticality: Criticality | None,
                settings: Settings) -> bool:
    """Half the population failing AND three times the baseline share."""
    return _share(evidence, floor=SHARE_BREAKTHROUGH)


def _block_shift_rule(evidence: Evidence, criticality: Criticality | None,
                      settings: Settings) -> bool:
    """IPS block-ratio shift: a rise of at least 0.5 absolute AND >= 3x."""
    facts = evidence.extra or {}
    rise = _number(facts.get("absolute_deviation"))
    ratio = _ratio(evidence)
    return (
        rise is not None and ratio is not None and rise >= SHARE_BREAKTHROUGH
        and ratio >= SHARE_MULTIPLE
    )


def _concentration_rule(evidence: Evidence, criticality: Criticality | None,
                        settings: Settings) -> bool:
    return _share(evidence, floor=CONCENTRATION_SHARE)


# The §5.4 constants table, one entry per value of every family's detector
# enum. A missing entry is a test failure (the unit suite enumerates every
# enum), and an unknown detector is "never": break-through requires positive
# evidence of extremity, never a magnitude the detector did not measure.
#
# Evidence fields, read where each detector writes them:
#   Phase A volume       extra["ratio"], extra["ratio_basis"], extra["observed_events"]
#                        `app/anomaly/detectors.py:276`, `:287`, `:253`
#   WAF request rate     extra["ratio"]                       `app/waf/detector.py:327`
#   WAF ratio shares     extra["observed"]["proportion"], extra["ratio"]
#                        `app/waf/behavior.py:483`, `:498`
#   WAF concentration    extra["observed"]["proportion"], extra["ratio"]
#                        `app/waf/behavior.py:648`, `:660`
#   IPS spikes           extra["ratio"]                       `app/ips/detector.py:587`
#   IPS share/block      extra["observed"]["proportion"], extra["absolute_deviation"],
#                        extra["ratio"]                       `app/ips/behavior.py:142`,
#                        `:153`, `:155`
#   PW spike/drop        extra["ratio"]                       `app/property_watch/
#                        detectors.py:411`, `:519`
#   Rule storm           extra["ratio"]                       `app/rules/anomalies.py`
#                        (a null ratio, including a zero median, is not evidence)
#   Rule silence         none                                 never (design §4.3)
BREAKTHROUGH_RULES: dict[AnomalyFamily, dict[str, _Rule]] = {
    AnomalyFamily.LOG_SOURCE: {
        AnomalyType.VOLUME_SPIKE.value: _volume_spike,
        AnomalyType.VOLUME_DROP.value: _drop_rule,
        AnomalyType.NO_EVENTS.value: _critical_silence,
        # The other six do not run on live data (design §1.5): never.
        AnomalyType.PARSING_DEGRADATION.value: _never,
        AnomalyType.UNKNOWN_EVENT_SPIKE.value: _never,
        AnomalyType.TIMESTAMP_DELAY.value: _never,
        AnomalyType.CARDINALITY_DROP.value: _never,
        AnomalyType.COLLECTION_ERROR.value: _never,
        AnomalyType.REPEATED_PAYLOAD.value: _never,
    },
    AnomalyFamily.WAF: {
        WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value: _spike_rule,
        WafAnomalyType.WAF_BLOCK_RATIO_SPIKE.value: _share_rule,
        WafAnomalyType.WAF_ERROR_RATIO_SPIKE.value: _share_rule,
        WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE.value: _share_rule,
        WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE.value: _concentration_rule,
        # Novelty has no magnitude; the fanout spike is unsupported.
        WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS.value: _never,
        WafAnomalyType.WAF_CLIENT_ENDPOINT_FANOUT_SPIKE.value: _never,
    },
    AnomalyFamily.IPS: {
        IpsAnomalyType.IPS_PORT_RATE_SPIKE.value: _spike_rule,
        IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE.value: _spike_rule,
        IpsAnomalyType.IPS_SIGNATURE_BURST.value: _spike_rule,
        IpsAnomalyType.IPS_PORT_FANOUT_SPIKE.value: _spike_rule,
        IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE.value: _concentration_rule,
        IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT.value: _block_shift_rule,
        # Novelty has no magnitude.
        IpsAnomalyType.IPS_NEW_DESTINATION_PORT.value: _never,
    },
    AnomalyFamily.PROPERTY_WATCH: {
        PwAnomalyType.PW_VALUE_SPIKE.value: _spike_rule,
        PwAnomalyType.PW_VALUE_DROP.value: _drop_rule,
        # A value vanishing is itself the extreme.
        PwAnomalyType.PW_VALUE_DISAPPEARED.value: _always,
        # Novelty has no magnitude.
        PwAnomalyType.PW_NEW_VALUE.value: _never,
        PwAnomalyType.PW_NEW_COMBINATION.value: _never,
    },
    AnomalyFamily.RULE: {
        # Ten times the usual hour. A null ratio (zero median) does not break
        # through: D-54 requires positive evidence of a magnitude.
        RuleAnomalyType.RULE_STORM.value: _spike_rule,
        # A proven zero has no further extreme. The suppression holds for the
        # whole window (D-60).
        RuleAnomalyType.RULE_SILENCE.value: _never,
    },
    AnomalyFamily.IDENTITY: {
        # The two rate detectors carry a ratio. Ten times the band breaks through.
        IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE.value: _spike_rule,
        IdentityAnomalyType.IDENTITY_LOCKOUT_SPIKE.value: _spike_rule,
        # Threshold and novelty have no magnitude that breaks a hold.
        IdentityAnomalyType.IDENTITY_AUDIT_LOG_CLEARED.value: _never,
        IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD.value: _never,
        IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT.value: _never,
        IdentityAnomalyType.IDENTITY_ACCOUNT_CREATED.value: _never,
        IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED.value: _never,
    },
}


def breaks_through(
    family: AnomalyFamily,
    detector: str,
    evidence: Evidence,
    *,
    criticality: Criticality | None,
    settings: Settings,
) -> bool:
    """Whether this bucket's evidence is extreme enough to alert through.

    The §5.4 table, row by row:

    ============ ================================= =====================
    Family       Detector                        Breaks through when
    ============ ================================= =====================
    Phase A      ``VOLUME_SPIKE``                ratio >= 10; when the baseline is 0, at least
                                                  ``10 x anomaly_min_bucket_events`` events
    Phase A      ``VOLUME_DROP``                 ratio <= 0.1 (a 90 % fall)
    Phase A      ``NO_EVENTS``                   the source's criticality is CRITICAL
    Phase A      the other six                   never (they do not run on live data)
    WAF          ``WAF_REQUEST_RATE_SPIKE``      ratio >= 10
    WAF          block/error/client-error ratio  observed share >= 0.5 AND
                                                  ratio >= 3x the baseline share
    WAF          ``WAF_CLIENT_CONCENTRATION_SPIKE``  top-client share >= 0.8 AND ratio >= 3x
    WAF          new-endpoint access, fanout     never (novelty / unsupported)
    IPS          the four spike detectors        ratio >= 10
    IPS          source concentration            share >= 0.8 AND >= 3x baseline
    IPS          block-ratio shift               rise >= 0.5 absolute AND >= 3x baseline
    IPS          new destination port            never (novelty)
    PW           ``PW_VALUE_SPIKE``              ratio >= 10
    PW           ``PW_VALUE_DROP``               ratio <= 0.1
    PW           ``PW_VALUE_DISAPPEARED``        always (a value vanishing is the extreme)
    PW           new value, new combination      never (novelty)
    Rule         ``RULE_STORM``                  ratio >= 10; a null ratio never
    Rule         ``RULE_SILENCE``                never (a muted silence stays muted)
    Identity     failed-logon and lockout spikes ratio >= 10
    Identity     threshold and new account       never (no magnitude)
    ============ ================================= =====================

    A detector with no magnitude in its evidence is "never", never "always":
    the function requires positive evidence of extremity. An unknown detector
    value is "never" too; a future detector that grows a magnitude must be
    added to ``BREAKTHROUGH_RULES`` (the unit suite fails on a missing entry).
    A ratio the detector itself calls undefined (an expected-zero baseline with
    no ``ratio_basis`` fallback, e.g. the WAF/IPS/PW spikes) is not evidence of
    ten times the normal, so it does not break through.
    """
    rule = BREAKTHROUGH_RULES.get(family, {}).get(detector)
    if rule is None:
        return False
    return rule(evidence, criticality, settings)


class SuppressionGate:
    """The open suppression rows loaded once per engine run.

    Construct one per engine instance, not per bucket. The load is lazy: a run
    that opens nothing (family switch off, maintenance, a record-only policy)
    never pays for it, and a run that does pays exactly one ``SELECT`` whatever
    the fleet size (AGENTS.md rule 9). With no rows every verdict is None, so
    the pre-17d path is unchanged.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._rows: list[AlertSuppression] | None = None

    async def _load(self) -> list[AlertSuppression]:
        if self._rows is None:
            self._rows = list(
                (
                    await self._session.scalars(
                        select(AlertSuppression).where(
                            AlertSuppression.ended_at.is_(None)
                        )
                    )
                ).all()
            )
        return self._rows

    async def active(
        self,
        *,
        family: AnomalyFamily,
        subject_id: uuid.UUID,
        detector: str,
        at: datetime,
    ) -> AlertSuppression | None:
        """The row that stops this condition's alert at ``at``, or None."""
        return active_suppression(
            await self._load(),
            family=family,
            subject_id=subject_id,
            detector=detector,
            at=at,
        )

    async def stops(
        self,
        *,
        family: AnomalyFamily,
        subject_id: uuid.UUID,
        detector: str,
        at: datetime,
        evidence: Evidence,
        criticality: Criticality | None,
        settings: Settings,
    ) -> tuple[AlertSuppression | None, bool]:
        """``(row, breakthrough)``: the row is None when nothing applies.

        A break-through returns the row *and* True: the caller opens the alert
        as today and leaves the row in force.
        """
        row = await self.active(
            family=family, subject_id=subject_id, detector=detector, at=at
        )
        if row is None:
            return None, False
        return row, breaks_through(
            family,
            detector,
            evidence,
            criticality=criticality,
            settings=settings,
        )


async def record_hit(
    session: AsyncSession,
    suppression: AlertSuppression,
    *,
    family: AnomalyFamily,
    anomaly_id: uuid.UUID,
    detector: str,
    bucket_start: datetime,
    at: datetime,
) -> None:
    """Write this suppressed incident's hit row, once.

    Idempotent on the unique ``(suppression_id, anomaly_id)`` key: a refresh of
    the same incident issues the same insert and changes nothing, so the count
    stays "incidents recorded while suppressed", never "buckets" (design §5.2).
    """
    await session.execute(
        pg_insert(AlertSuppressionHit)
        .values(
            suppression_id=suppression.id,
            family=family.value,
            anomaly_id=anomaly_id,
            detector_type=detector,
            bucket_start=bucket_start,
            occurred_at=at,
        )
        .on_conflict_do_nothing(index_elements=["suppression_id", "anomaly_id"])
    )
