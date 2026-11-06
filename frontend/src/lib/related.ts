// Helpers for the Related activity section of an anomaly page (SOC-3b): the pad choices and how
// the URL names them, the window the heading states, and the age of a stale list.

import type { RelatedPad } from "@/lib/api";

/** The pads the control offers, shortest first. */
export const RELATED_PADS: readonly RelatedPad[] = ["30m", "1h", "2h", "6h"];
export const DEFAULT_PAD: RelatedPad = "2h";

/** The pad a `?pad=` value names. An absent or unknown value is the default, never an error. */
export function parsePad(value: string | string[] | undefined): RelatedPad {
  const raw = Array.isArray(value) ? value[0] : value;
  return RELATED_PADS.find((pad) => pad === raw) ?? DEFAULT_PAD;
}

const two = (n: number) => String(n).padStart(2, "0");

function clock(d: Date): string {
  return `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;
}

function dayOf(d: Date): string {
  return `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())}`;
}

/**
 * The window as the heading states it, always in UTC: `16:30–20:30 UTC` inside one UTC day, and
 * `2026-11-05 22:00 – 2026-11-06 02:00 UTC` across midnight, so a window never reads backwards.
 * An unreadable bound gives `null`: the heading then names no window rather than a wrong one.
 */
export function windowText(start: string, end: string): string | null {
  const a = new Date(start);
  const b = new Date(end);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null;
  if (dayOf(a) === dayOf(b)) return `${clock(a)}–${clock(b)} UTC`;
  return `${dayOf(a)} ${clock(a)} – ${dayOf(b)} ${clock(b)} UTC`;
}

/**
 * The newest `captured_at` among the shown offenses, as epoch milliseconds, or `null` when the
 * list is empty or unreadable. The response carries no collector timestamp, so this is the only
 * age a stale list can honestly name: that of its newest snapshot.
 */
export function newestCapture(captured: readonly string[]): number | null {
  const times = captured.map((c) => Date.parse(c)).filter((n) => !Number.isNaN(n));
  return times.length ? Math.max(...times) : null;
}
