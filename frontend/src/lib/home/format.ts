// Number formatting the Home cards share.

/** A percentage with a sign and a true minus (U+2212): "+3.2 %", "−3.9 %", "0.0 %". */
export function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(1)} %`;
}
