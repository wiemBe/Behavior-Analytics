// One relative age and one duration, for every screen.
//
// Unit style is the guidelines' (§10 "12 min ago"): a number, a space, then
// the unit. Units are min, h, d and s — "min" rather than "m", because that
// is the form the guidelines write. A relative age uses the largest unit
// ("12 min ago", "3 h ago", "2 d ago") and says "under 1 min ago" below a
// minute. A duration keeps every non-zero part ("3 h 20 min", "2 d 4 h",
// "45 s"). Null, non-finite and negative input is an em dash, never "0".
// A measured duration of zero is "0 s", because that zero was measured.

const MISSING = "—";

/** "under 1 min ago", "12 min ago", "3 h ago", "2 d ago". */
export function relativeAgeFromSeconds(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return MISSING;
  const whole = Math.floor(seconds);
  if (whole < 60) return "under 1 min ago";
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/**
 * The age of an instant, after its absolute UTC time, never instead of it.
 * A missing, unparsable or future instant is an em dash: a future clock skew
 * is not "0 min ago".
 */
export function relativeAge(iso: string | null | undefined, now: Date | number): string {
  if (!iso) return MISSING;
  const then = Date.parse(iso);
  const nowMs = typeof now === "number" ? now : now.getTime();
  if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return MISSING;
  const seconds = Math.floor((nowMs - then) / 1000);
  if (seconds < 0) return MISSING;
  return relativeAgeFromSeconds(seconds);
}

/**
 * A duration: "0 s", "59 s", "1 min", "3 h 20 min", "2 d 4 h".
 * Zero parts are dropped. A measured zero is "0 s". Anything that is not a
 * measured duration is an em dash.
 */
export function formatDuration(totalSeconds: number | null | undefined): string {
  if (typeof totalSeconds !== "number" || !Number.isFinite(totalSeconds) || totalSeconds < 0) {
    return MISSING;
  }
  const total = Math.round(totalSeconds);
  if (total === 0) return "0 s";
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts: string[] = [];
  if (days) parts.push(`${days} d`);
  if (hours) parts.push(`${hours} h`);
  if (minutes) parts.push(`${minutes} min`);
  if (seconds) parts.push(`${seconds} s`);
  return parts.join(" ");
}

/** The same duration spelling. Offense ages and stale-data ages use this name. */
export function formatAge(seconds: number | null | undefined): string {
  return formatDuration(seconds);
}
