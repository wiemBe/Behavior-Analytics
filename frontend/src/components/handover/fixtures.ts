// Shared records for the handover tests.

import type { HandoverActedOn, HandoverBody, HandoverItem, HandoverRecord, QueueSource } from "@/lib/api";

export const WRITTEN = "2026-11-11T06:00:00Z";

export function openSource(kind: QueueSource["kind"], over: Partial<QueueSource> = {}): QueueSource {
  return { kind, state: "OK", count: 1, new_count: 0, permission: null, as_of: null, reason_code: null, ...over };
}

export const SOURCES_OK: QueueSource[] = (["anomaly", "alert", "offense", "source_review", "catchall_sender"] as const).map(
  (k) => openSource(k),
);

export function item(over: Partial<HandoverItem> = {}): HandoverItem {
  return {
    kind: "alert",
    family: null,
    id: "al-1",
    link: "/alerts/al-1",
    state: "OPEN",
    owner: null,
    owner_kind: null,
    severity: "HIGH",
    waiting_since: "2026-11-11T05:00:00Z",
    age_seconds: 3600,
    change: null,
    watch: false,
    over_sla: null,
    title: "Gateway volume drop",
    title_code: null,
    title_params: null,
    ...over,
  };
}

export const ACTED: HandoverActedOn[] = [
  {
    kind: "triage",
    id: "ev-1",
    related_id: "an-9",
    link: "/anomalies/an-9",
    event: "STATUS",
    at: "2026-11-11T04:00:00Z",
    from_value: "IN_PROGRESS",
    to_value: "CLOSED",
  },
  {
    kind: "alert",
    id: "al-3:RESOLVED",
    related_id: "al-3",
    link: "/alerts/al-3",
    event: "RESOLVED",
    at: "2026-11-11T05:50:00Z",
    from_value: "ACKNOWLEDGED",
    to_value: "RESOLVED",
  },
];

export function body(over: Partial<HandoverBody> = {}): HandoverBody {
  return {
    as_of: WRITTEN,
    window_started_at: "2026-11-10T22:00:00Z",
    sources: SOURCES_OK,
    total: 3,
    total_exact: true,
    truncated: false,
    items: [
      item({ id: "al-1", watch: true, title: "Watched gateway drop", over_sla: true, change: "NEW" }),
      item({ id: "off-1", kind: "offense", link: "/offenses/1", title: "Offense 4412", severity: "MEDIUM", over_sla: true }),
      item({ id: "al-2", title: "Quiet disk alert", owner: "sam", severity: "LOW", over_sla: false, change: "UPDATED" }),
    ],
    acted_on: ACTED,
    acted_on_sources: [
      { kind: "triage", state: "OK", count: 1, permission: null, reason_code: null },
      { kind: "alert", state: "OK", count: 1, permission: null, reason_code: null },
    ],
    watch_dropped: [],
    ...over,
  };
}

export function record(over: Partial<HandoverRecord> = {}): HandoverRecord {
  return {
    id: "8c1f58a0-44f5-4c20-a968-22dd0300fb73",
    author: "ada",
    written_at: WRITTEN,
    window_started_at: "2026-11-10T22:00:00Z",
    previous_id: null,
    note: "Night shift: the gateway volume followed the release.",
    body: body(),
    ...over,
  };
}
