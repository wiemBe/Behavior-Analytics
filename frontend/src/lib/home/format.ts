// Small formatters the Home tables share. A time carries its zone and its age.

/**
 * "12 min ago". Null when the instant is unusable or lies in the future, so a
 * clock skew prints no age rather than a wrong one.
 */
export function formatAgo(iso: string | null | undefined, nowMs: number): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t) || t > nowMs) return null;
  const seconds = Math.floor((nowMs - t) / 1000);
  if (seconds < 60) return "under 1 min ago";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/** A percentage with a sign and a true minus (U+2212): "+3.2 %", "−3.9 %", "0.0 %". */
export function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(1)} %`;
}
