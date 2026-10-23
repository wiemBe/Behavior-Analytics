// Staleness of a widget's data (UR.5.4): derived, never stored.

/** The cadence assumed when the family's is unknown: 2 h (UR.5.4). */
const DEFAULT_CADENCE_SECONDS = 2 * 60 * 60;

interface Staleness {
  stale: boolean;
  /** Whole seconds since `asOf`, never negative; null when `asOf` cannot be read. */
  ageSeconds: number | null;
}

/**
 * Stale when `asOf` is older than twice the collection cadence (UR.5.4). With an unknown
 * cadence the default of 2 h is the cadence, so the threshold is 4 h.
 * A time that cannot be read is stale: a widget must not vouch for data whose age
 * it cannot state. A time ahead of `now` (clock skew) is fresh, with an age of zero.
 */
export function deriveStale(
  asOf: string,
  cadenceSeconds: number | null,
  now: number | Date,
): Staleness {
  const asOfMs = Date.parse(asOf);
  if (Number.isNaN(asOfMs)) return { stale: true, ageSeconds: null };
  const nowMs = typeof now === "number" ? now : now.getTime();
  const ageSeconds = Math.max(0, Math.floor((nowMs - asOfMs) / 1000));
  const cadence =
    cadenceSeconds !== null && cadenceSeconds > 0 ? cadenceSeconds : DEFAULT_CADENCE_SECONDS;
  return { stale: ageSeconds > 2 * cadence, ageSeconds };
}
