// The rules that carry the matching load, ranked by matched events (default)
// or by match records. The rate is matched events per second over the seconds
// that were measured, so a half-collected span does not read as a low rate.
//
// A `null` list is a measurement that does not exist; its reason is shown and
// it is never drawn as an empty ranking.

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { MatchRankRow } from "@/lib/api";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";
import { useBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator } from "@/i18n/textTranslator";

import {
  formatCount,
  formatRate,
  kindLabel,
  statsHref,
  typeLabel,
  typeTitle,
  type Rank,
  type StatsQuery,
} from "./ruleStatistics";

const RANKS: Rank[] = ["events", "matches"];

/**
 * A rate under the display floor reads "< 0.01". As a lower bound, "≥ < 0.01" is gibberish and
 * "≥ 0.01" would be false (the measured bound is below 0.01), so the cell says what is true: the
 * figure is below the floor and it is a lower bound. No digits are invented for it.
 */
function RateBound({
  lower,
  perSecond,
  locale,
  t,
}: {
  lower: boolean;
  perSecond: number;
  locale: Locale;
  t: ReturnType<typeof useTranslations<"rules.matched">>;
}) {
  const text = formatRate(perSecond, locale);
  if (lower && text.startsWith("<")) return <span>{t("floorLower", { value: text })}</span>;
  return <Bound lower={lower}>{text}</Bound>;
}

function Bound({ lower, children }: { lower: boolean; children: string }) {
  return lower ? <span>≥&nbsp;{children}</span> : <>{children}</>;
}

export function MostMatched({
  rows,
  reason,
  reasonCode,
  reasonParams,
  query,
}: {
  /** The list for `query.rank`; null when matches could not be measured. */
  rows: MatchRankRow[] | null;
  reason: string | null;
  /** The code and raw values `reason` was built from, when the API sent them. */
  reasonCode?: string | null;
  reasonParams?: Record<string, unknown> | null;
  query: StatsQuery;
}) {
  const t = useTranslations("rules.matched");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const backend = useBackendMessage();
  const reasonText =
    reason === null ? null : backend({ text: reason, code: reasonCode, params: reasonParams });
  const anyLower = (rows ?? []).some((r) => r.lower_bound);

  return (
    <section aria-labelledby="stats-top">
      <div className="section-head row between">
        <h2 className="section-title" id="stats-top">
          {t("heading")}
        </h2>
        <nav className="range-selector" aria-label={t("rankBy")}>
          <span className="muted">{t("rankBy")}</span>
          {RANKS.map((rank) => (
            <Link
              key={rank}
              href={statsHref({ ...query, rank })}
              className={`range-option${rank === query.rank ? " range-option-active" : ""}`}
              aria-current={rank === query.rank ? "page" : undefined}
            >
              {rank === "events" ? t("eventsRank") : t("recordsRank")}
            </Link>
          ))}
        </nav>
      </div>

      {rows === null ? (
        <div className="banner" role="status">
          <div>
            <p>
              <strong>{reasonText ?? t("missingTitle")}</strong>
            </p>
            <p>{t("missingHelp")}</p>
          </div>
        </div>
      ) : rows.length === 0 ? (
        <div className="notice">
          <p>
            <strong>{t("empty")}</strong>
          </p>
        </div>
      ) : (
        <>
          {reason && (
            <p className="table-note stats-top-reason">
              <strong>{t("partial")}</strong> {reasonText}
            </p>
          )}
          <TableScroll label={t("label")}>
            <table className="stats-table stats-top">
              <caption className="sr-only">
                {query.rank === "events" ? t("captionEvents") : t("captionRecords")}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("rule")}</th>
                  <th scope="col" className="stats-wide-fact">
                    {t("type")}
                  </th>
                  <th scope="col" className="stats-wide-fact">
                    {t("buildingBlock")}
                  </th>
                  <th scope="col" className="num">
                    {t("matchedEvents")}
                  </th>
                  <th scope="col" className="num">
                    {t("matchRecords")}
                  </th>
                  <th scope="col" className="num">
                    {t("matchRate")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.rule_id}>
                    <th scope="row" className="cell-rule">
                      <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                      <span className="cell-sub stats-narrow-fact">
                        {kindLabel(r.rule_type, r.is_building_block, say)}
                        {r.enabled ? "" : t("disabledSuffix")}
                      </span>
                    </th>
                    <td className="stats-wide-fact">
                      <span title={typeTitle(r.rule_type, say)}>{typeLabel(r.rule_type, say)}</span>
                    </td>
                    <td className="stats-wide-fact">
                      {r.is_building_block ? say("rules.detail.yes") : say("rules.detail.no")}
                    </td>
                    <td className="num">
                      <Bound lower={r.lower_bound}>{formatCount(r.matched_events, locale)}</Bound>
                    </td>
                    <td className="num">
                      <Bound lower={r.lower_bound}>{formatCount(r.match_records, locale)}</Bound>
                    </td>
                    <td className="num">
                      <RateBound lower={r.lower_bound} perSecond={r.match_rate_per_second} locale={locale} t={t} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          {anyLower && (
            <p className="table-note">
              <strong>≥</strong> {t("lowerNote", { meaning: say("rules.helpers.lowerBoundMeaning") })}
            </p>
          )}
        </>
      )}
    </section>
  );
}
