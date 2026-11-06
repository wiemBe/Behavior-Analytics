// The query, links and figures of `/behavior/noise`.
//
// Everything the screen shows is in the URL: the window, the order and the page. An unknown value
// falls back to the default instead of being forwarded, so a crafted link is never reflected into a
// request or into the page.
//
// URL keys: window (7 | 28, default 28), order (incidents | alerts | noise_share, default
// incidents), offset (a multiple of the page size, default 0).

import type {
  NoiseCurrent,
  NoiseDirection,
  NoiseKnob,
  NoiseOrder,
  NoiseSourceRow,
  NoiseThresholds,
  NoiseWindow,
} from "@/lib/api";

export const NOISE_WINDOWS: readonly NoiseWindow[] = [7, 28];
export const NOISE_ORDERS: readonly NoiseOrder[] = ["incidents", "alerts", "noise_share"];
export const NOISE_PAGE_SIZE = 50;
export const DEFAULT_NOISE_WINDOW: NoiseWindow = 28;
export const DEFAULT_NOISE_ORDER: NoiseOrder = "incidents";

/** The backend withholds the share until this many closed items stand behind it (SOC-17a). */
export const MIN_NOISE_DENOMINATOR = 5;

export interface NoiseQuery {
  window: NoiseWindow;
  order: NoiseOrder;
  offset: number;
}

type Raw = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function parseNoiseQuery(params: Raw): NoiseQuery {
  const rawWindow = Number(first(params.window));
  const window = NOISE_WINDOWS.find((w) => w === rawWindow) ?? DEFAULT_NOISE_WINDOW;
  const rawOrder = first(params.order);
  const order = NOISE_ORDERS.find((o) => o === rawOrder) ?? DEFAULT_NOISE_ORDER;
  const rawOffset = Number(first(params.offset) ?? 0);
  const offset = Number.isFinite(rawOffset) && rawOffset > 0 ? Math.floor(rawOffset) : 0;
  return { window, order, offset };
}

/** The query string of a state, with every default left out so a link stays short. */
export function noiseHref(query: NoiseQuery, change: Partial<NoiseQuery> = {}): string {
  const q = { ...query, ...change };
  const qs = new URLSearchParams();
  if (q.window !== DEFAULT_NOISE_WINDOW) qs.set("window", String(q.window));
  if (q.order !== DEFAULT_NOISE_ORDER) qs.set("order", q.order);
  if (q.offset > 0) qs.set("offset", String(q.offset));
  const text = qs.toString();
  return text ? `/behavior/noise?${text}` : "/behavior/noise";
}

/** The query params `Pagination` carries on its own links: all but the offset. */
export function noisePaginationParams(query: NoiseQuery): Record<string, string | undefined> {
  return {
    window: query.window !== DEFAULT_NOISE_WINDOW ? String(query.window) : undefined,
    order: query.order !== DEFAULT_NOISE_ORDER ? query.order : undefined,
  };
}

/** The source's own behavior page. */
export function sourceHref(row: Pick<NoiseSourceRow, "log_source_id">): string {
  return `/behavior/sources/${encodeURIComponent(row.log_source_id)}`;
}

/** The alert list filtered to the source (SOC-7b key `log_source_id`). */
export function sourceAlertsHref(row: Pick<NoiseSourceRow, "log_source_id">): string {
  return `/alerts?log_source_id=${encodeURIComponent(row.log_source_id)}`;
}

/**
 * Where a row's tuning page is (`/behavior/noise/[id]`). The list's own window, order and page ride
 * along when they are not the defaults, so the page's back link returns to the same list.
 */
export function noiseDetailHref(
  row: Pick<NoiseSourceRow, "log_source_id">,
  query?: NoiseQuery,
): string {
  const base = `/behavior/noise/${encodeURIComponent(row.log_source_id)}`;
  if (!query) return base;
  const qs = noiseHref(query).split("?")[1];
  return qs ? `${base}?${qs}` : base;
}

/** A source that is not monitored has no counts at all: they are null, not zero. */
export function isNotMonitored(row: Pick<NoiseSourceRow, "monitoring_enabled" | "judgement">): boolean {
  return !row.monitoring_enabled || row.judgement === "NOT_COLLECTED";
}

/** A source is shown as judged only when the backend says so and the source is monitored. */
export function isJudged(row: NoiseSourceRow): boolean {
  return row.judged && row.judgement === "JUDGED" && !isNotMonitored(row);
}

// --- The tuning page (SOC-17b) ---------------------------------------------------------------------
// The form, its bounds and the diff against what is in force. The backend is the authority on the
// bounds (`ThresholdKnobs`); these only keep an impossible value from being sent.

export const TUNING_KNOBS = [
  "deviation_z",
  "spike_ratio",
  "drop_ratio",
  "min_absolute_delta_events",
  "open_after",
  "direction",
] as const satisfies readonly NoiseKnob[];

export type NumericKnob = Exclude<NoiseKnob, "direction">;

export const KNOB_BOUNDS: Record<NumericKnob, { min: number; max: number; step: number; integer: boolean }> = {
  deviation_z: { min: 0.5, max: 20, step: 0.1, integer: false },
  spike_ratio: { min: 1.01, max: 100, step: 0.01, integer: false },
  drop_ratio: { min: 0.01, max: 0.99, step: 0.01, integer: false },
  min_absolute_delta_events: { min: 0, max: 1_000_000, step: 1, integer: false },
  open_after: { min: 1, max: 10, step: 1, integer: true },
};

export const DIRECTIONS: readonly NoiseDirection[] = ["both", "spike", "drop"];
export const CRITICALITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;

/** What the controls hold. Numbers stay text while they are typed. */
export interface TuningForm {
  values: Record<NumericKnob, string>;
  direction: NoiseDirection;
  criticality: string;
  window: NoiseWindow;
}

function numberText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

/** The form as the engine resolves it now. A stored value that is not a number leaves its field empty. */
export function formFromDetail(current: NoiseCurrent, window: NoiseWindow = 28): TuningForm {
  const eff = current.effective_thresholds;
  const direction = DIRECTIONS.find((d) => d === eff.direction.value) ?? "both";
  return {
    values: {
      deviation_z: numberText(eff.deviation_z.value),
      spike_ratio: numberText(eff.spike_ratio.value),
      drop_ratio: numberText(eff.drop_ratio.value),
      min_absolute_delta_events: numberText(eff.min_absolute_delta_events.value),
      open_after: numberText(eff.open_after.value),
    },
    direction,
    criticality: current.criticality,
    window,
  };
}

export type KnobProblem = "empty" | "notNumber" | "range" | "whole";

/** The first problem with one numeric field, or null. An empty field over an unreadable stored value means "keep". */
export function knobProblem(knob: NumericKnob, text: string, current: NoiseCurrent): KnobProblem | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return numberText(current.effective_thresholds[knob].value) === "" ? null : "empty";
  }
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return "notNumber";
  const b = KNOB_BOUNDS[knob];
  if (n < b.min || n > b.max) return "range";
  if (b.integer && !Number.isInteger(n)) return "whole";
  return null;
}

export function formProblems(form: TuningForm, current: NoiseCurrent): Partial<Record<NumericKnob, KnobProblem>> {
  const out: Partial<Record<NumericKnob, KnobProblem>> = {};
  for (const knob of Object.keys(KNOB_BOUNDS) as NumericKnob[]) {
    const problem = knobProblem(knob, form.values[knob], current);
    if (problem) out[knob] = problem;
  }
  return out;
}

/**
 * The knobs whose value differs from what is in force. Only these are sent: a knob left alone keeps
 * resolving through its own layer (default, criticality, LAB_MODE) instead of being pinned as custom.
 */
export function changedKnobs(form: TuningForm, current: NoiseCurrent): NoiseThresholds {
  const out: NoiseThresholds = {};
  const eff = current.effective_thresholds;
  for (const knob of Object.keys(KNOB_BOUNDS) as NumericKnob[]) {
    const text = form.values[knob].trim();
    if (text === "") continue;
    const n = Number(text);
    if (Number.isFinite(n) && n !== eff[knob].value) out[knob] = n;
  }
  if (form.direction !== eff.direction.value) out.direction = form.direction;
  return out;
}

/** True when nothing in the form differs from what is saved. */
export function formIsUnchanged(form: TuningForm, current: NoiseCurrent): boolean {
  return Object.keys(changedKnobs(form, current)).length === 0 && form.criticality === current.criticality;
}

/**
 * What identifies a preview: the proposed knobs, the criticality and the window. A preview is current
 * only for the exact key it ran under, so a change to any control makes the shown numbers stale.
 */
export function previewKey(form: TuningForm, current: NoiseCurrent): string {
  const changed = changedKnobs(form, current);
  const ordered = TUNING_KNOBS.map((k) => [k, changed[k] ?? null]);
  return JSON.stringify({ knobs: ordered, criticality: form.criticality, window: form.window });
}

/** The stored `custom_thresholds` with the changed knobs on top; keys outside the six are kept. */
export function mergedCustomThresholds(
  form: TuningForm,
  current: NoiseCurrent,
): Record<string, unknown> {
  return { ...current.custom_thresholds, ...changedKnobs(form, current) };
}

/** Saving a narrower direction closes this source's open incidents of the detector it disables (D-54). */
export function closesIncidents(form: TuningForm, current: NoiseCurrent): boolean {
  return form.direction !== "both" && form.direction !== current.effective_thresholds.direction.value;
}

/** The field and message a 422 names, from the project's error shape. */
export interface TuningFailure {
  fields: Partial<Record<NoiseKnob, string>>;
  general: string | null;
}

export function tuningFailure(detail: unknown, message: string): TuningFailure {
  const fields: Partial<Record<NoiseKnob, string>> = {};
  let general: string | null = null;
  const items: string[] = [];
  if (typeof detail === "string") items.push(detail);
  else if (Array.isArray(detail)) {
    for (const item of detail) {
      const msg = (item as { msg?: unknown } | null)?.msg;
      if (typeof msg === "string") items.push(msg);
    }
  }
  if (items.length === 0) items.push(message);
  for (const raw of items) {
    const text = raw.replace(/^Value error,\s*/, "");
    const match = /custom_thresholds\.([a-z_]+)\s*:\s*(.*)$/s.exec(text);
    const knob = match ? TUNING_KNOBS.find((k) => k === match[1]) : undefined;
    if (match && knob) fields[knob] = match[2].trim();
    else general = general ? `${general} ${text}` : text;
  }
  return { fields, general };
}
