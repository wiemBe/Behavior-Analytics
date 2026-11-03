"""Geography API (D-10): events per country, by source or destination address.

Guarded by `read:geo` at the router (`read:*` satisfies it). The answer is aggregate counts
per country and never an address. Two statements whatever the range: one aggregate and one
coverage count, which also counts the enabled instances that should have been collected —
the same rows `_for_each_instance` enumerates — so an instance with no row at all still
raises the expected count, and carries, as one bounded JSON aggregate, how the buckets in
range were collected (`collected_with`). A range with no measured bucket answers NOT_COLLECTED
and carries no measurement and no zeros.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import distinct, func, literal_column, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB

from app.api.deps import SessionDep, SettingsDep
from app.geo.fold import GeoDirection
from app.models.geo import GeoCountryBucket
from app.models.instance import QRadarInstance
from app.schemas.geo import (
    GeoCollectedWayOut,
    GeoCollectedWithOut,
    GeoCountriesOut,
    GeoCountryOut,
    GeoCoverageOut,
    GeoMeasurement,
    GeoRemaindersOut,
    GeoStatus,
    country_source_fields,
)

router = APIRouter(prefix="/geo", tags=["geo"])


def _validate_range(since: datetime, until: datetime, max_days: int) -> None:
    """Both bounds are timezone-aware, ordered and no wider than `max_days`, or a 422."""
    if since.tzinfo is None or until.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "since and until must carry a timezone"
        )
    if until <= since:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "range end must be after its start"
        )
    if until - since > timedelta(days=max_days):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"range exceeds the maximum of {max_days} days",
        )


def _expected_buckets(
    since: datetime, until: datetime, bucket_seconds: int, now: datetime, settle: int
) -> tuple[datetime, int]:
    """The first aligned bucket in range, and how many settled buckets the range holds."""
    first = -(-int(since.timestamp()) // bucket_seconds) * bucket_seconds
    settled = min(until, now - timedelta(seconds=settle))
    last = int(settled.timestamp()) // bucket_seconds * bucket_seconds
    return (
        datetime.fromtimestamp(first, tz=UTC),
        max(0, (last - first) // bucket_seconds),
    )


def _collected_with(ways: list[dict]) -> GeoCollectedWithOut:
    """The distinct ways in range, most buckets first, and the buckets that do not say."""
    known = [
        GeoCollectedWayOut(
            country_source=item["source"],
            country_property=item["property"],
            buckets=int(item["buckets"]),
        )
        for item in ways
        if item["source"] is not None
    ]
    known.sort(
        key=lambda way: (-way.buckets, way.country_source.value, way.country_property or "")
    )
    return GeoCollectedWithOut(
        ways=known,
        unknown_buckets=sum(int(item["buckets"]) for item in ways if item["source"] is None),
    )


@router.get("/countries", response_model=GeoCountriesOut)
async def get_countries(
    session: SessionDep,
    settings: SettingsDep,
    since: Annotated[datetime, Query()],
    until: Annotated[datetime, Query()],
    direction: Annotated[GeoDirection, Query()],
) -> GeoCountriesOut:
    _validate_range(since, until, settings.api_max_range_days)
    first, expected_per_instance = _expected_buckets(
        since,
        until,
        settings.collection_interval_seconds,
        datetime.now(UTC),
        settings.search_cadence_settle_seconds,
    )
    in_range = (
        GeoCountryBucket.direction == direction.value,
        GeoCountryBucket.bucket_start >= first,
        GeoCountryBucket.bucket_start < until,
    )
    aggregate = (
        await session.execute(
            select(
                GeoCountryBucket.country_code,
                GeoCountryBucket.remainder_kind,
                func.sum(GeoCountryBucket.events),
                func.bool_or(GeoCountryBucket.measurement == "LOWER_BOUND"),
            )
            .where(*in_range)
            .group_by(GeoCountryBucket.country_code, GeoCountryBucket.remainder_kind)
        )
    ).all()
    # The second statement counts the settled buckets it saw, and folds in the instances
    # that should have been collected: the enabled rows `_for_each_instance` enumerates.
    # An instance with no row at all still raises the expected count, so half an estate
    # missing cannot read as a complete range.
    should_collect = (
        select(func.count())
        .select_from(QRadarInstance)
        .where(QRadarInstance.enabled.is_(True))
        .scalar_subquery()
    )
    # How the buckets were collected: one row per distinct (source, property) pair present,
    # folded into one JSON value so it rides in the same statement. NULL source = stored
    # before the buckets recorded it.
    per_way = (
        select(
            GeoCountryBucket.country_source.label("source"),
            GeoCountryBucket.country_property.label("property"),
            func.count(
                distinct(tuple_(GeoCountryBucket.instance_id, GeoCountryBucket.bucket_start))
            ).label("buckets"),
        )
        .where(*in_range)
        .group_by(GeoCountryBucket.country_source, GeoCountryBucket.country_property)
        .subquery()
    )
    ways_json = (
        select(
            func.coalesce(
                func.jsonb_agg(
                    func.jsonb_build_object(
                        "source", per_way.c.source,
                        "property", per_way.c.property,
                        "buckets", per_way.c.buckets,
                    ),
                    type_=JSONB,
                ),
                literal_column("'[]'::jsonb"),
            )
        )
        .select_from(per_way)
        .scalar_subquery()
    )
    measured, instances, newest_end, ways = (
        await session.execute(
            select(
                func.count(
                    distinct(
                        tuple_(GeoCountryBucket.instance_id, GeoCountryBucket.bucket_start)
                    )
                ),
                should_collect,
                # The end of the newest measured bucket: its start plus its own width.
                func.max(
                    GeoCountryBucket.bucket_start
                    + GeoCountryBucket.bucket_seconds * literal_column("INTERVAL '1 second'")
                ),
                ways_json,
            ).where(*in_range)
        )
    ).one()

    countries = sorted(
        ((str(code).strip(), int(events)) for code, kind, events, _lb in aggregate if kind is None),
        key=lambda item: (-item[1], item[0]),
    )
    total = sum(events for _code, events in countries)
    remainders = GeoRemaindersOut(
        **{str(kind): int(events) for _code, kind, events, _lb in aggregate if kind is not None}
    )
    lower_bound = any(bool(lb) for *_rest, lb in aggregate)
    expected = expected_per_instance * int(instances)
    if not aggregate:
        verdict = GeoStatus.NOT_COLLECTED
    elif total == 0:
        verdict = GeoStatus.INSUFFICIENT_DATA
    elif int(measured) < expected:
        verdict = GeoStatus.PARTIAL
    else:
        verdict = GeoStatus.OK
    # Nothing was measured in a NOT_COLLECTED range, so it carries neither an exactness
    # claim nor a zero for a remainder kind that was never counted.
    unmeasured = verdict is GeoStatus.NOT_COLLECTED
    source, country_property, note, note_code, note_params = country_source_fields(
        settings, direction
    )
    return GeoCountriesOut(
        direction=direction,
        since=since,
        until=until,
        countries=[
            GeoCountryOut(country_code=code, events=events, share=events / total)
            for code, events in countries
        ],
        remainders=None if unmeasured else remainders,
        measurement=(
            None
            if unmeasured
            else GeoMeasurement.LOWER_BOUND if lower_bound else GeoMeasurement.EXACT
        ),
        coverage=GeoCoverageOut(measured_buckets=int(measured), expected_buckets=expected),
        status=verdict,
        latest_bucket_end=None if unmeasured else newest_end,
        country_source=source,
        country_property=country_property,
        collected_with=None if unmeasured else _collected_with(ways),
        unresolved_note=note,
        unresolved_note_code=note_code,
        unresolved_note_params=note_params,
    )
