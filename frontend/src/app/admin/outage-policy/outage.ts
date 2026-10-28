// What the outage alert policy page says, and the arithmetic behind it.
//
// Everything here is pure. The rule the page lives by (design C.7, decision
// D-9): every monitored source alerts on an outage unless an operator opts a
// class or a source into RECORD_ONLY, and RECORD_ONLY changes only whether a
// NO_EVENTS anomaly raises an alert. A figure the API did not supply stays
// null, and the sentence says it is unavailable rather than counting zero.

import {
  ApiError,
  actionErrorMessage,
  type OutageClassSummary,
  type OutageFleetSummary,
  type OutageMode,
  type OutagePolicyRow,
  type SourceClass,
} from "@/lib/api";
import { classLabel } from "@/lib/fleet";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { fixed, plainCount } from "@/lib/figureText";

import enums from "../../../../messages/en/enums.json";
import errors from "../../../../messages/en/errors.json";
import outage from "../../../../messages/en/outage.json";

// Every helper that builds visible text takes a `BehaviorI18n` (a translator over the whole catalog,
// and the locale) as its LAST optional argument and defaults to English, so the Dashboard's outage
// widget and every existing test keep today's text. Route code passes its own
// (`useBehaviorI18n()` in a component, `getBehaviorI18n()` in a server page).

/** The English of this area, for the helpers' defaults. */
export const ENGLISH_OUTAGE: BehaviorI18n = {
  t: createTextTranslator("en", { enums, errors, outage }),
  locale: "en",
};

const k = (key: string) => `outage.${key}`;

/** The fleet-text bundle `lib/fleet` helpers take, built from this area's. */
const fleetOf = (i18n: BehaviorI18n) => ({ say: i18n.t, locale: i18n.locale });

export const GRACE_MIN = 0;
export const GRACE_MAX = 48;

export const MODE_LABELS: Record<OutageMode, string> = {
  ALERT: "Alert",
  RECORD_ONLY: "Record only",
};

export function modeLabel(mode: OutageMode, i18n: BehaviorI18n = ENGLISH_OUTAGE): string {
  return Object.prototype.hasOwnProperty.call(MODE_LABELS, mode)
    ? i18n.t(k(`mode.${mode}`))
    : String(mode);
}

/** A counted noun (`unit.bucket`, `unit.monitoredSource`, `unit.source`) in the interface language. */
function counted(i18n: BehaviorI18n, key: string, count: number): string {
  return i18n.t(k(key), { count, n: plainCount(count, i18n.locale) });
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

// --- grace ---------------------------------------------------------------------

/** `6 buckets · 30 min`, or the buckets alone when the width is not known. */
export function formatGrace(
  buckets: number,
  bucketSeconds: number | null,
  i18n: BehaviorI18n = ENGLISH_OUTAGE,
): string {
  const b = counted(i18n, "unit.bucket", buckets);
  if (bucketSeconds === null) return b;
  return `${b} · ${formatMinutes((buckets * bucketSeconds) / 60, i18n)}`;
}

/** The two halves of a grace, for a cell that puts the minutes on a second line. */
export function graceParts(
  buckets: number,
  bucketSeconds: number | null,
  i18n: BehaviorI18n = ENGLISH_OUTAGE,
): { buckets: string; minutes: string | null } {
  return {
    buckets: counted(i18n, "unit.bucket", buckets),
    minutes:
      bucketSeconds === null ? null : formatMinutes((buckets * bucketSeconds) / 60, i18n),
  };
}

export function formatMinutes(minutes: number, i18n: BehaviorI18n = ENGLISH_OUTAGE): string {
  const rounded = Math.round(minutes * 10) / 10;
  const value = Number.isInteger(rounded) ? plainCount(rounded, i18n.locale) : fixed(rounded, 1, i18n.locale);
  return i18n.t(k("unit.minutes"), { value });
}

export type GraceParse = { ok: true; value: number | null } | { ok: false; message: string };

/**
 * A grace field's text. Blank means "use the default"; anything else must be a
 * whole number. The range is the server's to enforce and its 422 is shown at
 * the field, so an out-of-range number is sent rather than judged here.
 */
export function parseGrace(text: string, i18n: BehaviorI18n = ENGLISH_OUTAGE): GraceParse {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!/^-?\d+$/.test(t)) {
    return { ok: false, message: i18n.t(k("grace.blank")) };
  }
  return { ok: true, value: Number(t) };
}

// --- rows ----------------------------------------------------------------------

export function isGlobal(row: OutagePolicyRow): boolean {
  return row.instance_id === null;
}

/** The global class row for a class, if any. */
export function classRow(rows: OutagePolicyRow[], cls: string): OutagePolicyRow | undefined {
  return rows.find((r) => r.scope_kind === "CLASS" && r.scope_value === cls && isGlobal(r));
}

export function classRows(rows: OutagePolicyRow[]): OutagePolicyRow[] {
  return rows.filter((r) => r.scope_kind === "CLASS");
}

export function sourceRows(rows: OutagePolicyRow[]): OutagePolicyRow[] {
  return rows
    .filter((r) => r.scope_kind === "SOURCE")
    .sort(
      (a, b) =>
        (a.source_name ?? "￿").localeCompare(b.source_name ?? "￿") ||
        a.scope_value.localeCompare(b.scope_value),
    );
}

/** A SOURCE row whose source has left the inventory. */
export function isGone(row: OutagePolicyRow): boolean {
  return row.scope_kind === "SOURCE" && row.source_name === null;
}

/** An instance id is not a name; only its first block identifies it. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

/** The mode a class with no row resolves to: `outage_default` (D-9), from settings. */
export function defaultMode(summary: OutageFleetSummary | null): OutageMode | null {
  return summary ? summary.outage_default : null;
}

/** `1 bucket (5 min)`, or null when the default grace is not reported. */
export function defaultGraceText(
  summary: OutageFleetSummary | null,
  i18n: BehaviorI18n = ENGLISH_OUTAGE,
): string | null {
  const g = summary?.default_silence_grace_buckets;
  if (g === null || g === undefined) return null;
  const p = graceParts(g, summary ? summary.bucket_seconds : null, i18n);
  return p.minutes ? `${p.buckets} (${p.minutes})` : p.buckets;
}

export interface ClassLine {
  /** Row key, unique across the table. */
  key: string;
  source_class: SourceClass;
  /** The row that decides, or undefined when the default does. */
  row: OutagePolicyRow | undefined;
  mode: OutageMode | null;
  grace: number | null;
  /** Monitored sources this row governs; null when it is not known. */
  covered: number | null;
  overrides: number | null;
  summary: OutageClassSummary | undefined;
}

/**
 * One line per class that has an enabled source or a policy row, plus a line for
 * each instance-scoped class row. Ordered by class label.
 */
export function classLines(
  summary: OutageFleetSummary | null,
  rows: OutagePolicyRow[],
  i18n: BehaviorI18n = ENGLISH_OUTAGE,
): ClassLine[] {
  const byClass = new Map<string, OutageClassSummary>(
    (summary?.classes ?? []).map((c) => [c.source_class, c]),
  );
  const names = new Set<string>();
  for (const c of summary?.classes ?? []) {
    if (c.sources.enabled > 0) names.add(c.source_class);
  }
  for (const r of classRows(rows)) names.add(r.scope_value);

  const lines: ClassLine[] = [];
  for (const name of names) {
    const cls = name as SourceClass;
    const s = byClass.get(name);
    const global = classRow(rows, name);
    const overrides = s ? s.outage_overrides : null;
    lines.push({
      key: `${name}`,
      source_class: cls,
      row: global,
      mode: global ? global.mode : (s?.outage_mode ?? null),
      grace: global ? global.silence_grace_buckets : null,
      covered: global
        ? global.governed_monitored_sources
        : s
          ? Math.max(s.monitored_sources - (overrides ?? 0), 0)
          : null,
      overrides,
      summary: s,
    });
    for (const r of classRows(rows)) {
      if (r.scope_value !== name || isGlobal(r)) continue;
      lines.push({
        key: `${name}:${r.instance_id}`,
        source_class: cls,
        row: r,
        mode: r.mode,
        grace: r.silence_grace_buckets,
        covered: null,
        overrides: null,
        summary: s,
      });
    }
  }
  const fleet = fleetOf(i18n);
  // English keeps the runtime's own collation; Turkish orders by its alphabet.
  const collate = (a: string, b: string) =>
    i18n.locale === "tr" ? a.localeCompare(b, "tr") : a.localeCompare(b);
  return lines.sort(
    (a, b) =>
      collate(classLabel(a.source_class, fleet), classLabel(b.source_class, fleet)) ||
      (a.row?.instance_id ?? "").localeCompare(b.row?.instance_id ?? ""),
  );
}

/** Source overrides in a class, split by what they do: they decide the impact text. */
export function overrideSplit(
  rows: OutagePolicyRow[],
  cls: string,
): { alerting: number; recordOnly: number } {
  let alerting = 0;
  let recordOnly = 0;
  for (const r of sourceRows(rows)) {
    if (r.source_class !== cls || isGone(r)) continue;
    if (r.mode === "ALERT") alerting += 1;
    else recordOnly += 1;
  }
  return { alerting, recordOnly };
}

// --- impact --------------------------------------------------------------------

export interface Impact {
  /** One sentence per line, in the order they should be read. */
  lines: string[];
  /** Whether the numbers are known; false renders the notice as a caution. */
  known: boolean;
}

export interface ClassChange {
  source_class: SourceClass;
  /** What the class resolves to now (row or default); null when unknown. */
  fromMode: OutageMode | null;
  fromGrace: number | null;
  /** What it will be after the save; for a removal, the default. */
  toMode: OutageMode | null;
  toGrace: number | null;
  /** The count this row decides. */
  covered: number | null;
  /** The default grace as text ("1 bucket (5 min)"), when reported. */
  defaultGrace: string | null;
  /** Source overrides in the class that keep their own mode. */
  alertingOverrides: number;
  recordOnlyOverrides: number;
  removal: boolean;
  bucketSeconds: number | null;
}

function graceClause(
  g: number | null,
  bucketSeconds: number | null,
  defaultGrace: string | null,
  i18n: BehaviorI18n,
): string {
  if (g !== null) return formatGrace(g, bucketSeconds, i18n);
  return defaultGrace
    ? i18n.t(k("grace.defaultWith"), { default: defaultGrace })
    : i18n.t(k("grace.default"));
}

/** What saving (or removing) a class row does, in numbers, before it is saved. */
export function classImpact(c: ClassChange, i18n: BehaviorI18n = ENGLISH_OUTAGE): Impact {
  const lines: string[] = [];
  let known = true;
  const fleet = fleetOf(i18n);
  const cls = classLabel(c.source_class, fleet);
  const named = i18n.t(k("impact.classNamed"), { class: cls });
  const say = (key: string, values?: Record<string, string | number>) =>
    i18n.t(k(`impact.${key}`), values);
  const mode = (m: OutageMode) => modeLabel(m, i18n);
  const who =
    c.covered === null
      ? null
      : say("inClass", { sources: counted(i18n, "unit.monitoredSource", c.covered), class: cls });

  if (c.toMode === null) {
    known = false;
    lines.push(say("unknownDefault"));
  } else if (c.fromMode !== null && c.fromMode === c.toMode) {
    lines.push(
      c.removal
        ? say("sameRemoval", { class: named, mode: mode(c.toMode) })
        : say("sameKeep", { mode: mode(c.toMode) }),
    );
  } else if (who === null) {
    known = false;
    lines.push(
      say(c.toMode === "RECORD_ONLY" ? "unknownCountStop" : "unknownCountRaise", { class: named }),
    );
  } else if (c.covered === 0) {
    lines.push(say("none", { class: cls }));
  } else if (c.toMode === "RECORD_ONLY") {
    lines.push(say("stop", { who }));
    if (c.alertingOverrides > 0) {
      lines.push(
        say("keepAlerting", { count: c.alertingOverrides, n: plainCount(c.alertingOverrides, i18n.locale) }),
      );
    }
  } else {
    lines.push(say("raise", { who }));
    if (c.recordOnlyOverrides > 0) {
      lines.push(
        say("stayRecordOnly", {
          count: c.recordOnlyOverrides,
          n: plainCount(c.recordOnlyOverrides, i18n.locale),
        }),
      );
    }
  }

  if (c.fromGrace !== c.toGrace) {
    const to = graceClause(c.toGrace, c.bucketSeconds, c.defaultGrace, i18n);
    lines.push(
      c.covered === null || c.covered > 0
        ? say("graceChanged", {
            to,
            from: graceClause(c.fromGrace, c.bucketSeconds, c.defaultGrace, i18n),
          })
        : say("graceBecomes", { to }),
    );
  }
  if (lines.length === 1 && c.fromMode === c.toMode && c.fromGrace === c.toGrace) {
    lines.push(say("nothing"));
  }
  return { lines, known };
}

export interface SourceChange {
  name: string;
  source_class: SourceClass | null;
  /** The mode the source's class gives it, absent an override; null when unknown. */
  classMode: OutageMode | null;
  /** The mode it has now (its row, else its class); null when unknown. */
  fromMode: OutageMode | null;
  fromGrace: number | null;
  toMode: OutageMode;
  toGrace: number | null;
  removal: boolean;
  bucketSeconds: number | null;
  defaultGrace: string | null;
}

/** The same, for one source's override. */
export function sourceImpact(c: SourceChange, i18n: BehaviorI18n = ENGLISH_OUTAGE): Impact {
  const lines: string[] = [];
  const say = (key: string, values?: Record<string, string | number>) =>
    i18n.t(k(`impact.${key}`), values);
  const mode = (m: OutageMode) => modeLabel(m, i18n);
  const classText = c.source_class
    ? say("classNamed", { class: classLabel(c.source_class, fleetOf(i18n)) })
    : say("itsClass");
  const toMode = c.removal ? c.classMode : c.toMode;
  let known = true;
  if (toMode === null) {
    known = false;
    lines.push(say("sourceUnknown", { name: c.name, class: classText }));
  } else if (c.fromMode !== null && c.fromMode === toMode) {
    lines.push(
      c.removal
        ? say("sourceSameRemoval", { name: c.name, mode: mode(toMode) })
        : say("sourceSameKeep", { name: c.name, mode: mode(toMode) }),
    );
  } else if (toMode === "RECORD_ONLY") {
    lines.push(say("sourceStop", { name: c.name }));
  } else {
    lines.push(say("sourceRaise", { name: c.name }));
  }
  if (!c.removal && c.classMode !== null && toMode !== null) {
    lines.push(
      toMode === c.classMode
        ? say("pinned", { class: classText, mode: mode(c.classMode) })
        : say("differs", { class: classText, mode: mode(c.classMode) }),
    );
  }
  if (c.removal) {
    lines.push(say("follows", { class: classText }));
  }
  if (c.fromGrace !== c.toGrace) {
    lines.push(
      say("sourceGrace", {
        to: graceClause(c.toGrace, c.bucketSeconds, c.defaultGrace, i18n),
        from: graceClause(c.fromGrace, c.bucketSeconds, c.defaultGrace, i18n),
      }),
    );
  }
  return { lines, known };
}

// --- failures --------------------------------------------------------------------

export interface Failure {
  kind: "forbidden" | "error";
  text: string;
  /** Set when the message belongs at the grace field. */
  field: "grace" | null;
  /** True for a 404 on a delete: the row is already gone, so the list is stale. */
  gone: boolean;
}

export function saveFailure(err: unknown, i18n: BehaviorI18n = ENGLISH_OUTAGE): Failure {
  if (err instanceof ApiError) {
    if (err.status === 403) {
      return { kind: "forbidden", text: i18n.t(k("failure.forbidden")), field: null, gone: false };
    }
    if (err.status === 422 && err.message) {
      return { kind: "error", text: err.message, field: "grace", gone: false };
    }
  }
  return {
    kind: "error",
    text: i18n.t(k("failure.notSaved"), { reason: actionErrorMessage(err, i18n.t) }),
    field: null,
    gone: false,
  };
}

export function removeFailure(err: unknown, i18n: BehaviorI18n = ENGLISH_OUTAGE): Failure {
  if (err instanceof ApiError && err.status === 404) {
    return { kind: "error", text: i18n.t(k("failure.gone")), field: null, gone: true };
  }
  if (err instanceof ApiError && err.status === 403) {
    return { ...saveFailure(err, i18n) };
  }
  return {
    kind: "error",
    text: i18n.t(k("failure.notRemoved"), { reason: actionErrorMessage(err, i18n.t) }),
    field: null,
    gone: false,
  };
}

/** What a failed read of the policy says. Never the raw error. */
export function readError(
  err: unknown,
  i18n: BehaviorI18n = ENGLISH_OUTAGE,
): { forbidden: boolean; text: string } {
  if (err instanceof ApiError && err.status === 403) {
    return { forbidden: true, text: i18n.t(k("failure.readForbidden")) };
  }
  if (err instanceof ApiError && err.status === 401) {
    return { forbidden: false, text: i18n.t(k("failure.expired")) };
  }
  return { forbidden: false, text: i18n.t(k("failure.readFailed")) };
}
