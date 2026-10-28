// Enabled rules with a measured zero over a complete span.
//
// Silence is a claim, so the section says what it rests on: only rules whose
// matches an event search sees in full are judged, and the count of those that
// were not judged is stated beside the list. A `null` section is a judgement
// that was not made and is never drawn as "0 silent rules".

import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { SilentSection } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/locale";
import { useBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";

import {
  formatCount,
  kindLabel,
  originLabel,
  typeLabel,
  typeTitle,
} from "./ruleStatistics";

function evidenceText(evidence: string, locale: Locale, t: TextTranslator): string {
  if (evidence === "LOWER_BOUND") return t("rules.silent.evidenceLower");
  if (evidence === "NOT_MEASURED") return t("rules.silent.evidenceMissing");
  // Preserve the legacy English presentation while showing an API sentence verbatim in Turkish.
  return locale === "en" ? evidence.replaceAll("_", " ").toLowerCase() : evidence;
}

function Instant({ value, missing }: { value: string | null; missing: string }) {
  if (!value) return <span className="muted">{missing}</span>;
  return <UtcTime value={value} />;
}

export function SilentRules({
  silent,
  reason,
  reasonCode,
  reasonParams,
}: {
  silent: SilentSection | null;
  reason: string | null;
  /** The code and raw values `reason` was built from, when the API sent them. */
  reasonCode?: string | null;
  reasonParams?: Record<string, unknown> | null;
}) {
  const t = useTranslations("rules.silent");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const backend = useBackendMessage();
  return (
    <section aria-labelledby="stats-silent">
      <div className="section-head">
        <h2 className="section-title" id="stats-silent">
          {t("heading")}
        </h2>
      </div>

      {silent === null ? (
        <div className="banner" role="status">
          <div>
            <p>
              <strong>{t("notJudged")}</strong> {t("notJudgedHelp")}
            </p>
            <p className="stats-window-reason">{(reason === null
              ? null
              : backend({ text: reason, code: reasonCode, params: reasonParams })) ??
              t("noReason")}</p>
          </div>
        </div>
      ) : (
        <>
          <p className="subtitle">
            {silent.items_truncated
              ? t("showing", {
                  shown: formatCount(silent.items.length, locale),
                  total: formatCount(silent.total, locale),
                })
              : silent.total === 0
                ? t("none")
                : t("count", { count: silent.total })}{" "}
            {t("recency")}
          </p>

          {silent.items.length > 0 && (
            <TableScroll label={t("label")}>
              <table className="stats-table stats-silent">
                <caption className="sr-only">
                  {t("caption")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t("rule")}</th>
                    <th scope="col" className="stats-wide-fact">
                      {t("type")}
                    </th>
                    <th scope="col">{t("firstSeen")}</th>
                    <th scope="col">{t("lastFired")}</th>
                  </tr>
                </thead>
                <tbody>
                  {silent.items.map((r) => (
                    <tr key={r.rule_id}>
                      <th scope="row" className="cell-rule">
                        <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                        <span className="cell-sub stats-narrow-fact">
                          {kindLabel(r.rule_type, r.is_building_block, say)}, {originLabel(r.origin, say)}
                        </span>
                      </th>
                      <td className="stats-wide-fact">
                        <span title={typeTitle(r.rule_type, say)}>{typeLabel(r.rule_type, say)}</span>
                        <span className="cell-sub">{originLabel(r.origin, say)}</span>
                      </td>
                      <td>
                        <Instant value={r.first_seen_at} missing={t("notRecorded")} />
                      </td>
                      <td>
                        <Instant value={r.last_fired_at} missing={t("notObserved")} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}

          {silent.excluded_count > 0 && (
            <div className="stats-excluded">
              <p>
                <strong>
                  {t("excluded", { count: silent.excluded_count })}
                </strong>{" "}
                {t("because", {
                  // English lower-cases the API sentence to read on after "because". The Turkish
                  // sentence opens with the type name EVENT, which stays upper case mid-clause.
                  reason: locale === "en"
                    ? lowerFirst(silent.excluded_reason)
                    : backend({
                        text: silent.excluded_reason,
                        code: silent.excluded_reason_code,
                        params: silent.excluded_reason_params,
                      }),
                })}
              </p>
              <div className="stats-excluded-detail">
                <h3>{t("excludedHeading")}</h3>
                <TableScroll label={t("excludedLabel")}>
                <table className="stats-table stats-excluded-table">
                  <caption className="sr-only">{t("excludedCaption")}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("type")}</th>
                      <th scope="col">{t("why")}</th>
                      <th scope="col" className="num">
                        {t("rules")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {silent.excluded.map((x) => (
                      <tr key={`${x.rule_type ?? "null"}:${x.evidence}`}>
                        <th scope="row">
                          <span title={typeTitle(x.rule_type, say)}>{typeLabel(x.rule_type, say)}</span>
                        </th>
                        <td>{evidenceText(x.evidence, locale, say)}</td>
                        <td className="num">{formatCount(x.count, locale)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </TableScroll>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// Lower-cases the first letter so the reason reads on after "because", but leaves an
// initialism alone: "FLOW rules ..." must not become "fLOW rules ...".
function lowerFirst(s: string): string {
  if (s.length === 0 || (s.length > 1 && s.charAt(1) === s.charAt(1).toUpperCase() && /[A-Za-z]/.test(s.charAt(1)))) {
    return s;
  }
  return s.charAt(0).toLowerCase() + s.slice(1);
}
