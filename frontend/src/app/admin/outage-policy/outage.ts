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

export const GRACE_MIN = 0;
export const GRACE_MAX = 48;

export const MODE_LABELS: Record<OutageMode, string> = {
  ALERT: "Alert",
  RECORD_ONLY: "Record only",
};

export function modeLabel(mode: OutageMode): string {
  return MODE_LABELS[mode];
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

// --- grace ---------------------------------------------------------------------

/** `6 buckets · 30 min`, or the buckets alone when the width is not known. */
export function formatGrace(buckets: number, bucketSeconds: number | null): string {
  const b = plural(buckets, "bucket");
  if (bucketSeconds === null) return b;
  return `${b} · ${formatMinutes((buckets * bucketSeconds) / 60)}`;
}

/** The two halves of a grace, for a cell that puts the minutes on a second line. */
export function graceParts(
  buckets: number,
  bucketSeconds: number | null,
): { buckets: string; minutes: string | null } {
  return {
    buckets: plural(buckets, "bucket"),
    minutes: bucketSeconds === null ? null : formatMinutes((buckets * bucketSeconds) / 60),
  };
}

export function formatMinutes(minutes: number): string {
  const rounded = Math.round(minutes * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} min`;
}

export type GraceParse = { ok: true; value: number | null } | { ok: false; message: string };

/**
 * A grace field's text. Blank means "use the default"; anything else must be a
 * whole number. The range is the server's to enforce and its 422 is shown at
 * the field, so an out-of-range number is sent rather than judged here.
 */
export function parseGrace(text: string): GraceParse {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!/^-?\d+$/.test(t)) {
    return { ok: false, message: "Enter a whole number of buckets, or leave it blank for the default." };
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
export function defaultGraceText(summary: OutageFleetSummary | null): string | null {
  const g = summary?.default_silence_grace_buckets;
  if (g === null || g === undefined) return null;
  const p = graceParts(g, summary ? summary.bucket_seconds : null);
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
  return lines.sort(
    (a, b) =>
      classLabel(a.source_class).localeCompare(classLabel(b.source_class)) ||
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

function sources(n: number, cls: SourceClass): string {
  return `${plural(n, "monitored source")} in ${classLabel(cls)}`;
}

function graceClause(
  g: number | null,
  bucketSeconds: number | null,
  defaultGrace: string | null,
): string {
  if (g !== null) return formatGrace(g, bucketSeconds);
  return defaultGrace ? `the default grace, ${defaultGrace}` : "the default grace";
}

/** What saving (or removing) a class row does, in numbers, before it is saved. */
export function classImpact(c: ClassChange): Impact {
  const lines: string[] = [];
  let known = true;
  const who = c.covered === null ? null : sources(c.covered, c.source_class);

  if (c.toMode === null) {
    known = false;
    lines.push(
      "The default mode is not reported, so the effect of this change cannot be counted here.",
    );
  } else if (c.fromMode !== null && c.fromMode === c.toMode) {
    lines.push(
      c.removal
        ? `${classLabel(c.source_class)} already resolves to ${modeLabel(c.toMode)}; removing the row changes no mode.`
        : `The mode stays ${modeLabel(c.toMode)}.`,
    );
  } else if (who === null) {
    known = false;
    lines.push(
      `${classLabel(c.source_class)} will ${c.toMode === "RECORD_ONLY" ? "no longer raise outage alerts" : "raise outage alerts"}. The number of sources affected is unavailable because the fleet summary could not be read.`,
    );
  } else if (c.covered === 0) {
    lines.push(
      `No monitored source in ${classLabel(c.source_class)} is decided by this row, so no source changes behaviour.`,
    );
  } else if (c.toMode === "RECORD_ONLY") {
    lines.push(`${who} will no longer raise outage alerts.`);
    if (c.alertingOverrides > 0) {
      const one = c.alertingOverrides === 1;
      lines.push(
        `${plural(c.alertingOverrides, "source")} ${one ? "keeps" : "keep"} alerting through ${one ? "its" : "their"} own ${one ? "override" : "overrides"}.`,
      );
    }
  } else {
    lines.push(`${who} will raise outage alerts again.`);
    if (c.recordOnlyOverrides > 0) {
      const one = c.recordOnlyOverrides === 1;
      lines.push(
        `${plural(c.recordOnlyOverrides, "source")} ${one ? "stays" : "stay"} record-only through ${one ? "its" : "their"} own ${one ? "override" : "overrides"}.`,
      );
    }
  }

  if (c.fromGrace !== c.toGrace) {
    lines.push(
      c.covered === null || c.covered > 0
        ? `A silent source is judged after ${graceClause(c.toGrace, c.bucketSeconds, c.defaultGrace)} instead of ${graceClause(c.fromGrace, c.bucketSeconds, c.defaultGrace)}.`
        : `The grace becomes ${graceClause(c.toGrace, c.bucketSeconds, c.defaultGrace)}.`,
    );
  }
  if (lines.length === 1 && c.fromMode === c.toMode && c.fromGrace === c.toGrace) {
    lines.push("Nothing changes.");
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
export function sourceImpact(c: SourceChange): Impact {
  const lines: string[] = [];
  const classText = c.source_class ? classLabel(c.source_class) : "its class";
  const toMode = c.removal ? c.classMode : c.toMode;
  let known = true;
  if (toMode === null) {
    known = false;
    lines.push(
      `${c.name} falls back to the mode of ${classText}, which is not reported here, so the effect cannot be stated.`,
    );
  } else if (c.fromMode !== null && c.fromMode === toMode) {
    lines.push(
      c.removal
        ? `${c.name} already resolves to ${modeLabel(toMode)}; removing the override changes no mode.`
        : `${c.name} stays ${modeLabel(toMode)}.`,
    );
  } else if (toMode === "RECORD_ONLY") {
    lines.push(`${c.name} will no longer raise outage alerts.`);
  } else {
    lines.push(`${c.name} will raise outage alerts.`);
  }
  if (!c.removal && c.classMode !== null && toMode !== null) {
    lines.push(
      toMode === c.classMode
        ? `${classText} resolves to ${modeLabel(c.classMode)} as well, so this override pins the source against a later change to its class.`
        : `${classText} resolves to ${modeLabel(c.classMode)}; this source differs from it.`,
    );
  }
  if (c.removal) {
    lines.push(`It follows ${classText} again.`);
  }
  if (c.fromGrace !== c.toGrace) {
    lines.push(
      `It is judged silent after ${graceClause(c.toGrace, c.bucketSeconds, c.defaultGrace)} instead of ${graceClause(c.fromGrace, c.bucketSeconds, c.defaultGrace)}.`,
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

export function saveFailure(err: unknown): Failure {
  if (err instanceof ApiError) {
    if (err.status === 403) {
      return {
        kind: "forbidden",
        text: "Your role cannot change the outage alert policy (permission admin:outage). Nothing was changed.",
        field: null,
        gone: false,
      };
    }
    if (err.status === 422 && err.message) {
      return { kind: "error", text: err.message, field: "grace", gone: false };
    }
  }
  return { kind: "error", text: `Not saved. ${actionErrorMessage(err)}`, field: null, gone: false };
}

export function removeFailure(err: unknown): Failure {
  if (err instanceof ApiError && err.status === 404) {
    return {
      kind: "error",
      text: "This row was already removed by someone else. The list has been reloaded.",
      field: null,
      gone: true,
    };
  }
  if (err instanceof ApiError && err.status === 403) {
    return { ...saveFailure(err) };
  }
  return { kind: "error", text: `Not removed. ${actionErrorMessage(err)}`, field: null, gone: false };
}

/** What a failed read of the policy says. Never the raw error. */
export function readError(err: unknown): { forbidden: boolean; text: string } {
  if (err instanceof ApiError && err.status === 403) {
    return {
      forbidden: true,
      text: "Your role cannot read the outage alert policy (permission admin:outage). Reading it needs the same permission as changing it. Nothing was changed.",
    };
  }
  if (err instanceof ApiError && err.status === 401) {
    return { forbidden: false, text: "Your session has expired. Sign in again to continue." };
  }
  return {
    forbidden: false,
    text: "The outage alert policy could not be loaded, so nothing is shown here. The backend may be unreachable; check that it is running, then reload the page. Nothing was changed.",
  };
}
