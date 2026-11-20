// Fixed codes from the JDBC API become fixed English labels through `logSources.json`.
// An unknown code and any upstream free text are never shown: both map to one fixed
// "unavailable" label. A missing measurement stays null, never a zero.

import type catalog from "../../../../messages/en/logSources.json";
import type { TextTranslator } from "@/i18n/textTranslator";
import {
  JDBC_ATTEMPT_STATES, JDBC_COMPLETENESS, JDBC_CONFIG_STATES, JDBC_CONNECTION_CODES,
  JDBC_FIELD_STATE_CODES, JDBC_FIELD_STATE_REASONS, JDBC_FAILURE_CODES, JDBC_FLOW_STATES,
  JDBC_INCONSISTENCIES, JDBC_MEASUREMENTS, JDBC_ORIGINS, JDBC_REASON_CODES, JDBC_SUPPORT,
  type JdbcVolumeOut,
} from "@/lib/api";

const UNAVAILABLE = "logSources.jdbc.state.unavailable";
const NOT_REPORTED = "logSources.jdbc.connection.unreported";

/** Every backend enum value, grouped by the catalog object that labels it. */
const CODES = {
  flow: JDBC_FLOW_STATES,
  measurement: JDBC_MEASUREMENTS,
  connection: JDBC_CONNECTION_CODES,
  fieldState: JDBC_FIELD_STATE_CODES,
  inconsistency: JDBC_INCONSISTENCIES,
  completeness: JDBC_COMPLETENESS,
  origin: JDBC_ORIGINS,
  support: JDBC_SUPPORT,
  attempt: JDBC_ATTEMPT_STATES,
  config: JDBC_CONFIG_STATES,
  reason: JDBC_REASON_CODES,
  failure: JDBC_FAILURE_CODES,
  fieldReason: JDBC_FIELD_STATE_REASONS,
} as const;

export type JdbcStateKind = keyof typeof CODES;

type JdbcCatalog = (typeof catalog)["jdbc"];
type AssertNever<T extends never> = T;
type Unlabelled<K extends JdbcStateKind> = K extends keyof JdbcCatalog
  ? Exclude<(typeof CODES)[K][number], keyof JdbcCatalog[K]>
  : (typeof CODES)[K][number];

/**
 * Compile-time exhaustiveness: every backend code in every enum must have a fixed catalog label.
 * Adding a value to an `as const` array in `api.ts` without a matching key here, or a new kind,
 * is a type error. Exported so `noUnusedLocals` keeps it; it carries no runtime value.
 */
export type JdbcMissingLabels = AssertNever<
  { [K in JdbcStateKind]: Unlabelled<K> }[JdbcStateKind]
>;

/**
 * The label of a fixed backend code, never the code itself. A code outside its enum's list is
 * rejected before a key is built, so unknown upstream text can never reach the interface.
 */
export function jdbcStateLabel(
  kind: JdbcStateKind, code: string | null | undefined, say: TextTranslator,
): string {
  if (code == null) return say(kind === "connection" ? NOT_REPORTED : UNAVAILABLE);
  if (!(CODES[kind] as readonly string[]).includes(code)) return say(UNAVAILABLE);
  const key = `logSources.jdbc.${kind}.${code}`;
  return say.has?.(key) ? say(key) : say(UNAVAILABLE);
}

/** Nullable figures pass through untouched: `null` is not a measured zero, `0` stays `0`. */
export interface JdbcMeasurementView {
  eventCount: number | null;
  averageEps: number | null;
  collectedAt: string | null;
}

export function jdbcMeasurementView(volume: Pick<
  JdbcVolumeOut, "event_count" | "average_eps" | "collected_at"
>): JdbcMeasurementView {
  return {
    eventCount: volume.event_count ?? null,
    averageEps: volume.average_eps ?? null,
    collectedAt: volume.collected_at ?? null,
  };
}
