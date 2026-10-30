// Whether an empty log-source fleet may be drawn as a measured zero.
//
// A failed `log_source_inventory` watermark is an error, and a watermark that
// was never collected is not collected. Neither is a count of zero. A clean
// watermark is a real empty inventory. Sources that exist are shown even when
// the latest sync failed: those counts are a measurement. While the overview
// is still loading, wait, so a zero is not painted first. A refused or failed
// overview read is not treated as a failed inventory; the card already says
// the total is missing.

import type { ResourceState } from "@/hooks/useApiResource";
import type { SocOverview } from "@/lib/api";

export type InventoryHonesty = "show" | "wait" | "failed" | "not_collected";

export function inventoryHonesty(
  sourceCount: number,
  overview: ResourceState<SocOverview>,
): InventoryHonesty {
  if (sourceCount > 0) return "show";
  if (overview.status === "idle" || overview.status === "loading") return "wait";
  if (overview.status !== "ok") return "show";
  const state = overview.data.log_sources.inventory_state;
  if (state === "FAILED") return "failed";
  if (state === "NOT_COLLECTED") return "not_collected";
  return "show";
}
