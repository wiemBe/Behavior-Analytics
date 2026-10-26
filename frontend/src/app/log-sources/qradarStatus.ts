// How a log source's QRadar-reported status is toned and labelled, shared by the inventory
// and the detail page so the two never disagree.

import type { TextTranslator } from "@/i18n/textTranslator";

/** SUCCESS is healthy, WARNING a warning, ERROR a problem. Any other reported value (DISABLED,
 *  NA, ...) is not a verdict on the source, and neither is a status QRadar never reported:
 *  both are neutral. */
export function qradarStatusTone(status: string | null): "ok" | "warn" | "crit" | "" {
  if (status === "SUCCESS") return "ok";
  if (status === "WARNING") return "warn";
  if (status === "ERROR") return "crit";
  return "";
}

/**
 * The status as QRadar reported it; a status never reported reads "unknown". With a translator the
 * values the catalog knows (`logSources.status`) read in the interface language; a status it does
 * not know is shown as QRadar sent it, never as an empty string.
 */
export function qradarStatusLabel(status: string | null, say?: TextTranslator): string {
  if (status === null) return say ? say("logSources.status.unknown") : "unknown";
  const key = `logSources.status.${status}`;
  return say && status !== "unknown" && !status.includes(".") && say.has?.(key) ? say(key) : status;
}
