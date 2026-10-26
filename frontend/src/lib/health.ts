// Presentation rules for rule-health and coverage verdicts.
//
// The central honesty rule of this platform lives here: a verdict meaning "we
// have not established this" must never be rendered in the same visual language
// as a verdict meaning "this is broken", and neither may be rendered as if it
// were "this is fine". An unevaluated technique shown in green is a false
// assurance; shown in red it is a false alarm. Both get their own neutral tone.

export type Tone = "ok" | "warn" | "crit" | "";

/** Verdicts that mean "not enough evidence", not "no detection". */
const UNESTABLISHED = new Set([
  "INSUFFICIENT_DATA",
  "NOT_EVALUATED",
  "UNKNOWN",
]);

export function isUnestablished(status: string | null | undefined): boolean {
  return status != null && UNESTABLISHED.has(status);
}

export function healthTone(status: string | null | undefined): Tone {
  if (status == null) return "";
  if (isUnestablished(status)) return ""; // neutral: an absence of evidence
  switch (status) {
    case "HEALTHY":
    case "COVERED":
      return "ok";
    case "NOISY":
    case "INACTIVE":
    case "DEGRADED":
    case "PARTIAL":
    case "DEPENDENCY_DEGRADED":
      return "warn";
    case "NEVER_OBSERVED":
    case "MISSING":
    case "NOT_COVERED":
      return "crit";
    case "DISABLED":
      return "warn";
    default:
      return "";
  }
}

/** The English of `healthMeaning`, for callers that pass no translator (and for the bundle of every
 *  page that imports this file: the catalog is not imported here). */
const MEANING: Record<string, string> = {
  INSUFFICIENT_DATA: "Not enough collected evidence to judge — this is not a finding.",
  NOT_EVALUATED: "No evaluation has run for this technique yet.",
  HEALTHY: "Firing within expected bounds.",
  DISABLED: "Disabled in QRadar, so it cannot contribute detection.",
  NOISY: "Firing far above its expected rate.",
  INACTIVE: "Has fired before, but not recently.",
  NEVER_OBSERVED: "Observed continuously and never seen to fire.",
  DEPENDENCY_DEGRADED: "A building block or log source it depends on is unhealthy.",
  COVERED: "At least one healthy enabled rule maps to this technique.",
  PARTIAL: "Mapped, but the contributing detection is not fully healthy.",
  DEGRADED: "Mapped rules exist but are impaired.",
  MISSING: "No rule maps to this technique.",
};

/**
 * Operator-facing explanation of what a verdict actually asserts. With a translator (the
 * `behavior.health.meaning` messages) it reads in the interface language; the rules, offenses and
 * coverage pages pass none until their wave is merged and keep today's English.
 */
export function healthMeaning(
  status: string | null | undefined,
  t?: (key: string) => string,
): string {
  if (status == null || !Object.hasOwn(MEANING, status)) return "";
  return t ? t(`behavior.health.meaning.${status}`) : MEANING[status];
}

export function magnitudeTone(magnitude: number | null | undefined): Tone {
  if (magnitude == null) return "";
  if (magnitude >= 7) return "crit";
  if (magnitude >= 4) return "warn";
  // Below 4 is not a good outcome. A low score stays neutral, never ok.
  return "";
}

/** Only a closed offense is a good outcome. OPEN stays a warning; anything else is neutral. */
export function offenseStatusTone(status: string | null | undefined): Tone {
  if (status === "CLOSED") return "ok";
  if (status === "OPEN") return "warn";
  return "";
}

export { formatAge } from "@/lib/time";

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * An instant as `2026-10-12 09:15:00 UTC`.
 *
 * Always UTC and always says so. The locale default rendered the viewer's
 * timezone without naming it, and on a server-rendered page that is the
 * server's timezone, which differs from the browser's. QRadar and the backend
 * both report UTC, so the same instant now reads the same everywhere.
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
  );
}

/**
 * A timestamp for a dense table column, as `10-12 09:15 UTC`.
 *
 * Drops the year and the seconds so the value stays on one line in a narrow
 * column, and keeps the timezone. It is for table cells only: anywhere the
 * exact instant matters, `formatDateTime` remains the right function.
 */
export function formatDateTimeCompact(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return (
    `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}
