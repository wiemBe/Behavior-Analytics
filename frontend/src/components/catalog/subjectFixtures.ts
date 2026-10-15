// Builders for the subject-chart and IPS widget tests (UI-3.5b): bucket series and baseline
// cells in the backend's real shapes (`MetricBucketOut`, `BaselineCellOut`).

import type { BaselineCell, BucketSeries, MetricBucket, WafMetricBucket } from "@/lib/api";

export const SRC = "5c000000-0000-4000-8000-000000000001";
export const EP = "e9000000-0000-4000-8000-000000000001";
export const SOURCE = "a1000000-0000-4000-8000-000000000001";

/** 2026-10-12 is a Monday: ISO weekday 1. */
export const T0 = "2026-10-12T09:00:00Z";
const at = (minutes: number) => new Date(Date.parse(T0) + minutes * 60_000).toISOString();

export function eps(minutes: number, over: Partial<MetricBucket> = {}): MetricBucket {
  return {
    bucket_start: at(minutes),
    bucket_seconds: 300,
    event_count: 30_000,
    average_eps: 100,
    peak_eps: null,
    completeness: "COMPLETE",
    last_event_at: at(minutes + 4),
    ...over,
  };
}

export function epsCell(over: Partial<BaselineCell> = {}): BaselineCell {
  return {
    metric_name: "average_eps",
    weekday: 1,
    hour: 9,
    median: 100,
    mad: 8,
    p05: 70,
    p95: 135,
    sample_count: 24,
    is_reliable: true,
    completeness: 0.95,
    baseline_version: 3,
    computed_at: "2026-10-12T03:00:00Z",
    ...over,
  };
}

export function series<B>(buckets: B[], over: Partial<BucketSeries<B>> = {}): BucketSeries<B> {
  return { buckets, total: buckets.length, truncated: false, ...over };
}

export function reqAt(minutes: number, over: Partial<WafMetricBucket> = {}): WafMetricBucket {
  return {
    bucket_start: at(minutes),
    bucket_seconds: 300,
    request_count: 41,
    request_count_measurement: "EXACT",
    completeness: "COMPLETE",
    partial: false,
    endpoint_coverage_ratio: 0.97,
    in_maintenance: false,
    normalizer_version: 2,
    distinct_client_count: 14,
    ...over,
  };
}
