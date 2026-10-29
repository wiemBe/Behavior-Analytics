// The costliest enabled rules by QRadar's own capacity benchmark.
//
// The benchmark is how fast QRadar could evaluate the rule, so a LOWER number
// is a COSTLIER rule. It is not a firing rate and it is not derived from
// matches; the section says so before the first figure.

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { CostSection } from "@/lib/api";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";
import { useBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator } from "@/i18n/textTranslator";

import { formatCapacity, kindLabel } from "./ruleStatistics";

export function CostliestRules({ cost }: { cost: CostSection }) {
  const t = useTranslations("rules.cost");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const backend = useBackendMessage();
  const label = backend({ text: cost.label, code: cost.label_code, params: cost.label_params });
  const unit = backend({ text: cost.unit, code: cost.unit_code, params: cost.unit_params });
  return (
    <section aria-labelledby="stats-cost">
      <div className="section-head">
        <h2 className="section-title" id="stats-cost">
          {t("heading")}
        </h2>
      </div>
      <p className="subtitle">{label}</p>

      {cost.items.length === 0 ? (
        <div className="notice">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>{t("emptyHelp")}</p>
        </div>
      ) : (
        <TableScroll label={t("label")}>
          <table className="stats-table stats-cost">
            <caption className="sr-only">
              {t("caption", { unit })}
            </caption>
            <thead>
              <tr>
                <th scope="col">{t("rule")}</th>
                <th scope="col" className="num">
                  {t("average")}
                  <span className="cell-sub">{unit}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {cost.items.map((r) => (
                <tr key={r.rule_id}>
                  <th scope="row" className="cell-rule">
                    <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                    <span className="cell-sub">{kindLabel(r.rule_type, r.is_building_block, say)}</span>
                  </th>
                  <td className="num">{formatCapacity(r.average_capacity, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}

      <p className="table-note">
        {t("excluded", { count: cost.no_benchmark_count })}
      </p>
    </section>
  );
}
