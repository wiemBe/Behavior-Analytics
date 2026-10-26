// Rule counts as a pivot: rule type by enabled and disabled, rules first and
// building blocks after. Origin is a filter, because the counts are the same
// rules seen through a different lens, not a different table.

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { RuleCountRow } from "@/lib/api";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

import {
  ALL_ORIGINS,
  formatCount,
  originFilterLabel,
  originsPresent,
  pivotCounts,
  statsHref,
  typeLabel,
  typeTitle,
  type StatsQuery,
} from "./ruleStatistics";

export function RuleComposition({
  counts,
  query,
}: {
  counts: RuleCountRow[];
  query: StatsQuery;
}) {
  const t = useTranslations("rules.composition");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const origins = originsPresent(counts);
  const groups = pivotCounts(counts, query.origin);

  return (
    <section aria-labelledby="stats-composition">
      <div className="section-head row between">
        <h2 className="section-title" id="stats-composition">
          {t("heading")}
        </h2>
        {origins.length > 0 && (
          <nav className="range-selector" aria-label={t("origin")}>
            <span className="muted">{t("origin")}</span>
            {[ALL_ORIGINS, ...origins].map((o) => (
              <Link
                key={o}
                href={statsHref({ ...query, origin: o })}
                className={`range-option${o === query.origin ? " range-option-active" : ""}`}
                aria-current={o === query.origin ? "page" : undefined}
              >
                {originFilterLabel(o, say)}
              </Link>
            ))}
          </nav>
        )}
      </div>

      {groups.length === 0 ? (
        <div className="notice">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>
            {t("emptyHelp")}
          </p>
        </div>
      ) : (
        <TableScroll label={t("label")}>
          <table className="stats-table stats-composition">
            <caption className="sr-only">
              {query.origin === ALL_ORIGINS
                ? t("captionAll")
                : t("captionOrigin", { origin: originFilterLabel(query.origin, say) })}
            </caption>
            <thead>
              <tr>
                <th scope="col">{t("type")}</th>
                <th scope="col" className="num">
                  {t("enabled")}
                </th>
                <th scope="col" className="num">
                  {t("disabled")}
                </th>
                <th scope="col" className="num">
                  {t("total")}
                </th>
              </tr>
            </thead>
            {groups.map((g) => (
              <tbody key={g.buildingBlocks ? "bb" : "rules"}>
                <tr className="stats-group">
                  <th scope="rowgroup" colSpan={4}>
                    {g.buildingBlocks ? t("buildingBlocks") : t("rules")}
                  </th>
                </tr>
                {g.rows.map((r) => (
                  <tr key={r.type ?? "null"}>
                    <th scope="row">
                      <span title={typeTitle(r.type, say)}>{typeLabel(r.type, say)}</span>
                    </th>
                    <td className="num">{formatCount(r.enabled, locale)}</td>
                    <td className="num">{formatCount(r.disabled, locale)}</td>
                    <td className="num">{formatCount(r.total, locale)}</td>
                  </tr>
                ))}
                <tr className="stats-total">
                  <th scope="row">{g.buildingBlocks ? t("allBuildingBlocks") : t("allRules")}</th>
                  <td className="num">{formatCount(g.totals.enabled, locale)}</td>
                  <td className="num">{formatCount(g.totals.disabled, locale)}</td>
                  <td className="num">{formatCount(g.totals.total, locale)}</td>
                </tr>
              </tbody>
            ))}
          </table>
        </TableScroll>
      )}
    </section>
  );
}
