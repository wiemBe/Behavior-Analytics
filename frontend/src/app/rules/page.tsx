import { api, type Page, type RuleHealthCount, type RuleSummary } from "@/lib/api";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { rulesAsOf } from "@/lib/home/asOf";
import { loadErrorMessage } from "@/lib/loadError";
import { StatCard } from "@/components/StatCard";
import { Pagination } from "@/components/Pagination";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  healthMeaning,
  healthTone,
  isUnestablished,
} from "@/lib/health";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { renderBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import type { Locale } from "@/i18n/locale";
import { formatCount } from "@/lib/format";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "@/components/ui/controls.css";
import "./rules.css";

const PAGE_SIZE = 25;

/** The sentence under a rule name: why the verdict is not HEALTHY. Null shows nothing. */
function rowReason(
  r: RuleSummary,
  fallback: string,
  locale: Locale,
  say: TextTranslator,
): string | null {
  // A healthy rule needs no explanation, and a rule never evaluated has no verdict to explain.
  if (r.health_status == null || r.health_status === "HEALTHY" || r.health_status === "UNKNOWN") {
    return null;
  }
  const text = r.health_reason?.trim();
  if (!text) return fallback;
  // The reader falls back to the API text when the code is absent or unknown.
  return renderBackendMessage(
    { text, code: r.health_reason_code, params: r.health_reason_params },
    locale,
    say,
  );
}

export async function generateMetadata() {
  const t = await getTranslations("rules.meta");
  return { title: t("list") };
}

async function RulesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const search = params.search ?? "";
  const health = params.health_status ?? "";
  const t = await getTranslations("rules");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;

  let page: Page<RuleSummary> | null = null;
  let summary: RuleHealthCount[] = [];
  let error: string | null = null;

  try {
    [page, summary] = await Promise.all([
      api.rules({ limit: PAGE_SIZE, offset, search, health_status: health }),
      api.ruleHealthSummary(),
    ]);
  } catch (err) {
    error = loadErrorMessage(err, t("loadNoun"), "read:rules", say);
  }

  if (error) {
    return (
      <>
        <PageHeader
          guide={guideFor("/rules")}
          title={t("title")}
          actions={<Link href="/rules/statistics">{t("statisticsLink")}</Link>}
        />
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      </>
    );
  }

  const items = page?.items ?? [];
  const unestablished = summary
    .filter((s) => isUnestablished(s.status))
    .reduce((n, s) => n + s.count, 0);

  return (
    <>
      <ReportDataAge iso={rulesAsOf(items)} />
      <PageHeader
        guide={guideFor("/rules")}
        title={t("title")}
        description={t("description")}
        actions={<Link href="/rules/statistics">{t("statisticsLink")}</Link>}
      />

      {summary.length > 0 && (
        <div className={`cards rules-cards rules-cards-${Math.ceil(summary.length / 2)}`}>
          {summary.map((s) => (
            <StatCard
              key={s.status}
              label={enumLabel(say, "ruleHealth", s.status)}
              value={formatCount(s.count, locale)}
              tone={healthTone(s.status) || undefined}
            />
          ))}
        </div>
      )}

      {unestablished > 0 && (
        <div className="notice">
          <strong>{t("summaryNoticeTitle", { count: formatCount(unestablished, locale) })}</strong>{" "}
          {t("summaryNotice")}
        </div>
      )}

      <form className="filters" method="get" action="/rules">
        <input
          type="search"
          name="search"
          defaultValue={search}
          placeholder={t("filters.placeholder")}
          aria-label={t("filters.search")}
        />
        <select
          name="health_status"
          defaultValue={health}
          aria-label={t("filters.health")}
        >
          <option value="">{t("filters.all")}</option>
          {summary.map((s) => (
            <option key={s.status} value={s.status}>
              {enumLabel(say, "ruleHealth", s.status)}
            </option>
          ))}
        </select>
        <button type="submit">{t("filters.filter")}</button>
        {(search || health) && <Link href="/rules">{t("filters.clear")}</Link>}
      </form>

      {items.length === 0 ? (
        <div className="notice">
          {search || health
            ? t("emptyFiltered")
            : t("empty")}
        </div>
      ) : (
        <>
          <TableScroll label={t("table.label")}>
          <table className="rule-list-table table-dense" aria-label={t("table.label")}>
            <thead>
              <tr>
                <th>{t("table.rule")}</th>
                <th>{t("table.type")}</th>
                <th>{t("table.enabled")}</th>
                <th>{t("table.health")}</th>
                <th>{t("table.meaning")}</th>
                <th className="col-time">{t("table.lastFired")}</th>
                <th className="num">{t("table.offenses")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => {
                const reason = rowReason(r, t("noReason"), locale, say);
                return (
                <tr key={r.id}>
                  <td className="cell-rule-name">
                    <Link href={`/rules/${r.id}`}>{r.name}</Link>
                    {reason && (
                      <p
                        className="muted rule-reason"
                        title={reason}
                        aria-label={t("table.healthReason", { reason })}
                      >
                        {reason}
                      </p>
                    )}
                  </td>
                  <td>{r.rule_type ?? "—"}</td>
                  <td>
                    <span className={`pill ${r.enabled ? "ok" : "warn"}`}>
                      {r.enabled ? t("table.enabledValue") : t("table.disabledValue")}
                    </span>
                  </td>
                  <td>
                    <span className={`pill ${healthTone(r.health_status)}`}>
                      {r.health_status ? enumLabel(say, "ruleHealth", r.health_status) : "—"}
                    </span>
                  </td>
                  <td className="muted cell-meaning">{healthMeaning(r.health_status, say)}</td>
                  <td>
                    {r.last_fired_at ? (
                      <UtcTime value={r.last_fired_at} wrapZone />
                    ) : (
                      <span className="muted">{t("table.notObserved")}</span>
                    )}
                  </td>
                  <td className="num">{formatCount(r.offense_contribution_count, locale)}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
          </TableScroll>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/rules"
            params={{ search, health_status: health }}
          />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/rules", RulesPage);
