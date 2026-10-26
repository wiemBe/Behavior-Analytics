// QRadar's capacity benchmark for one rule over time.
//
// A sample exists only when QRadar re-benchmarked the rule, so the history is
// sparse by construction and its length says nothing about activity. Empty is
// a fact about QRadar, not a fault. A failed request is a different state and
// is worded as one.
//
// No chart: the samples are few, irregular and not a series with a cadence, so
// a table is the honest form. Revisit if a rule ever carries a long history.

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { CapacitySample } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";

import { formatCapacity } from "./ruleStatistics";

export const CAPACITY_UNIT = "millions of events per second (QRadar's unit)";

function Figure({ value, locale, missing }: { value: number | null; locale: Locale; missing: string }) {
  return value === null ? (
    <span className="muted" aria-label={missing}>
      —
    </span>
  ) : (
    <>{formatCapacity(value, locale)}</>
  );
}

export function CapacityHistory({
  samples,
}: {
  /** Null when the request failed; an empty list when QRadar never re-benchmarked. */
  samples: CapacitySample[] | null;
}) {
  const t = useTranslations("rules.capacity");
  const locale = useLocale() as Locale;
  return (
    <section aria-labelledby="rule-capacity">
      <h3 id="rule-capacity">{t("heading")}</h3>
      {samples === null ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("failedTitle")}</strong>
          </p>
          <p>
            {t("failedHelp")}
          </p>
        </div>
      ) : samples.length === 0 ? (
        <div className="notice">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>
            {t("emptyHelp")}
          </p>
        </div>
      ) : (
        <>
          <p className="subtitle">
            {t("description", { unit: locale === "en" ? CAPACITY_UNIT : t("unit") })}
          </p>
          <TableScroll label={t("label")}>
            <table className="stats-table stats-capacity">
              <caption className="sr-only">
                {t("caption")}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("benchmarked")}</th>
                  <th scope="col" className="num">
                    {t("average")}
                  </th>
                  <th scope="col" className="num">
                    {t("base")}
                  </th>
                  <th scope="col" className="num">
                    {t("host")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {samples.map((s) => (
                  <tr key={s.capacity_timestamp}>
                    <th scope="row">
                      <UtcTime value={s.capacity_timestamp} />
                    </th>
                    <td className="num">
                      <Figure value={s.average_capacity} locale={locale} missing={t("notReported")} />
                    </td>
                    <td className="num">
                      <Figure value={s.base_capacity} locale={locale} missing={t("notReported")} />
                    </td>
                    <td className="num">
                      {s.base_host_id === null ? (
                        <span className="muted" aria-label={t("notReported")}>
                          —
                        </span>
                      ) : (
                        s.base_host_id
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </>
      )}
    </section>
  );
}
