// The admission-overflow range of `/behavior/waf`.
//
// Overflow is summed over the collected buckets in a range, so the range is the
// question. Three bounded options; the backend caps a range at its own maximum,
// and none of these gets near it.

export const WAF_RANGES = [
  { key: "24h", label: "24 hours", hours: 24 },
  { key: "7d", label: "7 days", hours: 24 * 7 },
  { key: "30d", label: "30 days", hours: 24 * 30 },
] as const;

export type WafRangeKey = (typeof WAF_RANGES)[number]["key"];

export function parseWafRange(value: string | undefined): (typeof WAF_RANGES)[number] {
  return WAF_RANGES.find((r) => r.key === value) ?? WAF_RANGES[0];
}
