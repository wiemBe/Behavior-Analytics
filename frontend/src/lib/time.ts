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

/**
 * The age words of the interface language: the translator of the `format` namespace
 * (`underMinuteAgo`, `minutesAgo`, `hoursAgo`, `daysAgo`). Without one every function here keeps
 * its English, so a caller that is not converted yet is unchanged.
 */
export type AgeWords = (key: string, values?: { count: number }) => string;

/** "under 1 min ago", "12 min ago", "3 h ago", "2 d ago". */
export function relativeAgeFromSeconds(
  seconds: number | null | undefined,
  say?: AgeWords,
): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return MISSING;
  const whole = Math.floor(seconds);
  if (whole < 60) return say ? say("underMinuteAgo") : "under 1 min ago";
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return say ? say("minutesAgo", { count: minutes }) : `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return say ? say("hoursAgo", { count: hours }) : `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return say ? say("daysAgo", { count: days }) : `${days} d ago`;
}

/**
 * The age of an instant, after its absolute UTC time, never instead of it.
 * A missing, unparsable or future instant is an em dash: a future clock skew
 * is not "0 min ago".
 */
export function relativeAge(
  iso: string | null | undefined,
  now: Date | number,
  say?: AgeWords,
): string {
  if (!iso) return MISSING;
  const then = Date.parse(iso);
  const nowMs = typeof now === "number" ? now : now.getTime();
  if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return MISSING;
  const seconds = Math.floor((nowMs - then) / 1000);
  if (seconds < 0) return MISSING;
  return relativeAgeFromSeconds(seconds, say);
}

/**
 * How many consecutive units a duration shows. Two keeps an age readable: "1 d" or "1 d 3 h",
 * not "1 d 7 min 39 s", where the seconds after a day are noise.
 */
export const DURATION_UNITS = 2;

/** Enough units to show every part of a duration. */
export const EXACT_UNITS = 4;

/**
 * The parts of a whole number of seconds, largest first, cut to `units` consecutive units that
 * start at the largest non-zero one. Zero parts inside the window are dropped. The rest is
 * floored away, so an age is never overstated. Shared with `i18n/measures.durationText`.
 */
export function durationParts(
  total: number,
  units: number,
): { unit: "d" | "h" | "min" | "s"; count: number }[] {
  const all = [
    { unit: "d" as const, count: Math.floor(total / 86400) },
    { unit: "h" as const, count: Math.floor((total % 86400) / 3600) },
    { unit: "min" as const, count: Math.floor((total % 3600) / 60) },
    { unit: "s" as const, count: total % 60 },
  ];
  const first = all.findIndex((part) => part.count > 0);
  if (first < 0) return [];
  return all.slice(first, first + units).filter((part) => part.count > 0);
}

/**
 * A duration, at most two units: "0 s", "59 s", "1 min", "3 h 20 min", "2 d 4 h". Smaller units
 * are floored away ("1 d 7 min 39 s" is "1 d"), zero parts are dropped. A measured zero is
 * "0 s". Anything that is not a measured duration is an em dash. For an age or a measured
 * span. A configured value (a cadence, an interval) uses `formatExactDuration`.
 */
export function formatDuration(totalSeconds: number | null | undefined): string {
  return spell(totalSeconds, DURATION_UNITS);
}

/** Every non-zero unit, "1 h 1 s": a configured value must not be shortened. */
export function formatExactDuration(totalSeconds: number | null | undefined): string {
  return spell(totalSeconds, EXACT_UNITS);
}

function spell(totalSeconds: number | null | undefined, units: number): string {
  if (typeof totalSeconds !== "number" || !Number.isFinite(totalSeconds) || totalSeconds < 0) {
    return MISSING;
  }
  const total = Math.floor(totalSeconds);
  if (total === 0) return "0 s";
  return durationParts(total, units)
    .map((part) => `${part.count} ${part.unit}`)
    .join(" ");
}

/** The same duration spelling. Offense ages and stale-data ages use this name. */
export function formatAge(seconds: number | null | undefined): string {
  return formatDuration(seconds);
}
