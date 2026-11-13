"""Stored identity top-N rows are the only usernames that become mentions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.identity.fold import (
    CountRow,
    CountSearch,
    NameRow,
    NameSearch,
    SeriesKey,
    fold_tick,
)
from app.models.enums import IDENTITY_BUCKET_SECONDS
from app.services.entity_mention_folds import identity_top_mentions

START = datetime(2026, 11, 13, 11, 55, tzinfo=UTC)


def _mentions_for_fold():
    source = uuid.uuid4()
    scope = [SeriesKey(source, 4625, START), SeriesKey(source, 1102, START)]
    folded = fold_tick(
        scope,
        CountSearch(
            True,
            False,
            (
                CountRow(source, 4625, START, 9),
                CountRow(source, 1102, START, 1),
            ),
        ),
        NameSearch(True, False, (NameRow(source, 1102, START, "Auditor", 1),)),
        NameSearch(
            True,
            False,
            (
                NameRow(source, 4625, START, "alice", 5),
                NameRow(source, 4625, START, "bob", 1),
                NameRow(source, 4625, START, "carol", 3),
                NameRow(source, 4625, START, "DOMAIN\\Alice", 4),
            ),
        ),
    )
    mentions = []
    for fact in folded.buckets:
        for series in fact.series:
            mentions.extend(
                identity_top_mentions(
                    uuid.uuid4(),
                    log_source_id=series.log_source_id,
                    event_id=series.event_id,
                    bucket_start=series.bucket_start,
                    bucket_end=series.bucket_start + timedelta(seconds=IDENTITY_BUCKET_SECONDS),
                    usernames=series.usernames,
                )
            )
    return mentions


def test_a_name_below_the_floor_never_becomes_a_mention() -> None:
    mentions = _mentions_for_fold()
    norms = {mention.value_norm for mention in mentions}
    assert "bob" not in norms
    assert norms == {"alice", "carol", "domain\\alice", "auditor"}
    assert all(mention.kind == "USER" for mention in mentions)
    assert all(mention.role == "USER" for mention in mentions)
    assert all(mention.source == "IDENTITY_TOP" for mention in mentions)
    assert all(mention.ref_table == "identity_top_username" for mention in mentions)
    # One stored form. user_match_forms would also have emitted the account "alice".
    assert sum(mention.value_norm == "domain\\alice" for mention in mentions) == 1
    assert all(
        mention.occurred_end == START + timedelta(seconds=IDENTITY_BUCKET_SECONDS)
        for mention in mentions
    )
