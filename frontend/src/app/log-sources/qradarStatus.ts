// How a log source's QRadar-reported status is toned and labelled, shared by the inventory
// and the detail page so the two never disagree.

/** SUCCESS is healthy, WARNING a warning, ERROR a problem. Any other reported value (DISABLED,
 *  NA, ...) is not a verdict on the source, and neither is a status QRadar never reported:
 *  both are neutral. */
export function qradarStatusTone(status: string | null): "ok" | "warn" | "crit" | "" {
  if (status === "SUCCESS") return "ok";
  if (status === "WARNING") return "warn";
  if (status === "ERROR") return "crit";
  return "";
}

/** The status as QRadar reported it; a status never reported reads "unknown". */
export function qradarStatusLabel(status: string | null): string {
  return status ?? "unknown";
}
