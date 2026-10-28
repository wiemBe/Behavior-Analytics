// The Threshold rules tab (design §G.13.3): the custom rules that pin a value of this watch.
//
// `threshold_rules` carries the rule and its pinned value's text from the same backend read.
// Rule names and values are attacker-influenced text and are rendered as text. Creating a rule
// is the rule editor's job and needs `custom_rule:write`; a reader sees the list and is told
// which permission adds it.

import Link from "next/link";
import { useTranslations } from "next-intl";

import type { PwPinRule } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { valueText } from "./WatchValuesTable";

export type RuleAccess = "writer" | "reader" | "unknown";

export const RULE_WRITE_PERMISSION = "custom_rule:write";

export function ThresholdRules({
  rules,
  access,
}: {
  /** null: the single-watch read did not carry the list. */
  rules: PwPinRule[] | null;
  access: RuleAccess;
}) {
  const t = useTranslations("propertyWatch.thresholdRules");
  const common = useTranslations("propertyWatch.common");
  const i18n = useBehaviorI18n();
  if (rules === null) {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("failedTitle")}</strong>
        </p>
        <p>{t.rich("failedBody", { link: (chunks) => <Link href="/custom-rules">{chunks}</Link> })}</p>
      </div>
    );
  }
  const create =
    access === "writer" ? (
      <p>
        <Link href="/custom-rules/new" className="action secondary button-link">
          {t("new")}
        </Link>
      </p>
    ) : (
      <p className="subtitle">
        {access === "unknown"
          ? t("unknownPermission", { permission: RULE_WRITE_PERMISSION })
          : t("permission", { permission: RULE_WRITE_PERMISSION })}
      </p>
    );
  return (
    <div className="stack-6">
      <p className="subtitle">
        {t.rich("intro", { link: (chunks) => <Link href="/alerts">{chunks}</Link> })}
      </p>
      {rules.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>
            {t("emptyBody")}
          </p>
        </div>
      ) : (
        <table className="table-dense pw-rules" aria-label={t("tableLabel")}>
          <thead>
            <tr>
              <th scope="col">{t("rule")}</th>
              <th scope="col">{t("pinnedValue")}</th>
              <th scope="col">{common("state")}</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id}>
                <td className="cell-name">
                  <Link href={`/custom-rules/${r.id}`}>{r.name}</Link>
                </td>
                <td className="pw-value-cell">
                  <span className="pw-value-text">{valueText(r, i18n)}</span>
                </td>
                <td>
                  <span className={`pill pill-quiet ${r.enabled ? "ok" : ""}`}>
                    {r.enabled ? common("enabled") : common("disabled")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {create}
    </div>
  );
}
