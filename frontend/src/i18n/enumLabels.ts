// One message per enum value, looked up by the value, for values the interface shows as words
// (severity, anomaly lifecycle state, offense status, source class). The same value shown as code
// (a filter in a URL, a query parameter, an API field name, a code panel) stays as it is and does
// not come through here.
//
// An unknown value is shown as it was sent, never as an empty string: a value this build does not
// know is still information. The tables live in the `enums` namespace; later waves reuse them and
// add a group there only for a value set that no other namespace owns.

import { useTranslations } from "next-intl";
import enEnums from "../../messages/en/enums.json";

export type EnumGroup =
  | "severity"
  | "severityPill"
  | "anomalyState"
  | "offenseStatus"
  | "sourceClass"
  | "anomalyType";

type ExtendedEnumGroup =
  | "alertStatus"
  | "alertStatusPill"
  | "notificationChannel"
  | "notificationStatus"
  | "alertTransition"
  | "ruleHealth"
  | "propertyWatchAnomalyType"
  | "propertyWatchStatus"
  | "propertyWatchHistoryState"
  | "propertyWatchPreviewOutcome"
  | "profileValidationStatus"
  | "bucketCompleteness"
  | "executionStatus"
  | "executionTrigger"
  | "role";

/** The part of a translator this module needs: next-intl's `t` and its `has`. */
export interface EnumTranslator {
  (key: string): string;
  has?(key: string): boolean;
}

/** The label of `value` in `group`, or `value` itself when the catalog has no message for it. */
export function enumLabel(
  t: EnumTranslator,
  group: EnumGroup | ExtendedEnumGroup,
  value: string,
): string {
  // A "." in a value would read as a path into the catalog, so such a value is never a key.
  if (value === "" || value.includes(".")) return value;
  const table = (enEnums as Record<string, unknown>)[group];
  // `next-intl`'s `has()` follows the prototype chain. Only a catalog's own value keys are
  // eligible, otherwise a backend value such as "constructor" would become interface copy.
  if (typeof table !== "object" || table === null || !Object.hasOwn(table, value)) return value;
  const key = `${group}.${value}`;
  if (t.has?.(key)) return t(key);
  const rootKey = `enums.${key}`;
  return t.has?.(rootKey) ? t(rootKey) : value;
}

/** The anomaly state as a pill shows it; a state the catalog lacks is its code in capitals. */
export function statePillLabel(t: EnumTranslator, value: string): string {
  const known = enumLabel(t, "anomalyState", value);
  return known === value ? value.toUpperCase() : known;
}

export function useEnumLabels() {
  const t = useTranslations("enums") as unknown as EnumTranslator;
  return {
    label: (group: EnumGroup, value: string) => enumLabel(t, group, value),
    /** The severity as a pill shows it: the code in capitals ("HIGH"), as before. */
    severityPill: (value: string) => enumLabel(t, "severityPill", value),
    /** The anomaly state as a pill shows it (see `statePillLabel`). */
    statePill: (value: string) => statePillLabel(t, value),
  };
}
