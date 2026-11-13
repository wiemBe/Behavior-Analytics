"""Geography API: statuses, shares, measurement, coverage, ranges, permissions. DB-gated."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings, get_settings
from app.models.geo import GeoCountryBucket
from app.schemas.geo import UNRESOLVED_NOTE
from app.security.rbac import Principal
from tests.integration.factories import make_instance
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run geo API integration tests",
    ),
]

VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
GEO_ONLY = Principal(subject="geo", permissions=frozenset({"read:geo"}))
FLEET_ONLY = Principal(subject="fleet", permissions=frozenset({"read:fleet"}))
NOTHING = Principal(subject="nobody", permissions=frozenset())
HOUR = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
URL = "/api/v1/geo/countries"


def params(**over: object) -> dict:
    base = {
        "since": HOUR.isoformat(),
        "until": (HOUR + timedelta(hours=1)).isoformat(),
        "direction": "SOURCE",
    }
    return {**base, **over}


def row(  # type: ignore[no-untyped-def]
    inst, index: int, code: str | None, events: int, *, kind=None, direction="SOURCE", m="EXACT"
):
    return GeoCountryBucket(
        instance_id=inst.id,
        bucket_start=HOUR + timedelta(minutes=5 * index),
        bucket_seconds=300,
        direction=direction,
        country_code=code,
        remainder_kind=kind,
        events=events,
        measurement=m,
        query_version="geo-country-v1",
        collected_at=HOUR,
    )


def seed(rows_for):  # type: ignore[no-untyped-def]
    async def _seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add_all(rows_for(inst))
        await s.flush()
        return inst.id

    return run(_seed)


def full_hour(inst):  # type: ignore[no-untyped-def]
    out = []
    for index in range(12):
        out += [
            row(inst, index, "US", 60),
            row(inst, index, "DE", 20),
            row(inst, index, None, 10, kind="UNRESOLVED"),
        ]
    return out


@pytest.fixture
def full(db_schema):  # type: ignore[no-untyped-def]
    return seed(full_hour)


class TestAuth:
    def test_read_star_and_read_geo_are_allowed(self, full) -> None:
        assert Client(VIEWER).get(URL, params=params()).status_code == 200
        assert Client(GEO_ONLY).get(URL, params=params()).status_code == 200

    def test_a_principal_without_read_geo_is_forbidden(self, full) -> None:
        assert Client(NOTHING).get(URL, params=params()).status_code == 403
        assert Client(FLEET_ONLY).get(URL, params=params()).status_code == 403


class TestStatus:
    def test_nothing_collected_in_range_is_not_collected(self, db_schema) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "NOT_COLLECTED"
        assert body["countries"] == []
        assert body["coverage"] == {"measured_buckets": 0, "expected_buckets": 0}

    def test_rows_outside_the_range_or_direction_do_not_count(self, db_schema) -> None:
        seed(lambda inst: [
            row(inst, 0, "US", 5, direction="DESTINATION"),
            row(inst, 0, "US", 5),
        ])
        later = (HOUR + timedelta(hours=3)).isoformat()
        body = Client(VIEWER).get(
            URL, params=params(since=later, until=(HOUR + timedelta(hours=4)).isoformat())
        ).json()
        assert body["status"] == "NOT_COLLECTED"
        body = Client(VIEWER).get(URL, params=params(direction="DESTINATION")).json()
        assert [c["country_code"] for c in body["countries"]] == ["US"]

    def test_rows_with_no_country_event_are_insufficient_data_never_normal(
        self, db_schema
    ) -> None:
        seed(lambda inst: [
            row(inst, index, None, 40, kind="UNRESOLVED") for index in range(12)
        ])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "INSUFFICIENT_DATA"
        assert body["countries"] == []
        assert body["remainders"]["UNRESOLVED"] == 480
        assert body["coverage"] == {"measured_buckets": 12, "expected_buckets": 12}

    def test_a_measured_empty_range_is_insufficient_data_too(self, db_schema) -> None:
        seed(lambda inst: [row(inst, 0, None, 0, kind="UNRESOLVED")])
        assert Client(VIEWER).get(URL, params=params()).json()["status"] == "INSUFFICIENT_DATA"

    def test_missing_buckets_make_it_partial(self, db_schema) -> None:
        seed(lambda inst: [row(inst, index, "US", 10) for index in range(0, 12, 2)])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "PARTIAL"
        assert body["coverage"] == {"measured_buckets": 6, "expected_buckets": 12}

    def test_a_complete_range_is_ok(self, full) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "OK"
        assert body["coverage"] == {"measured_buckets": 12, "expected_buckets": 12}

    def test_two_instances_double_the_expected_buckets(self, db_schema) -> None:
        seed(full_hour)
        seed(lambda inst: [row(inst, index, "US", 1) for index in range(6)])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["coverage"] == {"measured_buckets": 18, "expected_buckets": 24}
        assert body["status"] == "PARTIAL"

    def test_an_instance_with_no_row_at_all_still_raises_the_expected_buckets(
        self, db_schema
    ) -> None:
        seed(full_hour)
        seed(lambda inst: [])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["coverage"] == {"measured_buckets": 12, "expected_buckets": 24}
        assert body["status"] == "PARTIAL"

    def test_a_disabled_instance_does_not_raise_the_expected_buckets(self, db_schema) -> None:
        seed(full_hour)

        def disabled_rows(inst):  # type: ignore[no-untyped-def]
            inst.enabled = False
            return [row(inst, index, "US", 1) for index in range(12)]

        seed(disabled_rows)
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["coverage"] == {"measured_buckets": 24, "expected_buckets": 12}
        assert body["status"] == "OK"

    def test_a_bucket_that_has_not_settled_is_not_expected(self, db_schema) -> None:
        now = datetime.now(UTC)
        top = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
        since = top - timedelta(minutes=30)

        def rows_for(inst):  # type: ignore[no-untyped-def]
            return [
                GeoCountryBucket(
                    instance_id=inst.id, bucket_start=since + timedelta(minutes=5 * i),
                    bucket_seconds=300, direction="SOURCE", country_code="US", events=1,
                    measurement="EXACT", query_version="geo-country-v1", collected_at=now,
                )
                for i in range(5)
            ]

        seed(rows_for)
        body = Client(VIEWER).get(
            URL,
            params=params(
                since=since.isoformat(), until=(top + timedelta(minutes=5)).isoformat()
            ),
        ).json()
        assert body["coverage"]["expected_buckets"] <= 6
        assert body["coverage"]["measured_buckets"] == 5


class TestContent:
    def test_countries_are_sorted_and_the_share_excludes_the_remainders(self, full) -> None:
        client = Client(VIEWER)
        # The rows are lookup rows and the note is the lookup sentence (GEO-3).
        client.http.app.dependency_overrides[get_settings] = lambda: Settings(
            geo_country_source="lookup"
        )
        body = client.get(URL, params=params()).json()
        assert body["countries"] == [
            {"country_code": "US", "events": 720, "share": 0.75},
            {"country_code": "DE", "events": 240, "share": 0.25},
        ]
        assert body["remainders"] == {"UNRESOLVED": 120, "OVER_CAP": 0, "UNPARSEABLE": 0}
        assert sum(c["share"] for c in body["countries"]) == pytest.approx(1.0)
        assert body["unresolved_note"] == UNRESOLVED_NOTE
        assert body["direction"] == "SOURCE"

    def test_all_three_remainders_are_reported_beside_the_countries(self, db_schema) -> None:
        seed(lambda inst: [
            row(inst, 0, "US", 10),
            row(inst, 0, None, 1, kind="UNRESOLVED"),
            row(inst, 0, None, 2, kind="OVER_CAP"),
            row(inst, 0, None, 3, kind="UNPARSEABLE"),
        ])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["remainders"] == {"UNRESOLVED": 1, "OVER_CAP": 2, "UNPARSEABLE": 3}
        assert body["countries"] == [{"country_code": "US", "events": 10, "share": 1.0}]

    def test_ties_are_broken_by_code(self, db_schema) -> None:
        seed(lambda inst: [row(inst, 0, "ZA", 5), row(inst, 0, "AU", 5), row(inst, 0, "FR", 5)])
        body = Client(VIEWER).get(URL, params=params()).json()
        codes = [c["country_code"] for c in body["countries"]]
        assert codes == ["AU", "FR", "ZA"]

    def test_measurement_is_exact_unless_any_bucket_is_a_lower_bound(self, db_schema) -> None:
        seed(lambda inst: [row(inst, 0, "US", 5), row(inst, 1, "US", 5)])
        assert Client(VIEWER).get(URL, params=params()).json()["measurement"] == "EXACT"

    def test_one_lower_bound_bucket_makes_the_range_a_lower_bound(self, db_schema) -> None:
        seed(lambda inst: [row(inst, 0, "US", 5), row(inst, 1, "US", 5, m="LOWER_BOUND")])
        assert Client(VIEWER).get(URL, params=params()).json()["measurement"] == "LOWER_BOUND"

    def test_a_lower_bound_in_the_other_direction_does_not_leak(self, db_schema) -> None:
        seed(lambda inst: [
            row(inst, 0, "US", 5),
            row(inst, 0, "US", 5, direction="DESTINATION", m="LOWER_BOUND"),
        ])
        assert Client(VIEWER).get(URL, params=params()).json()["measurement"] == "EXACT"

    def test_two_statements_whatever_the_data(self, full) -> None:
        client = Client(VIEWER)
        assert client.get(URL, params=params()).status_code == 200
        assert client.statements == 2

    def test_two_statements_when_nothing_is_collected(self, db_schema) -> None:
        client = Client(VIEWER)
        assert client.get(URL, params=params()).status_code == 200
        assert client.statements == 2

    def test_not_collected_carries_no_measurement_and_no_remainder_zeros(self, db_schema) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "NOT_COLLECTED"
        assert body["countries"] == []
        assert body["measurement"] is None
        assert body["remainders"] is None

    def test_a_measured_range_reports_a_measurement_and_true_zero_remainders(
        self, db_schema
    ) -> None:
        seed(lambda inst: [row(inst, 0, "US", 10), row(inst, 0, None, 2, kind="UNRESOLVED")])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "PARTIAL"
        assert body["measurement"] == "EXACT"
        assert body["remainders"] == {"UNRESOLVED": 2, "OVER_CAP": 0, "UNPARSEABLE": 0}


class TestSource:
    def test_the_default_is_the_property_and_names_it(self, full) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["country_source"] == "PROPERTY"
        assert body["country_property"] == "Source Geographic Location"
        assert body["unresolved_note"] == (
            "UNRESOLVED counts events whose country property was empty."
        )
        assert body["unresolved_note_code"] == "geo.note.unresolved_property"

    def test_a_direction_with_no_property_has_no_name(self, full) -> None:
        body = Client(VIEWER).get(URL, params=params(direction="DESTINATION")).json()
        assert body["country_source"] == "PROPERTY"
        assert body["country_property"] is None
        assert body["status"] == "NOT_COLLECTED"

    def test_the_configured_destination_property_is_named(self, full) -> None:
        client = Client(VIEWER)
        client.http.app.dependency_overrides[get_settings] = lambda: Settings(
            geo_destination_country_property="Destination Country"
        )
        body = client.get(URL, params=params(direction="DESTINATION")).json()
        assert body["country_property"] == "Destination Country"

    def test_lookup_mode_reports_lookup_and_no_property_even_when_one_is_set(self, full) -> None:
        client = Client(VIEWER)
        client.http.app.dependency_overrides[get_settings] = lambda: Settings(
            geo_country_source="lookup"
        )
        body = client.get(URL, params=params()).json()
        assert body["country_source"] == "LOOKUP"
        assert body["country_property"] is None
        assert body["unresolved_note"] == UNRESOLVED_NOTE
        assert body["unresolved_note_code"] == "geo.note.unresolved_lookup"

    def test_a_property_mode_run_is_served_with_its_query_version(self, db_schema) -> None:
        def property_rows(inst):  # type: ignore[no-untyped-def]
            rows = [row(inst, 0, "TR", 9)]
            rows[0].query_version = "geo-country-property-v1"
            return rows

        seed(property_rows)
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["countries"][0]["country_code"] == "TR"
        assert body["country_source"] == "PROPERTY"


class TestLatestBucketEnd:
    def test_it_is_the_end_of_the_newest_measured_bucket(self, full) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert datetime.fromisoformat(body["latest_bucket_end"]) == HOUR + timedelta(hours=1)

    def test_it_follows_the_newest_bucket_not_the_range_end(self, db_schema) -> None:
        seed(lambda inst: [row(inst, 0, "US", 10), row(inst, 3, "DE", 4)])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert datetime.fromisoformat(body["latest_bucket_end"]) == HOUR + timedelta(minutes=20)

    def test_it_is_per_direction(self, db_schema) -> None:
        seed(
            lambda inst: [
                row(inst, 0, "US", 10),
                row(inst, 7, "US", 10, direction="DESTINATION"),
            ]
        )
        source = Client(VIEWER).get(URL, params=params()).json()
        destination = Client(VIEWER).get(URL, params=params(direction="DESTINATION")).json()
        assert datetime.fromisoformat(source["latest_bucket_end"]) == HOUR + timedelta(minutes=5)
        assert datetime.fromisoformat(destination["latest_bucket_end"]) == HOUR + timedelta(
            minutes=40
        )

    def test_it_is_null_when_nothing_is_collected(self, db_schema) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "NOT_COLLECTED"
        assert body["latest_bucket_end"] is None


def tagged(inst, index, source, prop, **kw):  # type: ignore[no-untyped-def]
    """A country row of one bucket that records how it was collected."""
    out = row(inst, index, "US", 10, **kw)
    out.country_source = source
    out.country_property = prop
    return out


class TestCollectedWith:
    """GEO-6: how the plotted buckets were collected, not what the setting says now."""

    def test_one_source_in_the_range(self, db_schema) -> None:
        def rows(inst):  # type: ignore[no-untyped-def]
            out = []
            for index in range(3):
                out += [
                    tagged(inst, index, "PROPERTY", "Source Geographic Location"),
                    # A second row of the same bucket is still one bucket.
                    row(inst, index, "DE", 5),
                ]
                out[-1].country_source = "PROPERTY"
                out[-1].country_property = "Source Geographic Location"
            return out

        seed(rows)
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["collected_with"] == {
            "ways": [
                {
                    "country_source": "PROPERTY",
                    "country_property": "Source Geographic Location",
                    "buckets": 3,
                }
            ],
            "unknown_buckets": 0,
        }

    def test_two_sources_are_listed_most_buckets_first(self, db_schema) -> None:
        seed(
            lambda inst: [
                tagged(inst, 0, "LOOKUP", None),
                tagged(inst, 1, "PROPERTY", "Source Geographic Location"),
                tagged(inst, 2, "PROPERTY", "Source Geographic Location"),
            ]
        )
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["collected_with"]["ways"] == [
            {
                "country_source": "PROPERTY",
                "country_property": "Source Geographic Location",
                "buckets": 2,
            },
            {"country_source": "LOOKUP", "country_property": None, "buckets": 1},
        ]
        assert body["collected_with"]["unknown_buckets"] == 0

    def test_the_same_source_with_another_property_is_another_way(self, db_schema) -> None:
        seed(
            lambda inst: [
                tagged(inst, 0, "PROPERTY", "Source Geographic Location"),
                tagged(inst, 1, "PROPERTY", "Other Country"),
            ]
        )
        ways = Client(VIEWER).get(URL, params=params()).json()["collected_with"]["ways"]
        assert [(w["country_property"], w["buckets"]) for w in ways] == [
            ("Other Country", 1),
            ("Source Geographic Location", 1),
        ]

    def test_buckets_stored_before_the_columns_existed_are_counted_as_unknown(
        self, db_schema
    ) -> None:
        seed(
            lambda inst: [
                row(inst, 0, "US", 10),
                row(inst, 0, None, 3, kind="UNRESOLVED"),
                row(inst, 1, "US", 10),
                tagged(inst, 2, "LOOKUP", None),
            ]
        )
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["collected_with"] == {
            "ways": [{"country_source": "LOOKUP", "country_property": None, "buckets": 1}],
            "unknown_buckets": 2,
        }

    def test_only_unknown_buckets_name_no_way(self, db_schema) -> None:
        seed(lambda inst: [row(inst, 0, "US", 10)])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["collected_with"] == {"ways": [], "unknown_buckets": 1}

    def test_it_follows_the_range_and_the_direction(self, db_schema) -> None:
        seed(
            lambda inst: [
                tagged(inst, 0, "LOOKUP", None),
                tagged(inst, 1, "PROPERTY", "P", direction="DESTINATION"),
                # Outside the range.
                tagged(inst, 24, "PROPERTY", "Elsewhere"),
            ]
        )
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["collected_with"]["ways"] == [
            {"country_source": "LOOKUP", "country_property": None, "buckets": 1}
        ]

    def test_two_instances_count_their_buckets_apart(self, db_schema) -> None:
        async def _seed(s):  # type: ignore[no-untyped-def]
            for _ in range(2):
                inst = await make_instance(s)
                s.add(tagged(inst, 0, "LOOKUP", None))
            await s.flush()

        run(_seed)
        ways = Client(VIEWER).get(URL, params=params()).json()["collected_with"]["ways"]
        assert ways == [{"country_source": "LOOKUP", "country_property": None, "buckets": 2}]

    def test_an_empty_range_says_nothing_about_how_it_was_collected(self, db_schema) -> None:
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["status"] == "NOT_COLLECTED"
        assert body["collected_with"] is None

    def test_the_setting_is_kept_beside_it_and_may_differ(self, db_schema) -> None:
        seed(lambda inst: [tagged(inst, 0, "LOOKUP", None)])
        body = Client(VIEWER).get(URL, params=params()).json()
        assert body["country_source"] == "PROPERTY"
        assert body["collected_with"]["ways"][0]["country_source"] == "LOOKUP"

    def test_the_statement_count_does_not_grow_with_the_ways_or_the_buckets(
        self, db_schema
    ) -> None:
        def many(inst):  # type: ignore[no-untyped-def]
            return [
                tagged(inst, index, "PROPERTY", f"Property {index % 5}") for index in range(12)
            ]

        seed(many)
        client = Client(VIEWER)
        body = client.get(URL, params=params()).json()
        assert len(body["collected_with"]["ways"]) == 5
        assert client.statements == 2


class TestRange:
    @pytest.mark.parametrize(
        "over",
        [
            {"since": "2026-10-13T12:00:00", "until": "2026-10-13T13:00:00+00:00"},
            {"since": "2026-10-13T12:00:00+00:00", "until": "2026-10-13T13:00:00"},
            {"since": "2026-10-13T13:00:00+00:00", "until": "2026-10-13T12:00:00+00:00"},
            {"since": "2026-10-13T12:00:00+00:00", "until": "2026-10-13T12:00:00+00:00"},
            {"since": "2024-01-01T00:00:00+00:00", "until": "2026-10-13T12:00:00+00:00"},
            {"since": "not a date"},
            {"direction": "BOTH"},
            {"direction": "sourceip"},
        ],
    )
    def test_a_bad_range_or_direction_is_422(self, db_schema, over: dict) -> None:
        assert Client(VIEWER).get(URL, params=params(**over)).status_code == 422

    @pytest.mark.parametrize("missing", ["since", "until", "direction"])
    def test_every_parameter_is_required(self, db_schema, missing: str) -> None:
        query = params()
        del query[missing]
        assert Client(VIEWER).get(URL, params=query).status_code == 422

    def test_the_widest_allowed_range_is_accepted(self, db_schema) -> None:
        until = datetime(2026, 10, 13, tzinfo=UTC)
        query = params(since=(until - timedelta(days=365)).isoformat(), until=until.isoformat())
        assert Client(VIEWER).get(URL, params=query).status_code == 200


class TestCadenceFamily:
    def test_the_family_is_listed_last_with_the_collection_bucket(self, db_schema) -> None:
        body = Client(VIEWER).get("/api/v1/admin/search-cadence").json()
        by_family = {item["family"]: item for item in body}
        # D-42 appended QRADAR_HEALTH; SOC-10b appended IDENTITY_COLLECTION.
        assert [item["family"] for item in body][-3:] == [
            "GEO_COLLECTION",
            "QRADAR_HEALTH",
            "IDENTITY_COLLECTION",
        ]
        assert by_family["GEO_COLLECTION"]["bucket_seconds"] == 300
        assert by_family["GEO_COLLECTION"]["cadence_seconds"] == 300
        assert by_family["IDENTITY_COLLECTION"]["cadence_seconds"] == 900
        assert by_family["IDENTITY_COLLECTION"]["bucket_seconds"] == 300
