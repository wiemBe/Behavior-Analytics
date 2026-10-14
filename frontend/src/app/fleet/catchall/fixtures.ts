import type { CatchallBucket, CatchallSender, CatchallSourceList } from "@/lib/api";

export const SRC_A = "5a1f0000-0000-4000-8000-00000000a001";
export const SRC_B = "5a1f0000-0000-4000-8000-00000000b002";

export function sender(over: Partial<CatchallSender> = {}): CatchallSender {
  return {
    id: "s1",
    catchall_log_source_id: SRC_A,
    catchall_log_source_name: "SIM Generic Log DSM-7 :: qradar-lab",
    identifier: "fw-edge-03",
    first_seen_at: "2026-10-13T15:00:00Z",
    last_seen_at: "2026-10-13T17:35:00Z",
    events_24h: 18420,
    active: true,
    review_status: "NEW",
    reviewed_by: null,
    reviewed_at: null,
    ...over,
  };
}

export function sources(over: Partial<CatchallSourceList> = {}): CatchallSourceList {
  return {
    catchall_type_ids: [39, 67, 212, 367, 4000],
    items: [
      {
        id: SRC_A,
        name: "SIM Generic Log DSM-7 :: qradar-lab",
        type_name: "SIM Generic Log DSM",
        latest_bucket_start: "2026-10-13T17:30:00Z",
        new_sender_count: 2,
      },
      {
        id: SRC_B,
        name: "Universal LEEF :: leef-gw-eu",
        type_name: "Universal LEEF",
        latest_bucket_start: "2026-10-13T17:30:00Z",
        new_sender_count: 0,
      },
    ],
    ...over,
  };
}

/** A bucket starting `minutes` after 10:00 UTC. */
export function bucket(minutes: number, over: Partial<CatchallBucket> = {}): CatchallBucket {
  return {
    catchall_log_source_id: SRC_A,
    bucket_start: new Date(Date.UTC(2026, 9, 13, 10, minutes)).toISOString(),
    bucket_seconds: 300,
    events: 100,
    records: 10,
    distinct_senders: 4,
    distinct_senders_lower_bound: false,
    measurement: "EXACT",
    top_senders: [{ identifier: "fw-edge-01", events: 60 }],
    admitted: 1,
    below_admission_threshold: 2,
    rejected_over_quota: 3,
    untracked_senders: 5,
    untracked_events: 7,
    query_provenance: {},
    collected_at: "2026-10-13T11:00:00Z",
    ...over,
  };
}
