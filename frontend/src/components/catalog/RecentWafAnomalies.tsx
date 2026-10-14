"use client";

// Recent WAF anomalies (UR.5.1): the Home's recent-anomalies table, narrowed to WAF endpoints.
// It wraps `RecentAnomalies` with a subject; it is not a second table.

import { RecentAnomalies } from "@/components/home/RecentAnomalies";

export function RecentWafAnomalies() {
  return <RecentAnomalies subject="WAF_ENDPOINT" />;
}
